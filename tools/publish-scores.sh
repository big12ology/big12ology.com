#!/usr/bin/env bash
# Hand the scores straight to the Worker.
#
#     tools/publish-scores.sh [path-to-pickem-scores.json]
#
# Needs SCORES_INGEST_KEY in the environment. Optional: B12_API_BASE, to point
# at somewhere other than production.
#
# WHAT THIS REPLACES. The scores used to reach the Worker by being written into
# a 415-file static deploy: build.py wrote the file, assemble.sh copied it,
# Pages published it, and up to an hour later the Worker's sweep fetched it
# from the Pages origin and graded. Publishing one changed number meant
# redeploying the whole domain, which is why pages.yml's cron had to stay under
# Pages' deployment rate limiting and why a final could sit on television for
# an hour before a board moved.
#
# This is the direct channel. The Worker verifies, stores, and grades in the
# same request, so the board moves while this script is still waiting on the
# response.
#
# THE SIGNATURE COVERS THE BYTES, NOT THIS SCRIPT. `${timestamp}.${body}` is
# HMAC-SHA256'd with the shared key, and worker/src/ingest.js verifies with
# crypto.subtle.verify. So a truncated upload or a half-written file is
# refused rather than graded. Do not "fix" a signature mismatch by
# re-serializing the JSON here — sign and send the same bytes, or the two ends
# stop agreeing about what was signed.
#
# The key is NOT a Cloudflare token, deliberately. worker.yml's header explains
# why this workflow must not hold one: it runs hundreds of times a season, and
# a Cloudflare token would let every one of those runs deploy the Worker or
# read D1. This key publishes scores and can do nothing else.
set -euo pipefail

FILE="${1:-tiebreaker/site/pickem-scores.json}"
BASE="${B12_API_BASE:-https://big12ology.com}"

[ -n "${SCORES_INGEST_KEY:-}" ] || {
  echo "SCORES_INGEST_KEY is not set. Nothing was published." >&2
  exit 1
}
[ -f "$FILE" ] || {
  echo "no scores file at $FILE" >&2
  exit 1
}

# Sanity before signing. A file that is not what we think it is should fail
# here, where the message is about this repo, rather than as a 400 from the
# Worker that somebody has to go and read.
python3 - "$FILE" <<'PY'
import json, os, sys
path = sys.argv[1]
doc = json.load(open(path))
if not isinstance(doc.get("games"), dict):
    sys.exit("scores file has no games object")
if not isinstance(doc.get("season"), int):
    sys.exit("scores file has no integer season")

# AND THAT IT SAYS WHAT THE REPO KNOWS. Shape was all that was checked here,
# which let through a file of exactly the right shape and a month out of date.
#
# 2026-09-05: scores.yml never set B12_PICKEM, so build.py's pick'em block --
# write_scores included -- was skipped on every run of the workflow whose one
# job is publishing scores. The file was never rewritten, so this script signed
# and shipped the copy git had checked out: 120 games, every one of them
# [null, null, false]. Games inside the void window were unharmed, because a
# missing result reads as "still to come". Games past thirty-six hours were
# written off. pages.yml, which does set the variable, published a correct file
# on its own cadence and put them back, so the two workflows spent a weekend
# undoing each other. A week 0 game reached revision 42 before the results
# history landed and made the flapping visible.
#
# games_<season>.json is what build.py built this payload FROM, sits in the
# same checkout, and is committed by these same workflows. If it knows a game
# finished and the payload does not, the payload is stale whatever its shape.
games_file = os.path.join(os.path.dirname(os.path.abspath(path)),
                          "..", "data", f"games_{doc['season']}.json")
scored = sum(1 for v in doc["games"].values()
             if isinstance(v, list) and len(v) == 3 and v[2]
             and v[0] is not None and v[1] is not None)
if os.path.exists(games_file):
    known = sum(1 for g in json.load(open(games_file))
                if g.get("completed") and g.get("home_points") is not None
                and g.get("away_points") is not None)
    if scored < known:
        sys.exit(f"REFUSING TO PUBLISH. games_{doc['season']}.json has {known} "
                 f"finished game(s) and this file carries {scored}, so it was "
                 f"not rebuilt this run. Check that B12_PICKEM is set: "
                 f"build.py writes no scores without it.")
else:
    # The e2e test signs a payload from a temp directory with no repo around
    # it. Said out loud rather than passed over, so a real run that has somehow
    # lost its data directory cannot look like a clean one.
    print(f"note: no {os.path.basename(games_file)} beside the payload, "
          f"published without the cross-check")
print(f"publishing {len(doc['games'])} games for {doc['season']}, "
      f"{scored} finished")
PY

TS=$(date -u +%s)

# base64url, to match crypto.js's b64url: standard base64, + and / swapped for
# - and _, padding stripped. The Worker's unb64url puts the padding back.
#
# The timestamp and a literal dot are prepended to the file's bytes. `cat` and
# a printf rather than a shell variable: a JSON file passed through $(...)
# loses trailing newlines and mangles anything the shell thinks is special,
# and the signature is over bytes.
SIG=$( { printf '%s.' "$TS"; cat "$FILE"; } \
       | openssl dgst -sha256 -hmac "$SCORES_INGEST_KEY" -binary \
       | openssl base64 -A \
       | tr '+/' '-_' | tr -d '=' )

# --data-binary, never --data: the latter strips newlines, which would change
# the bytes after they were signed and produce a bad_signature that looks like
# a key problem.
#
# Posted on every run rather than only when something changed. The Worker
# already knows whether anything moved — scoreWeek computes it and skips the
# downstream rebuild when it did not — so a quiet run costs one invocation and
# a few reads, and the alternative is keeping a published-hash somewhere
# between runs that has its own ways of being wrong.
code=$(curl -sS --max-time 30 -o /tmp/publish-scores.out -w '%{http_code}' \
         -X POST "$BASE/api/ingest/scores" \
         -H "Content-Type: application/json" \
         -H "X-B12-Timestamp: $TS" \
         -H "X-B12-Signature: $SIG" \
         --data-binary "@$FILE") || {
  echo "could not reach $BASE/api/ingest/scores" >&2
  exit 1
}

echo "HTTP $code: $(cat /tmp/publish-scores.out)"

if [ "$code" != "200" ]; then
  echo "the scores were NOT published." >&2
  # Worth naming, because these two are the ones that will actually happen and
  # they look nothing alike from the outside.
  case "$code" in
    401|403) echo "  signature refused — is SCORES_INGEST_KEY the same on" >&2
             echo "  both ends, and is this runner's clock right?" >&2 ;;
    503)     echo "  the Worker has no key or no SCORES namespace bound." >&2 ;;
  esac
  exit 1
fi

#!/usr/bin/env bash
# The pick'em, end to end, against a real Worker.
#
#     tools/pickem-e2e.sh
#
# Stands up the whole stack, drives the API the built client is written
# against, and tears it down. Exits non-zero on the first thing that is wrong.
#
# WHAT THIS EXISTS TO CATCH. worker/test/*.test.js calls the handlers directly
# with a Request it builds itself, so everything between the browser and the
# handler is assumed rather than tested: that the Origin the client sends is
# the one SITE_ORIGIN expects, that the session cookie survives the round trip,
# that the slate pickem.py writes is the shape importWeek parses, that a lock
# landing mid-week reaches the player as a 409. The section was dark on
# production for a fortnight with none of that ever having been run together.
#
# IT TOUCHES NOTHING OUTSIDE ITS RUN DIRECTORY. Every previous attempt at this
# by hand went wrong in the same two ways, so both are designed out here:
#
#   * dist/ is not used. assemble.sh empties its target on every run, so a
#     concurrent build in another terminal deletes the fixtures mid-test. The
#     site is assembled into the run directory instead.
#   * .wrangler/state is not used. That is the developer's own local database,
#     usually holding a simulated season, and a published week whose lock_at
#     disagrees with it aborts every import on weeks_lock_monotonic. A fresh
#     --persist-to gets a clean D1 and leaves theirs alone.
#
# NO REAL CREDENTIALS. The values below are dev-only and generated per run;
# nothing here needs a client secret, because the sign-in flow is deliberately
# not part of this. See worker/test/e2e/seed.mjs for why, and for what that
# leaves uncovered.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Not 8787/8788. Those are what a developer's own `wrangler dev` and proxy sit
# on, and a harness that cannot run while you are looking at the thing it tests
# is a harness that gets run less.
PORT_PROXY="${B12_E2E_PROXY_PORT:-8799}"
PORT_API="${B12_E2E_API_PORT:-8798}"
SEASON="${B12_E2E_SEASON:-2026}"
BASE="http://localhost:${PORT_PROXY}"

# lsof is not on every runner image, so this degrades rather than breaks: the
# process-group kill in cleanup is the primary mechanism and needs none of
# this. What is lost without a way to list a port's holders is the preflight
# and the sweep, so say so once rather than silently running with less.
if command -v lsof >/dev/null 2>&1; then
  port_pids() { lsof -ti tcp:"$1" 2>/dev/null || true; }
elif command -v ss >/dev/null 2>&1; then
  port_pids() {
    ss -ltnHp "sport = :$1" 2>/dev/null |
      grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true
  }
else
  echo "  note: no lsof or ss, so ports are not pre-checked or swept" >&2
  port_pids() { :; }
fi

# BEFORE ANYTHING IS OWNED. cleanup sweeps these ports, which is only safe
# because nothing was listening on them when this started — so the check has to
# happen before the trap is installed, not after. The first version had it the
# other way round and a refused run went on to kill whatever it had just
# refused to run alongside.
for port in "$PORT_PROXY" "$PORT_API"; do
  if [ -n "$(port_pids "$port")" ]; then
    echo "port $port is in use, so this run would either fail on bind or" >&2
    echo "adopt somebody else's server. Set B12_E2E_PROXY_PORT /" >&2
    echo "B12_E2E_API_PORT, or stop what is on it." >&2
    exit 2
  fi
done

# Job control in a script, purely so each background job below lands in its own
# process group and cleanup can take the whole tree. `disown` afterwards keeps
# bash from printing "Killed: 9" over the test output when it reaps them.
set -m

RUN="$(mktemp -d "${TMPDIR:-/tmp}/b12-e2e.XXXXXX")"
SITE="$RUN/site"
STATE="$RUN/state"
WORKER_LOG="$RUN/worker.log"
PROXY_LOG="$RUN/proxy.log"
pids=()

# KILLING THE PID IS NOT ENOUGH, and the first version of this leaked a server
# on every run. `wrangler dev` is a supervisor: the thing actually holding the
# port is a workerd child, and killing the shell's job either leaves it running
# or lets the parent respawn it. The next run then dies on "Address already in
# use" with a stack trace that says nothing about why.
#
# So the ports are swept as well as the pids. That is only safe because the
# preflight below refuses to start when either port is occupied: anything
# listening there at cleanup time is therefore ours, and never a developer's
# own dev server on 8787/8788.
cleanup() {
  local code=$?
  # THE PROCESS GROUP, not the pid. `wrangler dev` is a supervisor and the
  # thing actually holding the port is a workerd child: killing the job leaves
  # the child orphaned, or lets the parent respawn it against the port that was
  # just freed. Either way the next run dies on "Address already in use" with a
  # stack trace that says nothing about why. `set -m` above puts each background
  # job in its own process group, so the negative pid takes the whole tree at
  # once. The port sweep after it is the belt to that pair of braces.
  for p in "${pids[@]:-}"; do
    kill -9 -"$p" 2>/dev/null || kill -9 "$p" 2>/dev/null || true
  done
  for port in "$PORT_PROXY" "$PORT_API"; do
    local n=0
    while [ -n "$(port_pids "$port")" ] && [ "$n" -lt 15 ]; do
      kill -9 $(port_pids "$port") 2>/dev/null || true
      n=$((n + 1)); sleep 1
    done
    [ -n "$(port_pids "$port")" ] && echo "  warning: port $port did not free" >&2
  done
  if [ "$code" != 0 ] && [ -s "$WORKER_LOG" ]; then
    echo "--- worker log (last 20) ---"; tail -20 "$WORKER_LOG"
  fi
  if [ "${B12_E2E_KEEP:-}" = "1" ]; then
    echo "run directory kept at $RUN"
  else
    rm -rf "$RUN"
  fi
  exit $code
}
trap cleanup EXIT INT TERM

say() { printf '  %s\n' "$*"; }

# --- the site the Worker reads, and the client the browser would --------------
#
# B12_PICKEM=1, because the section is still dark on production and assemble.sh
# will not lay down /pools/pickem at all without it. This is the only thing
# that exercises the lit shape against a live API.
say "assembling the lit site"
B12_PICKEM=1 tools/assemble.sh "$SITE" >/dev/null

# The scores fallback fetches the committed file at its repo path,
# /tiebreaker/site/pickem-scores.json, which the assembled site flattens to
# /tiebreaker/. Lay the repo-layout copy down too, since the proxy is playing
# raw.githubusercontent.com as well as the website.
mkdir -p "$SITE/tiebreaker/site"
cp "$SITE/tiebreaker/pickem-scores.json" "$SITE/tiebreaker/site/"

# --- the published weeks ------------------------------------------------------
#
# publish_slate() refuses to freeze a week more than LEAD_DAYS out and only runs
# on --refresh, so out of season there is nothing on disk to import. build_slate
# is the same payload without the disk rules, which is what is wanted: the real
# shape, written where only this run can see it.
#
# load_games(year) and load_lines(year) both read committed data. No CFBD call
# is made, and none should ever be added here: the key allows 1,000 a month.
say "publishing weeks 0, 1, 2 from committed data"
python3 - "$ROOT" "$SITE" "$SEASON" <<'PY'
import datetime, json, os, sys
root, site, season = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, os.path.join(root, "tiebreaker"))
import build as B, pickem as P
games, lines = B.load_games(season), B.load_lines(season)
# The repo-layout path RAW_ORIGIN serves, not the /pools/data/ path the
# website serves: the Worker imports the committed file, and the proxy is
# standing in for raw.githubusercontent.com here.
out = os.path.join(site, "tiebreaker", "pickem", str(season))
os.makedirs(out, exist_ok=True)

slates = []
for wk in (0, 1, 2):
    slate = P.build_slate(season, games, lines, wk)
    if slate is None:
        sys.exit(f"no slate for week {wk}: the fixture cannot be built")
    slates.append(slate)

# ONE CLOCK READING, AND A SEASON POSITIONED AGAINST IT.
#
# The data is real and therefore dated: week 0 is North Carolina at TCU on
# 29 August 2026. Published verbatim, that fixture is a week which has already
# locked by the time the calendar reaches it, so the week 0 pick the lock
# checks are built on can never be entered, and the republish that walks the
# lock into the past is instead moving it LATER, which weeks_lock_monotonic
# aborts by design. The abort takes the whole import batch, week 0 never lands,
# and six checks downstream of it fail against no data at all. That is what
# happened on 29 August 2026, with no commit behind it.
#
# So the slate keeps its real shape, its real teams and its real lines, and
# only its clock moves: every time in all three weeks shifts by one delta, read
# once, chosen to put week 0's lock LEAD seconds out. The spacing between the
# weeks stays the season's own, the ordering is preserved, and no check here
# depends on what day it is any more. Same fix as 796fa67 made to
# worker/test/slate.test.js: a fixture time derived from a moving clock is the
# bug, whether the clock moves a second or a fortnight.
T0 = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
LEAD = 6 * 3600
anchor = slates[0]["lock_at"]
if anchor is None:
    sys.exit("week 0 has no lock_at: the fixture cannot be positioned")
delta = (T0 + LEAD) - anchor

for slate in slates:
    slate["lock_at"] = None if slate["lock_at"] is None else slate["lock_at"] + delta
    for g in slate["games"]:
        # kickoff_tbd carries no time to move, and pickem.py has already
        # marked it unpickable.
        if g.get("kickoff_at") is None:
            continue
        g["kickoff_at"] += delta
        g["kickoff"] = datetime.datetime.fromtimestamp(
            g["kickoff_at"], datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
    with open(os.path.join(out, f"week-{slate['week']:02d}.json"), "w") as f:
        json.dump(slate, f, indent=1, sort_keys=True)
    print(f"    week {slate['week']}: {slate['game_count']} games, "
          f"{slate['pickable_count']} with a line, locking in "
          f"{(slate['lock_at'] - T0) / 3600:.1f}h")
PY

# --- a database of its own ----------------------------------------------------
say "migrating a fresh D1"
(cd worker && npx wrangler d1 migrations apply b12_pickem \
   --local --persist-to "$STATE" >/dev/null 2>&1)

# --- config, generated ---------------------------------------------------------
#
# SITE_ORIGIN is load-bearing twice over: it is what csrfOk compares Origin
# against, and the harness asserts that a write from anywhere else is refused.
# RAW_ORIGIN points back at the proxy, standing in for the repo's raw view,
# so the import reads the slates just written rather than anything on the
# internet.
# THE dummy- PREFIX IS LOAD-BEARING, and not a naming preference. The
# pre-commit hook refuses a secret-shaped value assigned to IDENTITY_PEPPER,
# SIGNING_KEY or ADMIN_TOKEN, and exempts exactly one thing: a value that
# announces itself as fake by starting test-, fake-, dummy-, example-,
# placeholder- or paste-. That exemption is the convention for fixtures needing
# secret-shaped values, so it is used here rather than committing with
# --no-verify. Bypassing the hook once to land a placeholder is how a real
# secret gets bypassed later.
INGEST_KEY="dummy-e2e-$(head -c 18 /dev/urandom | base64 | tr -d '=+/')"
cat > "$RUN/e2e.env" <<EOF
SITE_ORIGIN=$BASE
RAW_ORIGIN=$BASE
SEASON=$SEASON
IDENTITY_PEPPER=dummy-e2e-identity-pepper
STATE_SIGNING_KEY=dummy-e2e-state-signing-key
ADMIN_TOKEN=dummy-e2e-admin-token
SCORES_INGEST_KEY=$INGEST_KEY
SURVIVOR_RANKED_ENTRY_BY=6
EOF

# --- the two halves -----------------------------------------------------------
say "starting the Worker on :$PORT_API"
# --show-interactive-dev-session=false, or wrangler writes its banner and a
# line per request straight to the terminal, past the redirect, and buries the
# check output underneath it.
(cd worker && exec npx wrangler dev --config wrangler.toml \
   --port "$PORT_API" --persist-to "$STATE" --test-scheduled \
   --show-interactive-dev-session=false \
   --env-file "$RUN/e2e.env" >"$WORKER_LOG" 2>&1) &
pids+=($!)
disown %% 2>/dev/null || true

say "starting the proxy on :$PORT_PROXY"
PORT="$PORT_PROXY" API="http://127.0.0.1:$PORT_API" ROOT="$SITE" \
  node worker/test/e2e/proxy.mjs >"$PROXY_LOG" 2>&1 &
pids+=($!)
disown %% 2>/dev/null || true

wait_for() {
  local url=$1 name=$2 n=0
  until curl -fsS -o /dev/null "$url" 2>/dev/null; do
    n=$((n + 1))
    [ "$n" -lt 120 ] || { echo "  $name never came up at $url"; return 1; }
    sleep 1
  done
  say "$name up"
}
wait_for "http://127.0.0.1:$PORT_API/api/health" "worker"
wait_for "$BASE/pools/pickem/" "proxy"

# --- a player, without a sign-in ----------------------------------------------
node worker/test/e2e/seed.mjs "$RUN" | sed 's/^/  /'
(cd worker && npx wrangler d1 execute b12_pickem --local \
   --persist-to "$STATE" --file "$RUN/seed.sql" >/dev/null 2>&1)

# --- import, then assert -------------------------------------------------------
say "importing the slates through the cron"
curl -fsS -o /dev/null "http://127.0.0.1:$PORT_API/__scheduled?cron=0+13+*+8-12+3"

echo
E2E_BASE="$BASE" E2E_WORKER="http://127.0.0.1:$PORT_API" \
E2E_SITE="$SITE" E2E_RUN="$RUN" E2E_SEASON="$SEASON" \
E2E_INGEST_KEY="$INGEST_KEY" \
  node worker/test/e2e/checks.mjs

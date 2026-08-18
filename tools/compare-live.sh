#!/usr/bin/env bash
#
# Compare an assembled dist/ against what big12ology.com serves right now.
# This is the pre-cutover check: the monorepo must produce the site that is
# already live before Pages is pointed at it.
#
#     tools/assemble.sh dist && tools/compare-live.sh dist
#     tools/compare-live.sh dist https://big12ology.com
#
# Files fall into three buckets:
#
#   static      non-HTML assets: must match byte for byte. Any difference is a
#               real finding.
#   stamped     every HTML page. All of them now carry "last updated <time>"
#               in the shared footer, so the live copy and a fresh assemble
#               never match byte for byte — they were stamped at different
#               minutes, and against a site deployed yesterday they differ by
#               a day. These are compared again with the stamp, dates, times
#               and ?v= hashes normalized away; if they match after that, the
#               difference is only the stamp.
#   missing     not served live. Expect a short list, and know each entry.
#
# Every page used to be byte-comparable because only lab.html carried a time.
# Tolerating the stamp on all HTML is the cost of one consistent footer; note
# that it is *only* the stamp being ignored — every other byte still has to
# match, so a real content change still shows up as DIFFERS.
#
# Exits non-zero if any static file differs, is missing, or the edge canary
# fails.
#
# Pages are fetched from the ORIGIN (GitHub Pages) rather than through
# Cloudflare's proxy — see the --connect-to note below. A separate six-request
# canary does go through the edge, to catch the settings that would silently
# rewrite or stale the site. `MODE=edge` compares everything through the proxy
# instead; that is a deliberate spot check, not the CI gate.
#
set -euo pipefail

DIST="${1:-dist}"
BASE="${2:-https://big12ology.com}"
[ -d "$DIST" ] || { echo "no such directory: $DIST" >&2; exit 2; }

# Fetch the ORIGIN, not the edge.
#
# big12ology.com is served through Cloudflare's proxy, so a plain GET of the
# public URL answers from Cloudflare: it can hand back an edge-cached copy of a
# file this build just changed, and any body-rewriting feature (email
# obfuscation, script injection) mutates the HTML on the way out. Both show up
# here as DIFFERS on a file that is actually correct, and the byte-exact bucket
# has no normalization to absorb it.
#
# --connect-to dials GitHub Pages directly while leaving Host and SNI as
# big12ology.com, so Pages serves the custom-domain site (no redirect) and TLS
# still validates against the real certificate (no -k). Cloudflare's cache, bot
# management and rate limiting are simply not in the path — which is also why
# this loop no longer adds ~265 requests to the site's own traffic analytics.
#
# MODE=edge compares against Cloudflare on purpose. Useful once after a DNS
# change; never as the CI gate, which must not be coupled to a CDN's cache.
#
# Since 2026-08-18 that coupling is real for HTML as well: pages are
# edge-cached for up to ten minutes (see the canary below for why), so an edge
# comparison inside that window of a deploy will report DIFFERS on pages that
# are actually correct. Origin mode never sees the window, which is one more
# reason it is the gate.
HOST="${BASE#*://}"; HOST="${HOST%%/*}"
MODE="${MODE:-origin}"
ORIGIN_HOST="${ORIGIN_HOST:-big12ology.github.io}"

CURL=(-sS --connect-timeout 10 --max-time 30
      --retry 3 --retry-delay 2 --retry-connrefused --retry-all-errors)
case "$MODE" in
  origin) CURL+=(--connect-to "${HOST}:443:${ORIGIN_HOST}:443") ;;
  edge)   ;;
  *) echo "MODE must be origin or edge (got '$MODE')" >&2; exit 2 ;;
esac
if [ "$MODE" = origin ]; then
  echo "comparing $DIST against $BASE (origin: $ORIGIN_HOST)"
else
  echo "comparing $DIST against $BASE (through the edge)"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

norm() {
  # The footer stamp is matched as a whole clause, up to the sentence period,
  # so it does not matter what format it is in: "August 7, 00:59 UTC" and a
  # stamp from three days ago both collapse to the same token. Matching the
  # clause rather than allowing a ±1 minute window is deliberate — a dry run
  # compares against whatever is live, which is routinely hours or days old,
  # and a one-minute tolerance would fail every time.
  sed -E -e 's/last updated [^.<]*/last updated STAMP/g' \
         -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}/DATE/g' \
         -e 's/[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?/TIME/g' \
         -e 's/\?v=[0-9A-Za-z]+/?v=HASH/g' "$1"
}

same=0; stamped=0; differ=0; missing=0
differ_list=(); missing_list=(); stamped_list=()

# --- fetch everything first, in parallel ----------------------------------
#
# One request at a time over four hundred files is four hundred round trips,
# which is why this script has never once run to completion: it takes minutes
# and gets killed before it says anything. The comparison itself is local and
# instant; only the fetching is slow, and fetching is the part that
# parallelises for free.
#
# PAR is deliberately modest. This points at GitHub Pages, and hammering
# somebody's origin to save forty seconds is rude at best.
PAR="${PAR:-12}"
mkdir -p "$TMP/live"
echo "fetching $(find "$DIST" -type f | wc -l | tr -d ' ') files, $PAR at a time..."
find "$DIST" -type f | sed "s|^$DIST/||" | grep -v '^CNAME$' \
  | xargs -P "$PAR" -I{} sh -c '
      out="$1/live/$2"
      mkdir -p "$(dirname "$out")"
      code=$(curl '"${CURL[*]}"' -o "$out" -w "%{http_code}" "$3/$2") || code=000
      printf "%s\t%s\n" "$code" "$2" >> "$1/codes"
    ' _ "$TMP" {} "$BASE"

while IFS=$'\t' read -r code rel; do
  file="$DIST/$rel"

  if [ "$code" != "200" ]; then
    missing=$((missing + 1)); missing_list+=("$rel  (HTTP $code)")
    continue
  fi

  if cmp -s "$file" "$TMP/live/$rel"; then
    same=$((same + 1))
    continue
  fi

  # Every HTML page carries the footer's build stamp, and the tiebreaker's
  # data exports carry their own; compare all of them normalized.
  case "$rel" in
    # */sitemap.xml catches the schedule section's own generated sitemap, whose
    # <lastmod> moves with every build. The root sitemap.xml is hand-maintained,
    # has no slash, and so stays byte-exact on purpose.
    *.html|*/sitemap.xml|tiebreaker/*.json|tiebreaker/*.xml|tiebreaker/*.csv)
      if diff -q <(norm "$file") <(norm "$TMP/live/$rel") >/dev/null; then
        stamped=$((stamped + 1)); stamped_list+=("$rel")
        continue
      fi
      ;;
  esac

  differ=$((differ + 1))
  differ_list+=("$rel")
  {
    echo "--- live/$rel"
    echo "+++ dist/$rel"
    # diff exits 1 on difference and pipefail would take that as fatal.
    diff <(norm "$TMP/live/$rel") <(norm "$file") | head -20 || true
  } >> "$TMP/diffs"
done < <(sort -k2 "$TMP/codes")

# --- edge canary -----------------------------------------------------------
# Fetching the origin above makes this script immune to the proxy, which also
# means it stops NOTICING the proxy. This is the payment for that: six requests
# that always go through Cloudflare and assert the things a misconfigured edge
# would silently change.
#
# The mailto assertion is positive on purpose — "this string must be present"
# is a claim no future tweak to norm() can quietly absorb, which is exactly how
# a detector like this normally dies.
edge_canary() {
  local body head rc=0 n
  head=$(curl -sSI --max-time 20 "$BASE/") || {
    echo "  CANARY  cannot reach $BASE/"; return 1; }

  # Is Cloudflare actually in front? While the DNS records are gray the apex
  # answers from GitHub directly, and every edge assertion below would be a
  # false alarm — there is no cf-cache-status to find because there is no edge.
  # Skipping keeps this script honest before the proxy is switched on, which is
  # also the state it has to pass in to prove origin mode works.
  if ! grep -qiE '^(server: *cloudflare|cf-ray:)' <<<"$head"; then
    echo "  not proxied (DNS-only, answering from the origin) — edge checks skipped"
    return 0
  fi

  body=$(curl -sS --max-time 30 "$BASE/") || {
    echo "  CANARY  cannot fetch $BASE/ through the edge"; return 1; }

  grep -q 'href="mailto:dept@big12ology.com"' <<<"$body" ||
    { echo "  CANARY  the mailto link is rewritten or gone (email obfuscation?)"; rc=1; }

  grep -qE 'email-decode|/cdn-cgi/(scripts|challenge)|rocket-loader' <<<"$body" &&
    { echo "  CANARY  Cloudflare injected a script into the HTML"; rc=1; }

  n=$(grep -c 'cloudflareinsights.com/beacon.min.js' <<<"$body" || true)
  [ "$n" = 1 ] ||
    { echo "  CANARY  analytics beacon appears $n times, expected 1 (auto-injection?)"; rc=1; }

  # HTML is edge-cached ON PURPOSE, since 2026-08-18. A Cache Rule makes pages
  # eligible (edge TTL follows GitHub's max-age=600, so ten minutes), a
  # 500-526 status override keeps error pages out of the cache, and a
  # stale-if-error response rule serves the last good copy through an origin
  # outage. The trade was taken after GitHub's 2026-08-17 incident served its
  # 503 unicorn through our domain: the cost is the cache window, a deploy can
  # take up to ten minutes to reach readers and the footer stamp lags with it;
  # the win is that a GitHub outage stops being our outage.
  #
  # So this assertion is the inverse of what it used to be. DYNAMIC or BYPASS
  # now means the Cache Rule is gone and every page view is back to hitting
  # the origin, with no stale copy standing by for the next incident.
  grep -qiE 'cf-cache-status: *(HIT|MISS|EXPIRED|UPDATING|REVALIDATED|STALE)' <<<"$head" ||
    { echo "  CANARY  HTML is not edge-cached (was the Cache Rule dropped?)"; rc=1; }

  # Mutable, frequently rebuilt, and carrying no ?v= — the files most likely to
  # go stale behind a CDN.
  local p
  for p in attendance/data/attendance.csv tiebreaker/standings.csv brand.css theme.js; do
    diff -q <(curl -sS --max-time 30 "$BASE/$p") \
            <(curl -sS --max-time 30 --connect-to "${HOST}:443:${ORIGIN_HOST}:443" \
                   "$BASE/$p") >/dev/null ||
      { echo "  CANARY  stale at the edge: $p"; rc=1; }
  done
  return $rc
}

canary=0
if [ "$MODE" = origin ]; then
  echo
  echo "edge canary ........"
  edge_canary || canary=1
  [ "$canary" -eq 0 ] && echo "  clean"
fi

echo
echo "identical .......... $same"
echo "stamp-only ......... $stamped   (generated, normalizes equal)"
echo "differ ............. $differ"
echo "not served live .... $missing"

for r in "${stamped_list[@]:-}"; do [ -n "$r" ] && echo "  stamp-only  $r"; done
for r in "${missing_list[@]:-}"; do [ -n "$r" ] && echo "  missing     $r"; done
for r in "${differ_list[@]:-}"; do [ -n "$r" ] && echo "  DIFFERS     $r"; done

if [ -s "$TMP/diffs" ]; then
  echo
  echo "=== differences (normalized, first 20 lines each) ==="
  cat "$TMP/diffs"
fi

[ "$differ" -eq 0 ] && [ "$missing" -eq 0 ] && [ "$canary" -eq 0 ]

#!/usr/bin/env bash
#
# Which pages people actually load, from Cloudflare Web Analytics.
#
#     tools/pageviews-report.sh [days]       # default: 7
#
# The other half of tools/events-report.sh, and the half that had been
# missing. That one reports the events the site sends from /api/e, which only
# exist once a reader scrolls: `read` fires at 25, 50, 75 and 100 percent and
# at nothing below that. So a page somebody opened and left produces no row at
# all, and a page shorter than the window fires 100 immediately and produces
# four. Ranking pages by those counts measures page LENGTH as much as traffic.
#
# It was wrong by enough to reorder the answer. On 2026-09-15, by scroll
# events, /schedule looked like a strong second to the Lab at 149 against 315.
# By pageloads it was 49 against 926. The Lab is about 65% of the site and the
# proxy had it at 42%; the per-game schedule pages looked dead and two of them
# are in the top fifteen.
#
# GRAPHQL, NOT THE REST ENDPOINT, and that distinction cost an afternoon.
# /accounts/{id}/rum/site_info/list answers 403 on this token and the
# permission it wants is not offered as an Account-scope option in the
# dashboard at all, which reads exactly like a permission that needs adding
# and is not one. The GraphQL analytics API serves the same RUM dataset and is
# happy with Account Analytics:Read, which the token has had all along. If
# this ever 403s, the answer is not to widen the token.
#
# Needs the same two variables events-report.sh does, and the same token:
#
#     CLOUDFLARE_ACCOUNT_ID   the account the site is on
#     CLOUDFLARE_API_TOKEN    a token with Account Analytics:Read
set -euo pipefail

DAYS="${1:-7}"
: "${CLOUDFLARE_ACCOUNT_ID:?set CLOUDFLARE_ACCOUNT_ID}"
: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN}"

# BSD date on this Mac, GNU date in CI, and neither accepts the other's flag.
if FROM="$(date -u -v-"${DAYS}"d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)"; then :; else
  FROM="$(date -u -d "${DAYS} days ago" +%Y-%m-%dT%H:%M:%SZ)"
fi
TO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# SCOPED TO THIS HOST, because the query is not. rumPageloadEventsAdaptiveGroups
# hangs off the ACCOUNT, and this account serves more than one site, so an
# unfiltered query quietly reports somebody else's traffic beside ours. It is not
# loud about it either: the first run of this script listed /about/ and /work/,
# which belong to another site on the account and simply look like pages nobody
# remembered building. Filter by host and the question matches the answer.
SITE="${B12_SITE:-big12ology.com}"
WHERE="datetime_geq:\\\"$FROM\\\",datetime_leq:\\\"$TO\\\",requestHost:\\\"$SITE\\\""

# rumPageloadEventsAdaptiveGroups is sampled and hands back a weight per
# group, the same trap sum(_sample_interval) exists for in the events report.
# `count` is already the estimated total rather than the rows kept, so it is
# the number to print and no reweighting is needed here.
#
# Pageloads, not sessions. The dataset has no `visits` field on this node, so
# there is no way to ask how many PEOPLE from here: two loads is one reader
# returning or two readers arriving and this cannot tell them apart. That is
# a real limit of the number below and not worth papering over.
query() {
  printf '{"query":"query{viewer{accounts(filter:{accountTag:\\"%s\\"}){%s}}}"}' \
    "$CLOUDFLARE_ACCOUNT_ID" "$1"
}

run() {
  local title="$1" selection="$2"
  echo
  echo "$title"
  printf -- '-%.0s' $(seq ${#title}); echo
  curl -sS -m 40 https://api.cloudflare.com/client/v4/graphql \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$(query "$selection")" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
try:
    d = json.loads(raw)
except json.JSONDecodeError:
    print("  " + (raw[:400] or "(no answer)")); sys.exit(0)
if d.get("errors"):
    print("  " + json.dumps(d["errors"])[:400]); sys.exit(0)
accts = ((d.get("data") or {}).get("viewer") or {}).get("accounts") or []
rows = []
for a in accts:
    for v in a.values():
        rows = v or []
if not rows:
    print("  (nothing yet)"); sys.exit(0)
flat = []
for r in rows:
    out = dict(r.get("dimensions") or {})
    for k, v in r.items():
        if k != "dimensions":
            out[k] = v
    flat.append(out)
cols = list(flat[0].keys())
w = {c: max(len(c), *(len(str(r.get(c, ""))) for r in flat)) for c in cols}
total = sum(r.get("count", 0) for r in flat)
print("  " + "  ".join(c.ljust(w[c]) for c in cols))
for r in flat:
    print("  " + "  ".join(str(r.get(c, "")).ljust(w[c]) for c in cols))
if total:
    print(f"\n  {total} pageloads in the rows above")
'
}

echo "pageviews for $SITE — last ${DAYS} days"

# EVERY HOST THE ACCOUNT SAW, ours marked, and it runs first on purpose.
# The filter below is exact and the mapping is one site tag per host, so the
# page counts cannot cross sites. But a comment claiming that is not evidence,
# and the way it goes wrong is silent in both directions: a second site appears
# and inflates the totals, or this site starts serving a host the filter does
# not name and the totals quietly shrink. Printing the boundary on every run
# makes both visible in the one place somebody is already looking.
#
# The site tag is not derivable from this repo. It is not the beacon token in
# the pages, which is a different value, so there is nothing to check it
# against here and the host is what the filter keys on.
run "hosts in this account" "rumPageloadEventsAdaptiveGroups(limit:20,filter:{datetime_geq:\\\"$FROM\\\",datetime_leq:\\\"$TO\\\"},orderBy:[count_DESC]){count,dimensions{siteTag,requestHost}}"

# The whole question, and usually the only one worth asking. Paths, not
# sections: the section rollup is what events-report.sh already gives and it
# is the per-page number that nothing else on this site can answer.
run "by page" "rumPageloadEventsAdaptiveGroups(limit:40,filter:{$WHERE},orderBy:[count_DESC]){count,dimensions{requestPath}}"

# Where they come from. The reason this is here rather than in the events
# report is that the events pipeline deliberately has no referrer in it, so
# this is the only place the question can be asked at all. Search engines
# showing up here is the first sign indexing is working.
run "by referrer" "rumPageloadEventsAdaptiveGroups(limit:15,filter:{$WHERE},orderBy:[count_DESC]){count,dimensions{refererHost}}"

# Phone or desktop, which decides which of the two layouts is the real one.
run "by device" "rumPageloadEventsAdaptiveGroups(limit:10,filter:{$WHERE},orderBy:[count_DESC]){count,dimensions{deviceType}}"

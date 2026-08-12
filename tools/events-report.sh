#!/usr/bin/env bash
#
# What people did on the pages, from the events /api/e writes.
#
#     tools/events-report.sh [days]        # default: 7
#
# The companion to tools/pool-report.sh, and the division between them is not
# arbitrary. That one answers everything about people who have accounts, from
# rows they created by asking to. This one answers the questions about
# everybody else — and because there is no identifier anywhere in the pipeline,
# every question it can answer is a question about actions rather than about
# people. There is no visitor here to count, no session to reconstruct, no
# path through the site to follow. Sums and averages, and that is the lot.
#
# Read tools/pool-report.sh's header for what is deliberately NOT here.
#
# Needs two things in the environment:
#
#     CLOUDFLARE_ACCOUNT_ID   the account the Worker is deployed to
#     CLOUDFLARE_API_TOKEN    a token with Account Analytics:Read
#
# The deploy workflow already carries the token; this is the same one.
set -euo pipefail

DAYS="${1:-7}"
DATASET=b12_events

: "${CLOUDFLARE_ACCOUNT_ID:?set CLOUDFLARE_ACCOUNT_ID}"
: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN}"

API="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/analytics_engine/sql"

# THE SAMPLE INTERVAL IS NOT OPTIONAL. Analytics Engine samples under load and
# hands back a weight per row saying how many real events that row stands for.
# COUNT(*) therefore returns the number of rows it kept, which is a number
# about Cloudflare's sampling and not about this site — and it is silently
# correct at low volume, so the mistake survives every test and only starts
# lying on the one busy Saturday anybody cares about. sum(_sample_interval) is
# the count. Always.
run() {
  local title="$1" sql="$2"
  echo
  echo "$title"
  printf -- '-%.0s' $(seq ${#title}); echo
  curl -sS "$API" \
       -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
       -d "$sql" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
try:
    rows = json.loads(raw).get("data") or []
except (json.JSONDecodeError, AttributeError):
    print("  " + (raw[:400] or "(no answer)"))
    sys.exit(0)
if not rows:
    print("  (nothing yet)")
    sys.exit(0)
cols = list(rows[0].keys())
def cell(v):
    return f"{v:.1f}" if isinstance(v, float) else str(v)
w = {c: max(len(c), *(len(cell(r.get(c, ""))) for r in rows)) for c in cols}
print("  " + "  ".join(c.ljust(w[c]) for c in cols))
for r in rows:
    print("  " + "  ".join(cell(r.get(c, "")).ljust(w[c]) for c in cols))
'
}

WINDOW="timestamp > NOW() - INTERVAL '${DAYS}' DAY"

echo "events report — last ${DAYS} days"

# Everything, by name and detail. The first thing to read and usually the only
# thing: it says at a glance whether the Lab is used, whether shared scenarios
# are opened, and whether anybody touches a card.
run "everything" "
  SELECT blob1 AS event,
         blob4 AS detail,
         sum(_sample_interval) AS n
    FROM ${DATASET}
   WHERE ${WINDOW}
   GROUP BY event, detail
   ORDER BY n DESC
   FORMAT JSON"

# Reading, by section. `secs` is engaged time — time the page was actually in
# front of somebody, not time since load — so a section with a high count and
# a low median is being bounced off rather than read. depth is how far down
# the page got, which is the other half of the same question: a long median
# time with a shallow depth means the top of the page is where everything is.
run "reading, by section" "
  SELECT blob2 AS section,
         sum(_sample_interval) AS reads,
         round(avg(double1)) AS avg_secs,
         round(quantileWeighted(0.5)(double1, _sample_interval)) AS median_secs
    FROM ${DATASET}
   WHERE ${WINDOW} AND blob1 = 'read'
   GROUP BY section
   ORDER BY reads DESC
   FORMAT JSON"

run "how far down the page" "
  SELECT blob2 AS section,
         blob4 AS depth,
         sum(_sample_interval) AS n
    FROM ${DATASET}
   WHERE ${WINDOW} AND blob1 = 'read'
   GROUP BY section, depth
   ORDER BY section, depth
   FORMAT JSON"

# The pages, ranked by whether anybody stayed rather than by whether anybody
# arrived. Cloudflare Web Analytics already ranks them by arrival; this is the
# column that one cannot have.
run "pages, by time spent" "
  SELECT blob3 AS page,
         sum(_sample_interval) AS reads,
         round(avg(double1)) AS avg_secs
    FROM ${DATASET}
   WHERE ${WINDOW} AND blob1 = 'read'
   GROUP BY page
   HAVING reads > 1
   ORDER BY avg_secs DESC
   LIMIT 25
   FORMAT JSON"

# The Lab. `pick` carries a count per visit rather than one event per click,
# so sum is total games rewritten and avg is how much of a season a typical
# session rewrites — one game or forty is the difference between a toy and a
# tool, and the pageview graph says neither.
run "the what-if" "
  SELECT blob4 AS action,
         sum(_sample_interval) AS times,
         round(sum(double1 * _sample_interval)) AS games,
         round(avg(double1), 1) AS avg_games
    FROM ${DATASET}
   WHERE ${WINDOW} AND blob1 = 'whatif'
   GROUP BY action
   ORDER BY times DESC
   FORMAT JSON"

# Whether the sharing feature is a feature. `share` above is somebody pressing
# copy; `opened` here is a link actually being followed by somebody. The two
# together are the only honest measure of it, and `stale` is the alarm — a
# scenario the page refused because the schedule moved under it, which is a
# silent failure nobody would ever report.
run "shared scenarios" "
  SELECT blob4 AS outcome,
         sum(_sample_interval) AS n
    FROM ${DATASET}
   WHERE ${WINDOW} AND blob1 = 'scenario'
   GROUP BY outcome
   ORDER BY n DESC
   FORMAT JSON"

# The top of the pools funnel, and the only part of it that is not in D1.
# Divide signin_click by signin_shown for the number that matters; every step
# after the click is in tools/pool-report.sh.
run "pools sign-in" "
  SELECT blob4 AS step,
         sum(_sample_interval) AS n
    FROM ${DATASET}
   WHERE ${WINDOW} AND blob1 = 'pool'
   GROUP BY step
   ORDER BY n DESC
   FORMAT JSON"

run "cards" "
  SELECT blob2 AS section,
         blob4 AS action,
         sum(_sample_interval) AS n
    FROM ${DATASET}
   WHERE ${WINDOW} AND blob1 = 'card'
   GROUP BY section, action
   ORDER BY n DESC
   FORMAT JSON"

echo
echo "accounts, retention and everything after a sign-in: tools/pool-report.sh"

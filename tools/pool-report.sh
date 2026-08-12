#!/usr/bin/env bash
#
# What the pools did, asked of the database that already knows.
#
#     tools/pool-report.sh [season]        # default: this year
#
# This is the half of the site's measurement that needs no client-side
# tracking of any kind, and it is the more useful half. The pools have real
# accounts, so retention, funnel completion and week-over-week participation
# are already facts in D1 — they are questions about rows, not about browsers.
# Nothing here reads a cookie, sets one, or depends on /api/e; the whole
# report would still work with JavaScript disabled across the internet.
#
# Say plainly what it does NOT cover, because the gap is the point: everything
# before a provider hands us a subject. How many people saw the sign-in card
# and walked away is not in this database and cannot be, which is why exactly
# two client events exist for it — pool/signin_shown and pool/signin_click in
# tools/events-report.sh. Those two are the denominator; everything below is
# what happened after.
#
# Needs wrangler authenticated for this account, the same as a deploy:
# CLOUDFLARE_API_TOKEN in the environment, or an interactive `wrangler login`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEASON="${1:-$(date -u +%Y)}"
DB=b12_pickem

cd "$ROOT/worker"

# --remote, always, and never a default that could be --local. A report run
# against the local dev database looks exactly like a report against
# production with nobody playing, and "nobody signed up this week" is a
# sentence somebody would believe.
ask() {
  npx wrangler d1 execute "$DB" --remote --json --command "$1" 2>/dev/null
}

# wrangler wraps every result set in an array of statement results. One
# formatter for all of them, so a query only has to be a query.
show() {
  python3 - "$1" <<'PY'
import json, sys

title = sys.argv[1]
raw = sys.stdin.read().strip()
print()
print(title)
print("-" * len(title))
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    print("  (no answer — is wrangler authenticated for this account?)")
    sys.exit(0)

rows = []
for stmt in payload if isinstance(payload, list) else [payload]:
    rows.extend(stmt.get("results") or [])
if not rows:
    print("  (nothing yet)")
    sys.exit(0)

cols = list(rows[0].keys())
width = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
print("  " + "  ".join(c.ljust(width[c]) for c in cols))
for r in rows:
    print("  " + "  ".join(str(r.get(c, "")).ljust(width[c]) for c in cols))
PY
}

echo "pools report — season $SEASON"

# --- the funnel ------------------------------------------------------------
# One row, read left to right, and every step is a strictly smaller set than
# the one before it. The two that matter are `named` and `picked`: an account
# with no display name cannot appear on a board and cannot pick, so the gap
# between `accounts` and `named` is people who signed in and then hit a form
# and stopped — the single most fixable drop-off on the site, and one that
# looked like nothing at all before it was counted.
show "the funnel" <<< "$(ask "
  SELECT
    (SELECT COUNT(*) FROM users)                                  AS accounts,
    (SELECT COUNT(*) FROM users WHERE display_name IS NOT NULL)   AS named,
    (SELECT COUNT(DISTINCT user_id) FROM picks WHERE season = $SEASON)
                                                                  AS picked,
    (SELECT COUNT(DISTINCT user_id) FROM survivor_picks WHERE season = $SEASON)
                                                                  AS survivor,
    (SELECT COUNT(*) FROM users WHERE status = 'active')          AS active
")"

# How long the name step takes, for the people who complete it. A median in
# SQLite without a percentile function: order the gaps and take the middle
# row. Seconds, because the interesting answers are all under a minute — a
# median of three minutes would mean the form is being wrestled with.
show "seconds from sign-in to a chosen name" <<< "$(ask "
  WITH gaps AS (
    SELECT MIN(h.set_at) - u.created_at AS secs
      FROM users u JOIN name_history h ON h.user_id = u.id
     GROUP BY u.id
  )
  SELECT COUNT(*) AS people,
         MIN(secs) AS fastest,
         (SELECT secs FROM gaps ORDER BY secs LIMIT 1
           OFFSET (SELECT COUNT(*) FROM gaps) / 2) AS median,
         MAX(secs) AS slowest
    FROM gaps
")"

# --- where they came from --------------------------------------------------
# audit_log has recorded the provider on every signup since day one, which
# means this question was answerable before any of this was built and simply
# was not being asked. Grouped by day so a spike lines up against whatever
# caused it.
show "signups by day and provider" <<< "$(ask "
  SELECT DATE(at, 'unixepoch') AS day,
         detail                AS provider,
         COUNT(*)              AS n
    FROM audit_log
   WHERE action = 'signup'
   GROUP BY day, provider
   ORDER BY day DESC, provider
   LIMIT 60
")"

# --- participation ---------------------------------------------------------
# The shape of a season. `entrants` is who picked at all that week; `full` is
# who picked every pickable game on it, which separates the people playing
# from the people who filled in three games and left.
show "participation by week" <<< "$(ask "
  SELECT w.week,
         w.pickable_count                                    AS games,
         COUNT(DISTINCT p.user_id)                           AS entrants,
         COUNT(DISTINCT CASE WHEN c.n = w.pickable_count
                             THEN p.user_id END)             AS full_cards,
         (SELECT COUNT(DISTINCT s.user_id) FROM survivor_picks s
           WHERE s.season = w.season AND s.week = w.week)    AS survivor
    FROM weeks w
    LEFT JOIN picks p ON p.season = w.season AND p.week = w.week
    LEFT JOIN (SELECT season, week, user_id, COUNT(*) AS n
                 FROM picks GROUP BY season, week, user_id) c
           ON c.season = p.season AND c.week = p.week AND c.user_id = p.user_id
   WHERE w.season = $SEASON
   GROUP BY w.week
   ORDER BY w.week
")"

# --- retention -------------------------------------------------------------
# The question no cookieless client-side measurement can answer, answered
# here for free because these people have accounts.
#
# Cohorts by the week somebody first played. `kept` is how many of that cohort
# were still playing in the most recent locked week — not "came back at some
# point", which flatters the number by counting one idle return as retention.
show "retention by entry week" <<< "$(ask "
  WITH latest AS (
    SELECT MAX(week) AS week FROM weeks
     WHERE season = $SEASON AND lock_at IS NOT NULL AND lock_at <= unixepoch()
  ),
  first_week AS (
    SELECT user_id, MIN(week) AS entered
      FROM picks WHERE season = $SEASON GROUP BY user_id
  )
  SELECT f.entered                                   AS entry_week,
         COUNT(*)                                    AS cohort,
         SUM(CASE WHEN EXISTS (
               SELECT 1 FROM picks p
                WHERE p.user_id = f.user_id AND p.season = $SEASON
                  AND p.week = (SELECT week FROM latest)) THEN 1 ELSE 0 END)
                                                     AS kept,
         (SELECT COUNT(DISTINCT week) FROM picks p2
           WHERE p2.user_id = f.user_id AND p2.season = $SEASON)
                                                     AS weeks_each
    FROM first_week f
   GROUP BY f.entered
   ORDER BY f.entered
")"

# Who has stopped, and after how long. A season-long churn list is the thing
# that says whether the pools are worth running again next year.
show "last seen" <<< "$(ask "
  WITH latest AS (
    SELECT MAX(week) AS week FROM weeks
     WHERE season = $SEASON AND lock_at IS NOT NULL AND lock_at <= unixepoch()
  ),
  seen AS (
    SELECT user_id, MAX(week) AS last_week
      FROM picks WHERE season = $SEASON GROUP BY user_id
  )
  SELECT last_week,
         COUNT(*) AS people,
         CASE WHEN last_week = (SELECT week FROM latest)
              THEN 'still playing' ELSE 'gone' END AS state
    FROM seen
   GROUP BY last_week
   ORDER BY last_week
")"

# --- survivor --------------------------------------------------------------
# survivor_board already holds the shape of the pool, recomputed wholesale by
# the scoring cron, so this is a read rather than a calculation.
show "survivor" <<< "$(ask "
  SELECT entered_week,
         COUNT(*)                                          AS entrants,
         SUM(alive)                                        AS alive,
         SUM(CASE WHEN out_reason = 'loss'   THEN 1 ELSE 0 END) AS lost,
         SUM(CASE WHEN out_reason = 'missed' THEN 1 ELSE 0 END) AS missed
    FROM survivor_board
   WHERE season = $SEASON
   GROUP BY entered_week
   ORDER BY entered_week
")"

echo
echo "the half this cannot see — who reached a sign-in and did not press it —"
echo "is in tools/events-report.sh."

#!/usr/bin/env python3
"""Where we are against the CollegeFootballData quota, right now.

    tools/api-budget.py            # this month
    tools/api-budget.py 2026-09    # a specific month

DERIVED, NOT LOGGED, and that is the whole design. Most CFBD calls this
project makes come from a workflow run whose cost is known and fixed:

    pages.yml            1 call   the live season's scores
      ...with --refresh  +9       four ratings, the lines, the broadcasts
    attendance-data.yml  1 call   one /games for the season
                                  (2 before 2026-08-24: each run also
                                  re-fetched /venues until 12fc00a cached it)

So the ledger already exists — it is the run history — and reading it costs
no quota, no commits and nothing in the hot path. The alternative was writing
a file on every call and committing it back, which is ~1,800 commits a season
of pure bookkeeping in a repo whose history is meant to be readable.

The one thing run history cannot see is a call made by hand from a laptop, so
fetch.py appends those to data/.api-local.log (gitignored) and they are added
here. That file is the exception, not the mechanism.

SCORES.YML BREAKS THE "KNOWN AND FIXED" PART, which is why it is reported
separately rather than folded into the total. It runs build.py --fetch about
232 times a week in season — more often than everything else combined — but
load_games() gates the fetch on pending_results(), so a run spends a call only
when some game is actually inside its settling window. Most runs spend nothing.
Counting them as one call each would forecast ~1,000 a month and cry wolf every
day; counting them as zero is what this tool did, and it was blind to its
largest potential consumer.

So both numbers are printed: a FLOOR of the calls whose cost is known, and a
CEILING that assumes every scores.yml run spent one. The real figure is near
the floor on a normal week. The ceiling is not idle arithmetic — a game CFBD
never marks completed stays pending for GIVE_UP_AFTER, which is four days
(build.py), and every run in that window fetches. One cancelled game can put
the month's real spend most of the way to the ceiling, and that is the case
worth seeing early.

Counts are an UPPER BOUND. A run that failed after the fetch still spent its
call and is counted; a run that failed before it did not, and is counted
anyway. Erring high is the right direction for a budget.
"""
import collections
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCAL_LOG = os.path.join(ROOT, "tiebreaker", "data", ".api-local.log")

LIMIT = 1000                       # calls per month on the free plan
REFRESH_CRON = "0 12 * 8-12 2"     # the Tuesday run that also pulls lines
REFRESH_EXTRA = 9                  # ratings x4 (x2 years worst case), lines, media

# When 12fc00a landed and attendance runs stopped re-fetching /venues every
# time. Before this instant each run spent 2 calls (/games + /venues); after
# it, 1. The undercount was invisible to this tool but not to CFBD's own
# dashboard, where /venues sat at 14% of all calls.
VENUES_CACHED = "2026-08-24T20:16:32Z"


def runs(workflow, since):
    """Completed runs of one workflow since `since`, newest first."""
    out = subprocess.run(
        ["gh", "run", "list", "--workflow", workflow, "--limit", "300",
         "--json", "conclusion,createdAt,event,displayTitle"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ,
             "GH_CONFIG_DIR": os.environ.get(
                 "GH_CONFIG_DIR",
                 os.path.expanduser("~/.config/gh-big12ology"))})
    if out.returncode != 0:
        sys.exit(f"gh failed for {workflow}: {out.stderr.strip()[:200]}")
    rows = json.loads(out.stdout or "[]")
    return [r for r in rows
            if r.get("createdAt", "") >= since
            # Cancelled runs are the interesting case: cancel-in-progress
            # kills a run that may already have fetched. Counted, because a
            # spent call does not come back.
            and r.get("conclusion") in ("success", "failure", "cancelled")]


def local_calls(month):
    """Calls made by hand, from fetch.py's own log."""
    if not os.path.exists(LOCAL_LOG):
        return []
    out = []
    for line in open(LOCAL_LOG, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        stamp, _, path = line.partition(" ")
        if stamp.startswith(month):
            out.append(path or "?")
    return out


def expected(y, m, through=None):
    """What the SCHEDULE alone should cost this month, to a given day.

    The point of comparing against this rather than against a flat share of
    the cap: spend is lumpy — weekends are hourly, midweek is not — so a
    straight-line projection over-warns on a Monday after a heavy Sunday and
    under-warns on a Friday. This is the shape the crons actually make.

    It deliberately does NOT model pushes. A push is a deploy nobody
    scheduled, so the gap between this and reality IS the development
    traffic, which is the thing worth being warned about.

    It also deliberately does not model scores.yml, whose per-run cost is 0 or
    1 depending on whether a game is mid-settle. Forecasting it would mean
    inventing a multiplier, and a forecast built on a guess is worse than one
    that says which half it covers. main() reports those runs as a ceiling
    instead — see the module docstring.
    """
    import calendar
    last = calendar.monthrange(y, m)[1]
    through = min(through or last, last)
    n = 0
    for d in range(1, through + 1):
        w = datetime.date(y, m, d).weekday()      # Mon=0 .. Sun=6
        if 8 <= m <= 12:                           # the crons are Aug-Dec
            if w in (5, 6):
                n += 24                            # hourly Sat + Sun
            if w in (0, 4, 5):
                # HOURLY, not every two hours. The cron is
                # "0 0-8 * 8-12 1,5,6" — nine hours, nine runs. Reading it as
                # every-2h under-counted by four a day on three days a week,
                # about 52 a month, which showed up as a permanent ~14% "above
                # forecast" drift on a schedule behaving exactly as designed.
                n += 9
            n += 1                                 # daily catch-all
            if w == 1:
                n += 1 + REFRESH_EXTRA             # Tuesday + its refresh
            # attendance: Fri/Sat x1, Sun x3, Mon x2. Doubled on days up
            # through the venues-cache cutoff (day granularity is enough:
            # the cutoff day's scheduled run fired hours before the fix
            # merged), so the forecast prices those runs the way they
            # actually cost, not the way they cost now.
            per_run = 2 if datetime.date(y, m, d) <= datetime.date.fromisoformat(
                VENUES_CACHED[:10]) else 1
            n += {4: 1, 5: 2, 6: 3, 0: 2}.get(w, 0) * per_run
    return n


def main(month=None):
    today = datetime.date.today()
    month = month or today.strftime("%Y-%m")
    since = f"{month}-01T00:00:00Z"

    pages = runs("pages.yml", since)
    att = runs("attendance-data.yml", since)
    sco = runs("scores.yml", since)

    # WHICH RUN WAS THE REFRESH. Not the one at 12:00 UTC — GitHub delays
    # scheduled workflows under load, sometimes by hours: the Tuesday 12:00
    # cron fired at 15:30 and the daily 18:00 at 20:11 on 2026-08-11. Matching
    # on the hour found nothing and quietly under-counted by nine calls a week.
    #
    # The refresh runs once per Tuesday, so count Tuesdays that saw any
    # scheduled run at all. That survives an arbitrary delay, and it errs high
    # if GitHub drops a schedule entirely — which is the right direction.
    tue = {r["createdAt"][:10] for r in pages
           if r.get("event") == "schedule"
           and datetime.date.fromisoformat(r["createdAt"][:10]).weekday() == 1}
    refreshes = sorted(tue)

    manual = local_calls(month)
    n_pages, n_att = len(pages), len(att)
    # Runs from before the venues cache each spent a second call on /venues.
    att_pre = sum(1 for r in att if r.get("createdAt", "") < VENUES_CACHED)
    att_calls = n_att + att_pre
    used = n_pages + len(refreshes) * REFRESH_EXTRA + att_calls + len(manual)

    print(f"CFBD budget — {month}\n")
    print(f"  {'deploy runs (1 call each)':<34} {n_pages:>5}")
    print(f"  {'weekly refresh (+9 each)':<34} {len(refreshes) * REFRESH_EXTRA:>5}"
          f"   ({len(refreshes)} run{'s' if len(refreshes) != 1 else ''})")
    if att_pre:
        print(f"  {'attendance runs':<34} {att_calls:>5}"
              f"   ({att_pre} x2 pre venues cache, {n_att - att_pre} x1)")
    else:
        print(f"  {'attendance runs (1 call each)':<34} {att_calls:>5}")
    if manual:
        by = collections.Counter(manual)
        print(f"  {'by hand':<34} {len(manual):>5}"
              f"   ({', '.join(f'{k} x{v}' for k, v in by.most_common(3))})")
    print(f"  {'-' * 34} {'-' * 5}")
    print(f"  {'used (floor)':<34} {used:>5}")
    print(f"  {'remaining of ' + str(LIMIT):<34} {LIMIT - used:>5}"
          f"   {used * 100 // LIMIT}% spent")

    # The half whose cost is not fixed. Most of these spent nothing; the
    # ceiling is what a stuck game would cost.
    ceiling = used + len(sco)
    if sco:
        print(f"\n  {'scores.yml runs (0 or 1 each)':<34} {len(sco):>5}")
        print(f"  {'used (ceiling)':<34} {ceiling:>5}"
              f"   {ceiling * 100 // LIMIT}% of cap if every run fetched")

    y, m = int(month[:4]), int(month[5:7])
    full = expected(y, m)
    if month == today.strftime("%Y-%m"):
        so_far = expected(y, m, today.day)
        over = used - so_far
        print(f"\n  {'forecast to today':<34} {so_far:>5}")
        print(f"  {'against forecast':<34} {over:>+5}"
              f"   {'over' if over > 0 else 'under'}")
        print(f"  {'forecast for the whole month':<34} {full:>5}")

        # Early beats late, so this warns on the RATE, not on the total. By
        # the time a straight total trips a cap it is the 28th and there is
        # nothing left to do about it; a percentage over forecast is visible
        # in the first week.
        import calendar
        span = calendar.monthrange(y, m)[1]
        drift = (over / so_far * 100) if so_far else 0
        # The schedule's own forecast, plus today's drift carried to the end
        # of the month. Not a straight-line projection of the total, which
        # would assume the rest of the month looks like the days so far —
        # and it does not, because weekends cost four times a Wednesday.
        projected = round(full + (over / max(today.day, 1)) * span)
        if used > LIMIT:
            print(f"\n  ** OVER THE CAP: {used} of {LIMIT} **")
        elif ceiling > LIMIT:
            # Not "we have overspent" — "we cannot rule out overspending".
            # The floor is fine and the fixed-cost runs are behaving; what
            # this says is that scores.yml has polled often enough that a
            # stuck game could have taken the rest. Worth a look at whether
            # any game has sat pending for days.
            print(f"\n  ** CEILING OVER THE CAP: floor {used}, ceiling "
                  f"{ceiling} of {LIMIT} **")
            print("     Check for a game CFBD never completed — it stays "
                  "pending for four days and every run refetches.")
        elif drift >= 25:
            print(f"\n  ** {drift:.0f}% above forecast — at this rate the "
                  f"month lands near {projected}, cap is {LIMIT} **")
            print("     Usually means unscheduled deploys: every push to "
                  "main spends a call.")
        elif drift >= 10:
            print(f"\n  note: {drift:.0f}% above forecast. Worth watching; "
                  f"the month still lands near {projected}.")
        else:
            print(f"\n  on forecast. The month lands near {projected}.")
    else:
        print(f"\n  {'forecast for that month':<34} {full:>5}")


def check():
    """For CI. Emits a GitHub annotation when spend drifts above forecast.

    Never exits non-zero. A budget warning is information, and failing a
    monitoring run over it would train somebody to ignore the workflow that
    also watches whether the site is up.
    """
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main()
    report = buf.getvalue()
    print(report)
    for line in report.splitlines():
        if "CEILING OVER THE CAP" in line:
            print(f"::warning::CFBD spend cannot be ruled out over cap — "
                  f"{line.strip(' *')}")
        elif "OVER THE CAP" in line:
            print(f"::error::CFBD quota exceeded — {line.strip()}")
        elif "above forecast" in line and line.strip().startswith("**"):
            print(f"::warning::CFBD spend is {line.strip(' *')}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        main(next((a for a in sys.argv[1:] if not a.startswith("-")), None))

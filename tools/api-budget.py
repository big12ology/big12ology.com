#!/usr/bin/env python3
"""Where we are against the CollegeFootballData quota, right now.

    tools/api-budget.py            # this month
    tools/api-budget.py 2026-09    # a specific month

DERIVED, NOT LOGGED, and that is the whole design. Every CFBD call this
project makes comes from a workflow run whose cost is known and fixed:

    pages.yml            1 call   the live season's scores
      ...with --refresh  +9       four ratings, the lines, the broadcasts
    attendance-data.yml  1 call   one /games for the season

So the ledger already exists — it is the run history — and reading it costs
no quota, no commits and nothing in the hot path. The alternative was writing
a file on every call and committing it back, which is ~300 commits a month of
pure bookkeeping in a repo whose history is meant to be readable.

The one thing run history cannot see is a call made by hand from a laptop, so
fetch.py appends those to data/.api-local.log (gitignored) and they are added
here. That file is the exception, not the mechanism.

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
                n += 5                             # every 2h, 00-08
            n += 1                                 # daily catch-all
            if w == 1:
                n += 1 + REFRESH_EXTRA             # Tuesday + its refresh
            # attendance: Fri/Sat x1, Sun x3, Mon x2
            n += {4: 1, 5: 2, 6: 3, 0: 2}.get(w, 0)
    return n


def main(month=None):
    today = datetime.date.today()
    month = month or today.strftime("%Y-%m")
    since = f"{month}-01T00:00:00Z"

    pages = runs("pages.yml", since)
    att = runs("attendance-data.yml", since)

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
    used = n_pages + len(refreshes) * REFRESH_EXTRA + n_att + len(manual)

    print(f"CFBD budget — {month}\n")
    print(f"  {'deploy runs (1 call each)':<34} {n_pages:>5}")
    print(f"  {'weekly refresh (+9 each)':<34} {len(refreshes) * REFRESH_EXTRA:>5}"
          f"   ({len(refreshes)} run{'s' if len(refreshes) != 1 else ''})")
    print(f"  {'attendance runs (1 call each)':<34} {n_att:>5}")
    if manual:
        by = collections.Counter(manual)
        print(f"  {'by hand':<34} {len(manual):>5}"
              f"   ({', '.join(f'{k} x{v}' for k, v in by.most_common(3))})")
    print(f"  {'-' * 34} {'-' * 5}")
    print(f"  {'used':<34} {used:>5}")
    print(f"  {'remaining of ' + str(LIMIT):<34} {LIMIT - used:>5}"
          f"   {used * 100 // LIMIT}% spent")

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
        if "OVER THE CAP" in line:
            print(f"::error::CFBD quota exceeded — {line.strip()}")
        elif "above forecast" in line and line.strip().startswith("**"):
            print(f"::warning::CFBD spend is {line.strip(' *')}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        main(next((a for a in sys.argv[1:] if not a.startswith("-")), None))

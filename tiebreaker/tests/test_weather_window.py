#!/usr/bin/env python3
"""The forecast window has to match the one Open-Meteo will answer for.

    python3 tests/test_weather_window.py

Every venue in range shares a single request, so the end of the window is
not a per-game detail: one game a day past the edge is rejected as a whole
request, and every card on the site falls back to the venue's ten-season
average. That is how three finals and a game kicking off that evening came
to read "Average 79F, 5 mph, 91% rain" on September 4.

Two things have to hold at once, and they pull in opposite directions. The
window cannot reach past the API's last date, and it has to cover all of
that date: Open-Meteo's range is in dates, so it answers for every hour of
its last day, and an edge held as a timestamp lands mid-afternoon and drops
that Saturday night's slate.

No network. The one live fact this pins — that the window is sixteen days
including today, so today+15 is the last date accepted — is in HORIZON_DAYS,
and _clip_to_allowed is the safety net for the day that changes.
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import weather                                           # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


NOW = datetime.datetime(2026, 9, 4, 19, 30, tzinfo=datetime.timezone.utc)


def game(gid, when, **kw):
    return dict({"id": gid, "start": when, "venue_id": 1}, **kw)


# The last date the API accepts, and the first it does not.
edge = (NOW + datetime.timedelta(days=weather.HORIZON_DAYS)).date()
past = edge + datetime.timedelta(days=1)

slate = [
    # Saturday night on the last legal date, hours after a timestamp edge
    # would have fallen. This is the one that was being dropped.
    game(1, f"{edge}T23:00:00.000Z"),
    game(2, f"{edge}T16:00:00.000Z"),
    # A day past the edge: excluded, and excluded quietly. Letting it in is
    # what killed the request for everything else.
    game(3, f"{past}T18:00:00.000Z"),
    # Played, inside the lookback: still wanted, because an observed hour
    # beats an average under a final score.
    game(4, "2026-08-29T19:00:00.000Z", completed=True),
    # Older than the lookback, so Open-Meteo no longer serves the hour.
    game(5, "2025-11-01T19:00:00.000Z", completed=True),
    # A roof answers the question already.
    game(6, f"{edge}T20:00:00.000Z", dome=True),
    game(7, None),
]
got = {g["id"] for g, _ in weather.in_range(slate, now=NOW)}

check(1 in got, f"a {edge} night kickoff was cut from the window")
check(2 in got, f"a {edge} afternoon kickoff was cut from the window")
check(3 not in got, f"{past} is past what the API accepts and was included")
check(4 in got, "a played game inside the lookback was skipped")
check(5 not in got, "a game older than the lookback was included")
check(6 not in got, "a dome was fetched")
check(7 not in got, "a game with no kickoff time was included")

# The end_date the request would carry, built the way attach() builds it.
end = max(w for _, w in weather.in_range(slate, now=NOW)).date()
check(end <= edge, f"end_date {end} reaches past the API's last date {edge}")

# ...and the whole point of the date-based edge: it still covers the last day.
check(end == edge, f"end_date {end} stops short of the API's last date {edge}")


# _clip_to_allowed: the net for the day Open-Meteo moves its window.
err = {"error": True,
       "reason": ("Parameter 'end_date' is out of allowed range from "
                  "2026-06-03 to 2026-09-19")}
check(weather._clip_to_allowed(err, "2026-06-11", "2026-09-20")
      == ("2026-06-11", "2026-09-19"),
      "an out-of-range end was not clipped to what the error named")
check(weather._clip_to_allowed(err, "2026-05-01", "2026-09-20")
      == ("2026-06-03", "2026-09-19"),
      "an out-of-range start was not clipped to what the error named")
check(weather._clip_to_allowed(err, "2026-06-11", "2026-09-19") is None,
      "clipped a range that was already legal, spending a second call")
check(weather._clip_to_allowed({"error": True, "reason": "Something else"},
                               "a", "b") is None,
      "tried to clip an error that names no range")
check(weather._clip_to_allowed({"hourly": {}}, "a", "b") is None,
      "tried to clip a successful response")
# A window entirely past the allowed range clips to nothing, and asking
# again for an impossible range is worse than raising the original error.
check(weather._clip_to_allowed(err, "2026-10-01", "2026-10-08") is None,
      "retried with a start later than the end")

if FAIL:
    print("weather window: FAILED")
    for m in FAIL:
        print("  FAIL:", m)
    sys.exit(1)
print(f"weather window: the request ends on {edge} and covers all of it; "
      f"{past} stays out, domes and stale games stay out, and a moved "
      f"API window clips instead of blanking the slate")

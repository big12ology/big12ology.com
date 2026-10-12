#!/usr/bin/env python3
"""When the week's ratings are refitted.

build.ratings_due answers from the clock and the stamp on the ratings file,
not from which cron fired: due once the last fit is older than this week's
Monday 07:00 UTC turnover, and only for five days after it. The refit moved
off the Tuesday refresh on 2026-10-12, and this is the whole of the rule
that replaced the slot.
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import build                                             # noqa: E402
import fetch                                             # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


def at(s):
    return datetime.datetime.fromisoformat(s).replace(
        tzinfo=datetime.timezone.utc)


# 2026-10-12 is a Monday.
last_week = at("2026-10-06T14:05:00")
check(not build.ratings_due(last_week, at("2026-10-12T06:59:00")),
      "Monday 06:59 UTC: the week has not turned over, last week's fit holds")
check(build.ratings_due(last_week, at("2026-10-12T07:00:00")),
      "Monday 07:00 UTC, midnight Pacific: due")
check(build.ratings_due(last_week, at("2026-10-13T07:00:00")),
      "Tuesday, with Monday's runs all dropped: still due")
check(not build.ratings_due(at("2026-10-12T07:21:00"),
                            at("2026-10-12T08:00:00")),
      "an hour after Monday's refit: not due, the run is free")
check(not build.ratings_due(at("2026-10-12T07:21:00"),
                            at("2026-10-13T07:00:00")),
      "the Tuesday refresh after a Monday refit: not due")
check(build.ratings_due(last_week, at("2026-10-16T23:00:00")),
      "Friday night, never refitted: last chance, still due")
check(not build.ratings_due(last_week, at("2026-10-17T18:00:00")),
      "Saturday afternoon: too late, half the weekend is played")
check(not build.ratings_due(last_week, at("2026-10-18T22:00:00")),
      "Sunday night before the next turnover: not due")
check(build.ratings_due(last_week, at("2026-10-19T07:00:00")),
      "the next Monday: due again")
check(build.ratings_due(None, at("2026-10-14T12:00:00")),
      "a file with no stamp inside the window is due")
check(not build.ratings_due(None, at("2026-10-17T18:00:00")),
      "and outside it is not")

# The stamp as fetch reads it: a full timestamp, and the one bare date.
import json                                              # noqa: E402
import tempfile                                          # noqa: E402
real = fetch.DATA
with tempfile.TemporaryDirectory() as d:
    fetch.DATA = d
    try:
        for stamp, want in (("2026-10-12T07:21:09Z", at("2026-10-12T07:21:09")),
                            ("2026-10-06", at("2026-10-06T00:00:00"))):
            with open(os.path.join(d, "ratings_2099.json"), "w") as f:
                json.dump({"systems": {fetch.OURS: {"fitted": stamp}}}, f)
            check(fetch.ratings_fitted(2099) == want,
                  f"the stamp {stamp} reads as {want.isoformat()}")
        with open(os.path.join(d, "ratings_2099.json"), "w") as f:
            json.dump({"systems": {fetch.OURS: {}}}, f)
        check(fetch.ratings_fitted(2099) is None,
              "and a rating with no stamp reads as None")
    finally:
        fetch.DATA = real


# --- which runs act on it ---------------------------------------------------
class Stub:
    """build.fetcher with the refits counted and nothing sent anywhere."""

    def __init__(self, fitted):
        self.fitted, self.refits = fitted, 0

    def ratings_fitted(self, year):
        return self.fitted

    def fetch_ratings(self, year):
        self.refits += 1

    def fetch_lines(self, year, force_cfbd=False):
        pass

    def media_due(self, year):
        return False

    def fetch_media(self, year, force=False):
        pass

    def fetch_season(self, year, force=False):
        return []

    def mark_ccg(self, games):
        return games


def refits(fitted, **flags):
    real_here, real_fetcher = build.HERE, build.fetcher
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "data"))
        for name, body in (("games_2099.json", "[]"), ("teams.json", "{}")):
            with open(os.path.join(d, "data", name), "w") as f:
                f.write(body)
        stub = Stub(fitted)
        build.HERE, build.fetcher = d, stub
        try:
            build.load_games(2099, refetch=True, **flags)
        finally:
            build.HERE, build.fetcher = real_here, real_fetcher
        return stub.refits


now = datetime.datetime.now(datetime.timezone.utc)
stale = now - datetime.timedelta(days=30)
# Whether a scheduled run refits a month-old file depends on the day this
# test runs, so the expectation is read from the rule it is testing the
# wiring of, not hard-coded.
want = 1 if build.ratings_due(stale) else 0
check(refits(stale, refresh_lines=True) == want,
      f"a scheduled lines run with a stale fit refits exactly when the rule "
      f"says so (today: {want})")
check(refits(stale, refresh=True) == want,
      "and so does the Tuesday refresh, which no longer refits on its own")
check(refits(now, refresh=True) == 0,
      "the Tuesday refresh after a fresh fit spends nothing on ratings")
check(refits(stale) == 0,
      "a push or a dispatched deploy never refits, however stale")
check(refits(now, refresh_ratings=True) == 1,
      "and the manual run refits regardless")

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

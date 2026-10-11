#!/usr/bin/env python3
"""A kickoff nobody had announced gets asked about, once a day.

load_games spends its one CFBD call only when a game should have a result and
does not. That is blind to a kickoff time being announced, which happens
after the weekend's last final has been fetched: on 2026-10-11 the slate read
"time TBD" for a Saturday game the provider had already given a time, and
nothing would have asked until the Tuesday refresh.

build.unannounced_soon names the games worth asking about, and load_games
acts on it only on a run where the day's broadcast check has just fired, so
the cost is bounded by that gate. Both halves are held here, with the
fetchers stubbed: nothing in this file touches the network.
"""
import datetime
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import build                                             # noqa: E402

FAIL = []
NOW = datetime.datetime.now(datetime.timezone.utc)


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


def game(gid, days, tbd=False, done=False):
    t = NOW + datetime.timedelta(days=days)
    return {"id": gid, "week": 7, "home": f"H{gid}", "away": f"A{gid}",
            "start": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "start_tbd": tbd,
            "completed": done, "conference_game": True,
            "home_points": 21 if done else None,
            "away_points": 14 if done else None}


# --- which games are worth asking about ------------------------------------
print("which kickoffs count:")
ids = lambda gs: [g["id"] for g in build.unannounced_soon(gs)]
check(ids([game(1, 6, tbd=True)]) == [1],
      "a game six days out with no time is asked about")
check(ids([game(2, 10, tbd=True)]) == [],
      "one ten days out is not: its window has not been assigned yet")
check(ids([game(3, 6)]) == [],
      "a game with a time already is not")
check(ids([game(4, -0.5, tbd=True)]) == [4],
      "game day still counts: the placeholder is midnight, hours before "
      "any kickoff")
check(ids([game(5, -3, tbd=True)]) == [],
      "a placeholder three days gone is pending_results' business, not "
      "this one's")
check(ids([game(6, -0.5, tbd=True, done=True)]) == [],
      "and a finished game has nothing left to announce")


# --- when the build acts on it ----------------------------------------------
class Stub:
    """build.fetcher with the calls counted and nothing sent anywhere."""

    def __init__(self, media_due, media_fails=False):
        self._due, self._fails = media_due, media_fails
        self.season_calls = 0

    def fetch_lines(self, year):
        pass

    def media_due(self, year):
        return self._due

    def fetch_media(self, year, force=False):
        if self._fails:
            raise RuntimeError("media down")

    def fetch_season(self, year, force=False):
        self.season_calls += 1
        return [game(1, 6)]

    def mark_ccg(self, games):
        return games


def run(games, media_due, refresh_lines=True, media_fails=False):
    real_here, real_fetcher = build.HERE, build.fetcher
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "data"))
        with open(os.path.join(d, "data", "games_2099.json"), "w") as f:
            json.dump(games, f)
        # teams.json present, so the fetch path never goes looking for it.
        with open(os.path.join(d, "data", "teams.json"), "w") as f:
            f.write("{}")
        stub = Stub(media_due, media_fails)
        build.HERE, build.fetcher = d, stub
        try:
            build.load_games(2099, refetch=True, refresh_lines=refresh_lines)
        finally:
            build.HERE, build.fetcher = real_here, real_fetcher
        return stub.season_calls


print("when the build spends the call:")
tbd_week = [game(1, 6, tbd=True), game(2, 6)]
check(run(tbd_week, media_due=True) == 1,
      "the day's broadcast check ran and a kickoff is unannounced: one call")
check(run(tbd_week, media_due=False) == 0,
      "the broadcast check already ran today: no second call")
check(run([game(2, 6)], media_due=True) == 0,
      "every kickoff this week is known: no call, as before")
check(run(tbd_week, media_due=True, refresh_lines=False) == 0,
      "a deploy that is not a scheduled refresh never asks")
check(run(tbd_week, media_due=True, media_fails=True) == 0,
      "a failed broadcast fetch does not count as the day's check, so the "
      "next run tries again")
check(run([game(3, -0.25)], media_due=False) == 1,
      "and a game due a result still fetches on its own, whatever the "
      "broadcast clock says")

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

#!/usr/bin/env python3
"""ESPN's finals, filling what CBBD has not finished.

Against a kept copy of the real Big 12 scoreboard for 2026-03-12, the
quarterfinal day: the join is ESPN's id, the sides are matched by name and
not by ESPN's home and away, a game CBBD has finished is never touched, and
a late tip is asked for on its Eastern date.
"""
import datetime
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import bb_espn as espn                                   # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


def game(espn_id, home, away, start, final=False, hp=None, ap=None):
    return {"espn": espn_id, "home": home, "away": away, "start": start,
            "final": final, "hp": hp, "ap": ap, "status": "scheduled"}


tmp = tempfile.mkdtemp()
espn.RAW = tmp
shutil.copy(os.path.join(HERE, "tests", "fixtures", "espn-scoreboard-20260312.json"),
            espn.path("20260312"))
logs = []
try:
    print("the overlay:")
    games = [
        game("401851299", "Arizona", "UCF", "2026-03-12T19:00:00.000Z"),
        # The same game with the sides the other way round, as a neutral
        # floor can list them: the score has to follow the names.
        game("401851300", "BYU", "Houston", "2026-03-12T23:00:00.000Z"),
        # CBBD already has this one; ESPN must not touch it.
        game("401851296", "Texas Tech", "Iowa State", "2026-03-12T16:30:00.000Z",
             final=True, hp=1, ap=2),
        # Names that match neither side are skipped, loudly.
        game("401851302", "Somebody", "Else", "2026-03-13T01:49:00.000Z"),
        # No ESPN id, nothing to join.
        game(None, "Kansas", "TCU", "2026-03-13T01:49:00.000Z"),
    ]
    n = espn.overlay(games, log=logs.append)
    a, b, c, d, e = games
    check(n == 2, "two games filled")
    check(a["final"] and (a["hp"], a["ap"]) == (81, 59) and a["src"] == "espn",
          "Arizona 81, UCF 59, marked as ESPN's")
    check(b["final"] and (b["hp"], b["ap"]) == (66, 73),
          "sides listed the other way round still get the right score")
    check((c["hp"], c["ap"]) == (1, 2) and "src" not in c,
          "a game CBBD has finished is left exactly as CBBD has it")
    check(not d["final"] and any("matches" in m for m in logs),
          "teams that match neither side are skipped, and the log says so")
    check(not e["final"], "a game without an ESPN id is left alone")

    print("what is worth asking for:")
    now = datetime.datetime(2026, 3, 13, 5, 0, tzinfo=datetime.timezone.utc)
    late = game("1", "Kansas", "TCU", "2026-03-13T01:49:00.000Z")
    soon = game("2", "A", "B", "2026-03-13T04:00:00.000Z")
    old = game("3", "A", "B", "2026-03-01T01:00:00.000Z")
    w = espn.waiting([late, soon, old, a], now)
    check(w == [late], "only a game two hours on and under four days old")
    check(espn.day_of(late) == "20260312",
          "a tip at 01:49 UTC is on the previous Eastern day's scoreboard")

    print("never fatal:")
    os.remove(espn.path("20260312"))
    with open(espn.path("20260312"), "w") as f:
        f.write("{not json")
    fresh = [game("401851299", "Arizona", "UCF", "2026-03-12T19:00:00.000Z")]
    check(espn.overlay(fresh, log=logs.append) == 0 and not fresh[0]["final"],
          "a damaged scoreboard on disk fills nothing and raises nothing")
finally:
    shutil.rmtree(tmp)

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

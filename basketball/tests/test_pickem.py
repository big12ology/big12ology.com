#!/usr/bin/env python3
"""The basketball pick'em publisher's rules, on the real 2025-26 season.

Weeks run Monday to Sunday Central from the first conference week; only the
regular-season conference games are on a slate; only the-odds-api's lines
are frozen; a published line never moves; a week opens eight days ahead and
a week that was never opened is not published after the fact.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import bb_pickem as pk                                   # noqa: E402
import bb_season as season                               # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


d = season.load(2026)
W = pk.weeks(d["games"])
print("the weeks:")
check(sum(len(v) for v in W.values()) == 144,
      "all 144 conference games, and nothing else")
check(sorted(W) == list(range(1, len(W) + 1)), "numbered 1 up with no gaps")
check(all(len({pk.monday(pk._ts(g["start"])) for g in gs}) == 1
          for gs in W.values()), "each week is one Monday-to-Sunday")
check(not any(g["tourney"] for gs in W.values() for g in gs),
      "Kansas City is not on any slate")

tmp = tempfile.mkdtemp()
pk.OUT = tmp
try:
    print("publishing:")
    first = min(pk._ts(g["start"]) for g in W[1])
    g1 = sorted(W[1], key=lambda g: g["start"])[0]
    lines = {g1["id"]: {"spread": -6.5, "source": pk.ODDS,
                        "books": [{"provider": "A", "spread": -6.5}]}}
    # A CBBD line for another game: never frozen.
    other = sorted(W[1], key=lambda g: g["start"])[1]
    lines[other["id"]] = {"spread": -3.0, "provider": "ESPN BET"}

    early = pk.publish(2026, d["games"], lines, now=first - datetime.timedelta(days=12),
                       log=lambda *_: None)
    check(early == [], "twelve days out, nothing opens")
    at = first - datetime.timedelta(days=3)
    got = pk.publish(2026, d["games"], lines, now=at, log=lambda *_: None)
    soon = [n for n, gs in W.items()
            if min(pk._ts(g["start"]) for g in gs) - at <= pk.LEAD]
    check(1 in got and got == sorted(soon),
          f"three days out, exactly the weeks starting within eight days open "
          f"({got})")
    w1 = json.load(open(os.path.join(tmp, "2026", "week-01.json")))
    by = {e["game_id"]: e for e in w1["games"]}
    check(by[int(g1["espn"])]["spread_x2"] == -13,
          "the-odds-api line is frozen, doubled: -6.5 is -13")
    check(by[int(other["espn"])]["spread_x2"] is None,
          "a CBBD line is not frozen; the game waits for the market")
    check(w1["lock_at"] == by[int(g1["espn"])]["kickoff_at"],
          "the week's lock is its first pickable tip")

    # The market moves; the frozen number does not. The CBBD-only game gains
    # its first real line.
    lines[g1["id"]]["spread"] = -9.0
    lines[other["id"]] = {"spread": -2.5, "source": pk.ODDS, "books": []}
    pk.publish(2026, d["games"], lines, now=first - datetime.timedelta(days=1),
               log=lambda *_: None)
    w1 = json.load(open(os.path.join(tmp, "2026", "week-01.json")))
    by = {e["game_id"]: e for e in w1["games"]}
    check(by[int(g1["espn"])]["spread_x2"] == -13, "a published line never moves")
    check(by[int(other["espn"])]["spread_x2"] == -5,
          "a game published without a line gains one, once")

    late = pk.publish(2026, d["games"], lines,
                      now=datetime.datetime(2026, 4, 1, tzinfo=datetime.timezone.utc),
                      log=lambda *_: None)
    check(late == [], "weeks long over that were never opened stay unopened")

    print("scores:")
    pk.write_scores(2026, d["games"])
    sc = json.load(open(os.path.join(tmp, "2026", "scores.json")))
    check(len(sc["games"]) == 144 and all(v[2] for v in sc["games"].values()),
          "every conference final, keyed by ESPN id")
    check(pk.write_scores(2026, d["games"]) is False, "rewritten only on change")
finally:
    shutil.rmtree(tmp)

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

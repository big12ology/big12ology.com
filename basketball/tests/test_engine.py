#!/usr/bin/env python3
"""The basketball tiebreaker, against the seeds the conference really used,
and the readings of the procedure the site had to make.

The finished seasons are the oracle. data/official_seeds.json records the
seeds the Big 12 used in 2025 and 2026, and the engine has to reproduce
every one from the same results: 2026 alone has a three-way tie at 12-6,
a four-way tie at 9-9 that needed the mini round-robin and then "record
against the next team down" to split UCF from Cincinnati, and two
head-to-head pairs.

Then the cases a finished season cannot show: the mid-season rule that a
comparison counts only when every team in it has played the opponent, a
road-record tiebreak, and a tie nothing breaks.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import bb_engine as engine                               # noqa: E402
import bb_season as season                               # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


# --- the record --------------------------------------------------------------
print("the seasons on file:")
official = json.load(open(os.path.join(HERE, "data", "official_seeds.json")))
for s, rec in sorted(official["seasons"].items()):
    d = season.load(int(s))
    st = engine.standings(d["games"], d["teams"])
    got = [r["team"] for r in st["rows"]]
    check(got == rec["seeds"],
          f"{s}: all sixteen seeds match the conference's "
          f"(first difference at seed "
          f"{next((i + 1 for i, (a, b) in enumerate(zip(got, rec['seeds'])) if a != b), '-')})")
    check(not any(r["unresolved"] for r in st["rows"]),
          f"{s}: no tie went to a draw")

d26 = season.load(2026)
check(sum(g["conf"] for g in d26["games"]) == 144,
      "2026: 144 regular-season conference games, the tournament not among them")
check(sum(g["tourney"] for g in d26["games"]) == 15,
      "2026: the 15 Big 12 tournament games are recognized as the tournament")
st = engine.standings(d26["games"], d26["teams"])
check(st["champions"] == ["Arizona"], "2026: Arizona the regular-season champion")
ucf = next(r for r in st["rows"] if r["team"] == "UCF")
check(any("TCU" in e["text"] for e in ucf["log"]),
      "2026: UCF over Cincinnati is explained by their records against TCU")


# --- the readings ---------------------------------------------------------------
def g(home, away, hp, ap, neutral=False):
    return {"home": home, "away": away, "hp": hp, "ap": ap,
            "neutral": neutral, "final": True, "conf": True,
            "start": "2027-01-10T01:00:00Z", "id": hash((home, away, hp)) % 10**6}


def seeds(games, teams):
    return [r["team"] for r in engine.standings(games, teams)["rows"]]


print("the readings:")
# A and B tie at 1-1 having not met. Against X (top), A has played it and B
# has not, so X is skipped; against Y, A beat it and B lost to it.
T = ["A", "B", "X", "Y", "Z"]
games = [g("X", "A", 70, 60), g("A", "Y", 70, 60), g("Y", "B", 70, 60),
         g("B", "Z", 70, 60), g("X", "Z", 70, 60), g("X", "Y", 70, 60)]
check(seeds(games, T)[:1] == ["X"], "the outright leader is first")
st = engine.standings(games, T)
order = [r["team"] for r in st["rows"]]
check(order.index("A") < order.index("B"),
      "a comparison counts only where both have played: X is skipped, Y decides")

# Two teams level, split head-to-head, level against everyone: road record.
T = ["A", "B", "C", "D"]
games = [g("A", "B", 70, 60), g("B", "A", 70, 60),          # split 1-1
         g("C", "A", 60, 70), g("B", "C", 70, 60),           # A won at C, B won at home
         g("D", "A", 70, 60), g("D", "B", 70, 60)]           # both lost at D
order = seeds(games, T)
check(order.index("A") < order.index("B"),
      "level on everything else, the better conference road record wins")

# Nothing separates them at all: the conference draws, the site says so.
T = ["A", "B"]
st = engine.standings([g("A", "B", 70, 60), g("B", "A", 70, 60)], T)
check(all(r["unresolved"] for r in st["rows"]),
      "a tie nothing breaks is marked for the conference's draw")

# Before conference play: everyone 0-0, nobody seeded, no champion.
st = engine.standings([], ["A", "B", "C"])
check(st["champions"] == [] and [r["w"] + r["l"] for r in st["rows"]] == [0, 0, 0],
      "before conference play there is no champion and no record")

# --- the data ---------------------------------------------------------------------
print("the data layer:")
import datetime                                          # noqa: E402
now = datetime.datetime(2027, 1, 10, 6, tzinfo=datetime.timezone.utc)
rows = [{"start": "2027-01-10T01:00:00Z", "final": False, "status": "scheduled"},
        {"start": "2027-01-10T04:30:00Z", "final": False, "status": "scheduled"},
        {"start": "2027-01-09T01:00:00Z", "final": True, "status": "final"},
        {"start": "2027-01-01T01:00:00Z", "final": False, "status": "scheduled"}]
check(len(season.pending(rows, now)) == 1,
      "a game is due a final three hours after tip, and given up after four days")
check(season.current_season(datetime.date(2026, 10, 20)) == 2027
      and season.current_season(datetime.date(2027, 3, 1)) == 2027,
      "the 2026-27 season is CBBD's 2027 from August to July")

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

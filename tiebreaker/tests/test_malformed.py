#!/usr/bin/env python3
"""A malformed game row must not produce a confident wrong answer.

    python3 tests/test_malformed.py

Differential fuzzing across the two engines found two rows they disagreed
about, and in both cases the disagreement was the bad kind: Python raised and
stopped, JavaScript answered and was wrong.

  A MISSING TEAM CLASS. `tiebreaker.py` subscripted g["home_class"], so a row
  without the field raised KeyError and took down the build. engine.js read
  undefined, fell through the truthiness check, and counted the win as an FBS
  win. Policy: absent or None means "fbs" — the JavaScript reading — because
  every committed game states its classes explicitly, so the only rows that
  arrive without one are rows nobody could classify, and counting an
  unclassified win in full can at worst cost a team the one FCS allowance,
  where guessing "fcs" would delete a win it earned.

  A NULL AWAY SCORE. Both engines filtered on home_points alone, so a row with
  an integer home score and no away score survived into winner(). Python
  compared int to None and raised TypeError; JavaScript coerced the null to 0
  and declared the home team the winner of a game with no away score. Policy:
  a game missing EITHER score is not a completed, countable result.

WHY IT STILL EXISTS NOW THAT THERE IS ONE ENGINE. It was written as a
differential test and that half of it is gone: the rules live in
site/engine.js alone, so there is nothing left to compare against. What it was
really pinning was never the agreement — it was the two policies, and those
outlived the engine that used to disagree about them. The silent failure is
also the one that survived the merge: the build is a place where a crash gets
noticed by lunchtime, and The Lab is a place where a reader edits scenarios in
the browser, where the same bad row puts a wrong standings table on screen with
nothing in the console.

So this now asserts both policies directly against the engine that ships, and
against `rules_lite`, which keeps a Python copy of exactly the predicate case B
is about.
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import engine                                            # noqa: E402
import rules_lite as rules                               # noqa: E402

FAIL = []
FIELDS = ("rank", "team", "conf_w", "conf_l", "nonconf_w", "nonconf_l",
          "overall_w", "overall_l", "tie_group", "resolved", "log", "events")


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def rows_of(result):
    return [tuple(r[f] for f in FIELDS) for r in result]


def played(season):
    """The completed conference games of a season, in schedule order."""
    return [g for g in sorted(season, key=lambda x: (x["week"], x["id"]))
            if g["conference_game"] and g["completed"] and not g.get("ccg")]


# 2024 is the fixture: finished, the season every other test reasons about,
# and its four-way tie at 7-2 exercises the whole ladder — including step (e),
# the step that reads the class fields.
SEASON = json.load(open(os.path.join(ROOT, "data", "games_2024.json")))
BASE = engine.standings(SEASON, {})

# The committed data is the control. If a season ever shipped a row that was
# already malformed, these tests would be measuring the wrong thing and the
# "absent means fbs" policy would be silently rewriting real results.
for g in SEASON:
    check("home_class" in g and "away_class" in g,
          f"2024 game {g['id']} is missing a class field — the fixture is "
          f"supposed to be well-formed data")
    check((g.get("home_points") is None) == (g.get("away_points") is None),
          f"2024 game {g['id']} is already half-scored")


# --------------------------------------------------------- case A: no class

# Strip the class fields from every game. Absent reads as "fbs", and 2024's
# FCS games are all non-conference wins that were already the only FCS win
# their team had, so capping at one changes nothing: the standings must come
# out exactly as they do with the classes present.
no_class = copy.deepcopy(SEASON)
for g in no_class:
    g.pop("home_class", None)
    g.pop("away_class", None)

try:
    a_rows = engine.standings(no_class, {})
    check(rows_of(a_rows) == rows_of(BASE),
          "case A: stripping the class fields changed the 2024 standings; "
          "absent is supposed to read as fbs")
except engine.EngineError as e:
    FAIL.append(f"case A: the engine refused a row with no class field: {e}")

# None is the other shape the field arrives in, and it has to read the same.
none_class = copy.deepcopy(SEASON)
for g in none_class:
    g["home_class"] = None
    g["away_class"] = None
try:
    check(rows_of(engine.standings(none_class, {})) == rows_of(BASE),
          "case A: a null class field is not being read as fbs")
except engine.EngineError as e:
    FAIL.append(f"case A: the engine refused a null class field: {e}")


# ------------------------------------------------- case B: half-scored game

# Null the away score of one completed conference game. That game is no longer
# a countable result, so both teams lose it from their conference and overall
# records — and nobody is awarded a win.
half = copy.deepcopy(SEASON)
victim = played(half)[0]
victim["away_points"] = None
v_home, v_away = victim["home"], victim["away"]

try:
    b_rows = engine.standings(half, {})
except engine.EngineError as e:
    b_rows = None
    FAIL.append(f"case B: the engine refused a game with a null away score: {e}")

if b_rows is not None:
    # The predicate itself, in both places it is written. rules_lite keeps a
    # Python copy precisely for callers that only ask this question, so it is
    # the one thing in the repo that can still drift from the engine.
    check(engine.winner(victim) is None,
          "case B: the engine named a winner for a game with no away score")
    check(rules.winner(victim) is None,
          "case B: rules_lite named a winner for a game with no away score")
    check(engine.has_score(victim) is False and rules.has_score(victim) is False,
          "case B: a half-scored row is being treated as having a score")

    base_by_team = {r["team"]: r for r in BASE}
    for t in (v_home, v_away):
        was = base_by_team[t]
        now = next(r for r in b_rows if r["team"] == t)
        check(was["conf_w"] + was["conf_l"] - 1 == now["conf_w"] + now["conf_l"],
              f"case B: {t} should have one fewer conference game once the "
              f"half-scored one is dropped ({was['conf_w']}-{was['conf_l']} -> "
              f"{now['conf_w']}-{now['conf_l']})")
        check(was["overall_w"] + was["overall_l"] - 1
              == now["overall_w"] + now["overall_l"],
              f"case B: {t} should have one fewer overall game "
              f"({was['overall_w']}-{was['overall_l']} -> "
              f"{now['overall_w']}-{now['overall_l']})")

    # THE SPECIFIC SILENT BUG. JavaScript used to hand this game to the home
    # team, and this is the assertion that catches it with nothing else in the
    # middle — no second engine to disagree with, no exception to notice.
    check(next(r for r in b_rows if r["team"] == v_home)["conf_w"]
          <= base_by_team[v_home]["conf_w"],
          f"case B: {v_home} gained a conference win from a game with no away "
          f"score — this is the wrong standing The Lab used to show")

# A missing away_points key, not merely a null one, is the same non-result.
missing = copy.deepcopy(SEASON)
del played(missing)[0]["away_points"]
try:
    check(rows_of(engine.standings(missing, {})) == rows_of(b_rows or []),
          "case B: a missing away_points key reads differently from a null one")
except engine.EngineError as e:
    FAIL.append(f"case B: the engine refused a row with no away_points key: {e}")

# The mirror image — a null HOME score — was always filtered, but only by
# accident of which field the filter named. Pin it too.
half_home = copy.deepcopy(SEASON)
victim_h = played(half_home)[0]
victim_h["home_points"] = None
try:
    check(engine.winner(victim_h) is None and rules.winner(victim_h) is None,
          "case B: a winner was named for a game with no home score")
    engine.standings(half_home, {})
except engine.EngineError as e:
    FAIL.append(f"case B: the engine refused a null home score: {e}")


# ------------------------------------------------------- the display half

# pad() reads scores of its own and appends the teams with no conference
# result yet, so it gets the same malformed rows. Every 2024 team has a
# conference result, so padding is a no-op here: what is asserted is that it
# neither raises nor invents a row.
for name, scenario in (("no class fields", no_class),
                       ("null away_points", half),
                       ("null home_points", half_home)):
    try:
        rows = engine.standings(scenario, {})
        padded = engine.pad(rows, scenario)
        check(len(padded) >= len(rows),
              f"pad(): dropped rows on a season with {name}")
        check(len({r["team"] for r in padded}) == len(padded),
              f"pad(): invented a duplicate row on a season with {name}")
    except engine.EngineError as e:
        FAIL.append(f"pad(): the engine refused a season with {name}: {e}")


if FAIL:
    print("malformed rows: FAILED")
    for m in FAIL:
        print("  FAIL:", m)
    sys.exit(1)
print(f"malformed rows: 6 scenarios over {len(SEASON)} games — absent classes "
      f"read as fbs, half-scored games are not results, and nobody is awarded "
      f"a win they did not play for")

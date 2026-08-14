#!/usr/bin/env python3
"""The strongest available ground truth: for every season with a Big 12
championship game (2017-2025), the engine's top two must be the pairing the
conference actually made.

Why not the conference's published standings? They list tied teams as a
block without applying the tiebreakers — in 2024 the four 7-2 teams are
printed BYU, Arizona State, Iowa State, Colorado, while the conference sent
Arizona State and Iowa State. The championship-game pairing is the only
published artifact that encodes the procedure's result.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import engine
from gen_history import season_games

FIRST_CCG, LAST = 2017, 2025


def main():
    bad = 0
    checked = 0
    for year in range(FIRST_CCG, LAST + 1):
        games = season_games(year)
        ccg = next((g for g in games if g.get("ccg") and g["completed"]), None)
        if not ccg:
            print(f"  [skip] {year}: no championship game in the data")
            continue
        rows = engine.standings(games)
        top2 = {r["team"] for r in rows if r["rank"] <= 2}
        actual = {ccg["home"], ccg["away"]}
        checked += 1
        if top2 == actual:
            print(f"  [ok] {year}: {' vs '.join(sorted(actual))}")
        else:
            bad += 1
            print(f"  [FAIL] {year}: engine {sorted(top2)} != actual {sorted(actual)}")
    print(f"{checked - bad}/{checked} championship pairings reproduced")
    sys.exit(1 if bad or checked < 9 else 0)


if __name__ == "__main__":
    main()

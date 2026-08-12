#!/usr/bin/env python3
"""Chaos Index invariants: range, direction over a real season, endpoints."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import chaos
import clinch
import odds
import tiebreaker as tb
from test_clinch import load, truncate

import build


def check(cond, msg):
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def score_at(games, systems, n=800):
    rows = tb.standings(games)
    cl = clinch.analyze(games)
    od = odds.simulate(games, systems, n=n)
    return chaos.index(rows, cl, od)


def main():
    ok = True
    games = load()
    systems = build.load_ratings(2026)["systems"]

    seq = []
    for cutoff in ["2025-08-01", "2025-10-20", "2025-11-24", "2025-12-01"]:
        res = score_at(truncate(games, cutoff), systems)
        seq.append((cutoff, res))
        print(f"{cutoff}: {res['score']:3d} {res['label']} "
              f"{ {k: round(v, 2) for k, v in res['components'].items()} }")

    ok &= check(all(0 <= r["score"] <= 100 for _, r in seq),
                "scores in [0, 100]")
    ok &= check(all(0 <= v <= 1 for _, r in seq
                    for v in r["components"].values()),
                "components in [0, 1]")
    ok &= check(seq[-1][1]["score"] < seq[0][1]["score"],
                "season end is calmer than preseason")
    final = seq[-1][1]
    ok &= check(final["components"]["entropy"] == 0.0,
                "finished race has zero entropy")
    ok &= check(final["components"]["breadth"] == 0.0,
                "finished race has zero breadth")
    pre = seq[0][1]
    ok &= check(pre["components"]["tangle"] == 1.0,
                "preseason tangle is the sixteen-way 0-0 tie")

    # THE FIRST CONFERENCE GAME MUST NOT EMPTY THE BOARD. tb.standings ranks
    # only teams with a conference result, so one game in, fourteen teams are
    # absent from `rows` while n_teams is still sixteen — and they are the
    # biggest tie on the board, all level at 0-0. Counting only what was
    # ranked read 0.00 here, a quarter of the index falling off a cliff
    # because a game was played. The invariants above could not see it: the
    # score stayed in range and the season still ended calmer than it began.
    teams = {f"T{i}": "alive" for i in range(16)}
    rows_one = [{"team": "T0", "tie_group": None},
                {"team": "T1", "tie_group": None}]
    one = chaos.tangle_component(rows_one, teams, 16)
    ok &= check(one > 0.8,
                f"one game played leaves the rest tied, not untangled ({one:.2f})")
    ok &= check(chaos.tangle_component([], teams, 16) == 1.0,
                "nothing played is still a sixteen-way tie")
    # Alone at 0-0 is tied with nobody.
    solo = {"T0": "alive"}
    ok &= check(chaos.tangle_component([], solo, 1) == 0.0,
                "a single unplayed team is not a tie")

    print("OK" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

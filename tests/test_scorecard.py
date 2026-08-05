#!/usr/bin/env python3
"""Scorecard invariants on real 2025 data: Vegas favorites must beat a
coin flip by a wide margin (sign-convention tripwire), every record is
bounded by the completed-game count, and rows exist for all systems."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import build
import scorecard


def check(cond, msg):
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def main():
    ok = True
    games = json.load(open(os.path.join(ROOT, "data", "games_2025.json")))
    lines = json.load(open(os.path.join(ROOT, "data", "lines_2025.json")))
    systems = build.load_ratings(2026)["systems"]
    tal = scorecard.tally(games, systems, lines)
    ok &= check("Vegas" in tal and all(n in tal for n in systems),
                "rows for Vegas and every model")
    n_done = sum(1 for g in games if g["completed"] and not g.get("ccg"))
    for n, v in tal.items():
        tot = v["w"] + v["l"] + v["push"]
        ok &= check(0 < tot <= n_done, f"{n}: {tot} games within bounds")
    vw, vl = tal["Vegas"]["w"], tal["Vegas"]["l"]
    acc = vw / (vw + vl)
    ok &= check(acc > 0.6, f"Vegas favorites {acc:.3f} — sign convention sane")
    print("OK" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

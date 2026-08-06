#!/usr/bin/env python3
"""Validate the tiebreaker engine against known season outcomes.

2024: Arizona State, Iowa State, BYU, Colorado tied at 7-2.
      CCG was Arizona State (#1) vs Iowa State (#2).
2025: Texas Tech and BYU tied at 8-1 having played (Texas Tech won);
      both in, Texas Tech #1 seed.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tiebreaker as tb

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")


def load(year):
    return json.load(open(os.path.join(DATA, f"games_{year}.json")))


def check(cond, msg):
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    return cond


def main():
    ok = True

    print("2024 season:")
    games = load(2024)
    groups = tb.placement_groups(games)
    ok &= check(len(groups[0]) == 4 and set(groups[0]) ==
                {"Arizona State", "BYU", "Colorado", "Iowa State"},
                f"four-way tie at top: {groups[0]}")
    ccg = tb.championship(games)
    ok &= check(ccg["seed1"] == "Arizona State",
                f"seed1 = {ccg['seed1']} (want Arizona State)")
    ok &= check(ccg["seed2"] == "Iowa State",
                f"seed2 = {ccg['seed2']} (want Iowa State)")
    order, log, resolved, events = tb.break_tie(groups[0], games)
    ok &= check(all(e["team"] and e["step"] and e["line"] for e in events),
                f"seed events populated: {[(e['team'], e['step']) for e in events]}")
    print("  tiebreaker narrative:")
    for line in log:
        print(f"    {line}")
    ok &= check(resolved, "2024 tie fully resolved without manual input")

    print("2025 season:")
    games = load(2025)
    groups = tb.placement_groups(games)
    ok &= check(set(groups[0]) == {"BYU", "Texas Tech"},
                f"two-way tie at top: {groups[0]}")
    ccg = tb.championship(games)
    ok &= check(ccg["seed1"] == "Texas Tech",
                f"seed1 = {ccg['seed1']} (want Texas Tech)")
    ok &= check(ccg["seed2"] == "BYU", f"seed2 = {ccg['seed2']} (want BYU)")
    if ccg.get("note"):
        print(f"    note: {ccg['note']}")

    print("OK" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

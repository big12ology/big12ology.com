#!/usr/bin/env python3
"""The women's section: ESPN's data read into the men's shape, and the
engine checked against the seeds the conference actually used.

The Big 12's women's procedure page is older than the men's (its text says
2015-16 and still describes ten teams) but its tiebreaker steps are the
same, so the one engine serves both. This is where that claim is held to
account: every official seed of both 16-team seasons, and the Kansas City
bracket reading every tournament game into the slot the men's bracket says
it belongs in.
"""
import json
import os
import sys

os.environ["B12_BB"] = "women"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import bb_engine as engine                               # noqa: E402
import bb_season as season                               # noqa: E402
import bb_sport as sport                                 # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


check(sport.WOMEN and sport.PATH == "womens-basketball",
      "B12_BB=women selects the women's section")
official = json.load(open(os.path.join(sport.DATA, "official_seeds.json")))

for y in (2025, 2026):
    print(f"{season.label(y)}:")
    d = season.load(y)
    games = d["games"]
    conf = [g for g in games if g["conf"]]
    per = {}
    for g in conf:
        for t in (g["home"], g["away"]):
            per[t] = per.get(t, 0) + 1
    check(len(conf) == 144 and set(per.values()) == {18},
          "144 conference games, eighteen for every team")
    check(all(g["final"] for g in conf), "every conference game has a final")
    check(sum(g["tourney"] for g in games) == 15,
          "fifteen Big 12 tournament games")
    st = engine.standings(games, d["teams"])
    got = [r["team"] for r in st["rows"]]
    want = official["seasons"][str(y)]["seeds"]
    check(got == want, f"the engine's seeds are the conference's, all 16 "
                       f"({sum(a == b for a, b in zip(got, want))} matched)")

    # The bracket: every tournament game lands in a slot, which is the men's
    # bracket shape holding for the women's tournament too.
    sys.argv = ["x"]
    import bb_build as build                             # noqa: E402
    res = build.bracket_results(want, d)
    check(len(res) == 15, f"all fifteen games read into the bracket "
                          f"({len(res)} placed)")

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

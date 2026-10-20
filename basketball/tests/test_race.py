#!/usr/bin/env python3
"""The odds, the bracket and what is certain.

The simulator seeds every simulated season with the same procedure the
standings use, so its job is mostly bookkeeping, and bookkeeping is what is
checked: every probability that should add up does, the same inputs give
the same odds, a finished season is certain, and the "clinched" and "out"
labels are never wrong, which the bounds have to guarantee however every
remaining game and tie goes.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import bb_engine as engine                               # noqa: E402
import bb_ratings as ratings                             # noqa: E402
import bb_season as season                               # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


d = season.load(2026)
model = ratings.load(2026)
teams = sorted(d["teams"])
official = json.load(open(os.path.join(HERE, "data", "official_seeds.json")))

# --- mid-February 2026: the last 40 conference games not yet played ---------
print("the simulator, from mid-February:")
conf = sorted((g for g in d["games"] if g["conf"]), key=lambda g: g["start"])
cut = len(conf) - 40
mid = [dict(g, final=False, hp=None, ap=None) if i >= cut else g
       for i, g in enumerate(conf)]
sim = engine.simulate(mid, teams, model, n=3000, seed=11)
T = sim["teams"]
for k in range(16):
    tot = sum(T[t]["seed"][k] for t in teams)
    if abs(tot - 1) > 1e-9:
        check(False, f"seed {k + 1} is held {tot:.4f} times per season")
check(True, "every seed is held exactly once a season")
check(abs(sum(T[t]["title"] for t in teams) - 1) < 1e-9,
      "exactly one tournament champion a season")
check(abs(sum(T[t]["top4"] for t in teams) - 4) < 1e-9
      and abs(sum(T[t]["top8"] for t in teams) - 8) < 1e-9,
      "four double byes and eight byes a season")
check(sum(T[t]["share"] for t in teams) >= 1,
      "at least one team shares the title every season")
G = sim["games"]
left_ids = [g["id"] for g in mid if g["conf"] and not g["final"]]
check(sorted(G) == sorted(str(i) for i in left_ids) or
      sorted(map(str, G)) == sorted(map(str, left_ids)),
      "every unplayed game has its own what-it-means")
worst = 0
for g in mid:
    if not (g["conf"] and not g["final"]):
        continue
    e = G[str(g["id"])]
    for side, t in (("home", g["home"]), ("away", g["away"])):
        for k in range(16):
            mix = (e["homeWins"] * e[side][0][k]
                   + (1 - e["homeWins"]) * e[side][1][k])
            worst = max(worst, abs(mix - T[t]["seed"][k]))
check(worst < 1e-9, "a game's two outcomes average back to the team's own "
                    "seed chances")
again = engine.simulate(mid, teams, model, n=3000, seed=11)
check(again == sim, "the same results and ratings give byte-identical odds")

# --- the bounds: never claim what a season could still undo ------------------
print("what is certain:")
cert = engine.certain(mid, teams)
for t in teams:
    o, c = T[t], cert[t]
    for k, key in ((1, "top1"), (4, "top4"), (8, "top8")):
        if c[f"in{k}"] and o[key] < 1:
            check(False, f"{t} 'clinched' top {k} but missed it in a simulation")
        if c[f"out{k}"] and o[key] > 0:
            check(False, f"{t} 'out' of top {k} but made it in a simulation")
check(True, "no simulated season contradicts a clinched or an out")

done = engine.certain(d["games"], teams)
champ = official["seasons"]["2026"]["seeds"][0]
check(done[champ]["in1"] and done[champ]["shareIn"],
      f"a finished season: {champ} has clinched the top seed and the title")
check(all(done[t]["out1"] for t in teams if t != champ),
      "and everyone else is out of the top seed")

# --- the exact proofs ------------------------------------------------------------
print("the exact proofs, with twelve games left:")
cut12 = len(conf) - 12
late = [dict(g, final=False, hp=None, ap=None) if i >= cut12 else g
        for i, g in enumerate(conf)]
ex = engine.exact(late, teams)
check(ex is not None and ex["left"] == 12 and ex["seasons"] == 4096,
      "twelve games left is 4,096 seasons, all of them tried")
E = ex["teams"]
late_sim = engine.simulate(late, teams, model, n=4000, seed=5)["teams"]
for t in teams:
    for k, p in enumerate(late_sim[t]["seed"]):
        if p > 0 and k + 1 not in E[t]["seeds"]:
            check(False, f"{t} took seed {k + 1} in a simulation the proof "
                         f"says is impossible")
check(True, "every simulated seed is one the proof allows")
check(all(set(E[t]["winOut"]) <= set(E[t]["seeds"])
          and set(E[t]["loseOut"]) <= set(E[t]["seeds"]) for t in teams),
      "winning out and losing out are inside what is possible at all")
check(all(E[t]["winOut"] and E[t]["loseOut"] for t in teams),
      "every team can win out and can lose out")
for k in range(16):
    if not any(k + 1 in E[t]["seeds"] for t in teams):
        check(False, f"no team can finish at seed {k + 1}")
check(True, "every seed is somebody's")
final_ex = engine.exact(d["games"], teams)
check(all(final_ex["teams"][t]["seeds"] == [official["seasons"]["2026"]["seeds"]
                                            .index(t) + 1] for t in teams),
      "a finished season has one possible seed per team, the official one")
check(engine.exact(mid, teams) is None,
      "forty games left is too many to try, and says so")

# --- the bracket ---------------------------------------------------------------
print("the bracket:")
order = official["seasons"]["2026"]["seeds"]
reach = engine.bracket_odds(order, model)
check(abs(sum(r[5] for r in reach.values()) - 1) < 1e-9,
      "exact bracket odds give one champion")
check(all(reach[t][2] == 1 for t in order[:4]),
      "seeds 1 to 4 start in the quarterfinals")
check(all(reach[t][1] == 1 and reach[t][2] < 1 for t in order[4:8]),
      "seeds 5 to 8 start in the second round")
check(all(reach[t][1] < 1 for t in order[8:]),
      "seeds 9 to 16 have to win a first-round game")

# --- the ratings ------------------------------------------------------------------
print("the ratings:")
check(model["games"] > 6000 and 2 < model["hfa"] < 5 and 9 < model["sigma"] < 13,
      f"2025-26 fit: {model['games']} games, home court {model['hfa']}, "
      f"spread {model['sigma']}")
w = ratings.windows(2027)
check(w[0][0] == "2026-11-01" and w[-1][1] == "2027-04-15"
      and all(a <= b for a, b in w),
      "the season is asked for in windows from November 1 to April 15")
calls = []
real_get = ratings.fetch.get


def fake(path, params, fresh=False):
    calls.append(params["startDateRange"][:10])
    span = params["startDateRange"][:10] == params["endDateRange"][:10]
    n = 10 if span else ratings.CAP
    return [{"id": f"{params['startDateRange']}-{i}"} for i in range(n)]


ratings.fetch.get = fake
try:
    got = ratings.window_games("2026-11-01", "2026-11-04", fresh=True)
finally:
    ratings.fetch.get = real_get
check(len(calls) > 1 and len(got) == 40,
      "a window that comes back at the cap is split until none does")

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

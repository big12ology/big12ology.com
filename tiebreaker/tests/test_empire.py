#!/usr/bin/env python3
"""The Big 12 imperialism map: the starting territories and the rules.

Two halves, both of which can be wrong without anything failing:

  * The geometry. Every lower-48 county must belong to exactly one school,
    each school's own stadium must sit in its own territory, the shares must
    add up to the whole map, and every outline must close. A stitching bug in
    the border dissolve draws a territory with a hole or a spike and the page
    builds anyway.

  * The rules. The winner takes everything the loser holds, a team with
    nothing can win it all back, only Big 12 against Big 12 counts, and games
    inside a week apply in kickoff order.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import empire                                            # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


load = lambda name: json.load(open(os.path.join(HERE, "data", name)))
games, teams, venues = load("games_2026.json"), load("teams.json"), load("venues.json")

# --- the territories -----------------------------------------------------------
print("the starting map:")
stadiums = empire.home_stadiums(games, teams, venues)
check(sorted(stadiums) == sorted(teams),
      f"every Big 12 school has a home stadium (missing "
      f"{sorted(set(teams) - set(stadiums))})")
T = empire.territories(stadiums)
blocks = T["blocks"]
check(sorted(blocks) == sorted(teams), "one territory per school")
check(abs(sum(b["area"] for b in blocks.values()) - 1) < 0.002,
      f"the shares cover the map (sum {sum(b['area'] for b in blocks.values()):.4f})")
check(all(b["counties"] > 0 and b["area"] > 0 for b in blocks.values()),
      "every school starts with land")

alb = json.load(open(os.path.join(empire.GEO, "counties-albers-10m.json")))
lower48 = [g for g in alb["objects"]["counties"]["geometries"]
           if str(g["id"]).zfill(5)[:2] not in empire.OUTSIDE]
check(sum(b["counties"] for b in blocks.values()) == len(lower48),
      f"every lower-48 county is held exactly once "
      f"({sum(b['counties'] for b in blocks.values())} of {len(lower48)})")

for t, b in blocks.items():
    d = b["d"]
    if d.count("M") != d.count("Z") or not d.startswith("M"):
        check(False, f"{t}'s outline has a ring that does not close")
check(True, "every outline closes")

# Each stadium's own county is in its own school's territory: the nearest
# school to a county containing a stadium is that stadium's school.
geo = json.load(open(os.path.join(empire.GEO, "counties-10m.json")))
shapes = empire._county_shapes(geo)
for t, at in stadiums.items():
    home = min(shapes, key=lambda f: empire._haversine(shapes[f][1], at))
    owner = min(stadiums, key=lambda s: (empire._haversine(shapes[home][1],
                                                         stadiums[s]), s))
    if owner != t:
        check(False, f"{t}'s stadium county is held by {owner}")
check(True, "every school holds the county its stadium stands in")

check(empire.territories(stadiums) == T,
      "the same stadiums draw the same map, byte for byte")

# --- the rules -------------------------------------------------------------------
print("the rules:")
SIX = ["A", "B", "C", "D", "E", "F"]


def g(gid, week, home, away, hp, ap, start="2026-10-10T19:00:00.000Z",
      done=True):
    return {"id": gid, "week": week, "home": home, "away": away,
            "home_points": hp, "away_points": ap, "completed": done,
            "start": start}


fr = empire.replay([g(1, 5, "A", "B", 30, 20)], SIX)
check(fr[-1]["owner"]["B"] == "A" and fr[-1]["owner"]["A"] == "A",
      "the winner takes the loser's land")

fr = empire.replay([g(1, 5, "A", "B", 30, 20), g(2, 6, "C", "A", 10, 24)], SIX)
own = fr[-1]["owner"]
check(own["A"] == "A" and own["B"] == "A" and own["C"] == "A",
      "and everything the loser holds, not only its own territory")

fr = empire.replay([g(1, 5, "A", "B", 30, 20), g(2, 6, "B", "A", 21, 14)], SIX)
own = fr[-1]["owner"]
check(own["A"] == "B" and own["B"] == "B",
      "a team with nothing that beats the holder takes it all back")

fr = empire.replay([g(1, 5, "A", "B", 30, 20), g(2, 6, "C", "B", 30, 20)], SIX)
check(fr[-1]["owner"]["C"] == "C" and fr[-1]["moves"][0]["took"] == [],
      "beating a team with nothing takes nothing")

fr = empire.replay([g(1, 5, "A", "Notre Dame", 10, 40),
                    g(2, 5, "A", "B", 20, 20),
                    g(3, 5, "A", "C", None, None, done=False)], SIX)
check(len(fr) == 1 and all(fr[0]["owner"][t] == t for t in SIX),
      "a non-conference loss, a tie and an unplayed game move nothing")

# Thursday: A beats B. Saturday: C beats A and takes both.
fr = empire.replay([g(2, 7, "C", "A", 31, 7, start="2026-10-17T19:00:00.000Z"),
                    g(1, 7, "A", "B", 28, 3, start="2026-10-15T23:00:00.000Z")],
                   SIX)
check(fr[-1]["owner"]["B"] == "C" and fr[-1]["owner"]["A"] == "C",
      "games inside a week apply in kickoff order")

rows = empire.standings({"owner": {"A": "A", "B": "A", "C": "C"}},
                        {"A": {"area": .5, "counties": 5},
                         "B": {"area": .3, "counties": 3},
                         "C": {"area": .2, "counties": 2}})
check([r["team"] for r in rows] == ["A", "C", "B"] and rows[-1]["blocks"] == [],
      "the holdings rank by share, with the landless last")

# --- every season the site builds ------------------------------------------------
print("the archived seasons:")
for y in (2024, 2025):
    gy = load(f"games_{y}.json")
    st = empire.home_stadiums(gy, teams, venues)
    check(sorted(st) == sorted(teams),
          f"{y}: every school has somewhere to claim from (missing "
          f"{sorted(set(teams) - set(st))})")
# Kansas played 2024 at Arrowhead, every game flagged neutral-site.
st24 = empire.home_stadiums(load("games_2024.json"), teams, venues)
check(abs(st24["Kansas"][1] - 39.05) < 0.1,
      f"2024: Kansas claims from Kansas City (lat {st24['Kansas'][1]})")

# --- this season ------------------------------------------------------------------
fr = empire.replay(games, teams)
check(fr[0]["week"] is None and all(fr[0]["owner"][t] == t for t in teams),
      "this season's replay starts from the starting map")
check(all(sorted(f["owner"]) == sorted(teams) for f in fr),
      "and every frame accounts for all sixteen territories")

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

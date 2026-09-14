#!/usr/bin/env python3
"""The rating this project computes itself, checked against known answers.

    python3 tests/test_massey.py

A least-squares rating is the kind of code that fails quietly. Get a sign
backwards and it still returns 136 plausible-looking numbers; drop the
home-field column and every rating absorbs a bit of it and nothing in the
output says so. So the checks here are mostly RECOVERY: invent teams with
ratings we chose, play out a schedule from them with no noise, and require
the fit to hand back the numbers we started with.

That is a real oracle. If the model is right, the answer is exact.

The one thing not checked here is whether the rating is any GOOD, which is
not a property of the arithmetic. That was measured out of sample against
the other four systems and is recorded in massey.py's docstring.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import massey                                             # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


# RECOVERY, the main event. Four teams, ratings we picked, a full
# round-robin home and away, margins generated with no noise at all. The
# fit must return exactly what generated them.
TRUE = {"A": 12.0, "B": 4.0, "C": -6.0, "D": -10.0}
HFA = 3.0
rows = []
for h in TRUE:
    for a in TRUE:
        if h != a:
            rows.append((h, a, TRUE[h] - TRUE[a] + HFA, False))
def try_fit(rs, label, ridge=0):
    """fit(), or a recorded failure. A model change that makes a solvable
    schedule unsolvable is a result to report, not a traceback to read:
    removing the sum-to-zero constraint did exactly that and showed up as
    a stack trace with no mention of which check had been testing it."""
    try:
        return massey.fit(rs, ridge=ridge)
    except Exception as e:                                # noqa: BLE001
        FAIL.append(f"{label}: fit raised instead of answering ({e})")
        return {}, None


got, hfa = try_fit(rows, "recovery")
check(close(hfa or 0, HFA), f"recovery: home field came back {hfa}, wanted {HFA}")
for t, want in TRUE.items():
    check(close(got.get(t, 0), want, 1e-4),
          f"recovery: {t} came back {got.get(t)}, wanted {want}")
check(close(sum(got.values()), 0.0, 1e-6),
      f"recovery: ratings sum to {sum(got.values())}, not zero")

# NEUTRAL GAMES CARRY NO HOME FIELD. Same teams, every game neutral, and
# the margins generated without the bump: the fit must find hfa ~ 0 rather
# than splitting the difference.
neutral = [(h, a, TRUE[h] - TRUE[a], True) for h in TRUE for a in TRUE if h != a]
got_n, hfa_n = try_fit(neutral, "neutral")
check(close(hfa_n or 0, 0.0, 1e-6),
      f"neutral: invented a {hfa_n}-point home edge on neutral games")
for t, want in TRUE.items():
    check(close(got_n.get(t, 0), want, 1e-4),
          f"neutral: {t} came back {got_n.get(t)}, wanted {want}")

# A MIXED SCHEDULE still separates the two. Home games carry the bump,
# neutral ones do not, and both sets are explained by one set of ratings.
mixed = [(h, a, TRUE[h] - TRUE[a] + HFA, False)
         for h in TRUE for a in TRUE if h != a]
mixed += [(h, a, TRUE[h] - TRUE[a], True)
          for h in ("A", "C") for a in ("B", "D")]
got_m, hfa_m = try_fit(mixed, "mixed")
check(close(hfa_m or 0, HFA, 1e-4),
      f"mixed: home field came back {hfa_m}, wanted {HFA}")

# THE SIGN OF THE HOME FIELD. A schedule where the home side always loses
# by 7 must produce a NEGATIVE coefficient, not a positive one applied
# backwards. Cheap, and it is the error that looks most like working code.
flipped = [(h, a, -7.0, False) for h in ("A", "B") for a in ("C", "D")]
flipped += [(h, a, -7.0, False) for h in ("C", "D") for a in ("A", "B")]
_, hfa_f = try_fit(flipped, "sign")
check(hfa_f is not None and hfa_f < 0, f"sign: home teams lost every game, hfa came back {hfa_f}")

# A DISCONNECTED SCHEDULE HAS NO ANSWER. Two teams who played each other
# and two who played each other, with no game between the groups: nothing
# fixes the two halves relative to one another. It must say so rather than
# return a confident answer built on a near-zero pivot.
split = [("A", "B", 10.0, False), ("B", "A", -4.0, False),
         ("C", "D", 10.0, False), ("D", "C", -4.0, False)]
try:
    massey.fit(split, ridge=0)
    check(False, "singular: a disconnected schedule returned an answer")
except ValueError as e:
    check("singular" in str(e).lower(),
          f"singular: raised the wrong thing ({e})")
# WITH ridge it is answerable, and that is the point of the penalty rather
# than an accident of it: shrinkage pins the level, so each half is rated
# against zero instead of against the other. Worth stating, because it is
# why system() stopped needing to decline in September.
r_split, _ = massey.fit(split)
check(set(r_split) == {"A", "B", "C", "D"},
      f"singular: ridge did not rescue a disconnected schedule ({r_split})")

# ROW SELECTION. FCS opponents are dropped, unplayed games are dropped,
# and CFBD's own field names are read without reshaping.
raw = [
    {"completed": True, "homeTeam": "A", "awayTeam": "B", "homePoints": 30,
     "awayPoints": 10, "homeClassification": "fbs", "awayClassification": "fbs"},
    {"completed": True, "homeTeam": "A", "awayTeam": "Z", "homePoints": 60,
     "awayPoints": 0, "homeClassification": "fbs", "awayClassification": "fcs"},
    {"completed": False, "homeTeam": "A", "awayTeam": "C", "homePoints": None,
     "awayPoints": None, "homeClassification": "fbs", "awayClassification": "fbs"},
    {"completed": True, "homeTeam": "A", "awayTeam": "C", "homePoints": 21,
     "awayPoints": None, "homeClassification": "fbs", "awayClassification": "fbs"},
]
picked = massey.rows_from_games(raw)
check(len(picked) == 2, f"rows: kept {len(picked)} of the 4, wanted 2")
check(("A", "B", 20, False) in picked, f"rows: lost the FBS game ({picked})")
check(("A", massey.POOLED, 60, False) in picked,
      f"rows: the FCS game was not pooled ({picked})")

# EMPTY IN, EMPTY OUT rather than an exception: a season with nothing
# played yet is the preseason, not a failure.
r, h = massey.fit([])
check(r == {} and h == 0.0, f"empty: returned {r}, {h}")

# The systems-dict shape the build consumes, with per_pt fixed at 1.0
# because the ratings are already points.
# system() is the BUILD's entry point and must not raise on a thin early
# schedule. It used to decline on one, because one game is one equation for
# two unknowns; ridge now pins the level and it answers instead. The
# guard stays anyway: declining is still the right failure, and it is one
# line to keep and expensive to rediscover.
s = massey.system(raw, 2026)
check(s["per_pt"] == 1.0, f"system: per_pt is {s['per_pt']}, must be 1.0")
check(s["year"] == 2026 and s["games"] == 2,
      f"system: wrong metadata {s['year']}, {s['games']}")
check(massey.POOLED not in s["ratings"],
      "system: the pooled team reached the page")

# ...and does produce ratings once the results connect.
enough = [dict(completed=True, homeTeam=h, awayTeam=a, homePoints=hp,
               awayPoints=ap, homeClassification="fbs",
               awayClassification="fbs")
          for h, a, hp, ap in (("A", "B", 30, 10), ("B", "C", 24, 17),
                               ("C", "A", 14, 35), ("B", "A", 21, 28),
                               ("C", "B", 20, 13), ("A", "C", 31, 10),
                               ("A", "B", 27, 24), ("C", "A", 17, 20),
                               ("B", "C", 31, 28))]
s2 = massey.system(enough, 2026)
check(set(s2["ratings"]) == {"A", "B", "C"},
      f"system: rated {sorted(s2['ratings'])} off a connected schedule")
check(s2["games"] == 9, f"system: counted {s2['games']} games, wanted 9")

# LEAST SQUARES, NOT AVERAGING. Two teams, two games, contradictory
# results: the fit must land between them, at the mean margin, rather than
# taking either game or the last one seen.
two = [("A", "B", 20.0, True), ("A", "B", 0.0, True)]
got2, _ = try_fit(two, "least squares")
check(close(got2.get("A", 0) - got2.get("B", 0), 10.0, 1e-6),
      f"least squares: split {got2.get('A')} vs {got2.get('B')}, wanted a gap of 10")

# RIDGE SHRINKS RATINGS TOWARD THE MIDDLE, AND ONLY RATINGS. The home
# field is a quantity being measured, not an opinion to be talked out of:
# shrink it and the fit reports a smaller home edge than the games show,
# which is a wrong answer rather than a cautious one.
plain, hfa_p = massey.fit(rows, ridge=0)
shrunk, hfa_s = massey.fit(rows, ridge=massey.RIDGE)
check(all(abs(shrunk[t]) < abs(plain[t]) for t in TRUE),
      "ridge: did not pull ratings toward the middle")
check(all((shrunk[t] > 0) == (plain[t] > 0) for t in TRUE),
      "ridge: changed the sign of a rating rather than shrinking it")
check(close(hfa_s, hfa_p, 0.25),
      f"ridge: shrank the home field too, {hfa_p} -> {hfa_s}")
check(close(sum(shrunk.values()), 0.0, 1e-6),
      f"ridge: shrunk ratings sum to {sum(shrunk.values())}, not zero")
# and the default is the measured constant, not zero
d_default, _ = massey.fit(rows)
check(d_default == shrunk, "ridge: the default is not RIDGE")

# POOLED NON-FBS OPPONENTS are used in the fit and absent from the output.
# Estimated because the arithmetic needs an opponent; dropped because it is
# not a team the site rates.
mixed_raw = [
    {"completed": True, "homeTeam": "A", "awayTeam": "B", "homePoints": 30,
     "awayPoints": 10, "homeClassification": "fbs", "awayClassification": "fbs"},
    {"completed": True, "homeTeam": "B", "awayTeam": "A", "homePoints": 17,
     "awayPoints": 24, "homeClassification": "fbs", "awayClassification": "fbs"},
    {"completed": True, "homeTeam": "A", "awayTeam": "Small", "homePoints": 49,
     "awayPoints": 7, "homeClassification": "fbs", "awayClassification": "fcs"},
    {"completed": True, "homeTeam": "B", "awayTeam": "Tiny", "homePoints": 21,
     "awayPoints": 20, "homeClassification": "fbs", "awayClassification": "fcs"},
]
pooled = massey.rows_from_games(mixed_raw)
check(len(pooled) == 4, f"pool: kept {len(pooled)} of 4 games")
names = {t for h, a, _, _ in pooled for t in (h, a)}
check(massey.POOLED in names, "pool: non-FBS opponents were not pooled")
check("Small" not in names and "Tiny" not in names,
      "pool: rated individual FCS teams instead of pooling them")
rr, _ = massey.fit(pooled)
check(massey.POOLED not in rr,
      "pool: the nuisance team leaked into the ratings")
check(set(rr) == {"A", "B"}, f"pool: rated {sorted(rr)}, wanted A and B")
# B only scraped past an FCS side; A hammered one. That has to tell.
check(rr["A"] > rr["B"], "pool: the non-FBS results carried no information")

# ...and pool=False still gives the old behavior, for anyone comparing.
dropped = massey.rows_from_games(mixed_raw, pool=False)
check(len(dropped) == 2, f"pool=False: kept {len(dropped)} of 4, wanted 2")

# THIN AND THEREFORE WRONG, not merely uncertain. Ridge makes a September
# schedule solvable without making it trustworthy, and the home-field term is
# the tell: fitted on two weeks of 2025 it read 12.6 points against a real
# figure near 3. So the fit is withheld until teams have played enough.
def season(n_teams, per_team, margin=10):
    """A round-robin-ish schedule of n_teams each playing per_team games."""
    ts = [f"T{i}" for i in range(n_teams)]
    out = []
    for i, h in enumerate(ts):
        for d in range(1, int(per_team) // 2 + 1):
            a = ts[(i + d) % n_teams]
            out.append(dict(completed=True, homeTeam=h, awayTeam=a,
                            homePoints=21 + margin, awayPoints=21,
                            homeClassification="fbs",
                            awayClassification="fbs"))
    return out


thin = massey.system(season(40, 2), 2026)
check(thin["ratings"] == {},
      f"gate: published a fit at 2 games per team ({len(thin['ratings'])})")
fat = massey.system(season(40, 12), 2026)
check(fat["ratings"] != {},
      "gate: withheld a fit that had plenty of games")
check(fat["games"] > 0, "gate: counted no games on a full schedule")
# the threshold is the published constant, not a number buried in the code
check(massey.MIN_GAMES_PER_TEAM >= 3,
      f"gate: threshold {massey.MIN_GAMES_PER_TEAM} is too low to mean anything")

# NO HOME FIELD AT A NEUTRAL SITE. Lives here because this is where a
# rating meets a schedule. Applied unconditionally the bump flipped the
# favorite on Arizona State vs Kansas at Wembley: every system rated Arizona
# State higher and four of five printed "Kansas". A rating is only as good
# as the game it is pointed at.
import build as build_                                    # noqa: E402

sysd = {"T": {"year": 2026, "hfa": 3.0, "per_pt": 1.0,
              "ratings": {"Home": 0.0, "Away": 1.0}}}
at_home = dict(id=1, home="Home", away="Away", completed=False,
               neutral_site=False)
neutral = dict(at_home, id=2, neutral_site=True)
fav = build_.favorites_for([at_home, neutral], sysd)["T"]
check(fav["1"]["team"] == "Home" and close(fav["1"]["margin"], 2.0, 1e-9),
      f"neutral: home game lost its bump ({fav['1']})")
check(fav["2"]["team"] == "Away" and close(fav["2"]["margin"], 1.0, 1e-9),
      f"neutral: a neutral-site game was given a home field ({fav['2']})")

if FAIL:
    print("massey: FAILED")
    for m in FAIL:
        print("  FAIL:", m)
    sys.exit(1)
print("massey: 40 checks: known ratings and a known home field are recovered "
      "exactly, neutral games carry no bump, a disconnected schedule raises "
      "instead of answering, and contradictory results least-square rather "
      "than overwrite; ridge shrinks ratings but never the home field; and "
      "non-FBS opponents are pooled into the fit and out of the output; "
      "and a schedule too thin to separate skill from home field is "
      "withheld; no rating gets a home-field bump at a neutral site")

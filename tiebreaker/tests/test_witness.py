#!/usr/bin/env python3
"""Invariant tests for the fewest-upsets witness search, replayed over the
real 2025 season truncated at successive dates.

  1. Validity: every witness, replayed onto the schedule, really does put
     its team in the top two under the full procedure, with the `sure`
     flag the procedure gives it, and its upset count is the number of
     listed results that go against the favorite.
  2. Clinched means chalk: a team the analyzer calls clinched has a
     zero-upset witness.
  3. Agreement with the enumeration: wherever exact mode runs, alive teams
     have a witness and eliminated teams have none (analyze() itself throws
     on the other direction).
  4. Monotonicity with the search on: eliminated stays eliminated.
  5. Season end: the title-game pair has zero-upset witnesses; everyone
     else has a complete search and no path.
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import engine


def load():
    return json.load(open(os.path.join(ROOT, "data", "games_2025.json")))


def systems():
    p = os.path.join(ROOT, "data", "ratings_2025.json")
    raw = json.load(open(p)) if os.path.exists(p) else {"systems": {}}
    return engine.regress_stale(raw.get("systems", {}), 2025)


def truncate(games, cutoff):
    out = copy.deepcopy(games)
    for g in out:
        if (g["start"] or "9999")[:10] > cutoff:
            g["completed"] = False
            g["home_points"] = g["away_points"] = None
    return out


def check(cond, msg):
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def replay(snap, wit):
    out = copy.deepcopy(snap)
    byid = {g["id"]: g for g in out}
    for x in wit["results"]:
        g = byid[x["id"]]
        g["completed"] = True
        if x["winner"] == g["home"]:
            g["home_points"], g["away_points"] = 28, 17
        else:
            g["home_points"], g["away_points"] = 17, 28
    return out


def main():
    games = load()
    sys_ = systems()
    ok = True
    cutoffs = ["2025-10-01", "2025-10-20", "2025-11-03", "2025-11-10",
               "2025-11-17", "2025-11-24", "2025-12-01"]
    results = []
    for c in cutoffs:
        snap = truncate(games, c)
        probs = engine.win_probs(snap, sys_)
        res = engine.clinch_analyze(snap, probs=probs)
        rem = len(engine.remaining_conf(snap))
        results.append((c, snap, probs, res))
        print(f"{c}: {rem:2d} left, mode={res['mode']}, search={res['search']}")
        for t, info in sorted(res["teams"].items()):
            w = info.get("witness")
            if w:
                print(f"    {t}: {info['status']} needs {w['upsets']} "
                      f"(sure={w['sure']}, nodes={w['nodes']})")
            else:
                print(f"    {t}: {info['status']} ({info['method']})")

    # 1. validity
    valid = True
    for c, snap, probs, res in results:
        ncf = {}
        for g in snap:
            if not g["completed"] and not g.get("ccg") and not g["conference_game"]:
                ncf[g["home"]] = 1
                ncf[g["away"]] = 1
        for t, info in res["teams"].items():
            w = info.get("witness")
            if not w:
                continue
            cm = engine._call("cutMembership", replay(snap, w), {}, ncf)
            inside = bool(cm["sure"].get(t)) or bool(cm["maybe"].get(t))
            if not inside or bool(cm["sure"].get(t)) != w["sure"]:
                valid = False
                print(f"    INVALID {t}@{c}: in={inside} sure={cm['sure'].get(t)}")
            ups = 0
            for x in w["results"]:
                ph = probs.get(str(x["id"]), probs.get(x["id"], 0.5))
                fav = x["home"] if ph >= 0.5 else x["away"]
                ups += x["winner"] != fav
            if ups != w["upsets"] or ups != sum(x["upset"] for x in w["results"]):
                valid = False
                print(f"    COUNT {t}@{c}: {ups} vs {w['upsets']}")
            if len(w["results"]) != len(engine.remaining_conf(snap)):
                valid = False
                print(f"    INCOMPLETE {t}@{c}")
    ok &= check(valid, "every witness is a real path with the count it claims")

    # 2. clinched means chalk
    chalk = True
    for c, snap, probs, res in results:
        for t, info in res["teams"].items():
            if info["status"] != "clinched":
                continue
            w = engine.witness(snap, t, probs=probs)
            if not w["found"] or w["upsets"] != 0:
                chalk = False
                print(f"    CLINCHED-NOT-CHALK {t}@{c}: {w}")
    ok &= check(chalk, "a clinched team has a zero-upset witness")

    # 3. agreement with the enumeration
    agree = True
    saw_exact = False
    for c, snap, probs, res in results:
        if res["mode"] != "exact":
            continue
        saw_exact = True
        for t, info in res["teams"].items():
            if info["status"] == "alive" and not info.get("witness"):
                agree = False
                print(f"    EXACT-ALIVE-NO-WITNESS {t}@{c}")
    ok &= check(saw_exact and agree,
                "wherever exact mode runs, every alive team has a witness")

    # 4. monotonic elimination with the search on
    mono = True
    for (c1, _, _, r1), (c2, _, _, r2) in zip(results, results[1:]):
        for t in r1["teams"]:
            if (r1["teams"][t]["status"] == "eliminated"
                    and r2["teams"][t]["status"] != "eliminated"):
                mono = False
                print(f"    VIOLATION {t}: eliminated@{c1} -> alive@{c2}")
    ok &= check(mono, "search eliminations only move forward in time")

    # 5. season end
    c, snap, probs, res = results[-1]
    end = True
    if engine.remaining_conf(snap):
        end = False
        print("    last cutoff still has conference games left")
    champ = engine.championship(snap)
    pair = {champ.get("seed1"), champ.get("seed2")}
    for t, info in res["teams"].items():
        w = engine.witness(snap, t, probs=probs)
        if t in pair:
            if not (w["found"] and w["upsets"] == 0):
                end = False
                print(f"    PAIR {t}: {w}")
        elif w["found"] or not w["complete"]:
            end = False
            print(f"    OTHER {t}: {w}")
    ok &= check(end, "season end: the pair needs nothing, nobody else has a path")

    print("ALL OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

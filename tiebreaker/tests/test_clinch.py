#!/usr/bin/env python3
"""Invariant tests for the clinch/elimination analyzer, replayed over the
real 2025 season truncated at successive dates.

Invariants (no external ground truth needed — these must hold by logic):
  1. Monotonicity: once clinched, always clinched; once eliminated, always
     eliminated, as more games go final.
  2. Season end: exactly the two championship-game participants are
     "clinched"; all fourteen others are "eliminated".
  3. Bounds/exact agreement: wherever both apply, a bounds claim is also an
     exact claim (asserted inside analyze()) — plus, run the enumerator at a
     mid-November truncation and require it to agree with the full-season
     reality for teams it calls decided.
  4. Timing sanity: exact mode engages by the last two weeks of 2025.
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import clinch
import tiebreaker as tb


def load():
    return json.load(open(os.path.join(ROOT, "data", "games_2025.json")))


def truncate(games, cutoff):
    """Season as it stood at end-of-day `cutoff` (YYYY-MM-DD)."""
    out = copy.deepcopy(games)
    for g in out:
        if (g["start"] or "9999")[:10] > cutoff:
            g["completed"] = False
            g["home_points"] = g["away_points"] = None
    return out


def check(cond, msg):
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def main():
    games = load()
    ok = True

    cutoffs = ["2025-10-01", "2025-10-20", "2025-11-03", "2025-11-10",
               "2025-11-17", "2025-11-24", "2025-12-01"]
    results = []
    for c in cutoffs:
        snap = truncate(games, c)
        res = clinch.analyze(snap)
        rem = len(clinch.remaining_conf(snap))
        results.append((c, rem, res))
        print(f"{c}: {rem:2d} conf games left, mode={res['mode']}")
        for t, info in res["teams"].items():
            if info["status"] != "alive" or info["scenarios"]:
                print(f"    {t}: {info['status']} ({info['method']})"
                      + (f" destiny" if info["destiny"] else "")
                      + (f" scenarios={info['scenarios']}"
                         if info["scenarios"] else ""))

    # 1. monotonicity
    mono = True
    for (c1, _, r1), (c2, _, r2) in zip(results, results[1:]):
        for t in r1["teams"]:
            s1, s2 = r1["teams"][t]["status"], r2["teams"][t]["status"]
            if s1 == "clinched" and s2 != "clinched":
                mono = False
                print(f"    VIOLATION {t}: clinched@{c1} -> {s2}@{c2}")
            if s1 == "eliminated" and s2 != "eliminated":
                mono = False
                print(f"    VIOLATION {t}: eliminated@{c1} -> {s2}@{c2}")
    ok &= check(mono, "statuses only move forward in time")

    # 2. season end: exactly the CCG pair clinched, everyone else eliminated
    final = results[-1][2]
    ccg = tb.championship(games)
    pair = {ccg["seed1"], ccg["seed2"]}
    clinched = {t for t, i in final["teams"].items() if i["status"] == "clinched"}
    elim = {t for t, i in final["teams"].items() if i["status"] == "eliminated"}
    ok &= check(clinched == pair, f"final clinched == CCG pair ({sorted(pair)})")
    ok &= check(len(elim) == 14, f"final eliminated == 14 (got {len(elim)})")

    # 3. exact-mode calls at a truncation must match full-season reality
    for c, rem, res in results:
        if res["mode"] != "exact" or rem == 0:
            continue
        for t, info in res["teams"].items():
            if info["status"] == "clinched":
                ok &= check(t in pair,
                            f"{c}: exact-clinched {t} did reach the CCG")
            if info["status"] == "eliminated" and t in pair:
                ok &= check(False, f"{c}: exact-eliminated {t} made the CCG!")

    # 4. exact mode engages late in the season
    ok &= check(any(r["mode"] == "exact" for _, _, r in results),
                "exact enumeration engaged at some truncation")

    print("OK" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

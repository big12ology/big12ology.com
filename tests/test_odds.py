#!/usr/bin/env python3
"""Invariant tests for the Monte Carlo odds module (no external truth):
probabilities live in [0,1] and sum to ~2 (two berths), same seed gives
identical results, odds align with clinch proofs on truncated 2025, and a
finished season yields exactly the CCG pair at 1.0.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import clinch
import odds
from test_clinch import load, truncate

import build


def check(cond, msg):
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def teams_of(res):
    return {k: v for k, v in res.items() if k != "_n"}


def main():
    ok = True
    games = load()
    systems = build.load_ratings(2026)["systems"]
    N = 1500

    for cutoff in ["2025-10-20", "2025-11-24"]:
        snap = truncate(games, cutoff)
        res = teams_of(odds.simulate(snap, systems, n=N))
        tot = sum(v["p_ccg"] for v in res.values())
        ok &= check(1.9 < tot < 2.1, f"{cutoff}: sum p = {tot:.3f} ~ 2")
        ok &= check(all(0 <= v["p_ccg"] <= 1 for v in res.values()),
                    f"{cutoff}: probabilities in [0,1]")
        cl = clinch.analyze(snap)["teams"]
        agree = True
        for t, info in cl.items():
            if info["status"] == "eliminated" and res[t]["p_ccg"] > 0:
                agree = False
                print(f"    {t}: eliminated but p={res[t]['p_ccg']}")
            if info["status"] == "clinched" and res[t]["p_ccg"] < 1:
                agree = False
                print(f"    {t}: clinched but p={res[t]['p_ccg']}")
        ok &= check(agree, f"{cutoff}: odds agree with clinch proofs")

    res2 = teams_of(odds.simulate(truncate(games, "2025-11-24"), systems, n=N))
    res3 = teams_of(odds.simulate(truncate(games, "2025-11-24"), systems, n=N))
    ok &= check(res2 == res3, "same seed -> identical results")

    done = teams_of(odds.simulate(games, systems, n=50))
    at1 = {t for t, v in done.items() if v["p_ccg"] == 1.0}
    ok &= check(at1 == {"BYU", "Texas Tech"},
                f"finished season: exactly the CCG pair at 1.0 ({sorted(at1)})")
    ok &= check(all(v["p_ccg"] == 0 for t, v in done.items() if t not in at1),
                "finished season: everyone else at 0")

    print("OK" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

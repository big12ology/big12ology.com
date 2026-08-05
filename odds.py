#!/usr/bin/env python3
"""Monte Carlo championship-game odds.

Simulates every remaining game (conference and non-conference — the latter
only matters if a tiebreak reaches the total-wins step) with win
probabilities from an ensemble of the fetched rating systems, then scores
top-2 membership per simulated season with the same lean evaluator the
clinch module uses.

Estimates only — the clinch module carries the proofs, and the caller is
expected to pin clinched teams to 1.0 and eliminated teams to 0.0. A fixed
RNG seed keeps builds deterministic: odds move when results or ratings
move, not between rebuilds of the same state.
"""
import math
import random

import clinch
import tiebreaker as tb

N_SIMS = 10000
SEED = 1996          # the year of the first Big 12 season
MARGIN_SIGMA = 13.5  # std dev of scoring margin vs the spread


def win_probs(games, systems):
    """{game_id: p_home} ensemble across rating systems. Unrated opponents
    (FCS and lower) get a floor well below the worst rated team."""
    per_system = []
    for s in systems.values():
        r, hfa, per = s["ratings"], s["hfa"], s.get("per_pt", 1.0) or 1.0
        floor = min(r.values()) - 10 * per
        probs = {}
        for g in games:
            if g["completed"] or g.get("ccg"):
                continue
            hr, ar = r.get(g["home"]), r.get(g["away"])
            if hr is None and ar is None:
                continue
            margin = ((hr if hr is not None else floor)
                      - (ar if ar is not None else floor) + hfa) / per
            probs[g["id"]] = 0.5 * (1 + math.erf(
                margin / (MARGIN_SIGMA * math.sqrt(2))))
        per_system.append(probs)
    out = {}
    for g in games:
        if g["completed"] or g.get("ccg"):
            continue
        ps = [p[g["id"]] for p in per_system if g["id"] in p]
        out[g["id"]] = sum(ps) / len(ps) if ps else 0.5
    return out


def simulate(games, systems, overrides=None, n=N_SIMS, seed=SEED):
    """Returns {team: {"p_ccg": float, "exp_w": float}} plus {"_n": n}.

    p_ccg counts sure top-2 membership as 1 and ambiguous membership
    (unresolved tie steps) as 0.5.
    """
    teams = clinch.conf_teams(games)
    probs = win_probs(games, systems)
    ncf = clinch.unplayed_nonconf_teams(games)

    base = [dict(g) for g in games]
    rem = [g for g in base if not g["completed"] and not g.get("ccg")
           and g["id"] in probs]
    rem_conf = [g for g in rem if g["conference_game"]]
    rng = random.Random(seed)

    in_count = {t: 0.0 for t in teams}
    win_sum = {t: 0 for t in teams}
    for _ in range(n):
        for g in rem:
            g["completed"] = True
            if rng.random() < probs[g["id"]]:
                g["home_points"], g["away_points"] = 28, 17
            else:
                g["home_points"], g["away_points"] = 17, 28
        sure, maybe = clinch.cut_membership(base, overrides, ncf)
        rec = tb.conf_records(base)
        for t in teams:
            if t in sure:
                in_count[t] += 1.0
            elif t in maybe:
                in_count[t] += 0.5
            win_sum[t] += rec.get(t, [0, 0])[0]
        for g in rem:
            g["completed"] = False
            g["home_points"] = g["away_points"] = None

    out = {t: {"p_ccg": in_count[t] / n, "exp_w": win_sum[t] / n}
           for t in teams}
    out["_n"] = n
    return out

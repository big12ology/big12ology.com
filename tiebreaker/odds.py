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

# A published rating is an estimate of a team's strength, not a measurement
# of it, and simulating as though it were exact is what produced a 92%
# per-game favourite nine times over in August 2026 — Texas Tech at 88% to
# reach the championship game with a 47% chance of running the table.
# Each simulated season now draws one strength offset per team, held across
# all of that team's games: if a team is really worse than its rating, it
# loses more of them together, which is exactly the correlation that moves
# a season-long distribution. Independent per-game noise would wash out.
RATING_SIGMA = 7.0   # preseason sd of true strength around a rating, points
SIGMA_SHRINK = 4.0   # games played at which that uncertainty is ~halved

# A system whose ratings are last season's finals is describing a roster
# that has since turned over. Year-over-year strength regresses toward the
# mean; a champion's closing rating is a peak, and carrying it forward
# unmodified overstates the gap to the field. Applied only when a system's
# own recorded year is not the season being simulated.
STALE_KEEP = 0.65


def regress_stale(systems, season):
    """Pull any system still on last season's numbers toward its own mean.

    Call this once, at load, so odds, favourites and strength of schedule
    all describe the same teams — Rule 6 in the README. Returns a new dict;
    the caller's copy is untouched."""
    out = {}
    for name, s in systems.items():
        r = s.get("ratings") or {}
        if s.get("year") == season or not r:
            out[name] = s
            continue
        mean = sum(r.values()) / len(r)
        out[name] = dict(s, regressed=STALE_KEEP, ratings={
            t: mean + STALE_KEEP * (v - mean) for t, v in r.items()})
    return out


def rating_sigma(games):
    """How unsure we are of a team's strength, in points, given how much of
    the season has been played. Full preseason uncertainty with nothing
    played; roughly half of it once every team has four games in."""
    conf = clinch.conf_teams(games)
    # Team-games, not games: a non-conference game informs one conference
    # team, not two, and September is mostly non-conference. Counting both
    # sides would shrink the uncertainty fastest in the weeks it is largest.
    team_games = sum((g["home"] in conf) + (g["away"] in conf)
                     for g in games if g["completed"] and not g.get("ccg"))
    gp = team_games / max(len(conf), 1)
    return RATING_SIGMA * math.sqrt(SIGMA_SHRINK / (SIGMA_SHRINK + gp))


def ensemble_margin(games, systems):
    """{game_id: expected home margin in points}, averaged across systems.

    Margins rather than probabilities, because a simulated season shifts a
    team's strength and the shift has to happen before the curve, not after
    it. Unrated opponents (FCS and lower) get a floor well below the worst
    rated team."""
    per_system = []
    for s in systems.values():
        r, hfa, per = s["ratings"], s["hfa"], s.get("per_pt", 1.0) or 1.0
        if not r:
            continue
        floor = min(r.values()) - 10 * per
        m = {}
        for g in games:
            if g["completed"] or g.get("ccg"):
                continue
            hr, ar = r.get(g["home"]), r.get(g["away"])
            if hr is None and ar is None:
                continue
            m[g["id"]] = ((hr if hr is not None else floor)
                          - (ar if ar is not None else floor) + hfa) / per
        per_system.append(m)
    out = {}
    for g in games:
        if g["completed"] or g.get("ccg"):
            continue
        ms = [m[g["id"]] for m in per_system if g["id"] in m]
        if ms:
            out[g["id"]] = sum(ms) / len(ms)
    return out


def p_from_margin(m):
    return 0.5 * (1 + math.erf(m / (MARGIN_SIGMA * math.sqrt(2))))


def team_strength(systems):
    """{team: strength in scoring points}, averaged across the ensemble.

    Each system is divided by its own per_pt first, so Elo's 27-points-per-
    point scale lands on the same axis as SP+'s. Factored out of the odds so
    anything asking "how good is this team" and anything asking "who wins
    this game" cannot drift apart."""
    tot, n = {}, {}
    for s in systems.values():
        r, per = s.get("ratings") or {}, s.get("per_pt", 1.0) or 1.0
        for t, v in r.items():
            tot[t] = tot.get(t, 0.0) + v / per
            n[t] = n.get(t, 0) + 1
    return {t: tot[t] / n[t] for t in tot}


def hfa_points(systems):
    """The ensemble's home-field bump, in scoring points."""
    vals = [s["hfa"] / (s.get("per_pt", 1.0) or 1.0)
            for s in systems.values() if s.get("ratings")]
    return sum(vals) / len(vals) if vals else 0.0


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


def simulate(games, systems, overrides=None, n=N_SIMS, seed=SEED, track=None):
    """Returns {team: {"p_ccg": float, "exp_w": float}} plus {"_n": n}.

    p_ccg counts sure top-2 membership as 1 and ambiguous membership
    (unresolved tie steps) as 0.5.

    track: optional iterable of game ids. For each, the result gains
    "_cond": {gid: {"n_home": int, "in": {team: [sum_if_home_won,
    sum_if_home_lost]}}} — enough to condition every team's CCG
    probability on that game's outcome (leverage).
    """
    teams = clinch.conf_teams(games)
    margins = ensemble_margin(games, systems)
    ncf = clinch.unplayed_nonconf_teams(games)

    base = [dict(g) for g in games]
    rem = [g for g in base if not g["completed"] and not g.get("ccg")
           and g["id"] in margins]
    rng = random.Random(seed)
    sigma_r = rating_sigma(games)
    # Every side of a remaining game, conference or not — a non-conference
    # opponent's strength is just as uncertain, and total wins is a tiebreak
    # step. Sorted, not a set: the draws below are pulled in iteration order,
    # and set order for strings varies between processes, so leaving it a set
    # hands each team a different offset on every run and quietly breaks the
    # fixed-seed guarantee this module is built on.
    sides = sorted({t for g in rem for t in (g["home"], g["away"])})

    track_ids = [g["id"] for g in rem if track and g["id"] in set(track)]
    cond = {gid: {"n_home": 0, "in": {t: [0.0, 0.0] for t in teams}}
            for gid in track_ids}

    in_count = {t: 0.0 for t in teams}
    win_sum = {t: 0 for t in teams}
    for _ in range(n):
        # One draw per team per season, not per game: a team that is really
        # a touchdown worse than its rating is worse in all nine of them.
        off = {t: rng.gauss(0, sigma_r) for t in sides} if sigma_r else {}
        outcomes = {}
        for g in rem:
            g["completed"] = True
            p = p_from_margin(margins[g["id"]]
                              + off.get(g["home"], 0.0)
                              - off.get(g["away"], 0.0))
            hw = rng.random() < p
            outcomes[g["id"]] = hw
            if hw:
                g["home_points"], g["away_points"] = 28, 17
            else:
                g["home_points"], g["away_points"] = 17, 28
        sure, maybe = clinch.cut_membership(base, overrides, ncf)
        rec = tb.conf_records(base)
        for t in teams:
            v = 1.0 if t in sure else (0.5 if t in maybe else 0.0)
            in_count[t] += v
            win_sum[t] += rec.get(t, [0, 0])[0]
            for gid in track_ids:
                cond[gid]["in"][t][0 if outcomes[gid] else 1] += v
        for gid in track_ids:
            if outcomes[gid]:
                cond[gid]["n_home"] += 1
        for g in rem:
            g["completed"] = False
            g["home_points"] = g["away_points"] = None

    out = {t: {"p_ccg": in_count[t] / n, "exp_w": win_sum[t] / n}
           for t in teams}
    out["_n"] = n
    if track_ids:
        out["_cond"] = cond
    return out


def leverage(sims, games):
    """Per tracked game: each team's P(CCG | home wins) - P(| home loses),
    plus the total absolute swing. Returns [{game, home, away, total,
    movers: [(team, delta), ...]}], biggest total first."""
    cond = sims.get("_cond", {})
    n = sims.get("_n", 0)
    by_id = {g["id"]: g for g in games}
    out = []
    for gid, c in cond.items():
        nh = c["n_home"]
        nl = n - nh
        if nh == 0 or nl == 0:
            continue
        movers = []
        total = 0.0
        for t, (sw, sl) in c["in"].items():
            d = sw / nh - sl / nl
            total += abs(d)
            if abs(d) >= 0.005:
                movers.append((t, d))
        movers.sort(key=lambda x: -abs(x[1]))
        g = by_id[gid]
        out.append({"game": g, "home": g["home"], "away": g["away"],
                    "total": total, "movers": movers})
    out.sort(key=lambda x: -x["total"])
    return out

#!/usr/bin/env python3
"""The Chaos Index: how tangled is the Big 12 championship race, 0-100.

Three components, all recomputed after every game:

  entropy (60%)  Shannon entropy of the Monte Carlo berth distribution
                 (each team's CCG probability / 2), normalized so that two
                 locked-in teams score 0 and a sixteen-way dead heat scores
                 1. The heart of the number.
  tangle (25%)   Fraction of the sixteen teams sitting in multi-team tie
                 groups in the live standings while still in the race
                 (eliminated and clinched teams don't tangle). Before any
                 conference games, everyone is 0-0: one giant tie, 1.0.
  breadth (15%)  How many teams beyond the two berths are still
                 mathematically alive: (alive - 2) / 14.

Score = 100 * (.60 * entropy + .25 * tangle + .15 * breadth).
"""
import math

WEIGHTS = {"entropy": 0.60, "tangle": 0.25, "breadth": 0.15}

LABELS = [
    (15, "Settled"),
    (35, "Orderly"),
    (55, "Simmering"),
    (75, "Chaotic"),
    (101, "Pandemonium"),
]


def entropy_component(p_ccg):
    """p_ccg: {team: probability}; probabilities sum to ~2 (two berths)."""
    total = sum(p_ccg.values())
    if total <= 0:
        return 1.0
    qs = [p / total for p in p_ccg.values() if p > 0]
    h = -sum(q * math.log(q) for q in qs)
    lo, hi = math.log(2), math.log(max(len(p_ccg), 3))
    return max(0.0, min(1.0, (h - lo) / (hi - lo)))


def tangle_component(rows, statuses, n_teams):
    """rows: engine standings rows; statuses: {team: clinched/eliminated/alive}.

    A TEAM WITH NO CONFERENCE RESULT IS TIED WITH EVERY OTHER SUCH TEAM, and
    counting it is the whole of this function's early-season correctness.
    tb.standings ranks only teams it has evidence for — that is why
    pad_standings exists — so `rows` in September is two or four teams, not
    sixteen, while n_teams stays sixteen. The dozen still on 0-0 are the
    largest tie on the board and they were falling out of the numerator
    entirely: measured on 2025 this read 0.00 through the first two weeks
    where it should have read 0.88, and 0.25 in week three where the whole
    league was still level. A quarter of the index, so the published score
    was low by up to twenty-five points for the first month.

    This used to be a special case for the empty list — "nobody has played:
    one sixteen-way tie" — which was the right instinct applied only at the
    one moment it was not needed yet. Now it falls out: with nothing played
    every team is unlisted, unlisted teams count, and the answer is 1.0
    without a branch saying so.

    One team alone at 0-0 is tied with nobody, so it takes two.
    """
    if not n_teams:
        return 1.0
    listed = {r["team"] for r in rows}
    unplayed = [t for t, s in statuses.items()
                if t not in listed and s == "alive"]
    tangled = sum(
        1 for r in rows
        if r["tie_group"] and statuses.get(r["team"]) == "alive")
    if len(unplayed) > 1:
        tangled += len(unplayed)
    return min(1.0, tangled / n_teams)


def breadth_component(statuses):
    alive = sum(1 for s in statuses.values() if s != "eliminated")
    n = len(statuses)
    if n <= 2:
        return 0.0
    return max(0.0, (alive - 2) / (n - 2))


def index(rows, clinch_result, odds_result):
    """Returns {"score": int, "label": str, "components": {...}}."""
    statuses = {t: i["status"] for t, i in clinch_result["teams"].items()}
    p_ccg = {t: v["p_ccg"] for t, v in odds_result.items()
             if not t.startswith("_")}
    # proofs pin the distribution exactly like the display does
    for t, s in statuses.items():
        if s == "clinched":
            p_ccg[t] = 1.0
        elif s == "eliminated":
            p_ccg[t] = 0.0
    comps = {
        "entropy": entropy_component(p_ccg),
        "tangle": tangle_component(rows, statuses, len(statuses)),
        "breadth": breadth_component(statuses),
    }
    score = round(100 * sum(WEIGHTS[k] * v for k, v in comps.items()))
    label = next(lbl for cap, lbl in LABELS if score < cap)
    return {"score": score, "label": label, "components": comps}

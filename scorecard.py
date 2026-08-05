#!/usr/bin/env python3
"""Model scorecard: how each rating system's favorites are doing in games
involving Big 12 teams this season.

Favorites are recomputed from the ratings as currently fetched — in-season
systems update weekly, so early-season picks are re-judged with slightly
newer numbers than existed at kickoff. Honest label for that lives in the
card note; the alternative (frozen weekly snapshots) needs state that the
stateless builds don't keep.
"""
import tiebreaker as tb


def tally(games, systems):
    """{system: {"w": int, "l": int, "push": int}} over completed,
    non-championship games where both sides are rated (FCS floor games are
    skipped — every model picks those, nobody gets credit)."""
    out = {}
    for name, s in systems.items():
        r, hfa = s["ratings"], s["hfa"]
        w = l = push = 0
        for g in games:
            if not g["completed"] or g.get("ccg"):
                continue
            win = tb.winner(g)
            if win is None:
                continue
            hr, ar = r.get(g["home"]), r.get(g["away"])
            if hr is None or ar is None:
                continue
            margin = hr - ar + hfa
            if margin == 0:
                push += 1
                continue
            fav = g["home"] if margin > 0 else g["away"]
            if fav == win:
                w += 1
            else:
                l += 1
        out[name] = {"w": w, "l": l, "push": push}
    return out

#!/usr/bin/env python3
"""Model scorecard: how each rating system's favorites are doing in games
involving Big 12 teams this season.

Favorites are recomputed from the ratings as currently fetched — in-season
systems update weekly, so early-season picks are re-judged with slightly
newer numbers than existed at kickoff. Honest label for that lives in the
card note; the alternative (frozen weekly snapshots) needs state that the
stateless builds don't keep.
"""
import rules_lite as rules


def tally(games, systems, lines=None):
    """{system: {"w": int, "l": int, "push": int}} over completed,
    non-championship games where both sides are rated (FCS floor games are
    skipped — every model picks those, nobody gets credit). When closing
    lines are supplied, a "Vegas" entry scores the market's favorites the
    same way (home spread, negative = home favored)."""
    out = {}
    if lines:
        w = l = push = 0
        for g in games:
            if not g["completed"] or g.get("ccg"):
                continue
            win = rules.winner(g)
            mkt = lines.get(str(g["id"])) or {}
            # Tolerate the pre-capture shape, where the value was the
            # spread itself rather than a market record.
            spread = mkt.get("spread") if isinstance(mkt, dict) else mkt
            if win is None or spread is None:
                continue
            if spread == 0:
                push += 1
                continue
            fav = g["home"] if spread < 0 else g["away"]
            if fav == win:
                w += 1
            else:
                l += 1
        out["Vegas"] = {"w": w, "l": l, "push": push}
    for name, s in systems.items():
        r, hfa = s["ratings"], s["hfa"]
        w = l = push = 0
        for g in games:
            if not g["completed"] or g.get("ccg"):
                continue
            win = rules.winner(g)
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

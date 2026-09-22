#!/usr/bin/env python3
"""Model scorecard: how each rating system's favorites are doing in games
involving Big 12 teams this season.

Favorites are recomputed from the ratings as currently fetched — in-season
systems update weekly, so early-season picks are re-judged with slightly
newer numbers than existed at kickoff. Honest label for that lives in the
card note; the alternative (frozen weekly snapshots) needs state that the
stateless builds don't keep.

THE FORECAST RECORD FIXES THAT for the live season. build.write_forecast
keeps, for every unplayed game, each system's expected margin and the
market's spread as they stood before the week began; tally_records grades
those, so a system is judged on what it said before kickoff and a Vegas
line is judged from the same moment. tally() remains for seasons without
records, with the caveat the card prints beside it.
"""
import rules_lite as rules


def tally_records(games, records):
    """{system: {"w", "l", "push"}} over completed, non-championship games
    that have a forecast record, from the record alone.

    `records` is {game_id: record} as forecast_for returns it: `systems`
    holds each system's expected home margin in points, `spread` the
    market's home line. A positive margin favors the home side; a negative
    spread does. Games with no record are not counted, and a system absent
    from a game's record (not yet published that week, or an opponent it
    did not rate) is not counted for that game.
    """
    out = {}

    def score(name, fav, win):
        v = out.setdefault(name, {"w": 0, "l": 0, "push": 0})
        if fav is None:
            v["push"] += 1
        elif fav == win:
            v["w"] += 1
        else:
            v["l"] += 1

    for g in games:
        if not g["completed"] or g.get("ccg"):
            continue
        win = rules.winner(g)
        rec = records.get(str(g["id"]))
        if win is None or not rec:
            continue
        for name, m in (rec.get("systems") or {}).items():
            score(name, None if m == 0 else (g["home"] if m > 0 else g["away"]),
                  win)
        sp = rec.get("spread")
        if sp is not None:
            score("Vegas", None if sp == 0 else (g["home"] if sp < 0 else g["away"]),
                  win)
    return out


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

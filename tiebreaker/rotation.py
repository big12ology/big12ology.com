#!/usr/bin/env python3
"""Who you miss, and how long it has been.

Nine conference games among sixteen teams means 48 of the 120 possible
pairings sit out every season. That is the permanent condition of this
format and nobody publishes it.

**Conference meetings only.** Every one of the 120 pairings has met at some
point — these are major programs, and they have played each other in
non-conference games and bowls for decades. "Never met" is only true, and
only interesting, as "never met as conference opponents", which is a fact
about the league rather than about the two schools.

Reads history/, not data/. The history corpus filters by conference
membership *at the time*, so a Utah-Baylor game in 2015 correctly is not a
Big 12 conference meeting — Utah was Pac-12. Reading data/, which filters by
today's membership, would invent conference history that never happened.
"""
import json
import os

import fetch as fetcher
import tiebreaker as tb

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "history")


def season_games(year):
    """The frozen history corpus for a season, [] if it is not on disk."""
    p = os.path.join(HIST, f"games_{year}.json")
    if not os.path.exists(p):
        return []
    return fetcher.mark_ccg(json.load(open(p)))


def meetings(games, year=None):
    """{frozenset(pair): [year, ...]} over conference games, CCG excluded.

    The championship game is a rematch by definition and would double-count
    a pairing that the schedule only granted once.
    """
    out = {}
    for g in games:
        if not g.get("conference_game") or g.get("ccg"):
            continue
        if not g.get("completed"):
            continue
        pair = frozenset((g["home"], g["away"]))
        y = year if year is not None else (g.get("start") or "")[:4]
        out.setdefault(pair, []).append(y)
    return out


def conference_history(years, teams=None):
    """Every conference meeting across `years`, from history/.

    `teams` restricts to pairings among the current membership. Without it
    the result runs past 120 pairs, because the historical Big 12 contained
    Oklahoma, Texas, Missouri and Texas A&M — real conference meetings, but
    not ones any current pairing can inherit.
    """
    keep = set(teams) if teams else None
    all_meet = {}
    for y in years:
        games = season_games(y)
        for pair, ys in meetings(games, year=y).items():
            if keep and not pair <= keep:
                continue
            all_meet.setdefault(pair, []).extend(ys)
    return all_meet


def all_time_records(years, teams, current=None):
    """{a: {b: [wins, losses]}} — every pairing's record as conference
    opponents across `years`, plus the season in progress.

    Deliberately the same corpus, and the same exclusions, as `report`:
    history/ rather than data/, membership as it was at the time,
    championship-game rematches dropped, and `years` already filtered
    through `usable_seasons`. Counting the record from one set of games and
    the last meeting from another is how a grid ends up claiming a win in a
    season the page beside it says never happened.

    `current` is the live season, which history/ has no file for yet.
    """
    names = sorted(teams)
    wl = {a: {b: [0, 0] for b in names if b != a} for a in names}
    seasons = [season_games(y) for y in years]
    if current is not None:
        seasons.append(current)
    for games in seasons:
        for g in games:
            if not g.get("conference_game") or g.get("ccg"):
                continue
            if not g.get("completed") or not tb.has_score(g):
                continue
            w = tb.winner(g)
            if w is None:
                continue
            loser = g["away"] if w == g["home"] else g["home"]
            if w in wl and loser in wl[w]:
                wl[w][loser][0] += 1
                wl[loser][w][1] += 1
    return wl


def scheduled(games):
    """{frozenset(pair)} the current season actually pairs."""
    return {frozenset((g["home"], g["away"])) for g in games
            if g.get("conference_game") and not g.get("ccg")}


def report(games, teams, history_years):
    """Per-team: who they miss this season, and when they last met.

    Returns (rows, stats). A row is {team, missing:[{opponent, last, gap}]},
    where `last` is None for a pairing that has never happened in
    conference play.
    """
    names = sorted(teams)
    plays = scheduled(games)
    past = conference_history(history_years, names)
    latest = max(history_years) if history_years else None

    rows, never, waits = [], [], []
    for t in names:
        missing = []
        for o in names:
            if o == t or frozenset((t, o)) in plays:
                continue
            ys = sorted(past.get(frozenset((t, o)), []))
            last = ys[-1] if ys else None
            missing.append({"opponent": o, "last": last,
                            "gap": (latest - last + 1) if last else None})
            if last is None:
                never.append((t, o))
            else:
                waits.append((last, t, o))
        missing.sort(key=lambda m: (m["last"] is not None, m["last"] or 0))
        rows.append({"team": t, "missing": missing})

    # The pairs meeting for the first time ever as conference opponents.
    # These are on the schedule, not missing from it, which is why they do
    # not appear in any team's missing list — and they are the most
    # interesting thing the rotation produces in a young conference.
    firsts = sorted(tuple(sorted(p)) for p in plays if p not in past)

    stats = {
        "pairs_total": len(names) * (len(names) - 1) // 2,
        "pairs_played": len(plays),
        "pairs_ever": len(past),
        "firsts": firsts,
        "never": sorted({tuple(sorted(p)) for p in never}),
        "longest": sorted({(y, *sorted((a, b))) for y, a, b in waits})[:8],
    }
    return rows, stats

"""A rating for every Division I team, in points, from the scores alone.

The football section's own method (tiebreaker/massey.py): the ratings and a
home-court term that best explain every margin played, by least squares.
Nothing about the method is football's; only the inputs change. It needs
every Division I game, not only the Big 12's, because a conference that only
plays itself cannot be placed against anyone.

THE GAMES come from /games with no conference filter, which CBBD caps at
3,000 a call. A season is about 6,000, so it is asked for in date windows,
and any window that comes back at the cap is split in two and asked again,
so the cap can never quietly drop games. Every window is kept on disk like
everything else, and a finished window is never asked for twice.

WHEN. Once a week, on the first build after 07:00 UTC Monday, the same
turnover football's ratings use; only the windows since the last fit are
asked for again.

EARLY SEASON. A November fit has three games a team behind it. Until a team
has played about ten, its rating is blended with last season's final one,
pulled most of the way back to average: a fit that thin is noise, and last
year's team regressed is a better guess than either half alone.
"""
import datetime
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(1, os.path.join(ROOT, "tiebreaker"))

import bb_fetch as fetch                                 # noqa: E402
import massey                                            # noqa: E402

DATA = os.path.join(HERE, "data")
CAP = 3000
FULL_AT = 10          # games a team needs before its own fit stands alone
CARRY = 0.6           # how much of last season's rating survives the summer
RIDGE = 1.0


def _d(s):
    return datetime.date.fromisoformat(s)


def windows(season, through=None):
    """Monthly windows from November 1 to April 15 (to `through` if sooner),
    as (start, end) ISO dates. Monthly keeps each well under the cap; the
    cap check below is the guarantee."""
    start = datetime.date(season - 1, 11, 1)
    end = datetime.date(season, 4, 15)
    if through:
        end = min(end, through)
    out = []
    a = start
    while a <= end:
        nxt = (a.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
        b = min(nxt - datetime.timedelta(days=1), end)
        out.append((a.isoformat(), b.isoformat()))
        a = nxt
    return out


def window_games(a, b, fresh):
    """Every game in [a, b], splitting the window if CBBD hands back the cap."""
    params = {"startDateRange": f"{a}T00:00:00Z",
              "endDateRange": f"{b}T23:59:59Z"}
    rows = fetch.get("games", params, fresh=fresh)
    if len(rows) >= CAP and a != b:
        mid = _d(a) + (_d(b) - _d(a)) / 2
        return (window_games(a, mid.isoformat(), fresh)
                + window_games((mid + datetime.timedelta(days=1)).isoformat(),
                               b, fresh))
    return rows


def season_games(season, refresh=False, today=None):
    """All Division I games of `season` so far. With `refresh`, windows that
    are not over yet are asked for again; finished ones come from disk."""
    today = today or datetime.date.today()
    out, seen = [], set()
    for a, b in windows(season, through=today):
        fresh = refresh and _d(b) >= today - datetime.timedelta(days=1)
        if not os.path.exists(fetch.raw_path("games", {
                "startDateRange": f"{a}T00:00:00Z",
                "endDateRange": f"{b}T23:59:59Z"})):
            fresh = True
        for g in window_games(a, b, fresh):
            if g["id"] not in seen:
                seen.add(g["id"])
                out.append(g)
    return out


POOLED = "~non-D1~"
POOL_UNDER = 6        # a team with fewer games than this is pooled


def rows(games):
    """(home, away, margin, neutral) for every finished game.

    TEAMS WITH A HANDFUL OF GAMES ARE ONE POOLED TEAM, the football fit's
    answer to the same problem. CBBD's season includes every non-Division I
    opponent a Division I team scheduled, each with a game or two; rated on
    its own, each would carry nothing but that game's noise. As one team
    with a few hundred results it is a stable floor to measure against."""
    played = {}
    raw = []
    for g in games:
        hp, ap = g.get("homePoints"), g.get("awayPoints")
        if g.get("status") != "final" or hp is None or ap is None:
            continue
        h, a = g.get("homeTeam"), g.get("awayTeam")
        if h and a and h != a:
            raw.append((h, a, hp - ap, bool(g.get("neutralSite"))))
            played[h] = played.get(h, 0) + 1
            played[a] = played.get(a, 0) + 1
    pool = lambda t: t if played[t] >= POOL_UNDER else POOLED
    return [(pool(h), pool(a), m, n) for h, a, m, n in raw
            if pool(h) != pool(a)]


def fit(games, prior=None):
    """{"ratings": {team: points}, "hfa": points, "sigma": points,
    "games": n} for one season's games, blended toward `prior` (last
    season's final ratings) while teams have played few games."""
    rs = rows(games)
    try:
        ratings, hfa = massey.fit(rs, ridge=RIDGE)
    except ValueError:
        ratings, hfa = {}, 0.0
    played = {}
    for h, a, _, _ in rs:
        played[h] = played.get(h, 0) + 1
        played[a] = played.get(a, 0) + 1
    prior = prior or {}
    out = {}
    for t in set(ratings) | set(prior):
        w = min(1.0, played.get(t, 0) / FULL_AT)
        mine = ratings.get(t)
        before = CARRY * prior[t] if t in prior else None
        if mine is None:
            out[t] = before if before is not None else 0.0
        elif before is None:
            out[t] = mine
        else:
            out[t] = w * mine + (1 - w) * before
    # The spread of a margin around its prediction, which is what turns a
    # rating gap into a probability. From the fit's own residuals when there
    # are enough of them; college basketball's long-run figure otherwise.
    res = [m - (out.get(h, 0) - out.get(a, 0) + (0 if n else hfa))
           for h, a, m, n in rs if h in out and a in out]
    sigma = (math.sqrt(sum(r * r for r in res) / len(res))
             if len(res) > 500 else 11.0)
    out.pop(POOLED, None)
    return {"ratings": {t: round(v, 2) for t, v in sorted(out.items())},
            "hfa": round(hfa or 3.0, 2), "sigma": round(sigma, 2),
            "games": len(rs)}


def path(season):
    return os.path.join(DATA, f"ratings_{season}.json")


def load(season):
    try:
        return json.load(open(path(season)))
    except (OSError, ValueError):
        return None


def due(rec, now=None):
    """Refit once a week: due when the stored fit predates this week's
    Monday 07:00 UTC, the same turnover the football ratings use."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not rec or not rec.get("fitted"):
        return True
    turn = (now - datetime.timedelta(days=now.weekday())).replace(
        hour=7, minute=0, second=0, microsecond=0)
    if turn > now:
        turn -= datetime.timedelta(days=7)
    fitted = datetime.datetime.fromisoformat(rec["fitted"].replace("Z", "+00:00"))
    return fitted < turn


def ratings(season, refresh=False, log=print):
    """This season's ratings, refitted when due and kept in
    data/ratings_<season>.json. Last season's final fit is the prior, and is
    computed once and kept the same way."""
    rec = load(season)
    if rec and not (refresh and due(rec)):
        return rec
    if not refresh and rec is None and season_complete(season):
        pass
    prev = load(season - 1)
    if prev is None and os.path.exists(fetch.raw_path("games", {
            "startDateRange": f"{season - 2}-11-01T00:00:00Z",
            "endDateRange": f"{season - 2}-11-30T23:59:59Z"})):
        prev = None
    try:
        games = season_games(season, refresh=refresh)
    except Exception as e:
        log(f"WARNING: basketball ratings {season}: fetch failed ({e}); "
            f"keeping the last fit")
        return rec
    out = fit(games, (prev or {}).get("ratings"))
    out["season"] = season
    out["fitted"] = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with open(path(season), "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    log(f"basketball ratings {season}: fitted on {out['games']} games, "
        f"home court {out['hfa']}, sigma {out['sigma']}")
    return out


def season_complete(season, today=None):
    today = today or datetime.date.today()
    return today > datetime.date(season, 4, 15)

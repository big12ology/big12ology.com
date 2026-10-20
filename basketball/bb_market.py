"""Men's basketball lines from the-odds-api.com, the site's source for every
line it shows.

CBBD's /lines was the section's first source and it was thin: one or two
books, and whole stretches of the schedule with nothing. The football
section learned the same about CFBD and moved to this feed (tiebreaker/
market.py), which carries seven to twelve US books a game. This module is
that one pointed at basketball_ncaab, and borrows its join, its book
parsing and its averaging rather than keeping a second copy: the neutral-
floor orientation trap and the Utah/Utah State prefix trap are exactly the
same in basketball.

CBBD STAYS UNDERNEATH, not as a rival: the feed only reaches about a week
ahead and has no openers, so a game it has not reached keeps CBBD's line
until it does. Where both have a game, this one wins.

COST. A call is markets x regions in credits, shared with football on one
500-a-month plan. Spreads and totals on the US region is 2 credits; the
moneyline is left out because no basketball page shows one. A call is made
only when a Big 12 game tips in the next two days and the newest capture is
20 hours old, which is about one a day from November to March, roughly 60
credits a month.

KEPT, like every other fetch: the feed's own events go to
data/odds/<season>-<YYYY-MM>.json, deduplicated on the numbers, so a join
fix is a re-parse and the file doubles as a record of line movement.
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(1, os.path.join(ROOT, "tiebreaker"))

import bb_sport as sport                                 # noqa: E402
import market                                            # noqa: E402

SPORT = sport.ODDS_SPORT
SOURCE = market.SOURCE
REGIONS = "us"
MARKETS = "spreads,totals"
MIN_AGE_HOURS = 20
AHEAD = datetime.timedelta(days=2)
DATA = sport.DATA
ARCHIVE = os.path.join(DATA, "odds")


def path(season):
    return os.path.join(DATA, f"lines_{season}.json")


def load(season):
    try:
        return json.load(open(path(season)))
    except (OSError, ValueError):
        return {}


def wanted(games, now):
    """Is a Big 12 game close enough that the books will have posted it?"""
    for g in games:
        t = market._ts(g["start"]) if g.get("start") else None
        if t and not g["final"] and now <= t <= now + AHEAD:
            return True
    return False


def _archive(season, captured, as_of):
    """Append the feed's events, deduplicated on the numbers, one file per
    month of tip-off. See tiebreaker/market.py's archive for why."""
    by_month = {}
    for gid, (g, event) in captured.items():
        by_month.setdefault((g.get("start") or "")[:7] or "xx", {})[gid] = event
    added = 0
    for month, events in by_month.items():
        p = os.path.join(ARCHIVE, f"{season}-{month}.json")
        try:
            book = json.load(open(p)) if os.path.exists(p) else {}
        except (OSError, ValueError):
            print(f"  basketball odds: {p} unreadable, not archiving")
            continue
        hit = 0
        for gid, event in events.items():
            hist = book.setdefault(gid, [])
            if hist and market._digest(hist[-1].get("event", {})) == market._digest(event):
                continue
            hist.append({"at": as_of, "event": event})
            hit += 1
        if hit:
            os.makedirs(ARCHIVE, exist_ok=True)
            with open(p, "w") as f:
                json.dump(book, f, separators=(",", ":"), sort_keys=True)
            added += hit
    return added


def records(raw, games, now, as_of):
    """{game id: record} for the feed events that join to `games`, plus
    the raw events that did, for the archive. Forward only: a game that has
    tipped is never written, so a late fetch cannot rewrite its line."""
    out, captured = {}, {}
    for e in raw:
        try:
            tip = market._ts(e["commence_time"])
        except (KeyError, ValueError):
            continue
        names = [e.get("home_team"), e.get("away_team")]
        if not all(names):
            continue
        # The time narrows to one day; the names decide.
        cands = [g for g in games if g.get("start")
                 and abs((market._ts(g["start"]) - tip).total_seconds()) <= 18 * 3600
                 and market._assign(names, g["home"], g["away"])]
        if len(cands) != 1:
            continue
        g = cands[0]
        if market._ts(g["start"]) <= now:
            continue
        amap = market._assign(names, g["home"], g["away"])
        books = market._books(e, amap[g["home"]], amap[g["away"]])
        rec = {"spread": market._avg(b.get("spread") for b in books),
               "over_under": market._avg(b.get("over_under") for b in books),
               "books": books, "source": SOURCE, "as_of": as_of}
        if rec["spread"] is None and rec["over_under"] is None:
            continue
        out[str(g["id"])] = {k: v for k, v in rec.items() if v not in (None, [])}
        captured[str(g["id"])] = (g, e)
    return out, captured


def refresh(season, games, log=print, now=None, force=False):
    """Ask the feed when a Big 12 game is near and the last capture is old,
    and merge what it says into data/lines_<season>.json. Never fatal."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    have = load(season)
    if not force and not (wanted(games, now) and market.due(
            have, now=now, min_age_hours=MIN_AGE_HOURS)):
        return 0
    try:
        k = market.key()
    except SystemExit:
        log("basketball odds: no ODDS_API_KEY here; keeping CBBD's lines")
        return 0
    try:
        raw, quota = market._get(f"sports/{SPORT}/odds/",
                                 {"regions": REGIONS, "markets": MARKETS,
                                  "oddsFormat": "american",
                                  "includeLinks": "true"}, k)
    except RuntimeError as e:
        log(f"WARNING: basketball odds fetch failed ({e}); keeping what is on disk")
        return 0
    as_of = now.replace(microsecond=0).isoformat()
    got, captured = records(raw, games, now, as_of)
    have.update(got)
    with open(path(season), "w") as f:
        json.dump(have, f, indent=1, sort_keys=True)
    added = _archive(season, captured, as_of)
    log(f"basketball odds: {len(got)} game(s) priced from {len(raw)} events, "
        f"{added} changed capture(s) archived; credits used "
        f"{quota.get('x-requests-last', '?')}, left {quota.get('x-requests-remaining', '?')}")
    return len(got)

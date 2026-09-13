#!/usr/bin/env python3
"""Pull Big 12 game results from collegefootballdata.com.

Needs a key in .env or the environment:  CFBD_API_KEY=...
.env is gitignored; the key is never printed or written to a tracked file.

    python3 fetch.py 2026            # fetch season, cache to data/games_2026.json
    python3 fetch.py 2026 --force    # refetch even if cached
    python3 fetch.py --venues        # one-time: every venue's coordinates

One API call per season fetched. The lines refresh also calls
the-odds-api.com through market.py, which wants ODDS_API_KEY and degrades
to CFBD alone without it.
"""
import collections
import datetime
import json
import os
import subprocess
import sys

import espn as espn_mod
import market as market_mod

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
API = "https://api.collegefootballdata.com"

# Not a season with an asterisk — a season that cannot be compared to any
# other. COVID left teams on 8, 9 and 10 conference games in the same
# standings, so records, rates and distributions all mean something
# different that year. Excluded outright rather than included with a
# footnote, because a footnote reads as optional. Anything iterating a year
# *range* rather than this list will silently pull it back in.
EXCLUDED_SEASONS = frozenset({2020})


def usable_seasons(years):
    """The seasons in `years` that any analysis may use."""
    return [y for y in years if y not in EXCLUDED_SEASONS]


BIG12 = [
    "Arizona", "Arizona State", "Baylor", "BYU", "Cincinnati", "Colorado",
    "Houston", "Iowa State", "Kansas", "Kansas State", "Oklahoma State",
    "TCU", "Texas Tech", "UCF", "Utah", "West Virginia",
]


def key():
    k = os.environ.get("CFBD_API_KEY")
    if not k:
        env = os.path.join(HERE, ".env")
        if os.path.exists(env):
            for line in open(env):
                if line.startswith("CFBD_API_KEY="):
                    k = line.split("=", 1)[1].strip()
                    break
    if not k:
        sys.exit("CFBD_API_KEY not set. Put it in .env or export it.")
    return k


# Calls made by hand, so tools/api-budget.py can add them to what it derives
# from the workflow history. Gitignored: it is a local fact about this laptop,
# not a property of the project, and committing it would put a write in the
# path of every scheduled build for no gain.
USAGE_LOG = os.path.join(DATA, ".api-local.log")


def _note_call(path):
    """Record one call. Never raises: a ledger is not worth a failed fetch."""
    try:
        os.makedirs(DATA, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds")
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {path.split('?')[0]}\n")
    except OSError:
        pass


def get(path, k):
    """One GET. curl rather than urllib so the key never lands in a URL log.

    Every CFBD call in this project comes through here, which is what makes
    the budget knowable: one choke point to count at.
    """
    _note_call(path)
    r = subprocess.run(
        ["curl", "-sS", "-m", "60", "-H", f"Authorization: Bearer {k}",
         f"{API}/{path}"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed on {path}: {r.stderr[:150]}")
    data = json.loads(r.stdout)
    # Quota and auth failures come back as an object, not the expected list.
    # Say so here — left alone, a caller iterates the error dict, gets its
    # keys, and dies with an AttributeError three frames from the cause.
    if isinstance(data, dict) and "message" in data:
        raise RuntimeError(f"CFBD refused {path}: {data['message']}")
    return data


def _refuse_shrink(path, kind, new_count):
    """Refuse to replace a committed file with a gutted one.

    An outage arrives as an exception, and every caller already handles
    those: build.py falls back to committed data, the lines refresh keeps
    the committed lines. But a glitch can also arrive as HTTP 200 with an
    empty or near-empty list, which raises nothing, overwrites the committed
    file, and gets committed back to the repo by the deploy's keep step.
    games_<year>.json going empty is the worst case: the no-new-results
    check in build.py then sees nothing due a score and stops asking, so
    the wipe outlives every hourly build until the weekly refresh.

    Ratings always had this rule in their own units (all sixteen teams
    present, or keep the prior file); this is the same idea for the
    fetchers that count rows rather than teams. Half, not just zero: a
    partial response is as wrong as an empty one, and no real update halves
    one of these files. Schedules add games, lines and media accrue, the
    venue catalog grows. If a fetch ever should legitimately shrink one,
    delete the committed file and refetch.

    Raises rather than skipping the write, so the caller's existing failure
    handling takes over and says so in the build log. Stale beats gutted.
    """
    try:
        old = len(json.load(open(path)))
    except (OSError, ValueError):
        return
    if old and new_count < old / 2:
        raise RuntimeError(
            f"{kind}: fetch returned {new_count} rows against {old} "
            f"committed, refusing to overwrite")


def fetch_season(year, force=False):
    os.makedirs(DATA, exist_ok=True)
    cache = os.path.join(DATA, f"games_{year}.json")
    # force was declared and never read, so every call spent a call —
    # including on seasons that finished years ago and are committed right
    # there. Only a caller that says it wants fresh scores gets them; the
    # build's --fetch path says so explicitly.
    if os.path.exists(cache) and not force:
        games = json.load(open(cache))
        print(f"{year}: {len(games)} games from cache, no call made "
              f"(--force to refetch)")
        return games
    raw = get(f"games?year={year}&seasonType=regular", key())

    games = []
    for g in raw:
        home, away = g.get("homeTeam"), g.get("awayTeam")
        if home not in BIG12 and away not in BIG12:
            continue
        notes = g.get("notes") or ""
        games.append({
            "id": g.get("id"),
            "week": g.get("week"),
            "notes": notes,
            # Both sides must be Big 12 for this to be OUR championship
            # game. Members who joined later drag their old conferences'
            # title games in with them — 2022 arrived carrying the Pac-12's
            # and the American's alongside the Big 12's.
            "ccg": ("championship" in notes.lower()
                    and g.get("homeConference") == "Big 12"
                    and g.get("awayConference") == "Big 12"),
            "start": g.get("startDate"),
            "completed": bool(g.get("completed")),
            "conference_game": bool(g.get("conferenceGame"))
            and g.get("homeConference") == "Big 12"
            and g.get("awayConference") == "Big 12",
            # Where it is played. CFBD returns these on the games call we
            # are already making, so they cost nothing extra; venue_id is
            # what joins a game to a coordinate in data/venues.json.
            "venue": g.get("venue"),
            "venue_id": g.get("venueId"),
            "neutral_site": bool(g.get("neutralSite")),
            # A kickoff window that has not been announced. CFBD still
            # returns a placeholder hour, so without this flag the page
            # publishes a time nobody set.
            "start_tbd": bool(g.get("startTimeTBD")),
            "home": home,
            "away": away,
            "home_conf": g.get("homeConference"),
            "away_conf": g.get("awayConference"),
            "home_class": g.get("homeClassification"),
            "away_class": g.get("awayClassification"),
            "home_points": g.get("homePoints"),
            "away_points": g.get("awayPoints"),
        })
    # Id breaks the tie. Games in the same week that kick off at the same
    # minute sort equal on the first two keys, and CFBD does not return them
    # in a stable order, so without this the same season fetched twice gives
    # byte-different pages — which is the whole basis for rebuilding the
    # domain on a schedule.
    games.sort(key=lambda x: (x["week"], x["start"] or "", x["id"]))
    _refuse_shrink(cache, f"games {year}", len(games))
    with open(cache, "w") as f:
        json.dump(games, f, indent=1)
    done = sum(1 for x in games if x["completed"])
    print(f"{year}: {len(games)} Big 12 games cached ({done} completed) -> {cache}")
    return games


# system -> (endpoint, rating field, home-field bump in the system's units,
#            system units per scoring point — for showing margins as points)
SYSTEMS = {
    "SP+": ("ratings/sp", "rating", 2.5, 1.0),
    "FPI": ("ratings/fpi", "fpi", 2.5, 1.0),
    "Elo": ("ratings/elo", "elo", 55.0, 27.0),
    "SRS": ("ratings/srs", "rating", 2.5, 1.0),
}


def mark_ccg(games):
    """Repair championship-game flags for historical seasons.

    1. A 'championship' note only counts as THE Big 12 CCG when both sides
       are Big 12 — future Big 12 members drag their old conferences' title
       games (Pac-12, AAC) into the data.
    2. CFBD's 2017-2021 feeds don't tag the Big 12 CCG at all. In a strict
       round-robin, no pair meets twice — so the season's rematch, by date,
       is the championship game.
    """
    for g in games:
        if g.get("ccg") and not (g.get("home_conf") == "Big 12"
                                 and g.get("away_conf") == "Big 12"):
            g["ccg"] = False
    if not any(g.get("ccg") for g in games):
        seen = {}
        conf = sorted((g for g in games if g["conference_game"]
                       and g["completed"]),
                      key=lambda g: g["start"] or "")
        for g in conf:
            pair = frozenset((g["home"], g["away"]))
            if pair in seen:
                g["ccg"] = True  # the rematch — round robins have none
            else:
                seen[pair] = g
    return games


def fetch_ratings(year):
    """Rating systems for the what-if favorites. One call per system, for the
    target year only. Preseason numbers appear in late August (Elo and SRS
    only once games are played); until a system covers the full Big 12, the
    prior season's numbers come from disk, not from a second call: they are
    already sitting in ratings_<year>.json from the last refresh, or in the
    committed ratings_<year-1>.json, and a finished season's ratings do not
    change.

    Writes data/ratings_<year>.json =
        {"systems": {name: {year, hfa, per_pt, ratings: {team: r}}}}
    """
    os.makedirs(DATA, exist_ok=True)
    k = key()
    out = os.path.join(DATA, f"ratings_{year}.json")

    def _on_disk(path):
        try:
            return json.load(open(path)).get("systems", {})
        except (OSError, ValueError):
            return {}

    have = _on_disk(out)
    prior = _on_disk(os.path.join(DATA, f"ratings_{year - 1}.json"))

    systems = {}
    for name, (path, field, hfa, per_pt) in SYSTEMS.items():
        raw = get(f"{path}?year={year}", k)
        got = {}
        if isinstance(raw, list):
            # keep every rated team, not just the Big 12 — non-conference
            # favorites need the opponents' numbers too
            got = {r["team"]: r.get(field) for r in raw
                   if r.get("team") and r.get(field) is not None}
        if all(t in got for t in BIG12):
            systems[name] = {"year": year, "hfa": hfa, "per_pt": per_pt,
                             "ratings": got}
            continue
        prev = have.get(name) or prior.get(name)
        if prev and all(t in prev.get("ratings", {}) for t in BIG12):
            # hfa and per_pt come from SYSTEMS, not from the old file, so a
            # constant tuned here is never pinned to a cached value
            systems[name] = {"year": prev["year"], "hfa": hfa,
                             "per_pt": per_pt, "ratings": prev["ratings"]}
    with open(out, "w") as f:
        json.dump({"systems": systems}, f, indent=1)
    years = {n: s["year"] for n, s in systems.items()}
    print(f"{year}: ratings for {years} -> {out}")
    return {"systems": systems}


def _cfbd_lines(year):
    """CFBD's market for games involving Big 12 teams, by book.

    One call returns spread, spreadOpen, overUnder, overUnderOpen and both
    moneylines per provider, and this used to keep the averages and throw
    the provider names away — so a page could say "average of 2 books" and
    never which two. Keeping each book costs nothing extra: same endpoint,
    same call, same quota.

    CFBD convention is the home-team spread (negative = home favored), and
    everything downstream is built on that. market.py converts to it.

    TWO PROVIDER STRINGS, ONE BOOK. CFBD reports "DraftKings" and "Draft
    Kings" separately: the first is its own scrape, the second is ESPN's
    feed passed through (ESPN's odds endpoint for a game returns exactly
    the latter, to the decimal). On 2026-09-13 both were present on 31 of
    42 games and disagreed on 9 of those, so the average was weighting one
    book two thirds against Bovada's one third and landing on numbers
    nobody was posting. Folded here rather than at the display layer,
    because book_count and book_names in build.py read the list directly
    and the pick'em freezes its length into a D1 column.
    """
    raw = get(f"lines?year={year}", key())

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    def canon(name):
        """One spelling per book, so a duplicate cannot be averaged twice."""
        return "".join((name or "").split()).lower()

    out = {}
    for g in raw if isinstance(raw, list) else []:
        if g.get("homeConference") != "Big 12" \
                and g.get("awayConference") != "Big 12":
            continue
        books, seen = [], set()
        for l in g.get("lines") or []:
            c = canon(l.get("provider"))
            if not c or c in seen:
                # First wins. CFBD's own scrape is listed before the ESPN
                # passthrough and carries the opening numbers, which the
                # passthrough does not, so keeping the first is also
                # keeping the richer of the two.
                continue
            seen.add(c)
            b = {"provider": l.get("provider"),
                 "spread": l.get("spread"),
                 "spread_open": l.get("spreadOpen"),
                 "over_under": l.get("overUnder"),
                 "over_under_open": l.get("overUnderOpen"),
                 "home_ml": l.get("homeMoneyline"),
                 "away_ml": l.get("awayMoneyline")}
            books.append({k: v for k, v in b.items() if v is not None})
        rec = {
            "spread": avg(b.get("spread") for b in books),
            "spread_open": avg(b.get("spread_open") for b in books),
            "over_under": avg(b.get("over_under") for b in books),
            "over_under_open": avg(b.get("over_under_open") for b in books),
            "home_ml": avg(b.get("home_ml") for b in books),
            "away_ml": avg(b.get("away_ml") for b in books),
            "books": books,
            "source": "collegefootballdata.com",
            # How many books opened it, kept because the number outlives
            # the books that reported it: an opener carried onto an
            # the-odds-api record sits beside eight books, none of which
            # opened anything, and the card has no other way to say so.
            # Left out entirely at zero rather than stored as 0, because a
            # record with no opener has no count to report and a stored
            # zero would read as a measured one.
            "spread_open_books": sum(
                1 for b in books if b.get("spread_open") is not None) or None,
        }
        if rec["spread"] is not None or rec["over_under"] is not None:
            out[str(g["id"])] = {k: v for k, v in rec.items()
                                 if v not in (None, [])}
    return out


# What a record's openers are called, wherever it came from. CFBD is the
# only source for these: the-odds-api reports what a book is posting now
# and has no concept of where it opened.
_OPENERS = ("spread_open", "over_under_open", "spread_open_books")

CFBD_SOURCE = "collegefootballdata.com"

# How stale CFBD's half of the merge has to be before it is refetched.
#
# THIS EXISTS BECAUSE THE TWO HALVES ARE METERED DIFFERENTLY. fetch_lines
# used to be reached only from the two daily crons, so its CFBD call cost
# about 65 a month and nobody had to think about it. pages.yml now also
# asks for a lines refresh from the hourly weekend builds, to sample the
# market often enough that a noon kickoff does not freeze on a 4:30am
# line. Ungated, that would have put a CFBD call on ~305 runs a month
# against a hard cap of 1,000 that the budget tool already forecasts at
# 555, and the market half would have dragged the CFBD half over a cliff
# it has no reason to be near.
#
# Twenty hours, because what CFBD uniquely supplies barely moves. An
# opening line is fixed the moment it is set and never changes again; the
# look-ahead weeks post over days, not hours. Once a day is generous for
# both, and it lands at ~31 CFBD calls a month, which is fewer than the
# ~65 this spent before the weekend slots existed.
#
# Under the 24 hours between one morning cron and the next, so the daily
# refresh is never the run that gets skipped. Over the 9.5 between the
# morning and the evening catch-all, so the catch-all does not spend a
# second one on the same day.
CFBD_MIN_AGE_HOURS = 20


def fetch_lines(year, force_cfbd=False, force_market=False):
    """The market, merged from two sources, forward only.

    MERGED, NOT OVERWRITTEN, which is the change. the-odds-api carries a
    dozen books against CFBD's two but only reaches about a week ahead
    (see market.py), so neither source alone covers the season. Each
    refresh layers what it learned onto the file rather than replacing it:

      CFBD          every game it has a line for, including the week 10
                    marquee games books have posted and the-odds-api has
                    not reached. Also the only source of openers.
      the-odds-api  the week actually being played, at 7 to 12 books,
                    which is the week a pick'em slate freezes.

    Precedence is by book count, not by source: once a game has an
    the-odds-api record, CFBD does not overwrite it, because replacing
    eleven books with two is a downgrade whichever arrived later. CFBD
    still contributes the openers to that record.

    EACH HALF IS RATE LIMITED ON ITS OWN CLOCK, because they are metered
    against different caps and move at different speeds. The market is
    sampled every few hours (market.MIN_AGE_HOURS); CFBD, whose unique
    contribution is opening lines that never move again, once a day
    (CFBD_MIN_AGE_HOURS). `force_cfbd` is for the Tuesday weekly refresh,
    which publishes the slate and should not freeze it against openers it
    declined to check. `force_market` is for a run made by hand, where the
    point is to see the market as it stands right now and the gate is a
    schedule's economy rather than this caller's.

    FORWARD ONLY. Neither source may touch a record whose game has already
    kicked off. A line that was in the file before its game started stays
    exactly as it was, so nothing here can move a number a published slate
    or a finished week was built from. This is also why the file no longer
    shrinks and _refuse_shrink no longer applies to it: a merge cannot
    lose a game, and an empty response now writes nothing instead of
    everything.

    Writes data/lines_<year>.json = {game_id: {spread, spread_open,
    over_under, over_under_open, home_ml, away_ml, source, as_of, books:
    [{provider, spread, ...}]}}. Older files hold a bare spread number, or
    a dict whose `books` is an integer count; load_lines and the book_*
    helpers normalize all three shapes.
    """
    os.makedirs(DATA, exist_ok=True)
    p = os.path.join(DATA, f"lines_{year}.json")
    existing = json.load(open(p)) if os.path.exists(p) else {}

    games = []
    gp = os.path.join(DATA, f"games_{year}.json")
    if os.path.exists(gp):
        games = json.load(open(gp))
    now = datetime.datetime.now(datetime.timezone.utc)
    started = set()
    for g in games:
        try:
            if datetime.datetime.fromisoformat(
                    g["start"].replace("Z", "+00:00")) <= now:
                started.add(str(g["id"]))
        except (KeyError, ValueError):
            pass

    if force_cfbd or market_mod.due(existing, now, CFBD_MIN_AGE_HOURS,
                                    CFBD_SOURCE):
        cfbd = _cfbd_lines(year)
    else:
        # Skipped, not failed. The openers already in the file carry
        # forward through the merge below, and the look-ahead weeks keep
        # the records they have.
        cfbd = {}
        print(f"{year}: CFBD lines are under {CFBD_MIN_AGE_HOURS}h old, "
              f"no call made")
    as_of = now.replace(microsecond=0).isoformat()

    market = {}
    if games:
        try:
            market, quota = market_mod.fetch(year, games, existing,
                                             force=force_market)
            if market:
                deep = max(len(r.get("books", [])) for r in market.values())
                left = quota.get("x-requests-remaining")
                print(f"{year}: the-odds-api has {len(market)} game(s), "
                      f"{deep} books at most"
                      + (f"; {left} credits left" if left else ""))
            else:
                print(f"{year}: the-odds-api "
                      f"{quota.get('skipped', 'returned no Big 12 games')}")
        except (RuntimeError, SystemExit) as e:
            # Same posture as every other fetcher here: stale beats gutted.
            # The CFBD half of the merge still lands, and the previous
            # capture stays in the file with its own as_of saying how old
            # it is.
            print(f"{year}: the-odds-api unavailable ({e}); keeping CFBD")

    out = dict(existing)
    for gid, rec in cfbd.items():
        if gid in started:
            continue
        prev = out.get(gid)
        # Do not trade a deep book count for a shallow one. The odds
        # record keeps its numbers and takes CFBD's openers.
        if (isinstance(prev, dict)
                and prev.get("source") == market_mod.SOURCE
                and gid not in market):
            for k in _OPENERS:
                if rec.get(k) is not None:
                    prev[k] = rec[k]
            continue
        out[gid] = {**rec, "as_of": as_of}
    for gid, rec in market.items():
        if gid in started:
            continue
        rec = dict(rec)
        for k in _OPENERS:
            v = (cfbd.get(gid) or {}).get(k)
            if v is None:
                v = (existing.get(gid) or {}).get(k) \
                    if isinstance(existing.get(gid), dict) else None
            if v is not None:
                rec[k] = v
        out[gid] = rec

    if not out:
        raise RuntimeError(f"lines {year}: both sources empty, keeping file")

    # A RUN THAT LEARNED NOTHING WRITES NOTHING, which matters now that this
    # is reached from the hourly weekend builds rather than twice a day.
    # Both halves are gated, so most of those runs make no call at all and
    # `out` comes back byte-identical to what was already on disk. Rewriting
    # the file anyway would be harmless; rewriting the SIDECAR would not,
    # because its stamp moves every time and the deploy's keep step commits
    # lines_*.json. That is a commit an hour, all weekend, saying nothing
    # except what time it was — several hundred a season in a history that
    # is meant to be readable.
    if out == existing:
        print(f"{year}: lines unchanged, nothing written")
        return out

    with open(p, "w") as f:
        json.dump(out, f, indent=1)
    # The sidecar stays for files that predate per-record stamps; anything
    # written now carries its own `as_of`, because a merged file holds
    # captures of several ages at once and one file-level stamp would be a
    # claim about the newest record printed against the oldest. The
    # pick'em freezes a line into a slate people are scored against, and
    # freezing last month's market as this week's is the kind of wrong
    # that looks right.
    with open(os.path.join(DATA, f"lines_{year}.meta.json"), "w") as f:
        json.dump({"fetched_at": as_of, "count": len(out)}, f, indent=1)
    src = collections.Counter(v.get("source", "?") for v in out.values()
                              if isinstance(v, dict))
    print(f"{year}: {len(out)} games lined ({dict(src)}) -> {p}")
    return out


def fetch_teams():
    """Team colors/abbreviations -> data/teams.json (one call, rarely changes)."""
    os.makedirs(DATA, exist_ok=True)
    raw = get("teams?conference=B12", key())
    out = {t["school"]: {"color": t.get("color"), "alt": t.get("alternateColor"),
                         "abbr": t.get("abbreviation")}
           for t in raw if t.get("school") in BIG12}
    p = os.path.join(DATA, "teams.json")
    _refuse_shrink(p, "teams", len(out))
    with open(p, "w") as f:
        json.dump(out, f, indent=1)
    print(f"teams: {len(out)} -> {p}")
    return out


def media_path(year):
    return os.path.join(DATA, f"media_{year}.json")


# How stale the broadcast file has to be before it is refetched.
#
# WEEKLY WAS THE WRONG SHAPE, and the reason is upstream rather than here.
# Networks pick games on a rolling 12-/6-day in-season window, and a 6-day
# window for a Saturday game closes the preceding SUNDAY. Refetched once a
# week on Tuesday, an assignment made on Sunday sat invisible for two days,
# and a week with several of them was wrong for most of its run-up.
#
# Twenty hours, matching CFBD_MIN_AGE_HOURS, for the same reasons: under the
# 24 between one morning cron and the next so the daily refresh is never the
# run that gets skipped, over the 9.5 between the morning and the evening
# catch-all so the catch-all does not spend a second call the same day. ~31
# calls a month, one endpoint, against a 1,000-call cap.
MEDIA_MIN_AGE_HOURS = 20


def media_meta_path(year):
    return os.path.join(DATA, f"media_{year}.meta.json")


def media_age_hours(year, now=None):
    """How long since the broadcast file was fetched, or None if unknown.

    A sidecar rather than the file's mtime, because mtime does not survive
    a CI checkout: every runner starts with a fresh clone and every file
    looks seconds old, so a gate reading mtime would skip forever.
    """
    try:
        with open(media_meta_path(year)) as f:
            stamp = json.load(f).get("fetched_at")
        when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - when).total_seconds() / 3600


def load_media(year):
    """{game_id: [{type, outlet}, ...]}. Read from disk; the build never
    calls for these."""
    p = media_path(year)
    return json.load(open(p)) if os.path.exists(p) else {}


def fetch_media(year, force=False):
    """Who is carrying each game -> data/media_<year>.json.

    One call for the season. CFBD returns every FBS game, so this keeps only
    the ones already in games_<year>.json — the file stays small and a game
    we do not track cannot appear on a page.

    Radio is RARE rather than absent, which is a correction: this said there
    was no radio row for any 2026 game, and on 2026-09-13 one arrived
    (ERADM, on a week 2 game already played). Nothing displays it — the
    slate reads tv and web and ignores the rest — so it is stored as it
    comes and filtered at the page.

    DAILY, NOT WEEKLY. Assignments land on a rolling 12-/6-day window, so a
    weekly refetch is up to a week behind one; see MEDIA_MIN_AGE_HOURS.
    `force` is the Tuesday --refresh, which asks regardless.
    """
    p = media_path(year)
    age = media_age_hours(year)
    if os.path.exists(p) and not force and age is not None \
            and age < MEDIA_MIN_AGE_HOURS:
        have = load_media(year)
        print(f"media {year}: fetched {age:.0f}h ago, no call made")
        return have
    ours = set()
    cache = os.path.join(DATA, f"games_{year}.json")
    if os.path.exists(cache):
        ours = {str(g["id"]) for g in json.load(open(cache)) if g.get("id")}
    raw = get(f"games/media?year={year}&seasonType=regular", key())
    out = {}
    for r in raw:
        gid = str(r.get("id"))
        if ours and gid not in ours:
            continue
        outlet = (r.get("outlet") or "").strip()
        kind = (r.get("mediaType") or "").strip()
        if not outlet or not kind:
            continue
        row = {"type": kind, "outlet": outlet}
        if row not in out.setdefault(gid, []):
            out[gid].append(row)
    # BEFORE the fill, so it still measures CFBD against CFBD. Run after,
    # a CFBD response gutted to a handful of rows could be padded back over
    # the threshold by ESPN and the outage would never be reported.
    _refuse_shrink(p, f"media {year}", len(out))

    # ESPN fills what CFBD left blank, and only that. CFBD wins wherever it
    # said anything, including on games where it is the one with a window.
    # See espn.py for why this is here at all; the short version is that
    # CFBD's feed is the preseason announcement and the season is assigned
    # on a rolling 12-/6-day window it does not follow.
    #
    # Guarded whole: this is a best-effort second source on an undocumented
    # endpoint, and it must never be the reason the CFBD half is lost.
    try:
        sched = json.load(open(cache)) if os.path.exists(cache) else []
        gap, note = espn_mod.fill(sched, out)
        for gid, rows in gap.items():
            out.setdefault(gid, []).extend(rows)
        if gap:
            print(f"media {year}: ESPN filled {len(gap)} blank(s) "
                  f"({note})")
    except Exception as e:
        print(f"media {year}: ESPN fallback unavailable ({e})")

    with open(p, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    # The stamp the gate reads. Written after the file, so a failed write
    # cannot leave a fresh stamp over stale contents and suppress the next
    # fetch for a day.
    with open(media_meta_path(year), "w") as f:
        json.dump({"fetched_at": datetime.datetime.now(datetime.timezone.utc)
                                         .replace(microsecond=0).isoformat(),
                   "count": len(out)}, f, indent=1)
    tv = sum(1 for v in out.values() if any(m["type"] == "tv" for m in v))
    print(f"media {year}: {len(out)} games, {tv} with a TV window -> {p}")
    return out


VENUES = os.path.join(DATA, "venues.json")


def load_venues():
    """venue_id -> {name, city, state, lat, lon}. Committed, and read from
    disk everywhere: nothing in a build is allowed to call for these."""
    if not os.path.exists(VENUES):
        return {}
    return json.load(open(VENUES))


def fetch_venues(force=False):
    """Every venue's coordinates -> data/venues.json.

    One call, once, for the whole catalog — stadiums do not move, and the
    handful that open or get renamed each year arrive with the next
    --venues run rather than with every build. The build never calls this;
    it reads the committed file and shows no forecast for a venue it has
    never heard of.
    """
    if os.path.exists(VENUES) and not force:
        have = load_venues()
        print(f"venues: {len(have)} already cached, no call made "
              f"(--force to refetch)")
        return have
    os.makedirs(DATA, exist_ok=True)
    raw = get("venues", key())
    out = {}
    for v in raw:
        vid = v.get("id")
        lat, lon = v.get("latitude"), v.get("longitude")
        if vid is None or lat is None or lon is None:
            continue
        rec = {"name": v.get("name"), "city": v.get("city"),
               "state": v.get("state"),
               "lat": round(float(lat), 4),
               "lon": round(float(lon), 4),
               "tz": v.get("timezone")}
        # Only when true, so the file does not carry 800 "dome": false lines
        # to say the ordinary thing. A roof means the forecast is not about
        # the game — see weather.py — and it is a property of the building,
        # which is exactly the kind of fact this cached catalog is for.
        if v.get("dome"):
            rec["dome"] = True
        out[str(vid)] = rec
    _refuse_shrink(VENUES, "venues", len(out))
    with open(VENUES, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print(f"venues: {len(out)} with coordinates -> {VENUES}")
    return out


if __name__ == "__main__":
    if "--venues" in sys.argv:
        fetch_venues(force="--force" in sys.argv)
        sys.exit(0)
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    if "--media" in sys.argv:
        fetch_media(year, force="--force" in sys.argv)
        sys.exit(0)
    fetch_season(year, force="--force" in sys.argv)
    fetch_ratings(year)
    fetch_lines(year)
    if not os.path.exists(os.path.join(DATA, "teams.json")):
        fetch_teams()

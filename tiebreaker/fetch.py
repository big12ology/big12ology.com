#!/usr/bin/env python3
"""Pull Big 12 game results from collegefootballdata.com.

Needs a key in .env or the environment:  CFBD_API_KEY=...
.env is gitignored; the key is never printed or written to a tracked file.

    python3 fetch.py 2026            # fetch season, cache to data/games_2026.json
    python3 fetch.py 2026 --force    # refetch even if cached
    python3 fetch.py --venues        # one-time: every venue's coordinates

One API call per season fetched.
"""
import datetime
import json
import os
import subprocess
import sys

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


def fetch_lines(year):
    """The market for games involving Big 12 teams: every book CFBD
    carries, by name, plus the averages the pages display. CFBD convention
    is the home-team spread (negative = home favored).

    One call returns spread, spreadOpen, overUnder, overUnderOpen and both
    moneylines per provider, and this used to keep the averages and throw
    the provider names away — so a page could say "average of 2 books" and
    never which two. Keeping each book costs nothing extra: same endpoint,
    same call, same quota.

    Writes data/lines_<year>.json = {game_id: {spread, spread_open,
    over_under, over_under_open, home_ml, away_ml, books: [{provider,
    spread, ...}]}}. Older files hold a bare spread number, or a dict whose
    `books` is an integer count; load_lines and the book_* helpers
    normalize all three shapes.
    """
    os.makedirs(DATA, exist_ok=True)
    raw = get(f"lines?year={year}", key())

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    out = {}
    for g in raw if isinstance(raw, list) else []:
        if g.get("homeConference") != "Big 12" \
                and g.get("awayConference") != "Big 12":
            continue
        books = []
        for l in g.get("lines") or []:
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
        }
        if rec["spread"] is not None or rec["over_under"] is not None:
            out[str(g["id"])] = {k: v for k, v in rec.items()
                                 if v not in (None, [])}
    p = os.path.join(DATA, f"lines_{year}.json")
    _refuse_shrink(p, f"lines {year}", len(out))
    with open(p, "w") as f:
        json.dump(out, f, indent=1)
    # When, beside what. The file itself carries no date and is overwritten in
    # place, so a stale one — a refresh that failed, a quota that ran out
    # mid-season — is indistinguishable from a fresh one by looking at it.
    # That was harmless while lines only decorated a page. The pick'em freezes
    # a line into a slate people are scored against, and freezing last month's
    # market as this week's is the kind of wrong that looks right. Kept as a
    # sidecar rather than a key inside the file so nothing reading the old
    # shape has to learn about it.
    with open(os.path.join(DATA, f"lines_{year}.meta.json"), "w") as f:
        json.dump({"fetched_at": datetime.datetime.now(datetime.timezone.utc)
                                          .replace(microsecond=0).isoformat(),
                   "count": len(out)}, f, indent=1)
    print(f"{year}: closing lines for {len(out)} games -> {p}")
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

    Radio is not in this feed. CFBD carries tv and web (and historically
    'ppv'); there is no radio row for any 2026 game, so the page says what
    it knows and stays quiet about the rest rather than showing an empty
    Radio label on every row.

    Assignments firm up roughly two weeks out and move, so this belongs on
    the weekly --refresh, beside ratings and lines, not on the hourly build.
    """
    p = media_path(year)
    if os.path.exists(p) and not force:
        have = load_media(year)
        print(f"media {year}: {len(have)} games already cached, no call made")
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
    _refuse_shrink(p, f"media {year}", len(out))
    with open(p, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
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

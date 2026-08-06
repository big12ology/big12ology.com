#!/usr/bin/env python3
"""Pull Big 12 game results from collegefootballdata.com.

Needs a key in .env or the environment:  CFBD_API_KEY=...
.env is gitignored; the key is never printed or written to a tracked file.

    python3 fetch.py 2026            # fetch season, cache to data/games_2026.json
    python3 fetch.py 2026 --force    # refetch even if cached

One API call per season fetched.
"""
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


def get(path, k):
    """One GET. curl rather than urllib so the key never lands in a URL log."""
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


def fetch_season(year, force=False):
    os.makedirs(DATA, exist_ok=True)
    cache = os.path.join(DATA, f"games_{year}.json")
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
    """Rating systems for the what-if favorites. Preseason numbers for a new
    year appear in late August; each system falls back to the prior season
    until then.

    Writes data/ratings_<year>.json =
        {"systems": {name: {year, hfa, per_pt, ratings: {team: r}}}}
    """
    os.makedirs(DATA, exist_ok=True)
    k = key()
    systems = {}
    for name, (path, field, hfa, per_pt) in SYSTEMS.items():
        got, used = {}, None
        for used in (year, year - 1):
            raw = get(f"{path}?year={used}", k)
            if isinstance(raw, list):
                # keep every rated team, not just the Big 12 — non-conference
                # favorites need the opponents' numbers too
                got = {r["team"]: r.get(field) for r in raw
                       if r.get("team") and r.get(field) is not None}
            if all(t in got for t in BIG12):
                break
        if got:
            systems[name] = {"year": used, "hfa": hfa, "per_pt": per_pt,
                             "ratings": got}
    out = os.path.join(DATA, f"ratings_{year}.json")
    with open(out, "w") as f:
        json.dump({"systems": systems}, f, indent=1)
    years = {n: s["year"] for n, s in systems.items()}
    print(f"{year}: ratings for {years} -> {out}")
    return {"systems": systems}


def fetch_lines(year):
    """Closing spreads for games involving Big 12 teams. Averaged across
    providers; CFBD convention is the home-team spread (negative = home
    favored). Writes data/lines_<year>.json = {game_id: spread}."""
    os.makedirs(DATA, exist_ok=True)
    raw = get(f"lines?year={year}", key())
    out = {}
    for g in raw if isinstance(raw, list) else []:
        if g.get("homeConference") != "Big 12" \
                and g.get("awayConference") != "Big 12":
            continue
        spreads = [l.get("spread") for l in g.get("lines", [])
                   if l.get("spread") is not None]
        if spreads:
            out[str(g["id"])] = round(sum(spreads) / len(spreads), 1)
    p = os.path.join(DATA, f"lines_{year}.json")
    with open(p, "w") as f:
        json.dump(out, f, indent=1)
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
    with open(p, "w") as f:
        json.dump(out, f, indent=1)
    print(f"teams: {len(out)} -> {p}")
    return out


if __name__ == "__main__":
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    fetch_season(year, force="--force" in sys.argv)
    fetch_ratings(year)
    fetch_lines(year)
    if not os.path.exists(os.path.join(DATA, "teams.json")):
        fetch_teams()

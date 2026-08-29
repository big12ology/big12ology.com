#!/usr/bin/env python3
"""Kickoff-hour forecasts for the games close enough to have one.

    python3 weather.py            # report what the current slate would show

Open-Meteo, which needs no key and meters nothing — the opposite of CFBD,
and the two want opposite handling. CFBD data is precious and permanent, so
it is fetched once and committed. A forecast is free and perishable: one
that is six hours old is worse than one fetched during this build, and
committing it would put an hourly churn of numbers nobody can verify into
the repo. So this is fetched at build time and never stored in git.

Three things keep it cheap anyway:

  * Only games inside the forecast horizon are asked about. Open-Meteo
    reaches about sixteen days; the rest of the season has no answer and
    costs nothing to not ask.
  * Every venue in range goes into one request. Open-Meteo takes parallel
    latitude and longitude lists, so a full week's slate is a single call
    rather than one per stadium.
  * A local cache with a short life covers repeated local builds. CI starts
    from a fresh checkout, so it never hits the cache and always renders a
    forecast fetched minutes earlier — which is the point.

Failure is not an error. No network, a bad response, a venue with no
coordinates: the games keep their other fields and the page shows no
forecast rather than a wrong one.
"""
import datetime
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "weather_cache.json")


def _played_path(season):
    """Observed conditions for games that have been played, one file a season.

    THIS ONE IS COMMITTED, and it is the opposite of the cache beside it. A
    forecast is perishable, so weather.py fetches it every build and stores
    nothing; what a game was played in never changes again, and Open-Meteo
    only serves the hour back about 92 days. Without a record, a September
    kickoff silently reverted to the venue's ten-season average some time in
    December -- the page quietly getting worse months after anybody would
    think to look at it.

    So it is written once, when the game is close enough to still have an
    answer, and read forever after. Same rule the rest of this repo follows
    for anything fetched from outside: pull it once, keep the response.
    """
    return os.path.join(DATA, f"weather_played_{season}.json")
API = "https://api.open-meteo.com/v1/forecast"

HORIZON_DAYS = 16       # as far as the forecast model actually goes
# And backwards, for games already played. Open-Meteo serves past hours from
# the forecast endpoint for about 92 days, so this stays inside that with
# room. Beyond it a game falls back to the venue average again, which is the
# honest answer when the observation is no longer retrievable.
LOOKBACK_DAYS = 85
CACHE_MINUTES = 90      # a rebuild inside this window reuses what it got


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse(iso):
    if not iso:
        return None
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_cache():
    try:
        c = json.load(open(CACHE))
        fetched = _parse(c.get("fetched"))
        if not fetched:
            return {}
        age = (_utcnow() - fetched).total_seconds() / 60
        return c.get("games", {}) if age < CACHE_MINUTES else {}
    except Exception:
        return {}


def _load_played(season):
    try:
        return json.load(open(_played_path(season))).get("games", {})
    except Exception:
        return {}


def _save_played(season, by_game):
    """Sorted and one key per line, so a diff shows the games that were added
    and nothing else. This lands in commits alongside the slate."""
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(_played_path(season), "w") as f:
            json.dump({"season": season,
                       "games": {k: by_game[k] for k in sorted(by_game)}},
                      f, indent=1, sort_keys=True)
            f.write("\n")
    except OSError:
        pass


def _save_cache(by_game):
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(CACHE, "w") as f:
            json.dump({"fetched": _utcnow().isoformat(), "games": by_game}, f)
    except OSError:
        pass                      # a cache that cannot be written is not fatal


def _get(url):
    r = subprocess.run(["curl", "-sS", "-m", "45", url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:150])
    return json.loads(r.stdout)


def in_range(games, now=None):
    """The games this can speak to: kickoff known, and inside the window.

    BOTH DIRECTIONS. A played game used to be skipped outright, so a game
    page that had a real answer available fell back to the venue's ten-season
    average and printed it under a final score: the 2026 opener in Dublin read
    "Average 61F, 10 mph, 60% of days see rain" when the hour it kicked off in
    was 63.8F and 2.5 mph. An average is what you show when nothing better
    exists, and once a game is played something better does.

    Open-Meteo serves observed hours from the same endpoint as the forecast,
    so this costs no extra request, only a wider date range on the one that
    was already going out.
    """
    now = now or _utcnow()
    edge = now + datetime.timedelta(days=HORIZON_DAYS)
    floor = now - datetime.timedelta(days=LOOKBACK_DAYS)
    out = []
    for g in games:
        # A roof settles it. Nothing renders a dome's forecast, so fetching
        # one buys a column of numbers no page will ever print — and the
        # request is shared with every other venue in range, so leaving it in
        # makes the whole batch wider for nothing.
        if g.get("dome"):
            continue
        when = _parse(g.get("start"))
        if when and floor <= when <= edge:
            out.append((g, when))
    return out


def attach(games, venues, quiet=False, season=None):
    """Hang a `weather` dict on every game close enough to have a forecast.

    Returns the number of games given one. Mutates `games` in place, which
    is what the build wants — the forecast is a property of the game
    everywhere it is rendered.
    """
    # The record first, and at any age. A game played last September is long
    # past the window Open-Meteo will answer for, and it does not need one:
    # the answer was written down when it was fresh.
    # Passed in rather than sniffed off the games: they carry no season field,
    # and a store keyed on a guess is a store that silently writes nowhere.
    played = _load_played(season) if season else {}
    for g in games:
        if g.get("completed") and not g.get("dome"):
            w = played.get(str(g.get("id")))
            if w:
                g["weather"] = dict(w, observed=True)

    # Only what is still missing an answer.
    due = [(g, w) for g, w in in_range(games) if not g.get("weather")]
    if not due:
        return sum(1 for g in games if g.get("weather"))

    cached = _load_cache()
    hit = {}
    need = []
    for g, when in due:
        gid = str(g.get("id"))
        if gid in cached:
            hit[gid] = cached[gid]
        else:
            need.append((g, when, gid))

    if need:
        # One request for every venue in range. Distinct coordinates only:
        # a doubleheader at one stadium is one column of the answer.
        spots, index = [], {}
        for g, when, gid in need:
            v = venues.get(str(g.get("venue_id")))
            if not v:
                continue
            pt = (v["lat"], v["lon"])
            if pt not in index:
                index[pt] = len(spots)
                spots.append(pt)
        if spots:
            lats = ",".join(str(p[0]) for p in spots)
            lons = ",".join(str(p[1]) for p in spots)
            start = min(w for _, w, _ in need).date().isoformat()
            end = max(w for _, w, _ in need).date().isoformat()
            url = (f"{API}?latitude={lats}&longitude={lons}"
                   "&hourly=temperature_2m,wind_speed_10m,"
                   "precipitation_probability"
                   "&temperature_unit=fahrenheit&wind_speed_unit=mph"
                   f"&timezone=UTC&start_date={start}&end_date={end}")
            try:
                raw = _get(url)
                # One location comes back as an object, several as a list.
                blocks = raw if isinstance(raw, list) else [raw]
                for g, when, gid in need:
                    v = venues.get(str(g.get("venue_id")))
                    if not v:
                        continue
                    b = blocks[index[(v["lat"], v["lon"])]]
                    w = _at_hour(b.get("hourly") or {}, when)
                    if w:
                        hit[gid] = w
                _save_cache(hit)
            except Exception as e:
                if not quiet:
                    print(f"weather: no forecast this build ({e})")

    fresh = {}
    for g, _ in due:
        w = hit.get(str(g.get("id")))
        if w:
            # Played games carry the same three numbers, but they are a record
            # rather than a forecast and the page has to be able to say so.
            was = bool(g.get("completed"))
            g["weather"] = dict(w, observed=was)
            # And a record gets written down. Only played games: a forecast
            # committed to the repo would be an hourly churn of numbers
            # nobody can check, which is what the cache beside this exists
            # to avoid.
            if was:
                fresh[str(g.get("id"))] = w
    if fresh and season:
        played.update(fresh)
        _save_played(season, played)
        if not quiet:
            print(f"weather: recorded {len(fresh)} played "
                  f"game{'' if len(fresh) == 1 else 's'}")
    return sum(1 for g in games if g.get("weather"))


NORMALS = os.path.join(DATA, "normals.json")


def load_normals():
    """venue_id -> {bucket: {tempF, windMph, rainPct}}. Committed by
    normals.py; read from disk and never fetched during a build."""
    try:
        return json.load(open(NORMALS))
    except (OSError, ValueError):
        return {}


def normal_for(g, normals):
    """What that venue is usually like when that game is played.

    Only consulted when there is no forecast — a real one always wins. The
    page renders this in the muted voice with the word "average" in front,
    because it is a fact about the place and not a claim about the day.
    """
    if not normals or g.get("weather"):
        return None
    # Same rule as the forecast: a ten-year average for a roofed stadium is
    # a fact about the city, not about the game, and the line says "Indoors"
    # instead. Guarded here as well as in the renderer so the average cannot
    # reappear through the other door.
    if g.get("dome"):
        return None
    when = _parse(g.get("start"))
    if not when:
        return None
    at = normals.get(str(g.get("venue_id")))
    if not at:
        return None
    d = when.date()
    return at.get(f"{d.month:02d}{'a' if d.day <= 15 else 'b'}")


def _at_hour(hourly, when):
    """The forecast for the hour the ball is kicked, not the day's."""
    times = hourly.get("time") or []
    if not times:
        return None
    want = when.replace(minute=0, second=0, microsecond=0)
    stamp = want.strftime("%Y-%m-%dT%H:00")
    try:
        i = times.index(stamp)
    except ValueError:
        return None

    def at(field):
        vals = hourly.get(field) or []
        return vals[i] if i < len(vals) and vals[i] is not None else None

    temp = at("temperature_2m")
    if temp is None:
        return None
    out = {"tempF": temp}
    wind = at("wind_speed_10m")
    if wind is not None:
        out["windMph"] = wind
    pop = at("precipitation_probability")
    if pop is not None:
        out["precipChance"] = pop
    return out


if __name__ == "__main__":
    import fetch as fetcher
    year = datetime.date.today().year
    path = os.path.join(DATA, f"games_{year}.json")
    if not os.path.exists(path):
        year -= 1
        path = os.path.join(DATA, f"games_{year}.json")
    games = json.load(open(path))
    got = attach(games, fetcher.load_venues(), season=year)
    print(f"{year}: {got} of {len(in_range(games))} games in range "
          f"have a kickoff forecast")
    for g in games:
        if g.get("weather"):
            print(f"  {g['away']} at {g['home']}: {g['weather']}")

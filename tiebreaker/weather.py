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
API = "https://api.open-meteo.com/v1/forecast"

HORIZON_DAYS = 16       # as far as the forecast model actually goes
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
    """The games a forecast can actually speak to: not yet played, kickoff
    known, and inside the horizon."""
    now = now or _utcnow()
    edge = now + datetime.timedelta(days=HORIZON_DAYS)
    out = []
    for g in games:
        if g.get("completed"):
            continue
        when = _parse(g.get("start"))
        if when and now - datetime.timedelta(hours=6) <= when <= edge:
            out.append((g, when))
    return out


def attach(games, venues, quiet=False):
    """Hang a `weather` dict on every game close enough to have a forecast.

    Returns the number of games given one. Mutates `games` in place, which
    is what the build wants — the forecast is a property of the game
    everywhere it is rendered.
    """
    due = in_range(games)
    if not due:
        return 0

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

    for g, _ in due:
        w = hit.get(str(g.get("id")))
        if w:
            g["weather"] = w
    return sum(1 for g, _ in due if g.get("weather"))


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
    got = attach(games, fetcher.load_venues())
    print(f"{year}: {got} of {len(in_range(games))} games in range "
          f"have a kickoff forecast")
    for g in games:
        if g.get("weather"):
            print(f"  {g['away']} at {g['home']}: {g['weather']}")

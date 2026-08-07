#!/usr/bin/env python3
"""What the weather is usually like, per venue, per fortnight of the season.

    python3 normals.py            # build data/normals.json (skips if present)
    python3 normals.py --force    # rebuild from scratch
    python3 normals.py --show 3636  # print one venue's buckets

A forecast reaches sixteen days. A season is four months. For most of the
schedule the honest thing to show is not a blank but the climate: this venue,
this fortnight, ten years of history. It is not a prediction and the page
never dresses it as one — it is a fact about the place.

Open-Meteo's archive is keyless and unmetered, and the attendance tracker
already reads the same source for games that have been played. Averages do
not change, so this runs by hand and its output is committed; the build reads
the file and calls nothing. Re-run it when new venues appear in the schedule,
the way `fetch.py --venues` works.

Only the venues a tracked season actually uses are asked about — the
catalogue holds 800 and the schedules touch a tenth of that — and only the
months a game is played in.
"""
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "normals.json")
API = "https://archive-api.open-meteo.com/v1/archive"

# Open-Meteo prices a request by locations x variables x days, not by
# request count, so one call for ten venues across ten whole years trips the
# per-minute limit on its own. Asking season by season for a handful of
# venues is the same data at a fraction of the weight — January in Manhattan
# is not something this page will ever need.
YEARS = 10               # of history behind each average
BATCH = 5                # venues per request
SEASON = ((8, 1), (12, 31))   # the only months a game is played in
MONTHS = (8, 9, 10, 11, 12)
WET_INCHES = 0.01        # a day the ground noticed
PACE = 0.8               # seconds between requests


def bucket(d):
    """Half-month key: '10a' is the first of October, '10b' the second.

    A fortnight is the resolution the underlying thing actually has. A
    single calendar day of history is thirty-odd observations of noise;
    a month spans the whole of November, which in Morgantown is two
    different climates.
    """
    return f"{d.month:02d}{'a' if d.day <= 15 else 'b'}"


def _get(url, retried=False):
    r = subprocess.run(["curl", "-sS", "-m", "180", url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:200])
    data = json.loads(r.stdout)
    # A refusal comes back as an object with a reason, and reading it as a
    # result yields no days, no error, and a file full of nothing. Say so.
    if isinstance(data, dict) and data.get("error"):
        reason = str(data.get("reason"))
        if "limit" in reason.lower() and not retried:
            time.sleep(65)
            return _get(url, retried=True)
        raise RuntimeError(f"open-meteo refused: {reason}")
    return data


def venues_in_play():
    """venue_id -> venue record, for venues a tracked season actually uses.

    The catalogue holds 800; the schedules touch a tenth of that. Asking
    about the rest would be ten times the work for pages nobody can reach.
    """
    import fetch as fetcher
    cat = fetcher.load_venues()
    used = set()
    for name in os.listdir(DATA):
        if not (name.startswith("games_") and name.endswith(".json")):
            continue
        for g in json.load(open(os.path.join(DATA, name))):
            if g.get("venue_id") is not None:
                used.add(str(g["venue_id"]))
    return {vid: cat[vid] for vid in sorted(used) if vid in cat}


def build(force=False):
    have = _existing()
    if have and not force:
        # A partial file resumes below; only a complete one short-circuits,
        # and "complete" means every venue the schedules actually use.
        missing = [v for v in venues_in_play() if v not in have]
        if not missing:
            print(f"normals: {len(have)} venues already cached, no call made "
                  f"(--force to rebuild)")
            return have

    venues = venues_in_play()
    if not venues:
        sys.exit("no venues with coordinates — run fetch.py --venues first")
    # The archive ends yesterday, and asking past it is a refused request,
    # not an empty one. The last complete season is the one before a season
    # now in progress.
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    last = yesterday.year - 1 if yesterday.month < 12 else yesterday.year
    seasons = list(range(last - YEARS + 1, last + 1))
    ids = list(venues)
    batches = -(-len(ids) // BATCH)
    print(f"normals: {len(venues)} venues, seasons {seasons[0]}-{seasons[-1]}, "
          f"{batches * len(seasons)} request(s)")

    # Resume rather than restart. Open-Meteo meters by the hour as well as
    # the minute, and a run that dies two hundred requests in should not
    # throw those two hundred away — the venues already answered for stay
    # answered.
    out = {} if force else _existing()
    todo = [v for v in ids if v not in out]
    if not todo:
        print(f"normals: all {len(ids)} venues already built")
        return out
    if len(todo) < len(ids):
        print(f"  resuming: {len(ids) - len(todo)} already done, "
              f"{len(todo)} to go")
    ids = todo
    raw = {v: {} for v in ids}
    done = 0
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        lats = ",".join(str(venues[v]["lat"]) for v in chunk)
        lons = ",".join(str(venues[v]["lon"]) for v in chunk)
        for yr in seasons:
            start = datetime.date(yr, *SEASON[0])
            end = min(datetime.date(yr, *SEASON[1]), yesterday)
            url = (f"{API}?latitude={lats}&longitude={lons}"
                   f"&start_date={start}&end_date={end}"
                   "&daily=temperature_2m_mean,wind_speed_10m_mean,"
                   "precipitation_sum"
                   "&temperature_unit=fahrenheit&wind_speed_unit=mph"
                   "&precipitation_unit=inch&timezone=UTC")
            blocks = _get(url)
            if not isinstance(blocks, list):
                blocks = [blocks]
            for vid, block in zip(chunk, blocks):
                d = block.get("daily") or {}
                for key, vals in d.items():
                    raw[vid].setdefault(key, []).extend(vals or [])
            done += 1
            time.sleep(PACE)
        # Bank the batch before asking for the next one.
        for vid in chunk:
            got = _aggregate(raw[vid])
            if got:
                out[vid] = got
        _save(out)
        print(f"  {min(i + BATCH, len(ids))}/{len(ids)} venues, "
              f"{done} requests")

    print(f"normals: {len(out)} venues -> {OUT}")
    return out


def _existing():
    try:
        return json.load(open(OUT))
    except (OSError, ValueError):
        return {}


def _save(out):
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)


def _aggregate(daily):
    """Daily history -> {bucket: {tempF, windMph, rainPct, days}}."""
    times = daily.get("time") or []
    temps = daily.get("temperature_2m_mean") or []
    winds = daily.get("wind_speed_10m_mean") or []
    rains = daily.get("precipitation_sum") or []
    acc = {}
    for i, stamp in enumerate(times):
        try:
            d = datetime.date.fromisoformat(stamp)
        except ValueError:
            continue
        if d.month not in MONTHS:
            continue
        t = temps[i] if i < len(temps) else None
        if t is None:
            continue
        w = winds[i] if i < len(winds) else None
        p = rains[i] if i < len(rains) else None
        a = acc.setdefault(bucket(d), {"t": [], "w": [], "wet": 0, "n": 0})
        a["t"].append(t)
        if w is not None:
            a["w"].append(w)
        if p is not None:
            a["n"] += 1
            if p >= WET_INCHES:
                a["wet"] += 1
    out = {}
    for k, a in acc.items():
        if not a["t"]:
            continue
        out[k] = {
            "tempF": round(sum(a["t"]) / len(a["t"])),
            "windMph": round(sum(a["w"]) / len(a["w"])) if a["w"] else None,
            "rainPct": round(100 * a["wet"] / a["n"]) if a["n"] else None,
            "days": len(a["t"]),
        }
    return out


if __name__ == "__main__":
    if "--show" in sys.argv:
        vid = sys.argv[sys.argv.index("--show") + 1]
        data = json.load(open(OUT))
        print(vid, json.dumps(data.get(vid, {}), indent=1))
    else:
        build(force="--force" in sys.argv)

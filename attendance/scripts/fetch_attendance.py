#!/usr/bin/env python3
"""Fetch Big 12 home-game attendance from the CollegeFootballData API.

Pulls all regular-season games for the season, keeps non-neutral home games for
Big 12 teams, and merges them into data/seasons/<year>.json. Games without
attendance yet (unplayed or unreported) are kept with attendance: null so the
site can show the schedule; totals only count reported games.

Each game is enriched with opponent, local kickoff date/time, final score,
the ESPN game id (CFBD reuses ESPN ids, so box-score links are free), and
kickoff-hour weather from the Open-Meteo historical archive (free, keyless;
skipped silently if unavailable).

Requires CFBD_API_KEY (free key: https://collegefootballdata.com/key).

Week numbers are derived from each game's venue-local date (see display_week)
rather than trusting CFBD's week field.

Usage: CFBD_API_KEY=... python3 scripts/fetch_attendance.py 2026
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.collegefootballdata.com"
WEATHER_API = "https://archive-api.open-meteo.com/v1/archive"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary"


def get_json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def cfbd(path: str, api_key: str, **params):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    return get_json(url, {"Authorization": f"Bearer {api_key}"})


def display_week(d: date, season: int) -> int:
    """Week number derived from the game's local date. Weeks run
    Tuesday–Monday: Week 1 ends on Labor Day Monday (so Labor Day games count
    toward Week 1, and a Tuesday game starts the next week); Week 0 is the
    week before. Neither the sheet's hand-entered weeks nor CFBD's week field
    is reliable at boundaries (CFBD has no Week 0; a Friday game belongs with
    the following Saturday)."""
    sept1 = date(season, 9, 1)
    labor_day = sept1 + timedelta(days=(7 - sept1.weekday()) % 7)
    week1_tuesday = labor_day - timedelta(days=6)
    return 1 + (d - week1_tuesday).days // 7


def kickoff_local(start: str, tz: ZoneInfo) -> datetime | None:
    """CFBD reports kickoff in UTC; convert to venue-local time (an evening
    game otherwise lands on the next calendar day)."""
    if not start:
        return None
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(tz)


def load_api_key() -> str:
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("CFBD_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        sys.exit(
            "CFBD_API_KEY is not set. Export it, or put CFBD_API_KEY=... in .env "
            "(gitignored). Free key: https://collegefootballdata.com/key"
        )
    return key


def espn_attendance(espn_id) -> int | None:
    """Second fetcher: ESPN's summary API, hit directly. Same upstream chain
    as CFBD but a different pipeline — on game night ESPN has attendance
    before CFBD's ingest picks it up, so 'first source that has it' wins.
    ESPN reports 0 when no attendance was recorded; treat that as missing."""
    try:
        d = get_json(f"{ESPN_SUMMARY}?event={espn_id}", {"User-Agent": "Mozilla/5.0"})
        att = d.get("gameInfo", {}).get("attendance")
        return att if att else None
    except Exception:
        return None


def update_seasons_index(year: int) -> None:
    path = ROOT / "data" / "seasons" / "index.json"
    index = json.loads(path.read_text())
    if year not in index["seasons"]:
        index["seasons"] = sorted(index["seasons"] + [year])
        path.write_text(json.dumps(index, indent=2) + "\n")


def fetch_weather(games: list, venues_by_id: dict) -> None:
    """Attach kickoff-hour weather to completed games, one Open-Meteo archive
    call per venue (hourly series spanning that venue's games). Best-effort:
    any failure just leaves games without weather."""
    by_venue = {}
    for g in games:
        vid = g.pop("_venueId", None)
        dt = g.pop("_kickoffUtc", None)
        if vid is None or dt is None or not g.get("_completed", True):
            continue
        coords = venues_by_id.get(vid)
        if not coords:
            continue
        by_venue.setdefault((vid, coords), []).append((g, dt))

    for (vid, (lat, lon)), items in by_venue.items():
        dates = [dt.date() for _, dt in items]
        url = f"{WEATHER_API}?" + urllib.parse.urlencode(
            {
                "latitude": lat,
                "longitude": lon,
                "start_date": min(dates).isoformat(),
                "end_date": max(dates).isoformat(),
                "hourly": "temperature_2m,precipitation,wind_speed_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": "UTC",
            }
        )
        # Open-Meteo's free tier rate-limits bursts (it dropped ~15% of venue
        # calls on the CI runner) — pace the calls and retry once with backoff.
        hourly = None
        for attempt in range(2):
            try:
                resp = get_json(url)
                hourly = resp["hourly"]
                break
            except Exception as e:
                if attempt == 0:
                    time.sleep(5)
                else:
                    print(f"  weather unavailable for venue {vid}: {e}")
        if hourly is None:
            continue
        idx = {t: i for i, t in enumerate(hourly["time"])}
        time.sleep(0.7)
        for g, dt in items:
            key = dt.strftime("%Y-%m-%dT%H:00")
            i = idx.get(key)
            if i is None:
                continue
            temp = hourly["temperature_2m"][i]
            wind = hourly["wind_speed_10m"][i]
            precip = hourly["precipitation"][i]
            if temp is None:
                continue
            g["weather"] = {
                "tempF": round(temp),
                "windMph": round(wind) if wind is not None else None,
                "precipIn": round(precip, 2) if precip else 0,
            }


def main(year: int) -> None:
    api_key = load_api_key()

    teams_data = json.loads((ROOT / "data" / "teams.json").read_text())["teams"]
    teams = {t["team"] for t in teams_data}
    timezones = {t["team"]: ZoneInfo(t["timezone"]) for t in teams_data}
    overrides_path = ROOT / "data" / "venue-overrides.json"
    venue_overrides = (
        json.loads(overrides_path.read_text()).get(str(year), {})
        if overrides_path.exists()
        else {}
    )
    manual_path = ROOT / "data" / "manual-attendance.json"
    manual = {
        (m["team"], m["week"]): m
        for m in (
            json.loads(manual_path.read_text()).get(str(year), [])
            if manual_path.exists()
            else []
        )
    }

    out = ROOT / "data" / "seasons" / f"{year}.json"
    # Conference-scoped fetch only finds today's members in the years they
    # were already in the Big 12. For backfilled seasons the current sixteen
    # were scattered across the Pac-12, AAC, Big East and independence, so
    # pull the full slate and filter by team.
    raw = cfbd("/games", api_key, year=year, seasonType="regular")
    raw = [g for g in raw
           if (g.get("homeTeam") or g.get("home_team")) in teams
           or (g.get("awayTeam") or g.get("away_team")) in teams]
    venues = cfbd("/venues", api_key)
    venues_by_id = {v["id"]: v for v in venues}
    coords_by_id = {
        v["id"]: (v["latitude"], v["longitude"])
        for v in venues
        if v.get("latitude") is not None
    }

    games = []
    for g in raw:
        # CFBD field names: homeTeam/neutralSite in current API, home_team/
        # neutral_site in older versions — accept either.
        home = g.get("homeTeam") or g.get("home_team")
        away = g.get("awayTeam") or g.get("away_team")
        neutral = g.get("neutralSite", g.get("neutral_site", False))
        venue = g.get("venue")
        venue_info = venues_by_id.get(g.get("venueId"), {})
        completed = g.get("completed", g.get("homePoints") is not None)

        # CFBD flags designated home games at alternate venues (e.g. Kansas
        # 2024 in Kansas City during the Memorial Stadium rebuild) as neutral.
        # venue-overrides.json whitelists those venues per team/season, with
        # each venue's capacity; anything else neutral is a true neutral-site
        # game (counted for nobody, shown dimmed for the Big 12 side).
        alt = venue_overrides.get(home, {}).get("venues", {}) if home in teams else {}
        home_counts = home in teams and (not neutral or venue in alt)

        # Seasons before ~2016 carry no venue in the CFBD feed, so a team
        # that played home games in more than one building needs the venue
        # named per game. venue-overrides.json supports a "games" map keyed
        # by opponent for exactly that (Houston 2013, split between Reliant
        # and BBVA Compass, is the case this exists for).
        per_game = venue_overrides.get(home, {}).get("games", {}) if home in teams else {}
        pg = per_game.get(away)
        if pg:
            venue = pg.get("venue") or venue
            venue_info = dict(venue_info or {}, name=venue)
            home_counts = home in teams

        # Kickoff in the venue's local time (falls back to a participant's
        # home timezone when CFBD lacks the venue record).
        tz = ZoneInfo(venue_info["timezone"]) if venue_info.get("timezone") else timezones.get(
            home if home in teams else away
        )
        kickoff = kickoff_local(g.get("startDate") or g.get("start_date"), tz)
        week = display_week(kickoff.date(), year) if kickoff else g.get("week")
        # Membership is not enough to infer this: two Big 12 teams meeting out
        # of conference happens most years (Baylor at Utah 2024, Kansas State
        # at Arizona 2025), and before 2024 most of the tracked sixteen were
        # playing each other in the Pac-12 and the AAC. Charts that compare
        # conference visitors need the schedule's own answer.
        home_conf = g.get("homeConference") or g.get("home_conference")
        away_conf = g.get("awayConference") or g.get("away_conference")
        base = {
            "week": week,
            "date": kickoff.date().isoformat() if kickoff else "",
            "time": (
                kickoff.strftime("%H:%M")
                if kickoff and not g.get("startTimeTBD")
                else None
            ),
            "espnId": g.get("id"),
            "conferenceGame": bool(
                g.get("conferenceGame", g.get("conference_game"))
            ) and home_conf == "Big 12" and away_conf == "Big 12",
        }

        if home_counts:
            game = {
                "team": home,
                **base,
                "opponent": away,
                "attendance": g.get("attendance"),
                "_venueId": g.get("venueId"),
                "_kickoffUtc": kickoff.astimezone(ZoneInfo("UTC")) if kickoff else None,
                "_completed": completed,
            }
            if completed and g.get("homePoints") is not None:
                game["pointsFor"] = g.get("homePoints")
                game["pointsAgainst"] = g.get("awayPoints")
            if venue in alt:
                game["venue"] = venue
                game["capacity"] = alt[venue]
            if pg:
                game["venue"] = pg.get("venue")
                if pg.get("capacity"):
                    game["capacity"] = pg["capacity"]
            if game["attendance"] is None and (home, week) in manual:
                m = manual[(home, week)]
                game["attendance"] = m["attendance"]
                game["attendanceSource"] = m["source"]
            games.append(game)

        # Non-summing perspective entries: a Big 12 team on the road, or in a
        # true neutral-site game. Shown dimmed on the site; excluded from all
        # attendance math (stats.js skips any entry with a role).
        perspectives = []
        if away in teams:
            perspectives.append((away, home, "away" if home_counts or not neutral else "neutral",
                                 g.get("awayPoints"), g.get("homePoints")))
        if home in teams and not home_counts:
            perspectives.append((home, away, "neutral", g.get("homePoints"), g.get("awayPoints")))
        for team, opp, role, pf, pa in perspectives:
            entry = {
                "team": team,
                **base,
                "opponent": opp,
                "attendance": g.get("attendance"),
                "role": role,
                "venue": venue,
            }
            if venue_info.get("city"):
                entry["city"] = venue_info["city"]
                entry["state"] = venue_info.get("state")
            if completed and pf is not None:
                entry["pointsFor"] = pf
                entry["pointsAgainst"] = pa
            games.append(entry)

    # Fill CFBD gaps from ESPN directly (completed home games only) — catches
    # CFBD ingest lag on game night. Manual entries above still take priority.
    for g in games:
        if (
            "role" not in g
            and g["attendance"] is None
            and g.get("_completed")
            and g.get("espnId")
        ):
            att = espn_attendance(g["espnId"])
            if att:
                g["attendance"] = att
                g["attendanceSource"] = "ESPN summary API (not yet in CFBD)"
                print(f"  filled from ESPN: {g['team']} wk{g['week']} = {att}")
            time.sleep(0.5)

    fetch_weather(games, coords_by_id)
    for g in games:
        g.pop("_venueId", None)
        g.pop("_kickoffUtc", None)
        g.pop("_completed", None)
    games.sort(key=lambda x: (x["week"], x["team"]))

    prior_season = json.loads(out.read_text()) if out.exists() else {}

    # Never let a flaky upstream erase enrichment we already have: if the
    # previous season file had weather for a game and this fetch didn't get
    # it, carry the old value forward.
    if prior_season:
        prior = {
            (p["team"], p["week"], p.get("role")): p
            for p in prior_season["games"]
        }
        for g in games:
            old = prior.get((g["team"], g["week"], g.get("role")))
            if old and "weather" in old and "weather" not in g:
                g["weather"] = old["weather"]

    num_weeks = max((g["week"] for g in games), default=14) + 1
    source = "CollegeFootballData API (collegefootballdata.com); weather via Open-Meteo"
    season_file = {
        "season": year,
        "source": source,
        "weekLabels": [f"Week {w}" for w in range(num_weeks)],
        "games": games,
    }
    # Same reasoning one level up: other scripts add top-level keys to this
    # file after the fetch (add_conferences.py writes conferences/big12Era,
    # which the site needs to label pre-2024 teams and suppress league-wide
    # totals). Carry forward anything this fetch doesn't produce itself, or
    # the weekly run would silently drop it.
    for k, v in prior_season.items():
        season_file.setdefault(k, v)

    out.write_text(json.dumps(season_file, indent=2) + "\n")
    update_seasons_index(year)
    home_games = [g for g in games if "role" not in g]
    reported = sum(1 for g in home_games if g["attendance"] is not None)
    weathered = sum(1 for g in home_games if "weather" in g)
    print(
        f"{year}: {len(home_games)} home games ({reported} with attendance, "
        f"{weathered} with weather), {len(games) - len(home_games)} road/neutral -> {out}"
    )
    per_team = {t: sum(1 for g in home_games if g["team"] == t) for t in teams}
    missing = [t for t, n in per_team.items() if n == 0]
    if missing:
        print(f"WARNING: no home games found for: {', '.join(sorted(missing))}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026)

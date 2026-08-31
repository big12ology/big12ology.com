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

Venue coordinates, timezones and locations come from the committed catalog
at tiebreaker/data/venues.json rather than a live /venues call: stadiums do
not move, and at ~7 runs a week in season that one endpoint was 14% of the
monthly CFBD quota. The live endpoint is only hit when the file is missing.
To pick up a new stadium, refresh the catalog with
`python3 tiebreaker/fetch.py --venues --force` (this script warns when a
game references a venue the catalog lacks).

Requires CFBD_API_KEY (free key: https://collegefootballdata.com/key). If CFBD
cannot be reached — a spent monthly quota, an outage — the run falls back to
refreshing the committed season file from ESPN alone and exits clean.

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


# Maintained by tiebreaker/fetch.py --venues; that script is the only writer.
VENUE_CATALOG = ROOT.parent / "tiebreaker" / "data" / "venues.json"


def load_venue_catalog() -> dict | None:
    """The committed venue catalog, keyed by CFBD venue id (as int).

    The catalog stores {lat, lon, tz, city, state, name}; the CFBD /venues
    records this replaces used {latitude, longitude, timezone, ...}, and the
    consumers below read the CFBD names, so normalize here and both sources
    look identical downstream. Returns None when the file is missing, and
    main() falls back to one live /venues call."""
    if not VENUE_CATALOG.exists():
        return None
    raw = json.loads(VENUE_CATALOG.read_text())
    return {
        int(vid): {
            "timezone": v.get("tz"),
            "city": v.get("city"),
            "state": v.get("state"),
            "latitude": v.get("lat"),
            "longitude": v.get("lon"),
        }
        for vid, v in raw.items()
    }


def espn_game(espn_id) -> dict | None:
    """Second fetcher: ESPN's summary API, hit directly. Same upstream chain
    as CFBD but a different pipeline — on game night ESPN has attendance
    before CFBD's ingest picks it up, so 'first source that has it' wins,
    and when the CFBD key is spent this is the only source that still
    answers at all (see refresh_from_espn).

    Returns attendance, whether the game is final, and each side's points
    and team name keyed by home/away. ESPN reports 0 attendance when none
    was recorded — treat that as missing. A game that has not kicked off
    carries no score field at all, so points stay None rather than 0."""
    try:
        d = get_json(f"{ESPN_SUMMARY}?event={espn_id}", {"User-Agent": "Mozilla/5.0"})
        comp = d["header"]["competitions"][0]
    except Exception:
        return None
    att = d.get("gameInfo", {}).get("attendance")
    sides = {}
    for c in comp.get("competitors", []):
        score = c.get("score")
        sides[c.get("homeAway")] = {
            "points": int(score) if str(score or "").strip().isdigit() else None,
            "team": (c.get("team") or {}).get("location"),
        }
    return {
        "attendance": att if att else None,
        "completed": bool(comp.get("status", {}).get("type", {}).get("completed")),
        "home": sides.get("home", {}),
        "away": sides.get("away", {}),
    }


def same_team(a: str, b: str) -> bool:
    """Exact match on names stripped to alphanumerics, nothing looser.
    Substring matching would read Arizona as Arizona State, and the cost of
    a miss here is a score written against the wrong team."""
    norm = lambda s: "".join(ch for ch in (s or "").lower() if ch.isalnum())
    return bool(norm(a)) and norm(a) == norm(b)


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


def espn_side(entry: dict, summary: dict) -> str | None:
    """Which side of the ESPN box score this entry's team is on.

    A row with no role is a home game by construction, and a role of "away"
    says so outright. Neutral-site rows exist for both teams, so neither
    position tells us anything and the team name has to settle it — falling
    back to None, which leaves the score alone."""
    role = entry.get("role")
    if role is None:
        return "home"
    if role == "away":
        return "away"
    for side in ("home", "away"):
        if same_team(entry.get("team"), summary.get(side, {}).get("team")):
            return side
    return None


def refresh_from_espn(out: Path, year: int) -> None:
    """Update the committed season file from ESPN alone, without CFBD.

    The key allows 1,000 calls a month and it does run out. A spent quota
    used to take the whole fetch down at the first /games call — including
    the ESPN fallback below it, which could have answered. Nothing about a
    game day actually needs CFBD: the schedule is already committed and
    every row carries the ESPN game id, so the two things that change once
    a game is played, attendance and the score, can be read directly.

    What this cannot do is discover a game the file does not have, or fill
    weather. A schedule change during a quota wall waits for the quota."""
    season = json.loads(out.read_text())
    by_id = {}
    today = date.today().isoformat()
    for g in season["games"]:
        # Unplayed games have nothing to report, and the season file is most
        # of a year long — without this every quota-wall run would spend a
        # few hundred ESPN calls to learn that August is still ahead.
        if not g.get("espnId") or (g.get("date") or "") > today:
            continue
        if g.get("attendance") is None or g.get("pointsFor") is None:
            by_id.setdefault(g["espnId"], []).append(g)

    filled_att = filled_score = 0
    for espn_id, entries in by_id.items():
        summary = espn_game(espn_id)
        time.sleep(0.5)
        if not summary:
            continue
        for g in entries:
            if summary["attendance"] and g.get("attendance") is None:
                g["attendance"] = summary["attendance"]
                g["attendanceSource"] = "ESPN summary API (CFBD unavailable)"
                filled_att += 1
            if not summary["completed"]:
                continue
            side = espn_side(g, summary)
            if side is None:
                continue
            pf = summary[side].get("points")
            pa = summary["away" if side == "home" else "home"].get("points")
            if pf is None or pa is None or g.get("pointsFor") is not None:
                continue
            g["pointsFor"], g["pointsAgainst"] = pf, pa
            filled_score += 1
            print(f"  filled from ESPN: {g['team']} wk{g['week']} "
                  f"vs {g.get('opponent')} — {pf}-{pa}")

    if not filled_att and not filled_score:
        # Nothing moved, so don't touch the file. A rewrite here is not
        # harmless: this writer indents at 2 and add_conferences.py at 1, so
        # a no-op run would push the whole season reformatted and read as a
        # data change to anyone looking at the commit.
        print(f"{year}: ESPN-only refresh, {len(by_id)} games checked, "
              f"nothing new — {out.name} left alone")
        return

    out.write_text(json.dumps(season, indent=2) + "\n")
    print(f"{year}: ESPN-only refresh of {len(by_id)} games — "
          f"{filled_att} attendance, {filled_score} scores -> {out}")


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
    venues_by_id = load_venue_catalog()
    try:
        raw = cfbd("/games", api_key, year=year, seasonType="regular")
        raw = [g for g in raw
               if (g.get("homeTeam") or g.get("home_team")) in teams
               or (g.get("awayTeam") or g.get("away_team")) in teams]
        if not raw:
            # An HTTP 200 with nothing in it is an outage wearing a success
            # code. Left alone it flowed all the way to write_text and
            # replaced the committed season with an empty one. Raise into
            # the handler below instead: it already knows what to do, ESPN
            # when there is a committed season to refresh, a loud failure
            # when there is not.
            raise RuntimeError(f"CFBD returned no games for {year}")
        if venues_by_id is None:
            # Self-heal for a checkout without the catalog; the normal path
            # never spends this call. Inside the try on purpose, so a spent
            # quota here still routes to the ESPN fallback below.
            venues_by_id = {v["id"]: v for v in cfbd("/venues", api_key)}
    except Exception as e:
        # A spent quota or an API outage must not cost a game day. The same
        # call the site is built on says so directly — CFBD answers a run
        # over its monthly limit with 429 and {"message": "Monthly call
        # quota exceeded."} — so fall back to ESPN, say so loudly, and let
        # the run succeed. Stale beats absent; absent beats wrong.
        warn = f"CFBD unavailable ({e}) — refreshing {year} from ESPN alone"
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning::{warn}")
        print(f"WARNING: {warn}")
        if not out.exists():
            raise  # no committed schedule to refresh: nothing to fall back to
        refresh_from_espn(out, year)
        return

    coords_by_id = {
        vid: (v["latitude"], v["longitude"])
        for vid, v in venues_by_id.items()
        if v.get("latitude") is not None
    }

    # A venue the catalog has never heard of degrades quietly (kickoff time
    # falls back to the team's timezone, no weather, no city/state on road
    # rows), so name it out loud instead. New stadiums are the only way this
    # fires, and the fix is one forced refresh on the tiebreaker side.
    unknown = sorted({
        f'{g.get("venue") or "?"} (id {g.get("venueId")})'
        for g in raw
        if g.get("venueId") is not None and g.get("venueId") not in venues_by_id
    })
    if unknown:
        print("WARNING: venues missing from tiebreaker/data/venues.json: "
              + ", ".join(unknown)
              + ". Refresh with: python3 tiebreaker/fetch.py --venues --force")

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
            summary = espn_game(g["espnId"])
            att = summary["attendance"] if summary else None
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

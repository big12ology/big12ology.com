#!/usr/bin/env python3
"""Fetch Big 12 home-game attendance from the CollegeFootballData API.

Pulls all regular-season games for the season, keeps non-neutral home games for
Big 12 teams, and merges them into data/seasons/<year>.json. Games without
attendance yet (unplayed or unreported) are kept with attendance: null so the
site can show the schedule; totals only count reported games.

Requires CFBD_API_KEY (free key: https://collegefootballdata.com/key).

Usage: CFBD_API_KEY=... python3 scripts/fetch_attendance.py 2026
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.collegefootballdata.com/games"


def local_date(start: str, tz: ZoneInfo) -> str:
    """CFBD reports kickoff in UTC; convert to the venue's local calendar date
    (an evening game otherwise lands on the next day)."""
    if not start:
        return ""
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return start[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(tz).date().isoformat()


def fetch_games(year: int, api_key: str) -> list:
    url = f"{API}?{urllib.parse.urlencode({'year': year, 'seasonType': 'regular', 'conference': 'B12'})}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


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


def update_seasons_index(year: int) -> None:
    path = ROOT / "data" / "seasons" / "index.json"
    index = json.loads(path.read_text())
    if year not in index["seasons"]:
        index["seasons"] = sorted(index["seasons"] + [year])
        path.write_text(json.dumps(index, indent=2) + "\n")


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
    raw = fetch_games(year, api_key)

    games = []
    for g in raw:
        # CFBD field names: homeTeam/neutralSite in current API, home_team/
        # neutral_site in older versions — accept either.
        home = g.get("homeTeam") or g.get("home_team")
        neutral = g.get("neutralSite", g.get("neutral_site", False))
        venue = g.get("venue")
        if home not in teams:
            continue
        # CFBD flags designated home games at alternate venues (e.g. Kansas
        # 2024 in Kansas City during the Memorial Stadium rebuild) as neutral.
        # venue-overrides.json whitelists those venues per team/season, with
        # each venue's capacity; anything else neutral is a true neutral-site
        # game and stays excluded.
        alt = venue_overrides.get(home, {}).get("venues", {})
        if neutral and venue not in alt:
            continue
        game = {
            "team": home,
            "week": g.get("week"),
            "opponent": g.get("awayTeam") or g.get("away_team"),
            "date": local_date(g.get("startDate") or g.get("start_date"), timezones[home]),
            "attendance": g.get("attendance"),
        }
        if venue in alt:
            game["venue"] = venue
            game["capacity"] = alt[venue]
        if game["attendance"] is None and (home, game["week"]) in manual:
            m = manual[(home, game["week"])]
            game["attendance"] = m["attendance"]
            game["attendanceSource"] = m["source"]
        games.append(game)
    games.sort(key=lambda x: (x["week"], x["team"]))

    out = ROOT / "data" / "seasons" / f"{year}.json"
    num_weeks = max((g["week"] for g in games), default=14) + 1
    out.write_text(
        json.dumps(
            {
                "season": year,
                "source": "CollegeFootballData API (collegefootballdata.com)",
                "weekLabels": [f"Week {w}" for w in range(num_weeks)],
                "games": games,
            },
            indent=2,
        )
        + "\n"
    )
    update_seasons_index(year)
    reported = sum(1 for g in games if g["attendance"] is not None)
    print(f"{year}: {len(games)} home games, {reported} with attendance -> {out}")
    per_team = {t: sum(1 for g in games if g["team"] == t) for t in teams}
    missing = [t for t, n in per_team.items() if n == 0]
    if missing:
        print(f"WARNING: no home games found for: {', '.join(sorted(missing))}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026)

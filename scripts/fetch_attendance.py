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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.collegefootballdata.com/games"


def fetch_games(year: int, api_key: str) -> list:
    url = f"{API}?{urllib.parse.urlencode({'year': year, 'seasonType': 'regular', 'conference': 'B12'})}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main(year: int) -> None:
    api_key = os.environ.get("CFBD_API_KEY")
    if not api_key:
        sys.exit("CFBD_API_KEY is not set")

    teams = {
        t["team"] for t in json.loads((ROOT / "data" / "teams.json").read_text())["teams"]
    }
    raw = fetch_games(year, api_key)

    games = []
    for g in raw:
        # CFBD field names: homeTeam/neutralSite in current API, home_team/
        # neutral_site in older versions — accept either.
        home = g.get("homeTeam") or g.get("home_team")
        neutral = g.get("neutralSite", g.get("neutral_site", False))
        if home not in teams or neutral:
            continue
        games.append(
            {
                "team": home,
                "week": g.get("week"),
                "opponent": g.get("awayTeam") or g.get("away_team"),
                "date": (g.get("startDate") or g.get("start_date") or "")[:10],
                "attendance": g.get("attendance"),
            }
        )
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
    reported = sum(1 for g in games if g["attendance"] is not None)
    print(f"{year}: {len(games)} home games, {reported} with attendance -> {out}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026)

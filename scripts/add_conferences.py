#!/usr/bin/env python3
"""Record which conference each tracked program actually played in, per season.

The sixteen current Big 12 members were scattered across five leagues before
2024, so any conference-wide total or ranking is meaningless for those years.
The site uses this map to label teams with their conference of the day and to
suppress league-wide aggregates outside the Big 12 era.

    python3 scripts/add_conferences.py            # all seasons in the index
    python3 scripts/add_conferences.py 2012 2013  # specific seasons
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.collegefootballdata.com"
BIG12_ERA = 2024  # first season this sixteen-team configuration existed


def key():
    k = os.environ.get("CFBD_API_KEY")
    if k:
        return k
    for p in (os.path.join(ROOT, ".env"),
              os.path.join(os.path.dirname(ROOT), "big12-tiebreaker", ".env")):
        if os.path.exists(p):
            for line in open(p):
                if line.startswith("CFBD_API_KEY="):
                    return line.split("=", 1)[1].strip()
    sys.exit("CFBD_API_KEY not set")


def fetch(path, k):
    req = urllib.request.Request(f"{API}/{path}",
                                 headers={"Authorization": f"Bearer {k}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main(years):
    k = key()
    teams = {t["team"] for t in
             json.load(open(os.path.join(ROOT, "data", "teams.json")))["teams"]}
    for year in years:
        p = os.path.join(ROOT, "data", "seasons", f"{year}.json")
        if not os.path.exists(p):
            print(f"{year}: no season file")
            continue
        raw = fetch(f"games?year={year}&seasonType=regular", k)
        conf = {}
        for g in raw:
            for side, cf in (("homeTeam", "homeConference"),
                             ("awayTeam", "awayConference")):
                t, c = g.get(side), g.get(cf)
                if t in teams and c:
                    conf[t] = c
        season = json.load(open(p))
        season["conferences"] = dict(sorted(conf.items()))
        season["big12Era"] = year >= BIG12_ERA
        json.dump(season, open(p, "w"), indent=1)
        leagues = sorted(set(conf.values()))
        print(f"{year}: {len(conf)} teams across {len(leagues)} leagues "
              f"({', '.join(leagues)})")


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:]]
    if not args:
        idx = json.load(open(os.path.join(ROOT, "data", "seasons",
                                          "index.json")))
        args = idx["seasons"]
    main(args)

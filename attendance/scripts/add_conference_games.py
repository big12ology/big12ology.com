#!/usr/bin/env python3
"""Mark which games in the season files were Big 12 conference games.

The attendance archive reaches back to 2012, when the current sixteen were
scattered across five leagues, so "every road trip to a tracked stadium" and
"every Big 12 road trip" are wildly different samples: Utah has made 30 trips
to what are now Big 12 venues and only 9 of them were Big 12 games. Charts
that mean to compare conference visitors need the flag to say so.

Membership alone does not settle it — two Big 12 teams meeting out of
conference is a real and recurring case (Baylor at Utah 2024, Arizona at
Kansas State 2024, Kansas State at Arizona 2025). So the flag comes from the
schedule itself, by way of the tiebreaker's cached CFBD seasons in the
adjacent tiebreaker/data, which already derive `conference_game` as
CFBD's conferenceGame with both sides in the Big 12. Joined on ESPN game id.

New seasons get the flag from fetch_attendance.py directly; this script is
the backfill for everything already on disk.

    python3 scripts/add_conference_games.py            # all seasons on disk
    python3 scripts/add_conference_games.py 2012 2013  # specific seasons
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIEBREAKER = os.path.join(os.path.dirname(ROOT), "tiebreaker", "data")


def indent_of(text):
    """The file's own indent width, so a backfill is a pure insert.

    Season files come from two writers at different widths (the fetch script
    and add_conferences.py). Reformatting one to match the other buries a
    one-line change in a two-thousand-line diff.
    """
    for line in text.split("\n")[1:]:
        stripped = line.lstrip(" ")
        if stripped:
            return len(line) - len(stripped)
    return 1


def flags(year):
    """ESPN game id -> was this a Big 12 conference game."""
    p = os.path.join(TIEBREAKER, f"games_{year}.json")
    if not os.path.exists(p):
        return None
    return {g["id"]: bool(g["conference_game"]) for g in json.load(open(p))}


def main(years):
    missing = []
    for year in years:
        p = os.path.join(ROOT, "data", "seasons", f"{year}.json")
        if not os.path.exists(p):
            print(f"{year}: no season file")
            continue
        by_id = flags(year)
        if by_id is None:
            print(f"{year}: no tiebreaker games file — skipped")
            continue
        text = open(p).read()
        indent = indent_of(text)
        season = json.loads(text)
        games, marked, gaps = [], 0, 0
        for g in season["games"]:
            flag = by_id.get(g.get("espnId"))
            if flag is None:
                # A game the tiebreaker cache never saw. Leave the flag off
                # rather than guessing; the count below makes it visible.
                gaps += 1
                games.append(g)
                continue
            # Rebuilt rather than assigned so the flag sits next to espnId
            # instead of trailing the weather blob.
            out = {}
            for k, v in g.items():
                out[k] = v
                if k == "espnId":
                    out["conferenceGame"] = flag
            games.append(out)
            marked += flag
        season["games"] = games
        out = json.dumps(season, indent=indent)
        with open(p, "w") as f:
            f.write(out + "\n" if text.endswith("\n") else out)
        if gaps:
            missing.append((year, gaps))
        print(f"{year}: {marked} of {len(games)} entries are Big 12 "
              f"conference games" + (f", {gaps} unmatched" if gaps else ""))
    if missing:
        # Unmatched games are silent holes in every conference-only chart, so
        # they fail the run rather than print and scroll away.
        sys.exit("unmatched games (refresh tiebreaker/data): "
                 + ", ".join(f"{y} ({n})" for y, n in missing))


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:]]
    if not args:
        d = os.path.join(ROOT, "data", "seasons")
        args = sorted(int(f[:-5]) for f in os.listdir(d)
                      if f[:-5].isdigit() and f.endswith(".json"))
    main(args)

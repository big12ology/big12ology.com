#!/usr/bin/env python3
"""Cross-check every completed home game's attendance against ESPN, fetched
directly — a different pipeline from CFBD's ingest of the same upstream.
Writes data/verification/<year>.json and prints anything that needs eyes.

The ESPN fetch is imported from fetch_attendance rather than written again
here. This script used to keep its own copy of the endpoint, so when the
summary host started answering 403 the fetch degraded quietly and the
verifier died on an uncaught HTTPError — and the workflow reported that
transport failure as a CFBD/ESPN data mismatch.

Statuses per game:
  agree        — both sources report the same number
  manual       — ours came from a hand-verified official box score
                 (ESPN/CFBD never got a number; nothing to check)
  espn_missing — we have a number, ESPN reports none (0). Informational.
  espn_error   — ESPN could not be reached at all. Says nothing about the
                 number we hold; never counted as a disagreement.
  missing_both — game completed but no attendance anywhere yet: candidate
                 for a manual-attendance.json entry from the school's box score
  espn_ahead   — ESPN has a crowd and we do not, yet. Not a disagreement:
                 there is only one number in it. See below.
  MISMATCH     — both report, numbers differ: investigate, official school
                 box score is the arbiter

WHY espn_ahead IS NOT A MISMATCH. The fetch and this check ask ESPN the same
question seconds apart, so ESPN attaching a crowd figure between the two reads
as a disagreement if the only categories are agree and MISMATCH. That is what
happened on 2026-09-06: Kansas State 71-3 over Nicholls, the fetcher found no
crowd at 06:36:07, this script got 51,719 at 06:36:16, the run went red, and
the next run filled the number in from ESPN with nothing else changed. Nobody
had disagreed with anybody. The arbiter advice above sends someone after a
school box score to settle a question that does not exist.

So a first sighting passes and says so. A game still espn_ahead on the NEXT run
does not: two runs is no longer a race, it means the fetch cannot pick up a
number this script can see, and the page renders a played game as if it were
still scheduled. The previous run's verification file is the memory — the
workflow commits it, so it is on disk at the next checkout.

Exit codes: 1 for mismatches and for a game stuck ahead of us, 2 when ESPN was
unreachable and nothing could be checked. They are different problems and want
different responses.

Usage: python3 scripts/verify_attendance.py 2026
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fetch_attendance import espn_game

ROOT = Path(__file__).resolve().parent.parent


def espn_attendance(espn_id):
    """(attendance, reached). attendance is None both when ESPN reports no
    crowd and when ESPN did not answer; `reached` is what separates them."""
    g = espn_game(espn_id)
    if g is None:
        return None, False
    return g["attendance"], True


def main(year: int) -> None:
    season = json.loads((ROOT / "data" / "seasons" / f"{year}.json").read_text())
    completed = [
        g
        for g in season["games"]
        if "role" not in g and g.get("pointsFor") is not None and g.get("espnId")
    ]
    # Last run's verdicts, before this run overwrites them. Keyed by espnId
    # because team and week are display strings and the id is the thing that
    # cannot drift. A missing or unreadable file is simply no memory: every
    # game then reads as a first sighting, which is the forgiving direction.
    out_dir = ROOT / "data" / "verification"
    prev = {}
    try:
        prev = {
            str(e.get("espnId")): e.get("status")
            for e in json.loads((out_dir / f"{year}.json").read_text()).get("games", [])
        }
    except (OSError, ValueError):
        pass

    results = []
    counts = {}
    for g in completed:
        espn, reached = espn_attendance(g["espnId"])
        ours = g["attendance"]
        src = g.get("attendanceSource", "CFBD")
        if not reached:
            # Nothing was compared, so nothing can be said about agreement.
            status = "espn_error"
        elif ours is not None and espn and ours == espn:
            status = "agree"
        elif ours is not None and src not in ("CFBD",) and not espn:
            status = "manual"
        elif ours is not None and not espn:
            status = "espn_missing"
        elif ours is None and not espn:
            status = "missing_both"
        elif ours is None:
            # One number, not two. The fetch has not caught up with ESPN yet,
            # which is a race on game night and a broken fill if it lasts.
            status = "espn_ahead"
        else:
            status = "MISMATCH"
        counts[status] = counts.get(status, 0) + 1
        entry = {
            "team": g["team"],
            "week": g["week"],
            "opponent": g.get("opponent"),
            "date": g.get("date"),
            "espnId": g["espnId"],
            "ours": ours,
            "espn": espn or None,
            "source": src,
            "status": status,
        }
        results.append(entry)
        if status in ("MISMATCH", "missing_both", "espn_error", "espn_ahead"):
            print(f"  {status}: {g['team']} wk{g['week']} vs {g.get('opponent')} — ours={ours}, espn={espn}")
        time.sleep(0.5)

    # Ahead of us twice running is not a race any more.
    stuck = [e for e in results
             if e["status"] == "espn_ahead"
             and prev.get(str(e["espnId"])) == "espn_ahead"]

    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{year}.json").write_text(
        json.dumps({"season": year, "summary": counts, "games": results}, indent=2) + "\n"
    )
    print(f"{year}: {len(completed)} completed games checked -> {counts}")
    if counts.get("MISMATCH"):
        sys.exit(f"{counts['MISMATCH']} attendance mismatch(es) — see "
                 f"data/verification/{year}.json")
    if stuck:
        which = ", ".join(f"{e['team']} wk{e['week']} (ESPN has {e['espn']})"
                          for e in stuck)
        sys.exit(f"{len(stuck)} game(s) ESPN has a crowd for and two runs of "
                 f"the fetch have not picked up: {which}. Not a disagreement "
                 f"between sources — the fill is not working. Start with the "
                 f"fetch's own ESPN step, not with a box score.")
    if counts.get("espn_ahead"):
        print(f"{counts['espn_ahead']} game(s) ESPN has and we do not yet; the "
              f"next fetch should pick them up. Red only if they are still "
              f"here next run.")
    if counts.get("espn_error") and counts["espn_error"] == len(completed):
        print(f"ESPN unreachable for all {len(completed)} games — cross-check "
              f"did not run. The numbers on file are untouched.")
        sys.exit(2)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026)

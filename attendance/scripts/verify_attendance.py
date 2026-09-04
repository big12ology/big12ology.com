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
  MISMATCH     — both report, numbers differ: investigate, official school
                 box score is the arbiter

Exit codes: 1 for mismatches, 2 when ESPN was unreachable and nothing could
be checked. They are different problems and want different responses.

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
        if status in ("MISMATCH", "missing_both", "espn_error"):
            print(f"  {status}: {g['team']} wk{g['week']} vs {g.get('opponent')} — ours={ours}, espn={espn}")
        time.sleep(0.5)

    out_dir = ROOT / "data" / "verification"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{year}.json").write_text(
        json.dumps({"season": year, "summary": counts, "games": results}, indent=2) + "\n"
    )
    print(f"{year}: {len(completed)} completed games checked -> {counts}")
    if counts.get("MISMATCH"):
        sys.exit(f"{counts['MISMATCH']} attendance mismatch(es) — see "
                 f"data/verification/{year}.json")
    if counts.get("espn_error") and counts["espn_error"] == len(completed):
        print(f"ESPN unreachable for all {len(completed)} games — cross-check "
              f"did not run. The numbers on file are untouched.")
        sys.exit(2)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026)

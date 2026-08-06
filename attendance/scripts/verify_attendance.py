#!/usr/bin/env python3
"""Cross-check every completed home game's attendance against ESPN's summary
API (fetched directly — a different pipeline from CFBD's ingest of the same
upstream). Writes data/verification/<year>.json and prints anything that
needs eyes.

Statuses per game:
  agree        — both sources report the same number
  manual       — ours came from a hand-verified official box score
                 (ESPN/CFBD never got a number; nothing to check)
  espn_missing — we have a number, ESPN reports none (0). Informational.
  missing_both — game completed but no attendance anywhere yet: candidate
                 for a manual-attendance.json entry from the school's box score
  MISMATCH     — both report, numbers differ: investigate, official school
                 box score is the arbiter

Usage: python3 scripts/verify_attendance.py 2026
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary"


def espn_attendance(espn_id):
    req = urllib.request.Request(
        f"{ESPN_SUMMARY}?event={espn_id}", headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        d = json.load(resp)
    return d.get("gameInfo", {}).get("attendance")


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
        espn = espn_attendance(g["espnId"])
        ours = g["attendance"]
        src = g.get("attendanceSource", "CFBD")
        if ours is not None and espn and ours == espn:
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
        if status in ("MISMATCH", "missing_both"):
            print(f"  {status}: {g['team']} wk{g['week']} vs {g.get('opponent')} — ours={ours}, espn={espn}")
        time.sleep(0.5)

    out_dir = ROOT / "data" / "verification"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{year}.json").write_text(
        json.dumps({"season": year, "summary": counts, "games": results}, indent=2) + "\n"
    )
    print(f"{year}: {len(completed)} completed games checked -> {counts}")
    if counts.get("MISMATCH"):
        sys.exit(f"{counts['MISMATCH']} attendance mismatch(es) — see data/verification/{year}.json")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026)

# Big 12 Attendance Tracker

Weekly home-game attendance for all 16 Big 12 teams — a static website that
replaces a manually maintained Google Sheet. No build step: GitHub Pages serves
the repo as-is, and a scheduled GitHub Action updates the data files weekly.

## Layout

- `index.html`, `site/` — the site. `site/stats.js` is the single source of
  truth for all attendance math (used by the browser and the snapshot builder).
- `data/teams.json` — teams, stadiums, capacities, **keyed by year** (resolved
  to the most recent entry at or before the season viewed). Sourcing policy:
  current season capacity always comes from the university/athletic
  department; past seasons may fall back to Wikipedia or newspapers of record.
  Sources are cited in the file's notes.
- `data/venue-overrides.json` — designated home games at alternate venues
  (CFBD flags these neutral-site), with per-venue capacities. E.g. Kansas 2024
  in Kansas City during the Memorial Stadium rebuild.
- `data/manual-attendance.json` — hand-verified figures for games the API
  lacks, each citing an official box score. Merged in by the fetch script.
- `data/seasons/<year>.json` — one game per Big 12 home game, with opponent,
  venue-local date/time, final score, ESPN game id (box-score links), and
  kickoff-hour weather (Open-Meteo historical archive, free/keyless).
  `attendance: null` means unplayed/unreported; such games are excluded from
  every total and game count. Games at alternate venues carry their own
  `venue` and `capacity`; percent-full always divides by the per-game capacity.
  **Week numbers are derived from the game date** (Week 1 = the Sun–Sat window
  containing the Saturday before Labor Day; Week 0 precedes it) — CFBD's week
  field has no Week 0, and hand-entered weeks proved unreliable at Fri/Sat
  boundaries.
- `data/snapshots/<year>/week-NN.json` — cumulative standings through each week.
- `tests/parity.test.mjs` — self-contained math-engine check: the original
  2025 sheet's inputs (embedded in the fixture) must reproduce the sheet's own
  outputs. Production 2025 data now comes from the CFBD API (values verified
  identical to the sheet, game for game) with date-derived weeks and corrected
  capacities, so it intentionally differs from the fixture.

## Differences from the spreadsheet (deliberate)

The sheet counted games and summed attendance with `COUNTIF/SUMIF(range, ">200")`
across interleaved attendance/percent columns — correct in practice, but a
game with attendance ≤ 200 would silently vanish. Here attendance and percent
are separate fields and a reported game always counts, whatever its value.

## 2026 automation

1. Get a free API key at <https://collegefootballdata.com/key> and add it as a
   repo secret named `CFBD_API_KEY`.
2. `.github/workflows/update-attendance.yml` runs Sundays 14:00 UTC during the
   season (and on demand): fetches Big 12 home games, rebuilds snapshots, runs
   the parity tests, and commits only if data changed.
3. Manual run: `CFBD_API_KEY=... python3 scripts/fetch_attendance.py 2026`
   then `node scripts/build_snapshots.mjs 2026`.

Note: CFBD numbers "Week 0" games as week 1; the 2025 data keeps the sheet's
original Week 0–14 labels. Week labels come from each season file, so the two
conventions coexist.

## Development

```bash
python3 -m http.server 8080   # from the repo root, then open localhost:8080
node --test tests/parity.test.mjs
```

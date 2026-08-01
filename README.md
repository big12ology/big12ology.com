# Big 12 Attendance Tracker

Weekly home-game attendance for all 16 Big 12 teams — a static website that
replaces a manually maintained Google Sheet. No build step: GitHub Pages serves
the repo as-is, and a scheduled GitHub Action updates the data files weekly.

## Layout

- `index.html`, `site/` — the site. `site/stats.js` is the single source of
  truth for all attendance math (used by the browser and the snapshot builder).
- `data/teams.json` — teams, stadiums, capacities. **Capacity is assumed
  constant within a season**; values imported from the 2025 sheet — verify
  before each season.
- `data/seasons/<year>.json` — one game per Big 12 home game (non-neutral).
  `attendance: null` means unplayed/unreported; such games are excluded from
  every total and game count.
- `data/snapshots/<year>/week-NN.json` — cumulative standings through each week.
- `tests/parity.test.mjs` — asserts the stats module reproduces every number
  from the audited 2025 spreadsheet (fixture = the sheet's own computed values).

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

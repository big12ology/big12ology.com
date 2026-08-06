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
  `conferenceGame` is true only for **Big 12** conference games — not merely
  two Big 12 teams meeting (Baylor at Utah 2024 was non-conference), and not
  the Pac-12 and AAC games the tracked sixteen played against each other
  before 2024. Charts that compare conference opponents filter on it.
  **Week numbers are derived from the game date** (weeks run Tuesday–Monday;
  Week 1 ends Labor Day Monday, Week 0 precedes it) — CFBD's week field has
  no Week 0, and hand-entered weeks proved unreliable at Fri/Sat boundaries.
- `data/snapshots/<year>/week-NN.json` — cumulative standings through each week.
- `tests/parity.test.mjs` — self-contained math-engine check: the original
  2025 sheet's inputs (embedded in the fixture) must reproduce the sheet's own
  outputs. Production 2025 data now comes from the CFBD API (values verified
  identical to the sheet, game for game) with date-derived weeks and corrected
  capacities, so it intentionally differs from the fixture.
- `tests/test_fetch_fallback.py` — the fetch with CFBD taken away: payload
  parsing, which side of an ESPN box score a row belongs to, and that a 429
  routes to ESPN instead of ending the run. Fully stubbed — it calls neither
  API and spends nothing from the quota. Run with
  `python3 -m unittest discover -s tests`.

## Differences from the spreadsheet (deliberate)

The sheet counted games and summed attendance with `COUNTIF/SUMIF(range, ">200")`
across interleaved attendance/percent columns — correct in practice, but a
game with attendance ≤ 200 would silently vanish. Here attendance and percent
are separate fields and a reported game always counts, whatever its value.

## 2026 automation

1. Get a free API key at <https://collegefootballdata.com/key> and add it as a
   repo secret named `CFBD_API_KEY`.
2. `.github/workflows/update-attendance.yml` runs five times each game
   weekend (Sat 3pm / 7pm / 11:30pm and Sun 7am / 7pm, Arizona time, plus on
   demand): fetches Big 12 games, rebuilds snapshots, runs the parity tests,
   and commits only if data changed.

### Attendance sources & verification

- **Primary: CFBD.** **Fallback: ESPN's summary API hit directly** — same
  upstream, different pipeline; on game night ESPN often has attendance
  before CFBD's ingest, so the first source that has a number wins
  (`attendanceSource` marks ESPN-filled games; the next CFBD ingest
  normally converges to the same value).
- **When CFBD is gone entirely** — a spent monthly quota (the key allows
  1,000 calls; see HANDOFF.md) or an outage — the fetch does not fail the
  run. It refreshes the committed season file from ESPN alone, keyed on the
  ESPN game id every row already carries, and fills the two things a game
  day changes: attendance and the final score. It prints a `::warning::` and
  exits clean. What it cannot do without CFBD is add a game the file does
  not already have, or fetch weather (venue coordinates come from CFBD), so
  a schedule change during a quota wall waits for the quota.
- **Cross-check:** `scripts/verify_attendance.py` compares every completed
  home game against ESPN and writes `data/verification/<year>.json`; the
  workflow turns red on any true mismatch. As of the 2024–2025 backfill:
  206 games checked, 203 agree, 3 manual, 0 mismatches.
- **The tail:** every historical gap traced to ESPN itself (attendance 0 at
  the source), so no ESPN-chain feed can fill it. Truly independent sources
  were evaluated and are not automatable: stats.ncaa.org (Akamai wall),
  sports-reference.com (Cloudflare wall), school/conference Sidearm sites
  (Incapsula; archived box-score APIs return empty), ncaa.com's modern feed
  (carries no attendance at all). `missing_both` entries in the
  verification report are the queue for `manual-attendance.json` fixes
  citing the school's official box score — the arbiter for any dispute.
3. Manual run: `CFBD_API_KEY=... python3 scripts/fetch_attendance.py 2026`
   then `node scripts/build_snapshots.mjs 2026`.

## Development

```bash
python3 -m http.server 8080   # from the repo root, then open localhost:8080
node --test tests/parity.test.mjs tests/render.test.mjs
python3 -m unittest discover -s tests
```

Two scripts backfill per-season context the weekly fetch does not carry on its
own, and both are idempotent — rerun them after adding a season file:

```bash
python3 scripts/add_conferences.py        # each team's league that season (CFBD, needs a key)
python3 scripts/add_conference_games.py   # per-game Big 12 conference flag (from tiebreaker/data)
```

## Visual rules

These hold across both trackers; break them only deliberately.

1. **Good-to-bad is always a gradient.** Any display that runs from good to
   bad in green/red uses a continuous shade, never two flat colors — the
   shade carries the magnitude. Attendance fill uses `pctHSL`, diverging
   comparisons use `divergeHSL`, and the tiebreaker's win-percentage column
   uses `winpct_color`/`winPctColor`. All three walk the same hue path:
   saturated red → amber → green.
2. **Hue carries meaning, lightness does not.** Never encode a value in
   lightness alone; it inverts between light and dark themes.
3. **Resolution goes where the data lives.** The scales are anchor curves,
   not linear ramps, so the crowded top of a range stays legible.
4. **One game card.** Every hover that describes a game renders
   `gametip.js` — opponent, result, attendance, fill, weather, box score.
5. **Dates in prose carry no year.** The season is already on screen.

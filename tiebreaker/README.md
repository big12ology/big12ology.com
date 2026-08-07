# Big 12 Tiebreaker Tracker

Unofficial fan site that applies the [official Big 12 football tiebreaking
procedures](https://s3.amazonaws.com/big12sports.com/documents/2025/11/4/Big_12_Football_2024_Tiebreaker_Policy.pdf)
to live results and re-projects the championship-game matchup after every
game.

## How it works

- `fetch.py <year>` — pulls every game involving a Big 12 team from
  [collegefootballdata.com](https://collegefootballdata.com) (one API call)
  into `data/games_<year>.json`. Needs `CFBD_API_KEY` in `.env` or the
  environment.
- `tiebreaker.py` — the rules engine. Implements the two-team ladder
  (head-to-head → common opponents → next-highest-placed common opponent →
  opponents' conference win% → total wins with the one-FCS-win cap →
  SportSource Analytics rating → coin toss) and the multiple-team procedure
  (win% among tied teams with the beat-everyone removal rule, then the same
  ladder; after each team is seeded the survivors restart). Every decision is
  logged in plain English.
- `build.py [year] [--fetch]` — renders `site/index.html`: projected/final
  matchup, tiebroken standings (sortable by official win% or raw wins),
  per-tie narratives, results, the rules, and the what-if simulator.
- `site/engine.js` — JavaScript port of the rules engine, powering the
  what-if tool client-side. Kept behaviorally identical to `tiebreaker.py`;
  change both together.
- `site/app.js` — page behavior: sort toggle, pick buttons, model selector.
- `tests/test_seasons.py` — validates the engine against 2024 (the
  ASU/BYU/Colorado/Iowa State four-way 7-2 tie → ASU #1, ISU #2) and 2025
  (Texas Tech/BYU).
- `tests/test_parity.py` — proves the two engines match: real seasons plus
  random and partially-played simulated seasons through both, diffing
  standings, tie groups, logs, and the projected matchup (needs `node`).
- `clinch.py` — proof-grade clinch/elimination analysis for the
  Championship race card. Win-count bounds (strict, tiebreaker-proof) all
  season; once the remaining conference schedule fits the compute budget
  (~18 games, mid-November), exhaustive enumeration through the real engine
  yields exact statuses and this-week clinch scenarios. Computed at build
  time only — the what-if simulator doesn't touch it.
- `tests/test_ccg_history.py` — the strongest ground truth available: the
  engine's top two must equal the championship-game pairing the conference
  actually made, for all nine seasons of the CCG era (2017–2025). Published
  standings can't be used — they list tied teams as an unordered block.
- `tests/test_clinch.py` — replays 2025 truncated at seven dates and checks
  the invariants: statuses only move forward, the final clinched pair is
  exactly the CCG pair, and exact-mode calls match full-season reality.
- `odds.py` — Monte Carlo championship-game odds: 10,000 season simulations
  per build, win probabilities from an ensemble of the fetched rating
  systems (normal model on the projected margin), scored with the clinch
  module's top-2 evaluator. Deterministic seed; proofs always override
  odds in the card. `tests/test_odds.py` holds the invariants (sum ≈ two
  berths, agreement with clinch proofs, determinism, finished-season
  exactness).
- `chaos.py` — the Chaos Index, 0 (decided) to 100 (sixteen-way pileup):
  60% odds entropy, 25% tie tangle among living teams, 15% alive breadth.
  Shown atop the race card; formula documented on the explainer page;
  invariants in `tests/test_chaos.py`.
- `feed.py` — the RSS feed (`site/feed.xml`): game finals with as-of
  records, clinch/elimination calls, and weekly Chaos Index wraps. Fully
  derived from season data with stable guids and game-clock pubDates, so
  the stateless builds never duplicate items. `tests/test_feed.py`.

Additional modules: `odds.leverage` (per-game title-race swing, the
"Games that matter" card), `scorecard.py` (each model's favorites record),
`gen_history.py` (the static tie-archaeology page — run locally and commit
`site/history.html`), plus build-time outputs `brief.html` (the
auto-written weekly Brief), `data.json`, and `standings.csv`.

## What-if simulator

Every unplayed conference game gets a pair of pick buttons; picks re-run the
official procedure instantly in the browser and update the matchup card,
standings, and tie narratives. A model selector marks favorites (★, with
projected margins): SP+, FPI, Elo, and SRS from collegefootballdata.com —
each falls back to the prior season until the new year's ratings exist.

Steps (f) SportSource and (g) coin toss use non-public inputs; when a real
tie reaches them, put the values in `overrides.json`:

```json
{"sportsource": {"BYU": 12, "Utah": 15}, "coin_toss": ["Utah"]}
```

## Auto-updating

`.github/workflows/pages.yml`, at the repo root, refetches and redeploys the
whole domain hourly during weekend game windows (Aug–Dec), every two hours in
the late-night Thu/Fri/Sun windows, and daily otherwise; Tuesday's run also
refreshes ratings and closing lines. The schedule is the tiebreaker's — it is
the only part of the site with a reason to rebuild on a clock. Setup:

1. Repo → Settings → Secrets and variables → Actions → add `CFBD_API_KEY`.
2. Repo → Settings → Pages → Source: **GitHub Actions**.

Team and conference marks are from Wikimedia Commons (provenance in
`site/logos/SOURCES.json`); they belong to their institutions and are used
for identification only. Not affiliated with the Big 12 Conference.

## Visual rules

Shared with the attendance tracker; break them only deliberately.

1. **Good-to-bad is always a gradient.** Green/red displays shade
   continuously so the color carries magnitude — `winpct_color` (Python) and
   `winPctColor` (JS) here, `pctHSL`/`divergeHSL` in the attendance repo. All
   walk the same hue path: saturated red → amber → green.
2. **Hue carries meaning, lightness does not** — lightness inverts between
   light and dark themes.
3. **Resolution goes where the data lives** — anchor curves, not linear ramps.
4. **Dates in prose carry no year** (`pretty_date`); the season is on screen.
   RSS keeps RFC 822 because parsers require it.
5. **One chrome.** Header, pill nav, and matchup card come from
   `tracker_top()` and `brand.css`; no page redefines them.
6. **The same quantity is presented the same way everywhere it appears.**
   Two tables of the same thing must encode it identically — same symbol,
   same colour, same position in the row. Movement between weeks is a
   `▲`/`▼` with the places gained, trailing the team name, on *both*
   standings boards; a rise is green and a fall is red in the arrow and in
   the row flash alike. When the structure of one view makes the shared
   placement awkward, change the placement in both, not in one. A reader
   comparing two boards should be spending their attention on the numbers,
   not relearning the legend.

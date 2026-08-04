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

`.github/workflows/update.yml` refetches and redeploys to GitHub Pages hourly
during weekend game windows (Sep–Dec), every two hours in the late-night
Thu/Fri windows, and daily otherwise. Setup after pushing to GitHub:

1. Repo → Settings → Secrets and variables → Actions → add `CFBD_API_KEY`.
2. Repo → Settings → Pages → Source: **GitHub Actions**.
3. Run the workflow once manually (Actions → Update site → Run workflow).

Team and conference marks are from Wikimedia Commons (provenance in
`site/logos/SOURCES.json`); they belong to their institutions and are used
for identification only. Not affiliated with the Big 12 Conference.

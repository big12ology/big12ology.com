/* The half of the clinch analysis the browser deliberately does not do.
 *
 * site/race.js carries the model The Lab runs while somebody waits: bounds,
 * membership, chaos, and an enumeration capped at 2^11 so the page stays
 * responsive. The build is answering a different question — it is deciding
 * what to publish and can afford to be sure — so it enumerates to 2^18, works
 * out the minimal this-week conditions for each clinch in prose, and merges
 * the two proofs. None of that belongs on the page: it would be slower than
 * the answer is worth and nobody would wait for it.
 *
 * So it lives here, outside site/, which assemble.sh rsyncs wholesale. The
 * primitives come from race.js — the same functions the card calls — rather
 * than being restated, which is the entire point of the exercise.
 */
"use strict";

const path = require("path");
const E = require(path.join(__dirname, "..", "site", "engine.js"));
const R = require(path.join(__dirname, "..", "site", "race.js"));

// ~80 seconds at the ceiling, which engages with about eighteen conference
// games left — mid-November. The browser's own cap is 2^11 and stays there.
const EXACT_BUDGET = 1 << 18;
const SCENARIO_MAX_TERMS = 3;

/* Every completion of the remaining conference schedule.
 *
 * Returns null when the schedule is too open to enumerate, which is most of
 * the season; the caller falls back to bounds. Otherwise it reports, per
 * team, whether it is in the top two under EVERY completion (clinched) and
 * under ANY (still alive) — plus, keyed by this week's results alone, which
 * of those weeks already settle it. That last part is what the page does not
 * compute and what the scenarios below are written from.
 */
function exact(games, overrides, budget) {
  budget = budget || EXACT_BUDGET;
  const rem0 = R.remainingConf(games);
  if (rem0.length > 30 || Math.pow(2, rem0.length) > budget) return null;

  const ncf = R.unplayedNonconf(games);
  const teams = R.confTeams(games);
  // Copies: a caller's games are never mutated, and the flags below are put
  // back after every combination anyway.
  const base = games.map((g) => Object.assign({}, g));
  const rem = base.filter(
    (g) => g.conference_game && !g.ccg && !g.completed);

  let week = null;
  for (const g of rem) if (week === null || g.week < week) week = g.week;
  const wkIdx = [];
  rem.forEach((g, i) => { if (g.week === week) wkIdx.push(i); });

  const always = {}, ever = {}, weekAlways = new Map();
  teams.forEach((t) => { always[t] = true; ever[t] = false; });

  const total = Math.pow(2, rem.length);
  for (let c = 0; c < total; c++) {
    for (let i = 0; i < rem.length; i++) {
      const g = rem[i];
      g.completed = true;
      // Bit set means the home team won, the same convention clinch.py's
      // _apply used and the same 28-17 that never reaches a reader.
      if ((c >> i) & 1) { g.home_points = 28; g.away_points = 17; }
      else { g.home_points = 17; g.away_points = 28; }
    }
    const cm = R.cutMembership(base, overrides || {}, ncf);
    const wkey = wkIdx.map((i) => ((c >> i) & 1)).join("");
    let cell = weekAlways.get(wkey);
    if (!cell) {
      cell = {};
      teams.forEach((t) => { cell[t] = true; });
      weekAlways.set(wkey, cell);
    }
    for (const t of teams) {
      const sure = !!cm.sure[t];
      always[t] = always[t] && sure;
      ever[t] = ever[t] || sure || !!cm.maybe[t];
      cell[t] = cell[t] && sure;
    }
    for (const g of rem) {
      g.completed = false; g.home_points = null; g.away_points = null;
    }
  }

  const clinchCombos = {};
  teams.forEach((t) => {
    clinchCombos[t] = [];
    weekAlways.forEach((cell, k) => { if (cell[t]) clinchCombos[t].push(k); });
  });

  const out = {};
  teams.forEach((t) => { out[t] = { always_in: always[t], ever_in: ever[t] }; });
  return {
    teams: out,
    week: week,
    week_games: rem.filter((g) => g.week === week).map((g) => [g.home, g.away]),
    clinch_combos: clinchCombos,
    n_outcomes: total,
  };
}

/* The smallest set of this-week results that already settles it, as prose.
 *
 * A team clinched under some of this week's outcomes and not others; the
 * useful sentence is not the list of those outcomes but the shortest
 * condition that guarantees one — "a Utah win", "BYU over TCU + a Baylor
 * loss". Search by size so the shortest sufficient conditions are found
 * first, and drop any condition a smaller one already covers.
 */
function scenarioTexts(team, weekGames, combos, maxTerms) {
  maxTerms = maxTerms || SCENARIO_MAX_TERMS;
  if (!combos || !combos.length) return [];
  const n = weekGames.length;
  const total = Math.pow(2, n);
  const have = new Set(combos);
  if (have.size === total) {
    return ["has already clinched (this week can't change it)"];
  }

  const keyOf = (c) => {
    let s = "";
    for (let i = 0; i < n; i++) s += ((c >> i) & 1);
    return s;
  };
  // cond is a sparse map of game index -> required outcome. Sufficient when
  // every full week-outcome extending it is one of the clinching ones.
  const covers = (cond) => {
    for (let c = 0; c < total; c++) {
      let matches = true;
      for (const i in cond) {
        if (((c >> i) & 1) !== cond[i]) { matches = false; break; }
      }
      if (matches && !have.has(keyOf(c))) return false;
    }
    return true;
  };

  const found = [];
  for (let size = 1; size <= maxTerms && !found.length; size++) {
    const idxs = [];
    const choose = (start, pick) => {
      if (pick.length === size) { idxs.push(pick.slice()); return; }
      for (let i = start; i < n; i++) { pick.push(i); choose(i + 1, pick); pick.pop(); }
    };
    choose(0, []);
    for (const combo of idxs) {
      for (let v = 0; v < Math.pow(2, size); v++) {
        const cond = {};
        combo.forEach((gi, k) => { cond[gi] = (v >> k) & 1; });
        const entries = Object.keys(cond).map((k) => [Number(k), cond[k]]);
        // A smaller sufficient condition already implies this one.
        const covered = found.some((f) =>
          f.every(([i, val]) => cond[i] === val));
        if (covered) continue;
        if (covers(cond)) found.push(entries.sort((a, b) => a[0] - b[0]));
      }
    }
  }

  const phrase = (i, v) => {
    const home = weekGames[i][0], away = weekGames[i][1];
    const winner = v ? home : away, loser = v ? away : home;
    const art = "AEIOU".indexOf(team[0]) >= 0 ? "an" : "a";
    if (team === winner) return art + " " + team + " win";
    if (team === loser) return art + " " + team + " loss";
    return winner + " over " + loser;
  };
  return found.map((cond) =>
    cond.map(([i, v]) => phrase(i, v)).join(" + "));
}

/* Bounds and enumeration, merged — the published statuses.
 *
 * Bounds hold all season and are strict; enumeration is exact but only
 * affordable late. Where both speak they must agree, and the assertions say
 * so: a bound that claims a clinch the enumeration does not confirm is a bug
 * in one of them, and finding out at build time beats publishing it.
 */
function analyze(games, overrides, budget) {
  const b = R.bounds(games);
  const ex = exact(games, overrides, budget);
  const out = {};
  for (const t of Object.keys(b).sort()) {
    const info = { w: b[t].w, r: b[t].r, destiny: b[t].destiny, scenarios: [] };
    if (ex) {
      const e = ex.teams[t];
      if (e.always_in) { info.status = "clinched"; info.method = "exact"; }
      else if (!e.ever_in) { info.status = "eliminated"; info.method = "exact"; }
      else {
        info.status = "alive"; info.method = "exact";
        info.scenarios = scenarioTexts(t, ex.week_games, ex.clinch_combos[t]);
      }
      if (b[t].clinched && !e.always_in) {
        throw new Error("bound/exact clinch mismatch: " + t);
      }
      if (b[t].eliminated && e.ever_in) {
        throw new Error("bound/exact elim mismatch: " + t);
      }
    } else if (b[t].clinched) { info.status = "clinched"; info.method = "bounds"; }
    else if (b[t].eliminated) { info.status = "eliminated"; info.method = "bounds"; }
    else { info.status = "alive"; info.method = "bounds"; }
    out[t] = info;
  }
  return {
    mode: ex ? "exact" : "bounds",
    teams: out,
    week: ex ? ex.week : null,
    n_outcomes: ex ? ex.n_outcomes : null,
  };
}

/* The Chaos Index, taking what the build has rather than what the card has.
 *
 * race.js's chaosIndex wants statuses and probabilities already separated;
 * the build holds an analyze() result and an odds result whose underscore
 * keys are bookkeeping. Same extraction chaos.py did before calling its own
 * components, so the number is the card's number.
 */
function chaosIndex(rows, clinchResult, oddsResult) {
  const statuses = {};
  Object.keys(clinchResult.teams).forEach((t) => {
    statuses[t] = clinchResult.teams[t].status;
  });
  const probs = {};
  Object.keys(oddsResult).forEach((t) => {
    if (t[0] !== "_") probs[t] = oddsResult[t].p_ccg;
  });
  return R.chaosIndex(rows, statuses, probs);
}


/* ------------------------------------------------------------------ odds
 *
 * The Monte Carlo, and the two ways of asking what a game is worth. Ported
 * from odds.py, which this replaces; the browser has a version of the
 * simulation in race.js but not of what is below it, because The Lab shows
 * one season's odds and does not rank games by consequence.
 *
 * THE NUMBERS MOVED ONCE WHEN THIS LANDED, and they were meant to. Python
 * drew from the Mersenne Twister through random.gauss; this draws mulberry32
 * through Marsaglia polar, and math.erf becomes Abramowitz & Stegun 7.1.26
 * (|error| < 1.5e-7). Same model, same distribution, a different sample of
 * it. What is bought is that The Race and The Lab now sample the SAME stream:
 * they still differ, because the card runs 2,000 seasons to the build's
 * 10,000 and builds its ensemble from published favorites rather than raw
 * ratings, but those two differences are written down and deliberate, and
 * nothing else is left to drift.
 */

const N_SIMS = 10000;
const SEED = 1996;            // the year of the first Big 12 season
const STALE_KEEP = 0.65;

/* Pull any system still on last season's numbers toward its own mean.
 *
 * A rating that is last season's finals describes a roster that has turned
 * over, and a champion's closing number is a peak. Called once at load so
 * odds, favorites and strength of schedule all describe the same teams.
 */
function regressStale(systems, season) {
  const out = {};
  for (const name of Object.keys(systems)) {
    const s = systems[name];
    const r = s.ratings || {};
    const keys = Object.keys(r);
    if (s.year === season || !keys.length) { out[name] = s; continue; }
    let sum = 0;
    for (const t of keys) sum += r[t];
    const mean = sum / keys.length;
    const scaled = {};
    for (const t of keys) scaled[t] = mean + STALE_KEEP * (r[t] - mean);
    out[name] = Object.assign({}, s, { regressed: STALE_KEEP, ratings: scaled });
  }
  return out;
}

// Shared by ensembleMargin and winProbs: one system's view of one game, in
// scoring points, or null when neither side is rated. Unrated opponents (FCS
// and below) get a floor well under the worst rated team.
function systemMargin(s, g) {
  const r = s.ratings, per = s.per_pt || 1.0;
  if (!r || !Object.keys(r).length) return null;
  let floor = Infinity;
  for (const t of Object.keys(r)) if (r[t] < floor) floor = r[t];
  floor -= 10 * per;
  const hr = r[g.home], ar = r[g.away];
  if (hr === undefined && ar === undefined) return null;
  return ((hr === undefined ? floor : hr) -
          (ar === undefined ? floor : ar) + s.hfa) / per;
}

/* {game_id: expected home margin}, averaged across systems.
 *
 * Margins rather than probabilities, because a simulated season shifts a
 * team's strength and the shift has to happen before the curve, not after it.
 *
 * NOT the same function as race.js's ensembleMargins, and the difference is
 * deliberate rather than an oversight. That one rebuilds the ensemble from
 * the favorites already published to the page, so the card does not have to
 * ship the raw ratings a second time. This one has the ratings in hand and
 * uses them. Same number by construction; two inputs because the two callers
 * hold different things.
 */
function ensembleMargin(games, systems) {
  const sum = {}, n = {};
  for (const name of Object.keys(systems)) {
    const s = systems[name];
    for (const g of games) {
      if (g.completed || g.ccg) continue;
      const m = systemMargin(s, g);
      if (m === null) continue;
      sum[g.id] = (sum[g.id] || 0) + m;
      n[g.id] = (n[g.id] || 0) + 1;
    }
  }
  const out = {};
  for (const id of Object.keys(sum)) out[id] = sum[id] / n[id];
  return out;
}

/* {team: strength in scoring points}, averaged over the ensemble.
 *
 * Each system divided by its own per_pt first, so Elo's 27-points-per-point
 * scale lands on the same axis as SP+'s. Factored out of the odds so that
 * anything asking "how good is this team" and anything asking "who wins this
 * game" cannot drift apart.
 */
function teamStrength(systems) {
  const tot = {}, n = {};
  for (const name of Object.keys(systems)) {
    const s = systems[name];
    const r = s.ratings || {}, per = s.per_pt || 1.0;
    for (const t of Object.keys(r)) {
      tot[t] = (tot[t] || 0) + r[t] / per;
      n[t] = (n[t] || 0) + 1;
    }
  }
  const out = {};
  for (const t of Object.keys(tot)) out[t] = tot[t] / n[t];
  return out;
}

// The ensemble's home-field bump, in scoring points.
function hfaPoints(systems) {
  const vals = [];
  for (const name of Object.keys(systems)) {
    const s = systems[name];
    if (s.ratings && Object.keys(s.ratings).length) {
      vals.push(s.hfa / (s.per_pt || 1.0));
    }
  }
  if (!vals.length) return 0.0;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

// {game_id: p_home}, ensemble across systems. Unrated games fall back to a
// coin toss rather than being dropped, because a caller asking for a game's
// probability wants an answer for every game it asked about.
function winProbs(games, systems) {
  const sum = {}, n = {};
  for (const name of Object.keys(systems)) {
    const s = systems[name];
    for (const g of games) {
      if (g.completed || g.ccg) continue;
      const m = systemMargin(s, g);
      if (m === null) continue;
      sum[g.id] = (sum[g.id] || 0) + R.pFromMargin(m);
      n[g.id] = (n[g.id] || 0) + 1;
    }
  }
  const out = {};
  for (const g of games) {
    if (g.completed || g.ccg) continue;
    out[g.id] = n[g.id] ? sum[g.id] / n[g.id] : 0.5;
  }
  return out;
}

/* n simulated seasons. {team: {p_ccg, exp_w}} plus {_n}.
 *
 * p_ccg counts sure top-two membership as 1 and ambiguous membership — a tie
 * that bottoms out at a step nobody can evaluate — as a half.
 *
 * `track` takes game ids and adds "_cond", enough to condition every team's
 * probability on that game's outcome. `sigma` is pinnable, and causalLeverage
 * is the reason: asserting a result makes that game completed, which makes
 * ratingSigma read the season as one game better understood, so both branches
 * of a fork would tighten against a baseline drawn at the looser value and
 * every arrow on the card would carry a shift that has nothing to do with who
 * won.
 */
function simulate(games, systems, overrides, opts) {
  opts = opts || {};
  const n = opts.n || N_SIMS;
  const teams = R.confTeams(games);
  const margins = ensembleMargin(games, systems);
  const ncf = R.unplayedNonconf(games);

  const base = games.map((g) => Object.assign({}, g));
  const rem = base.filter(
    (g) => !g.completed && !g.ccg && margins[g.id] !== undefined);
  const rng = R.makeRng(opts.seed === undefined ? SEED : opts.seed);
  const sigma = opts.sigma === undefined || opts.sigma === null
    ? R.ratingSigma(games) : opts.sigma;

  // Sorted, and this is load-bearing. The draws below are pulled in iteration
  // order, so an unordered set would hand each team a different offset from
  // run to run and quietly break the fixed-seed guarantee the whole module
  // rests on. Python had the same note for the same reason.
  const sideSet = {};
  for (const g of rem) { sideSet[g.home] = 1; sideSet[g.away] = 1; }
  const sides = Object.keys(sideSet).sort();

  const trackSet = {};
  for (const id of (opts.track || [])) trackSet[id] = 1;
  const trackIds = rem.filter((g) => trackSet[g.id]).map((g) => g.id);
  const cond = {};
  for (const gid of trackIds) {
    const inn = {};
    for (const t of teams) inn[t] = [0.0, 0.0];
    cond[gid] = { n_home: 0, in: inn };
  }

  const inCount = {}, winSum = {};
  for (const t of teams) { inCount[t] = 0.0; winSum[t] = 0; }

  for (let iter = 0; iter < n; iter++) {
    // One draw per team per season, not per game: a team that is really a
    // touchdown worse than its rating is worse in all nine of them, and that
    // correlation is what moves a season-long distribution.
    const off = {};
    if (sigma) for (const t of sides) off[t] = rng.gauss(sigma);
    const outcomes = {};
    for (const g of rem) {
      g.completed = true;
      const m = margins[g.id] + (off[g.home] || 0) - (off[g.away] || 0);
      const hw = rng.next() < R.pFromMargin(m);
      outcomes[g.id] = hw;
      if (hw) { g.home_points = 28; g.away_points = 17; }
      else { g.home_points = 17; g.away_points = 28; }
    }
    const cm = R.cutMembership(base, overrides || {}, ncf);
    const rec = E.confRecords(base);
    for (const t of teams) {
      const v = cm.sure[t] ? 1.0 : (cm.maybe[t] ? 0.5 : 0.0);
      inCount[t] += v;
      winSum[t] += rec[t] ? rec[t][0] : 0;
      for (const gid of trackIds) cond[gid].in[t][outcomes[gid] ? 0 : 1] += v;
    }
    for (const gid of trackIds) if (outcomes[gid]) cond[gid].n_home += 1;
    for (const g of rem) {
      g.completed = false; g.home_points = null; g.away_points = null;
    }
  }

  const out = {};
  for (const t of teams) {
    out[t] = { p_ccg: inCount[t] / n, exp_w: winSum[t] / n };
  }
  out._n = n;
  if (trackIds.length) out._cond = cond;
  return out;
}

// The shape both leverage functions return, so their callers do not have to
// know which one answered.
function movement(g, probs) {
  const movers = [];
  const pair = {};
  let total = 0.0;
  for (const t of Object.keys(probs).sort()) {
    const [pw, pl] = probs[t];
    const d = pw - pl;
    // HALVED. Every point one team gains another loses, so summing the
    // absolute changes counts each point twice — a game moving 27 points of
    // probability summed to 54, which made the scale's own anchor wrong.
    // Halved, 1.0 is one championship-game berth.
    total += Math.abs(d) / 2;
    // Both endpoints, never just the gap. A single signed number reads as a
    // change from where the team stands today — "+27% if BYU wins" against a
    // board showing 23% invites 23+27 — when it is the distance between two
    // futures, neither of which is today.
    if (Math.abs(d) >= 0.005) movers.push([t, d, pw, pl]);
    if (t === g.home || t === g.away) pair[t] = [pw, pl];
  }
  movers.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  return { game: g, home: g.home, away: g.away, total, movers, pair };
}

/* Conditional leverage: filter one baseline run to the seasons the home side
 * won. Cheap — every tracked game comes out of a single simulation — and it
 * answers a real question for a preview: if BYU beats Arizona, Sunday's board
 * reads 33%.
 */
function leverage(sims, games) {
  const cond = sims._cond || {};
  const n = sims._n || 0;
  const byId = {};
  for (const g of games) byId[g.id] = g;
  const out = [];
  for (const gid of Object.keys(cond)) {
    const c = cond[gid];
    const nh = c.n_home, nl = n - nh;
    if (nh === 0 || nl === 0) continue;
    const probs = {};
    for (const t of Object.keys(c.in)) {
      probs[t] = [c.in[t][0] / nh, c.in[t][1] / nl];
    }
    out.push(movement(byId[gid], probs));
  }
  out.sort((a, b) => b.total - a.total);
  return out;
}

// The season with one result asserted rather than simulated.
function forceResult(games, gid, homeWins) {
  return games.map((g) => {
    if (g.id !== gid) return g;
    return Object.assign({}, g, {
      completed: true,
      home_points: homeWins ? 28 : 17,
      away_points: homeWins ? 17 : 28,
    });
  });
}

/* Causal leverage: what a result DOES, which is not what leverage() measures.
 *
 * Filtering to the seasons BYU won preferentially keeps the seasons where BYU
 * was rolled strong, and a stronger BYU also wins more of its other eight. The
 * conditional therefore carries the result AND the re-rating the result would
 * justify. At full preseason uncertainty that was four points of BYU's ten.
 *
 * So assert the result and run the season around it, twice. Two things make
 * that honest: both branches run on the SAME SEED — the forced game is
 * completed in both, so the remaining schedule is the same list in the same
 * order and the draws line up game for game — and sigma is pinned to the
 * pre-game value. Run independently, each branch would carry its own noise,
 * and since `total` sums sixteen absolute differences, noise that averages to
 * zero would not: a game that moves nothing would report several points.
 *
 * Costs what it looks like: two full runs per game, against one for the week.
 */
function causalLeverage(games, systems, overrides, gids, opts) {
  opts = opts || {};
  const n = opts.n || N_SIMS;
  const seed = opts.seed === undefined ? SEED : opts.seed;
  const sigma = R.ratingSigma(games);
  const byId = {};
  for (const g of games) byId[g.id] = g;
  const out = [];
  for (const gid of gids || []) {
    const g = byId[gid];
    if (!g) continue;
    const sh = simulate(forceResult(games, gid, true), systems, overrides,
                        { n, seed, sigma });
    const sa = simulate(forceResult(games, gid, false), systems, overrides,
                        { n, seed, sigma });
    const probs = {};
    for (const t of Object.keys(sh)) {
      if (t[0] === "_") continue;
      probs[t] = [sh[t].p_ccg, sa[t].p_ccg];
    }
    out.push(movement(g, probs));
  }
  out.sort((a, b) => b.total - a.total);
  return out;
}

module.exports = {
  exact, scenarioTexts, analyze, chaosIndex,
  bounds: R.bounds, confTeams: R.confTeams, cutMembership: R.cutMembership,
  regressStale, ensembleMargin, teamStrength, hfaPoints, winProbs,
  simulate, leverage, forceResult, causalLeverage,
  ratingSigma: R.ratingSigma, pFromMargin: R.pFromMargin,
  EXACT_BUDGET, N_SIMS, SEED, MARGIN_SIGMA: R.MARGIN_SIGMA,
};

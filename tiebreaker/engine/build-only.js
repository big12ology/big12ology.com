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
require(path.join(__dirname, "..", "site", "engine.js"));
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

module.exports = {
  exact, scenarioTexts, analyze, chaosIndex,
  bounds: R.bounds,
  confTeams: R.confTeams,
  cutMembership: R.cutMembership,
  EXACT_BUDGET,
};

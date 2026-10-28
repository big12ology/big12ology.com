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
      // Number(i), because for..in hands back the key as a string and
      // `c >> "3"` only works by coercion. It gives the right answer today;
      // it is also the exact shape of the bug this codebase has already been
      // bitten by once, where `28 > null` quietly meant true.
      for (const i in cond) {
        if (((c >> Number(i)) & 1) !== cond[i]) { matches = false; break; }
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

/* ------------------------------------------------------------- witness
 *
 * The fewest-upsets path: for one team, a completion of the remaining
 * conference schedule that puts it in the top two under the full tiebreaker,
 * using the fewest results against the model's favorite, and among those the
 * likeliest.
 *
 * WHY A SEARCH AND NOT A COUNT. With 47 conference games left there are 2^47
 * completions and exact() will not engage until about eighteen remain, so
 * from October to mid-November the published status is the bounds test
 * alone: a team is "alive" until two others have already passed its ceiling.
 * Kansas at 0-3 with six to play is alive on that test, and alive on the
 * simulation at zero of 40,000. Neither says what the reader asks, which is
 * what it would take. One witness answers that, and finding one witness is
 * a search with a sharp prune rather than an enumeration.
 *
 * THE PRUNE. At any partial completion, a team X that is favored in enough
 * of its unfixed games to finish above T's ceiling must lose at least that
 * many of them, and each upset lowers exactly one favorite by one. So the
 * sum of those excesses, less the largest (one team may finish above T), is
 * a lower bound on the upsets still needed. Searching the upset count by
 * iterative deepening, the first depth with a witness is the minimum, and
 * the ordering (T's own games first, then games between the teams with the
 * most to lose) is what makes the bound bite early.
 *
 * WHAT COMES BACK. {upsets, sure, prob, results, complete} on a witness;
 * {found: false, complete} on none. `complete` is the claim: true means the
 * search ran to the end, so a missing witness is a PROOF of elimination, as
 * strict as the bounds test and stricter in practice. False means the node
 * budget ran out first and the caller keeps whatever it already said. `sure`
 * is whether the cut is settled by the procedure or bottoms out in a step
 * nobody can evaluate (a maybe in cutMembership's terms), the same reading
 * exact()'s ever_in gives.
 *
 * `probs` is winProbs' {id: p_home}; a game it does not cover is a coin
 * flip, and a coin flip is an upset whichever way it goes.
 */
// 4M nodes, measured and not guessed. On 2026-10-27, with 39 conference
// games left, Colorado at 0-4 needed 275,949 nodes to find its path and
// Kansas State 119,515: both over the 2^17 the budget started at, so both
// pages fell back to "needs help" and said nothing. The worst of the
// sixteen at this cap runs 0.4s; a team that exhausts it takes about five
// seconds and keeps the status it had, which is the right failure.
const WITNESS_BUDGET = 1 << 22;
const WITNESS_MAX_UPSETS = 40;

function witness(games, team, overrides, probs, budget, maxUpsets) {
  budget = budget || WITNESS_BUDGET;
  maxUpsets = maxUpsets || WITNESS_MAX_UPSETS;
  probs = probs || {};
  const teams = R.confTeams(games);
  if (teams.indexOf(team) < 0) return null;
  const ncf = R.unplayedNonconf(games);
  const base = games.map((g) => Object.assign({}, g));
  const rec = E.confRecords(games);
  const w = {}, r = {}, favLeft = {};
  teams.forEach((t) => {
    w[t] = rec[t] ? rec[t][0] : 0; r[t] = 0; favLeft[t] = 0;
  });
  const meta = base
    .filter((g) => g.conference_game && !g.ccg && !g.completed)
    .map((g) => {
      const ph = probs[g.id] === undefined ? 0.5 : probs[g.id];
      const homeFav = ph >= 0.5;
      const pFav = Math.min(1 - 1e-9, Math.max(1e-9, homeFav ? ph : 1 - ph));
      return { g, fav: homeFav ? g.home : g.away,
               dog: homeFav ? g.away : g.home, pFav };
    });
  meta.forEach((m) => {
    r[m.g.home] += 1; r[m.g.away] += 1; favLeft[m.fav] += 1;
  });
  const chalk = {};
  teams.forEach((t) => { chalk[t] = w[t] + favLeft[t]; });
  const own = (m) => m.g.home === team || m.g.away === team;
  meta.sort((a, b) => {
    const ao = own(a), bo = own(b);
    if (ao !== bo) return ao ? -1 : 1;
    if (!ao) {
      const ar = Math.max(chalk[a.g.home], chalk[a.g.away]);
      const br = Math.max(chalk[b.g.home], chalk[b.g.away]);
      if (ar !== br) return br - ar;
    }
    if (a.g.week !== b.g.week) return a.g.week - b.g.week;
    return String(a.g.id) < String(b.g.id) ? -1 : 1;
  });

  const n = meta.length;
  const logFav = meta.map((m) => Math.log(m.pFav));
  const logDog = meta.map((m) => Math.log(1 - m.pFav));
  const sufFav = new Array(n + 1).fill(0);
  for (let i = n - 1; i >= 0; i--) sufFav[i] = sufFav[i + 1] + logFav[i];
  const choice = new Array(n).fill(false);

  function lowerBound() {
    const maxT = w[team] + r[team];
    let sum = 0, mx = 0;
    for (const x of teams) {
      if (x === team) continue;
      const ex = w[x] + favLeft[x] - maxT;
      if (ex > 0) { sum += ex; if (ex > mx) mx = ex; }
    }
    return sum - mx;
  }

  function apply(i, upset) {
    const m = meta[i], g = m.g;
    const winner = upset ? m.dog : m.fav;
    w[winner] += 1; r[g.home] -= 1; r[g.away] -= 1; favLeft[m.fav] -= 1;
    g.completed = true;
    // The same 28-17 exact() writes: a score the ladder reads as a result
    // and nothing else.
    if (winner === g.home) { g.home_points = 28; g.away_points = 17; }
    else { g.home_points = 17; g.away_points = 28; }
    choice[i] = upset;
  }

  function undo(i, upset) {
    const m = meta[i], g = m.g;
    const winner = upset ? m.dog : m.fav;
    w[winner] -= 1; r[g.home] += 1; r[g.away] += 1; favLeft[m.fav] += 1;
    g.completed = false; g.home_points = null; g.away_points = null;
  }

  let nodes = 0, k = 0, best = null, bestLogp = -Infinity, truncated = false;

  function dfs(i, upsets, logp) {
    if (truncated) return;
    if (nodes >= budget) { truncated = true; return; }
    nodes += 1;
    if (upsets + lowerBound() > k) return;
    if (logp + sufFav[i] <= bestLogp) return;
    if (i === n) {
      const cm = R.cutMembership(base, overrides || {}, ncf);
      const sure = !!cm.sure[team];
      if (!sure && !cm.maybe[team]) return;
      // A settled cut beats an unsettled one at the same depth, whatever
      // the odds of either: "and wins the tiebreak" is a path, "and it
      // comes down to a step nobody can evaluate" is half of one. The
      // probability prune only switches on behind a settled best, since a
      // likelier maybe must not block the sure path that beats it.
      if (!best || (sure && !best.sure) || (sure === best.sure && logp > best.logp)) {
        best = { choice: choice.slice(), sure, upsets, logp };
        bestLogp = sure ? logp : -Infinity;
      }
      return;
    }
    apply(i, false); dfs(i + 1, upsets, logp + logFav[i]); undo(i, false);
    if (upsets < k) {
      apply(i, true); dfs(i + 1, upsets + 1, logp + logDog[i]); undo(i, true);
    }
  }

  for (k = 0; k <= maxUpsets && !best && !truncated; k++) dfs(0, 0, 0);

  if (!best) {
    return { team, found: false, complete: !truncated, nodes,
             remaining: n };
  }
  const results = meta.map((m, i) => ({
    id: m.g.id, week: m.g.week, home: m.g.home, away: m.g.away,
    winner: best.choice[i] ? m.dog : m.fav,
    upset: best.choice[i], p: best.choice[i] ? 1 - m.pFav : m.pFav,
    own: own(m),
  }));
  const finalW = w[team] + results.filter((x) => x.winner === team).length;
  const finalL = (rec[team] ? rec[team][1] : 0) +
    results.filter((x) => x.own && x.winner !== team).length;
  return {
    team, found: true, complete: true, upsets: best.upsets, sure: best.sure,
    prob: Math.exp(best.logp), results, record: [finalW, finalL],
    nodes, remaining: n,
  };
}

/* Bounds and enumeration, merged — the published statuses.
 *
 * Bounds hold all season and are strict; enumeration is exact but only
 * affordable late. Where both speak they must agree, and the assertions say
 * so: a bound that claims a clinch the enumeration does not confirm is a bug
 * in one of them, and finding out at build time beats publishing it.
 */
function analyze(games, overrides, budget, probs) {
  const b = R.bounds(games);
  const ex = exact(games, overrides, budget);
  // The search runs when a caller hands over win probabilities: the two
  // published calls, the race board and the team pages. The per-week frames
  // and the Brief's previous-week replay do not, and their statuses stay the
  // cheap ones, so a team the search proves out does not read as "news"
  // every Monday against a replay that never looked.
  const search = probs !== undefined && probs !== null;
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
    if (search && info.status === "alive") {
      const wit = witness(games, t, overrides, probs);
      if (wit && wit.found) {
        info.witness = wit;
      } else if (wit && wit.complete) {
        // Exhaustive and empty: a proof, and the enumeration must agree
        // with it wherever the enumeration ran at all.
        if (ex) throw new Error("exact/search elim mismatch: " + t);
        info.status = "eliminated"; info.method = "search";
      }
    }
    out[t] = info;
  }
  return {
    mode: ex ? "exact" : "bounds",
    search,
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
 * 40,000 and builds its ensemble from published favorites rather than raw
 * ratings, but those two differences are written down and deliberate, and
 * nothing else is left to drift.
 */

/* 40,000, and the number is chosen against a measurement rather than a feel.
 *
 * At 10,000 two runs of the SAME season disagree by 0.56 points on average
 * and 1.7 at the tail, purely on the seed. Every probability on this site is
 * published to one decimal, so that entire decimal was sampling error, and
 * the Brief's movement card was reporting it as news: a non-conference result
 * in week 0 of 2026, which cannot move a conference title race at all, pushed
 * five teams over its print threshold. At 40,000 the same false signal falls
 * to 0.16 average and 0.48 tail, under the half point the card prints at.
 *
 * It costs about 2.4s a call and the build makes two of them, against a build
 * that runs on a schedule in CI and that nobody waits on. The interactive
 * card in the browser is the one with a real budget, and it stays at 2,000.
 */
const N_SIMS = 40000;
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
  // No home field at a neutral site. Applied unconditionally this gave the
  // nominal home team a bump it had not earned, which is enough to flip the
  // favorite: Arizona State vs Kansas at Wembley came out as Kansas on four
  // of five systems that all rated Arizona State higher. It matters more
  // here than on the card, because these margins feed the simulations.
  const edge = g.neutral_site ? 0 : s.hfa;
  return ((hr === undefined ? floor : hr) -
          (ar === undefined ? floor : ar) + edge) / per;
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
    // R.berthShare, not a 0.5 restated here. A tie the procedure could not
    // cut is split by how many teams are in it and how many seats it is
    // contesting, and the card divides it the same way. The two pages report
    // one model, so the arithmetic is one function.
    const share = R.berthShare(R.SPOTS - R.countKeys(cm.sure),
                               R.countKeys(cm.maybe));
    for (const t of teams) {
      const v = cm.sure[t] ? 1.0 : (cm.maybe[t] ? share : 0.0);
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
  exact, scenarioTexts, analyze, chaosIndex, witness,
  bounds: R.bounds, confTeams: R.confTeams, cutMembership: R.cutMembership,
  remainingConf: R.remainingConf, tangleComponent: R.tangleComponent,
  regressStale, ensembleMargin, teamStrength, hfaPoints, winProbs,
  simulate, leverage, forceResult, causalLeverage,
  ratingSigma: R.ratingSigma, pFromMargin: R.pFromMargin,
  EXACT_BUDGET, WITNESS_BUDGET, N_SIMS, SEED, MARGIN_SIGMA: R.MARGIN_SIGMA,
};

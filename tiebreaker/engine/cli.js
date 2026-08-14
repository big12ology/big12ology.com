/* The rules engine, answering questions on stdin.
 *
 *   node engine/cli.js
 *
 * One JSON request per line in, one JSON response per line out:
 *
 *   {"id":1,"op":"standings","args":[games, overrides]}
 *   {"id":1,"ok":true,"result":[...]}
 *
 * WHY A PROCESS THAT STAYS UP. build.py asks the engine roughly fifty
 * questions per build and the count rises as more of the model moves over —
 * season_frames alone asks for standings once per conference week, times three
 * seasons. `node -e` costs 33ms to start, so a process per question is over a
 * second of pure startup before the engine has read anything, and it gets
 * worse with every phase. One process, spawned on the first question and
 * closed when the build exits, costs that once.
 *
 * The alternative was one batched call: hand over everything at the start,
 * get a document of every derived number back, render from that. It is the
 * better end state and it is where this is going, but it cannot be the first
 * step — season_frames builds its truncated seasons inside build.py's own
 * loop, so batching them means porting that loop too, in the same change that
 * first moves the rules. This protocol gets the boundary in place with the
 * call sites left where they are; batching is a later phase, and the process
 * does not have to change for it.
 *
 * NOTHING ELSE MAY WRITE TO STDOUT. The protocol is the stream. Anything
 * chatty in here desynchronises the shim's read loop, which is why errors go
 * back as a response rather than to the console.
 */
"use strict";

const readline = require("readline");
const path = require("path");

// The same file the browser loads. That is the entire point: the page and the
// build are running the same rules, not two ports of them.
const engine = require(path.join(__dirname, "..", "site", "engine.js"));
// The clinch model: race.js's primitives, plus the parts only a build runs.
const build = require(path.join(__dirname, "build-only.js"));

const OPS = {
  clinchAnalyze: (games, overrides, budget) =>
    build.analyze(games, overrides || {}, budget),
  clinchBounds: (games) => build.bounds(games),
  clinchExact: (games, overrides, budget) =>
    build.exact(games, overrides || {}, budget),
  confTeams: (games) => build.confTeams(games),
  remainingConf: (games) => build.remainingConf(games),
  tangleComponent: (rows, statuses, n) => build.tangleComponent(rows, statuses, n),
  cutMembership: (games, overrides, ncf) =>
    build.cutMembership(games, overrides || {}, ncf),
  chaosIndex: (rows, clinchResult, oddsResult) =>
    build.chaosIndex(rows, clinchResult, oddsResult),

  regressStale: (systems, season) => build.regressStale(systems, season),
  ensembleMargin: (games, systems) => build.ensembleMargin(games, systems),
  teamStrength: (systems) => build.teamStrength(systems),
  hfaPoints: (systems) => build.hfaPoints(systems),
  winProbs: (games, systems) => build.winProbs(games, systems),
  ratingSigma: (games) => build.ratingSigma(games),
  pFromMargin: (m) => build.pFromMargin(m),
  // The model's constants, so the page can say how it was computed without
  // Python holding a second copy of the numbers.
  constants: () => ({
    N_SIMS: build.N_SIMS, SEED: build.SEED,
    MARGIN_SIGMA: build.MARGIN_SIGMA, EXACT_BUDGET: build.EXACT_BUDGET,
  }),
  simulate: (games, systems, overrides, opts) =>
    build.simulate(games, systems, overrides || {}, opts || {}),
  leverage: (sims, games) => build.leverage(sims, games),
  causalLeverage: (games, systems, overrides, gids, opts) =>
    build.causalLeverage(games, systems, overrides || {}, gids || [], opts || {}),
  standings: (games, overrides) => engine.standings(games, overrides || {}),
  championship: (games, overrides) => engine.championship(games, overrides || {}),
  placementGroups: (games) => engine.placementGroups(games),
  breakTie: (tied, games, overrides) =>
    engine.breakTie(tied, games, overrides || {}),
  confRecords: (games) => engine.confRecords(games),
  pct: (w, l) => engine.pct(w, l),
  winner: (g) => engine.winner(g),
  hasScore: (g) => engine.hasScore(g),
  // Not a rule — a handshake. The shim calls it to prove the process came up
  // and is the engine it expected, before the build depends on an answer.
  ping: () => ({ ops: Object.keys(OPS).sort() }),
};

const rl = readline.createInterface({ input: process.stdin });

rl.on("line", (line) => {
  line = line.trim();
  if (!line) return;
  let req;
  try {
    req = JSON.parse(line);
  } catch (e) {
    process.stdout.write(
      JSON.stringify({ id: null, ok: false, error: "unparseable request: " + e.message }) + "\n");
    return;
  }
  const fn = OPS[req.op];
  if (!fn) {
    process.stdout.write(
      JSON.stringify({ id: req.id, ok: false, error: "no such op: " + req.op }) + "\n");
    return;
  }
  try {
    const result = fn.apply(null, req.args || []);
    process.stdout.write(JSON.stringify({ id: req.id, ok: true, result }) + "\n");
  } catch (e) {
    // The stack matters: this is a rules bug surfacing in a build, and the
    // Python side has no way to see into here otherwise.
    process.stdout.write(
      JSON.stringify({ id: req.id, ok: false, error: String(e && e.stack || e) }) + "\n");
  }
});

rl.on("close", () => process.exit(0));

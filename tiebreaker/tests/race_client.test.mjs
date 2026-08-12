/* The Championship race card as The Lab computes it, in the browser.
 *
 * race.js is a port of clinch.py, odds.py and chaos.py that has to run while
 * someone waits, so it cannot be checked by the Python suite. These are the
 * invariants that hold whatever the picks are — the same ones test_odds.py
 * and test_clinch.py assert on the build's side of the port.
 *
 *   node --test tests/race_client.test.mjs
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const SITE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..",
                       "site");

/* race.js expects the three globals the page gives it. Nothing here touches
   the DOM: mount() is never called, so render() never runs and `state` stays
   null — computeSync takes its payload as an argument for exactly that. */
function loadClient() {
  const sandbox = { window: undefined, console };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  for (const f of ["engine.js", "pct.js", "race.js"]) {
    vm.runInContext(fs.readFileSync(path.join(SITE, f), "utf8"), sandbox, f);
  }
  return sandbox;
}

// The payload the page ships, read out of the built page rather than rebuilt
// here, so the test is against what actually deploys.
function payloadFor(year) {
  const file = year
    ? path.join(SITE, String(year), "lab.html")
    : path.join(SITE, "lab.html");
  const html = fs.readFileSync(file, "utf8");
  const m = html.match(
    /<script id=payload type=application\/json>([\s\S]*?)<\/script>/);
  assert.ok(m, `no payload in ${file}`);
  return JSON.parse(m[1]);
}

// Apply what-if picks the way app.js does: a picked game is a played game.
function withPicks(payload, chooseWinner) {
  return payload.games.map((g) => {
    if (g.completed || g.ccg) return g;
    const w = chooseWinner(g);
    if (!w) return g;
    const homeWon = w === g.home;
    return {
      ...g, completed: true,
      home_points: homeWon ? 28 : 17,
      away_points: homeWon ? 17 : 28,
    };
  });
}

const YEARS = [null, 2025];

for (const year of YEARS) {
  const label = year ? String(year) : "live";

  test(`${label}: a fully picked season is settled, and settled is exact`,
    () => {
      const api = loadClient();
      const payload = payloadFor(year);
      // Home team wins everything: no simulation is possible or needed.
      const games = withPicks(payload, (g) => g.home);
      const m = api.B12Race.computeSync(games, { payload });

      assert.equal(m.proof, "settled");
      assert.equal(m.nSims, 0, "a decided season must not be simulated");

      const teams = Object.keys(m.statuses);
      assert.equal(teams.length, 16);
      const clinched = teams.filter((t) => m.statuses[t] === "clinched");
      const out = teams.filter((t) => m.statuses[t] === "eliminated");
      assert.equal(clinched.length, 2, "exactly two reach the title game");
      assert.equal(out.length, 14);

      // The card and the matchup card must name the same two teams.
      const ccg = api.B12Engine.championship(games, payload.overrides || {});
      assert.deepEqual(clinched.slice().sort(),
                       [ccg.seed1, ccg.seed2].sort());

      for (const t of clinched) assert.equal(m.probs[t], 1);
      for (const t of out) assert.equal(m.probs[t], 0);
    });

  test(`${label}: odds fill two berths and never contradict a proof`, () => {
    const api = loadClient();
    const payload = payloadFor(year);
    // Pick nothing extra: the season as it really stands, simulated.
    const games = payload.games.map((g) => ({ ...g }));
    const m = api.B12Race.computeSync(games, { payload, nSims: 400 });

    const teams = Object.keys(m.statuses);
    const total = teams.reduce((a, t) => a + (m.probs[t] ?? 0), 0);

    if (m.remaining !== 0) {
      // Two seats, so the column sums to about two. Sampling noise on 400
      // seasons is well inside this.
      assert.ok(Math.abs(total - 2) < 0.06,
                `berths should sum to ~2, got ${total.toFixed(3)}`);
    }

    for (const t of teams) {
      const p = m.probs[t];
      assert.ok(p >= 0 && p <= 1, `${t} probability out of range: ${p}`);
      // A proof outranks an estimate, in both directions.
      if (m.statuses[t] === "eliminated") {
        assert.equal(p, 0, `${t} is proven out but carries odds`);
      }
      if (m.statuses[t] === "clinched") {
        assert.equal(p, 1, `${t} is proven in but carries odds below 1`);
      }
    }
  });

  test(`${label}: the same picks always give the same odds`, () => {
    const payload = payloadFor(year);
    const games = payload.games.map((g) => ({ ...g }));
    const run = () => loadClient().B12Race
      .computeSync(games.map((g) => ({ ...g })), { payload, nSims: 200 });
    const a = run(), b = run();
    // Spread into this realm before comparing: each loadClient() builds its
    // own vm context, and deepStrictEqual checks prototypes, so objects born
    // in two contexts never match however equal their contents are.
    assert.deepEqual({ ...a.probs }, { ...b.probs },
                     "a fixed seed must be reproducible");
    assert.deepEqual({ ...a.statuses }, { ...b.statuses });
  });

  test(`${label}: bounds proofs are never contradicted by enumeration`, () => {
    const api = loadClient();
    const payload = payloadFor(year);
    // Leave a handful of conference games open so the exact phase engages
    // and can be checked against the cheap bounds it is meant to refine.
    // A finished season has nothing left to open, so the results come off
    // nine of its games first — the point is an enumerable schedule, not
    // which particular games are missing.
    let open = 0;
    const games = withPicks(payload, (g) => g.home).map((g) => {
      if (g.conference_game && !g.ccg && open < 9) {
        open += 1;
        return { ...g, completed: false, home_points: null, away_points: null };
      }
      return g;
    });
    assert.equal(open, 9, "expected nine conference games to open up");

    const b = api.B12Race.bounds(games);
    const m = api.B12Race.computeSync(games, { payload, nSims: 200 });
    assert.equal(m.proof, "exact", "nine open games should enumerate");

    for (const t of Object.keys(b)) {
      if (b[t].clinched) {
        assert.equal(m.statuses[t], "clinched",
                     `${t}: bounds proved a clinch the enumeration denies`);
      }
      if (b[t].eliminated) {
        assert.equal(m.statuses[t], "eliminated",
                     `${t}: bounds proved an elimination the enumeration denies`);
      }
    }
  });

  test(`${label}: the chaos index stays on its scale`, () => {
    const api = loadClient();
    const payload = payloadFor(year);
    const games = payload.games.map((g) => ({ ...g }));
    const m = api.B12Race.computeSync(games, { payload, nSims: 200 });
    const cx = m.chaos;
    assert.ok(cx, "a computed card always carries a chaos reading");
    assert.ok(cx.score >= 0 && cx.score <= 100, `score ${cx.score}`);
    assert.equal(cx.score, Math.round(cx.score), "the score is a whole number");
    for (const [k, v] of Object.entries(cx.components)) {
      assert.ok(v >= 0 && v <= 1, `component ${k} off its 0-1 scale: ${v}`);
    }
    assert.ok(typeof cx.label === "string" && cx.label.length);
  });
}

test("a decided season reads as Settled, an open one does not", () => {
  const api = loadClient();
  const payload = payloadFor(null);
  const decided = api.B12Race.computeSync(
    withPicks(payload, (g) => g.home), { payload });
  assert.equal(decided.chaos.label, "Settled");
  assert.equal(decided.chaos.score, 0);

  const open = api.B12Race.computeSync(
    payload.games.map((g) => ({ ...g })), { payload, nSims: 300 });
  assert.ok(open.chaos.score > decided.chaos.score,
            "an unplayed season cannot be as settled as a finished one");
});

test("the ensemble margin is the mean of the published favourites", () => {
  const api = loadClient();
  const payload = payloadFor(null);
  const margins = api.B12Race.ensembleMargins(payload);
  // The RATING systems, not every key in payload.favorites — that object
  // also holds their blend and the market, and neither belongs in a mean of
  // the four. This is the contract ensembleMargins now filters on, and the
  // reason it has to: without it the blend was averaged in with the systems
  // it is made of.
  const models = (payload.models || [])
    .filter((m) => m.kind === "rating").map((m) => m.name);
  assert.ok(models.length > 1, "expected several rating systems");
  assert.ok(Object.keys(payload.favorites).length > models.length,
            "expected the blend and the market alongside the ratings");

  // A game every rating has an opinion on. The market skips a pick'em and
  // any game with no posted line, so "every model rates this" is not a
  // property every fixture has.
  const g = payload.games.find((x) => x.conference_game && !x.completed &&
    models.every((m) => payload.favorites[m][String(x.id)]));
  const each = models.map((mName) => {
    const e = payload.favorites[mName][String(g.id)];
    return e.team === g.home ? e.margin : -e.margin;
  });
  const want = each.reduce((a, x) => a + x, 0) / each.length;
  assert.ok(Math.abs(margins[String(g.id)] - want) < 1e-9,
            `ensemble margin ${margins[String(g.id)]} != mean ${want}`);

  // A favourite really is more likely than not to win. The erf here is a
  // series approximation, so an even game lands on a half within its error
  // rather than exactly on it.
  assert.ok(Math.abs(api.B12Race.pFromMargin(0) - 0.5) < 1e-6);
  assert.ok(api.B12Race.pFromMargin(10) > 0.5);
  assert.ok(api.B12Race.pFromMargin(-10) < 0.5);
  // A touchdown of margin is worth about what the build's curve says.
  assert.ok(Math.abs(api.B12Race.pFromMargin(7) - 0.699) < 0.005);
});

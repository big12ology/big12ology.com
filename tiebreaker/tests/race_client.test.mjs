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

/* Copy into THIS realm before comparing. Arrays that come back from the
   engine were born in the vm context, and deepStrictEqual checks prototypes,
   so two lists of identical strings never match unless one is rebuilt here.
   The same trap the odds test spreads its objects to avoid. */
const sorted = (xs) => Array.from(xs).sort();

const YEARS = [null, 2025];

for (const year of YEARS) {
  const label = year ? String(year) : "live";

  /* THIS USED TO ASSERT THAT A FULLY PICKED SEASON NAMES TWO TEAMS, and that
     is not true of the Big 12 procedure. Its last step is a SportSource
     rating or a coin toss; neither is in the data, so a tie that reaches the
     bottom of the ladder stays unbroken however many games are played. Pick
     every 2026 game for the home side and eight teams finish 5-4 in one such
     group: the ladder seeds one of them and stops, and the title game has a
     single name in it. The season is over and the answer is still two-thirds
     of an answer.

     What survives is the invariant the old assertion was reaching for, which
     was never about the count. The card and the matchup card are two views of
     one engine, so they must report the same state of knowledge: every team
     the engine seeds is clinched here, every team it could not seed is alive
     here, and no team is either for any other reason. The berths sum to two
     whichever of those two shapes the season takes. */
  test(`${label}: a fully picked season is settled, and says only what the ` +
    `matchup card says`, () => {
      const api = loadClient();
      const payload = payloadFor(year);
      // Home team wins everything: no simulation is possible or needed.
      const games = withPicks(payload, (g) => g.home);
      const m = api.B12Race.computeSync(games, { payload });

      assert.equal(m.proof, "settled");
      assert.equal(m.nSims, 0, "a decided season must not be simulated");
      assert.equal(m.remaining, 0, "nothing is left to play");

      const teams = Object.keys(m.statuses);
      assert.equal(teams.length, 16);
      const clinched = teams.filter((t) => m.statuses[t] === "clinched");
      const alive = teams.filter((t) => m.statuses[t] === "alive");
      const out = teams.filter((t) => m.statuses[t] === "eliminated");
      assert.equal(clinched.length + alive.length + out.length, 16);

      const ccg = api.B12Engine.championship(games, payload.overrides || {});
      const seeds = [ccg.seed1, ccg.seed2].filter(Boolean);
      const pending = ccg.pending || [];

      // A group the engine could not cut but that fits inside the top two
      // whole is still in: two teams sharing first place both play, and the
      // card clinches them rather than calling either one uncertain. Above
      // that size the seats have to be shared, and `unbroken` is the card
      // saying which of the two happened.
      assert.equal(!!m.unbroken, pending.length > 2 - seeds.length,
                   "the card and the engine disagree about being stuck");
      const named = seeds.concat(m.unbroken ? [] : pending);
      assert.deepEqual(sorted(clinched), sorted(named),
                       "the card clinched a team the matchup card did not name");
      assert.deepEqual(sorted(alive), sorted(m.unbroken ? pending : []),
                       "the teams left alive are not the unbroken group");
      if (m.unbroken) {
        assert.equal(m.unbroken.seats, 2 - seeds.length);
        assert.deepEqual(sorted(m.unbroken.teams), sorted(pending));
      } else {
        assert.equal(clinched.length, 2, "exactly two reach the title game");
        assert.equal(out.length, 14);
      }

      // Two berths, however they end up divided.
      const total = teams.reduce((a, t) => a + m.probs[t], 0);
      assert.ok(Math.abs(total - 2) < 1e-9, `berths sum to ${total}`);
      for (const t of clinched) assert.equal(m.probs[t], 1);
      for (const t of out) assert.equal(m.probs[t], 0);
      for (const t of alive) {
        assert.ok(m.probs[t] > 0 && m.probs[t] < 1,
                  `${t} is neither in nor out but carries ${m.probs[t]}`);
      }
    });

  /* THE SEAM BETWEEN THE PROOF AND THE CARD.
   *
   * cutMembership is what the enumeration asks once per completion, and
   * championship() is what the matchup card asks once. They are two readings
   * of one breakTie, so on a season with nothing left to play they have to
   * place the same teams: `sure` is the seeds it named, `maybe` is the group
   * it could not cut. They did not, and the gap was the same `resolved` flag
   * read as all-or-nothing. A cut weaker than the board beside it publishes
   * a team as eliminated on one page and tied for second on another. */
  test(`${label}: the cut and the matchup card place the same teams`, () => {
    const api = loadClient();
    const payload = payloadFor(year);
    const games = withPicks(payload, (g) => g.home);
    const overrides = payload.overrides || {};

    const cm = api.B12Race.cutMembership(
      games, overrides, api.B12Race.unplayedNonconf(games));
    const ccg = api.B12Engine.championship(games, overrides);
    const seeds = [ccg.seed1, ccg.seed2].filter(Boolean);
    const pending = ccg.pending || [];
    // A group that fits inside the cut whole is in, not contested: nothing is
    // shared when there are seats for everyone sharing.
    const fits = pending.length > 0 && pending.length <= 2 - seeds.length;

    assert.deepEqual(sorted(Object.keys(cm.sure)),
                     sorted(seeds.concat(fits ? pending : [])),
                     "the cut and the card name different teams");
    assert.deepEqual(sorted(Object.keys(cm.maybe)),
                     sorted(fits ? [] : pending),
                     "the cut and the card disagree about who is still tied");
    // Nobody is in two places at once, and a finished season leaves no berth
    // unaccounted for: every seat is either filled or contested.
    for (const t of Object.keys(cm.sure)) {
      assert.ok(!cm.maybe[t], `${t} is both proven in and still tied`);
    }
    assert.ok(Object.keys(cm.sure).length +
              Object.keys(cm.maybe).length >= 2,
              "a finished season accounts for both berths");
  });

  test(`${label}: odds fill two berths and never contradict a proof`, () => {
    const api = loadClient();
    const payload = payloadFor(year);
    // Pick nothing extra: the season as it really stands, simulated.
    const games = payload.games.map((g) => ({ ...g }));
    const m = api.B12Race.computeSync(games, { payload, nSims: 400 });

    const teams = Object.keys(m.statuses);
    const total = teams.reduce((a, t) => a + (m.probs[t] ?? 0), 0);

    /* Two seats, so the column sums to two. EXACTLY, at any number of
       simulations, because that is a property of the estimator and not of
       the sample: every simulated season hands out exactly two berths, one
       apiece to the teams the procedure seated and the rest divided among
       the teams it could not separate. The mean of a column of twos is two,
       so there is no sampling noise here for a tolerance to absorb.

       This read `< 0.06`, under a guard, and needed both. A contested berth
       used to be worth a flat half to everyone holding it, so eight teams
       contesting two seats counted four, and the slack in this assertion was
       the only thing standing between that and a red test. */
    assert.ok(Math.abs(total - 2) < 1e-9,
              `berths should sum to 2, got ${total}`);

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

/* THE DECIDED SEASON HERE IS 2025, not the live one picked out to the end.
   Picking a season to the end does not always decide it: an unbroken tie
   leaves teams the ladder could not place, and the case below is the live
   season doing exactly that. 2025 was played to a champion, so it is the
   fixture for "nothing is uncertain" that a what-if cannot be. */
test("a decided season reads as Settled, an open one does not", () => {
  const api = loadClient();
  const p2025 = payloadFor(2025);
  const decided = api.B12Race.computeSync(
    p2025.games.map((g) => ({ ...g })), { payload: p2025 });
  assert.equal(decided.remaining, 0, "2025 has nothing left to play");
  assert.equal(decided.unbroken, null, "2025 named its title game");
  assert.equal(decided.chaos.label, "Settled");
  assert.equal(decided.chaos.score, 0);

  const payload = payloadFor(null);
  const open = api.B12Race.computeSync(
    payload.games.map((g) => ({ ...g })), { payload, nSims: 300 });
  assert.ok(open.chaos.score > decided.chaos.score,
            "an unplayed season cannot be as settled as a finished one");
});

/* A season with no game left to play and no championship-game field.
 *
 * Every 2026 game picked for the home side leaves eight teams at 5-4 in one
 * tie group. The ladder seeds Texas Tech out of it and then runs out of data:
 * seven teams share second place, and the title game has one name in it.
 * race.js used to read row 1 of that group off the standings and call it a
 * clinch at probability 1, which is the alphabet with a green badge on it,
 * and eliminate the six teams filed behind it.
 *
 * PINNED AS A FILE, not picked out of the live season, because the live
 * season stops producing this the week anything else is played. That is how
 * it arrived: as a failure in this suite on a commit that had touched none of
 * the code, on the first rebuild after week 3. A fixture makes the case a
 * thing the suite owns rather than a thing the calendar lends it.
 */
test("an unbroken tie clinches nobody the ladder could not name", () => {
  const api = loadClient();
  const fx = JSON.parse(fs.readFileSync(
    path.join(path.dirname(fileURLToPath(import.meta.url)),
              "unbroken_tie_fixture.json"), "utf8"));
  const games = fx.games.map((g) => ({ ...g }));
  const payload = { games: fx.games, overrides: fx.overrides,
                    favorites: {}, models: [] };

  const ccg = api.B12Engine.championship(games, fx.overrides);
  assert.equal(ccg.seed1, fx.expect.seed1);
  assert.equal(ccg.seed2, fx.expect.seed2);
  assert.equal(ccg.resolved, false);
  assert.deepEqual(sorted(ccg.pending), sorted(fx.expect.pending));

  const m = api.B12Race.computeSync(games, { payload });
  assert.equal(m.proof, "settled");
  assert.equal(m.remaining, 0);
  assert.equal(m.nSims, 0);

  const teams = Object.keys(m.statuses);
  const clinched = teams.filter((t) => m.statuses[t] === "clinched");
  const alive = teams.filter((t) => m.statuses[t] === "alive");
  assert.deepEqual(sorted(clinched), [fx.expect.seed1],
                   "only the seed the ladder actually made");
  assert.deepEqual(sorted(alive), sorted(fx.expect.pending),
                   "the tie the ladder could not cut is what is left alive");

  // One berth, seven ways, because the step that would split them is a
  // SportSource rating or a coin toss and the card holds neither.
  assert.equal(m.probs[fx.expect.seed1], 1);
  for (const t of fx.expect.pending) {
    assert.ok(Math.abs(m.probs[t] - 1 / 7) < 1e-12,
              `${t} should hold a seventh of a berth, holds ${m.probs[t]}`);
  }
  const total = teams.reduce((a, t) => a + m.probs[t], 0);
  assert.ok(Math.abs(total - 2) < 1e-9, `berths sum to ${total}`);

  // And the card says so in words, rather than leaving a reader to infer it
  // from seven bars at 14%.
  assert.equal(m.unbroken.seats, 1);
  assert.deepEqual(sorted(m.unbroken.teams), sorted(fx.expect.pending));
  assert.match(m.unbroken.note, /cannot separate them/);

  // A season whose title game has one name in it is not a settled race,
  // whatever the schedule says.
  assert.notEqual(m.chaos.label, "Settled");

  /* And the proof primitive says it too, which is the half that used to
     disagree. cutMembership read breakTie's `resolved` as all-or-nothing, so
     this group arrived as eight `maybe` and no `sure`: the ladder HAD seeded
     Texas Tech and the cut threw that away, leaving the enumeration unable to
     prove a clinch the matchup card was already printing. */
  const cm = api.B12Race.cutMembership(
    games, fx.overrides, api.B12Race.unplayedNonconf(games));
  assert.deepEqual(sorted(Object.keys(cm.sure)), [fx.expect.seed1]);
  assert.deepEqual(sorted(Object.keys(cm.maybe)), sorted(fx.expect.pending));
});

/* A contested berth, split by the size of the tie.
 *
 * It was a flat 0.5 in both simulations, which is the right answer for
 * exactly one shape of tie: two teams contesting one seat. Every other shape
 * it either over- or under-pays, and it over-pays in the direction that
 * matters, handing out berths that do not exist. The settled card had already
 * been divided properly since it learned to report an unbroken tie at all;
 * this is the same arithmetic reaching the two places that estimate.
 */
test("a contested berth is split by the size of the tie, not in half", () => {
  const B = loadClient().B12Race.berthShare;
  assert.equal(B(1, 7), 1 / 7, "seven teams, one seat");
  assert.equal(B(2, 8), 0.25, "eight teams, two seats");
  assert.equal(B(1, 2), 0.5, "the one shape the flat half got right");
  // A group that fits inside the cut whole is in, not contested: two teams
  // sharing first place both play.
  assert.equal(B(2, 2), 1, "never more than a whole berth each");
  assert.equal(B(2, 1), 1);
  assert.equal(B(0, 5), 0, "no seat left is no share");
  assert.equal(B(1, 0), 0, "no group is no share");

  // The property the column sum rests on: contested seats are handed out in
  // full, and never more than in full.
  for (let seats = 1; seats <= 2; seats += 1) {
    for (let group = seats + 1; group <= 16; group += 1) {
      assert.ok(Math.abs(B(seats, group) * group - seats) < 1e-12,
                `${group} teams sharing ${seats} seats does not total ${seats}`);
    }
  }
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

// The Kansas City bracket challenge, on the real 2025-26 men's bracket.
//
// The bracket is the publisher's own file (tools/make-hoops-fixture.py runs
// bb_pickem.write_bracket), imported the way production imports it. Before
// the lock: a bracket is checked for coherence, saved whole, read back, and
// hidden from the board. After it: nothing can be written, and the board
// scores every bracket against the real results, which this test also
// scores independently.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE } from "../src/cookies.js";
import { importBracket, invalid, score } from "../src/bracket.js";
import * as L from "../src/league.js";
import { makeEnv, seedUser, NOW, HOUR } from "./helpers/env.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const REAL = JSON.parse(readFileSync(
  join(HERE, "fixtures", "hoops-2026", "bracket.json"), "utf8"));
const ORIGIN = "https://big12ology.com";

/** The real bracket as a file served for this season, with the lock moved
 * and the results as given. */
function serve(env, { lockAt, results }) {
  const season = L.seasonOf(env, "mbb");
  const file = JSON.stringify({ ...REAL, season, lock_at: lockAt, results });
  const url = `${env.RAW_ORIGIN}/basketball/pickem/mbb/${season}/bracket.json`;
  globalThis.fetch = async (u) => (String(u) === url
    ? new Response(file, { status: 200 })
    : new Response("", { status: 404 }));
  return season;
}

async function signedIn(env, id) {
  seedUser(env, id, { name: `Player ${id}` });
  const s = await session.create(env, id);
  return `${SESSION_COOKIE}=${encodeURIComponent(s.raw)}`;
}

function call(env, path, { method = "GET", cookie, body } = {}) {
  const headers = { Accept: "application/json" };
  if (cookie) headers.Cookie = cookie;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers.Origin = ORIGIN;
  }
  return worker.fetch(new Request(`${ORIGIN}${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  }), env, {});
}

/** The real tournament as a bracket: every result picked right. */
const PERFECT = REAL.games.map((_, gi) => REAL.results[String(gi)]);
/** The chalk: the better seed in every game. */
function chalk(b) {
  const picks = [];
  b.games.forEach((pair, gi) => {
    const side = pair.map((x) => (typeof x === "number" ? x
                                  : b.seeds.indexOf(picks[Number(x.slice(1))]) + 1));
    picks[gi] = b.seeds[Math.min(...side) - 1];
  });
  return picks;
}

test("a bracket must be a bracket", () => {
  assert.equal(invalid(REAL, PERFECT), null, "the real tournament is coherent");
  assert.equal(invalid(REAL, chalk(REAL)), null, "so is the chalk");
  const half = PERFECT.map((p, gi) => (gi < 8 ? p : null));
  assert.equal(invalid(REAL, half), null, "half a bracket may be saved");
  const wrong = PERFECT.slice();
  wrong[0] = REAL.seeds[0];                 // the 1 seed in a 9-16 game
  assert.match(invalid(REAL, wrong), /cannot be in game 1/);
  // Game 5 is fed by game 1: clear game 1, and its winner can no longer be
  // sent on through game 5.
  const orphan = PERFECT.slice();
  orphan[4] = orphan[0];
  orphan[0] = null;
  assert.match(invalid(REAL, orphan), /cannot be in game 5/);
  assert.match(invalid(REAL, PERFECT.slice(0, 14)), /15 games/);
});

test("scoring: points by round, and what is still possible", () => {
  const all = score(REAL, PERFECT, REAL.results);
  assert.deepEqual(all, { score: 60, possible: 60, perfect: 60 });
  const none = score(REAL, PERFECT, {});
  assert.deepEqual(none, { score: 0, possible: 60, perfect: 60 });
  // After the first round only: the chalk lost what upsets cost it, and a
  // team knocked out takes every later point it carried with it.
  const first = Object.fromEntries([0, 1, 2, 3].map((g) => [String(g),
                                     REAL.results[String(g)]]));
  const c = score(REAL, chalk(REAL), first);
  const right = [0, 1, 2, 3].filter((g) => chalk(REAL)[g] === first[String(g)]).length;
  assert.equal(c.score, right);
  assert.ok(c.possible < 60 || right === 4);
});

test("the bracket challenge, before and after the lock", async () => {
  const env = makeEnv();
  const real = globalThis.fetch;
  try {
    // Before: published with the lock a day out and no results.
    serve(env, { lockAt: NOW() + 24 * HOUR, results: {} });
    const season = L.seasonOf(env, "mbb");
    const imp = await importBracket(env, "mbb", season);
    assert.equal(imp.ok, true, JSON.stringify(imp));

    const a = await signedIn(env, "u1");
    const b = await signedIn(env, "u2");
    assert.equal((await call(env, "/api/bracket?league=cfb")).status, 400);
    const bad = await call(env, "/api/bracket", { method: "PUT", cookie: a,
      body: { league: "mbb", picks: PERFECT.map((p, i) => (i === 0 ? REAL.seeds[0] : p)) } });
    assert.equal(bad.status, 400);
    const save = await call(env, "/api/bracket", { method: "PUT", cookie: a,
      body: { league: "mbb", picks: PERFECT } });
    assert.equal(save.status, 200, await save.clone().text());
    await call(env, "/api/bracket", { method: "PUT", cookie: b,
      body: { league: "mbb", picks: chalk(REAL) } });

    const mine = await (await call(env, "/api/bracket?league=mbb",
                                   { cookie: a })).json();
    assert.equal(mine.locked, false);
    assert.deepEqual(mine.entry.picks, PERFECT);
    const early = await (await call(env, "/api/bracket/board?league=mbb")).json();
    assert.deepEqual([early.entrants, early.rows.length], [2, 0],
                     "brackets are hidden until the lock");

    // After: the lock passes and the results arrive.
    serve(env, { lockAt: NOW() - HOUR, results: REAL.results });
    await importBracket(env, "mbb", season);
    const late = await call(env, "/api/bracket", { method: "PUT", cookie: a,
      body: { league: "mbb", picks: chalk(REAL) } });
    assert.equal(late.status, 409, "nothing is written after the lock");
    assert.throws(() => env.raw.prepare(
      `UPDATE bracket_entries SET picks = '[]' WHERE user_id = 'u1'`).run(),
      /bracket_locked/, "and the database says so too");

    const board = await (await call(env, "/api/bracket/board?league=mbb")).json();
    assert.equal(board.locked, true);
    assert.deepEqual(board.rows.map((r) => [r.user_id, r.score, r.rank]),
      [["u1", 60, 1],
       ["u2", score(REAL, chalk(REAL), REAL.results).score, 2]]);
    assert.equal(board.rows[0].champion, REAL.results["14"]);
  } finally {
    globalThis.fetch = real;
  }
});

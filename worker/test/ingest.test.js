// The publisher's channel.
//
// This endpoint is the only unauthenticated-by-cookie write on the site, and
// the only one a browser never calls. Both facts make it the place where a
// mistake is least likely to be noticed by using the site, so most of what is
// below is about refusal rather than about the happy path.
//
// The signature is the whole control. If it can be skipped, replayed, or
// satisfied by a body other than the one that was signed, then anybody who
// finds the path can rewrite every board on the site — the scores file is the
// input to all of them.

import test from "node:test";
import assert from "node:assert/strict";

import worker from "../src/index.js";
import { MAX_SKEW, SIG_HEADER, TS_HEADER } from "../src/ingest.js";
import { hmac } from "../src/crypto.js";
import { scoresKey, fetchScores } from "../src/scoring.js";
import { forceLock, makeEnv, seedPick, seedUser, seedWeek, HOUR, NOW }
  from "./helpers/env.js";

const SEASON = 2026;
const KEY = "test-ingest-key-not-the-production-one";
const URL_ = "https://big12ology.com/api/ingest/scores";

/** A KV binding that remembers, like the one makeEnv gives SESSIONS. */
function kv() {
  const map = new Map();
  return {
    map,
    async get(k, type) {
      if (!map.has(k)) return null;
      const v = map.get(k);
      if (type !== "json") return v;
      try { return JSON.parse(v); } catch { return null; }
    },
    async put(k, v) { map.set(k, v); },
    async delete(k) { map.delete(k); },
  };
}

function env0(extra = {}) {
  return makeEnv({ SCORES: kv(), SCORES_INGEST_KEY: KEY, ...extra });
}

const scoresFor = (games) => ({ season: SEASON, games });

/** Sign and send, exactly the way tools/publish-scores.sh does. */
async function post(env, doc, { key = KEY, ts = null, body = null,
                                sig = null } = {}) {
  const at = ts ?? Math.floor(Date.now() / 1000);
  const raw = body ?? JSON.stringify(doc);
  const signature = sig ?? await hmac(key, `${at}.${raw}`);
  return worker.fetch(new Request(URL_, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      [TS_HEADER]: String(at),
      [SIG_HEADER]: signature,
    },
    body: raw,
  }), env, {});
}

// --------------------------------------------------------------- the refusals

test("an unsigned post is refused", async () => {
  const env = env0();
  const res = await worker.fetch(new Request(URL_, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scoresFor({})),
  }), env, {});
  assert.equal(res.status, 401);
  assert.equal((await res.json()).error, "unsigned");
  assert.equal(env.SCORES.map.size, 0);
});

test("a signature made with the wrong key is refused", async () => {
  const env = env0();
  const res = await post(env, scoresFor({ 401: [21, 17, true] }),
                         { key: "not-the-key" });
  assert.equal(res.status, 403);
  assert.equal((await res.json()).error, "bad_signature");
  assert.equal(env.SCORES.map.size, 0);
});

// The one that matters most: a valid signature over a DIFFERENT body. This is
// what separates signing the payload from carrying a bearer token, and it is
// the case a token-based design would wave straight through.
test("a good signature does not travel to a different body", async () => {
  const env = env0();
  const honest = JSON.stringify(scoresFor({ 401: [21, 17, true] }));
  const at = Math.floor(Date.now() / 1000);
  const sig = await hmac(KEY, `${at}.${honest}`);
  const tampered = JSON.stringify(scoresFor({ 401: [0, 99, true] }));

  const res = await post(env, null, { ts: at, body: tampered, sig });
  assert.equal(res.status, 403);
  assert.equal((await res.json()).error, "bad_signature");
  assert.equal(env.SCORES.map.size, 0);
});

test("a capture from an hour ago cannot be replayed", async () => {
  const env = env0();
  const old = Math.floor(Date.now() / 1000) - (MAX_SKEW + 60);
  const res = await post(env, scoresFor({ 401: [21, 17, true] }), { ts: old });
  assert.equal(res.status, 401);
  assert.equal((await res.json()).error, "stale");
});

// Both directions. A timestamp far in the future would otherwise buy an
// unbounded replay window to anyone who can set a header.
test("a timestamp from the future is refused too", async () => {
  const env = env0();
  const ahead = Math.floor(Date.now() / 1000) + (MAX_SKEW + 60);
  const res = await post(env, scoresFor({}), { ts: ahead });
  assert.equal(res.status, 401);
  assert.equal((await res.json()).error, "stale");
});

test("a signed but malformed file never reaches the scorer", async () => {
  const env = env0();
  for (const [body, error] of [
    ["not json at all", "bad_json"],
    [JSON.stringify({ games: {} }), "bad_season"],
    [JSON.stringify({ season: SEASON }), "bad_games"],
    // An array is an object to typeof, and scoreWeek would index it by game id
    // and find nothing — a whole week of finals silently recorded as no-result.
    [JSON.stringify({ season: SEASON, games: [] }), "bad_games"],
  ]) {
    const res = await post(env, null, { body });
    assert.equal((await res.json()).error, error, `for body: ${body}`);
    assert.equal(env.SCORES.map.size, 0);
  }
});

test("with no secret set, nothing is accepted", async () => {
  const env = makeEnv({ SCORES: kv() });   // no SCORES_INGEST_KEY
  const res = await post(env, scoresFor({}));
  assert.equal(res.status, 503);
  assert.equal((await res.json()).error, "not_configured");
});

// ------------------------------------------------------------- the happy path

test("a signed file is stored and graded in the same request", async () => {
  const env = env0();
  seedWeek(env, { season: SEASON, week: 3 });
  seedUser(env, "U1", { name: "Reader" });
  // Iowa State -6.5 and they cover: 21-17 against a spread_x2 of -13.
  //
  // Picked BEFORE the lock is forced, because the schema will not have it any
  // other way — a week_locked trigger refuses an insert into a locked week.
  // That is the invariant the pool rests on, so the test bends to it.
  seedPick(env, "U1", SEASON, 3, 401, "home", -13);
  forceLock(env, SEASON, 3, NOW() - HOUR);

  const res = await post(env, scoresFor({
    401: [21, 17, true],
    402: [10, 30, true],
  }));

  assert.equal(res.status, 200);
  const out = await res.json();
  assert.equal(out.ok, true);
  assert.equal(out.season, SEASON);
  assert.equal(out.games, 2);
  assert.equal(out.graded, "ok");
  assert.ok(out.weeks_changed >= 1, "the locked week should have moved");

  // Stored under the season's key, as the exact bytes that were signed.
  const stored = await env.SCORES.get(scoresKey(SEASON), "json");
  assert.deepEqual(stored.games["401"], [21, 17, true]);

  // And actually graded — not merely reported as graded.
  const r = env.raw.prepare(
    `SELECT status, home_points, away_points FROM results
      WHERE season = ? AND week = ? AND game_id = 401`).get(SEASON, 3);
  assert.equal(r.status, "final");
  assert.equal(r.home_points, 21);
});

// The reason scoreAll takes an injected `scores` at all. KV is eventually
// consistent, so a read-back inside the same request may return the previous
// value; grading has to use the bytes that were just verified.
test("grading uses the posted body, not a read-back of KV", async () => {
  const env = env0();
  seedWeek(env, { season: SEASON, week: 3 });
  forceLock(env, SEASON, 3, NOW() - HOUR);

  // A stale value already sitting in KV, and a put that refuses to update it —
  // the worst case the eventual-consistency note describes.
  await env.SCORES.put(scoresKey(SEASON),
                       JSON.stringify(scoresFor({ 401: [0, 99, true] })));
  env.SCORES.put = async () => {};

  const res = await post(env, scoresFor({ 401: [21, 17, true] }));
  assert.equal(res.status, 200);

  const r = env.raw.prepare(
    `SELECT home_points, away_points FROM results
      WHERE season = ? AND week = ? AND game_id = 401`).get(SEASON, 3);
  assert.equal(r.home_points, 21, "graded the stale KV copy instead of the post");
  assert.equal(r.away_points, 17);
});

// ------------------------------------------------------------- the read path

test("fetchScores prefers KV and falls back to Pages", async () => {
  const env = env0();
  await env.SCORES.put(scoresKey(SEASON),
                       JSON.stringify(scoresFor({ 401: [21, 17, true] })));

  globalThis.fetch = async () => { throw new Error("Pages should not be asked"); };
  const fromKv = await fetchScores(env, SEASON);
  assert.deepEqual(fromKv.games["401"], [21, 17, true]);

  // Nothing published for that season yet: the Pages copy is what answers.
  globalThis.fetch = async () =>
    new Response(JSON.stringify(scoresFor({ 999: [1, 2, true] })), { status: 200 });
  const fallback = await fetchScores(env, 2099);
  assert.deepEqual(fallback.games["999"], [1, 2, true]);
});

// A namespace that holds something unparseable must not take the sweep down —
// it falls back, which is the whole reason the mock returns null rather than
// throwing.
test("an unreadable KV value falls back rather than throwing", async () => {
  const env = env0();
  await env.SCORES.put(scoresKey(SEASON), "{ this is not json");
  globalThis.fetch = async () =>
    new Response(JSON.stringify(scoresFor({ 7: [3, 0, true] })), { status: 200 });
  const got = await fetchScores(env, SEASON);
  assert.deepEqual(got.games["7"], [3, 0, true]);
});

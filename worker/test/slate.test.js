// Importing a published week, and the limiter.
//
// The import is the one place the frozen-line trigger is exercised by real
// code rather than by a test poking the table directly, which is the case
// that matters: a second Tuesday run must be able to fill in a line that was
// missing and must not be able to move one that was not.

import test from "node:test";
import assert from "node:assert/strict";
import { importWeek, currentWeek, readSlate } from "../src/slate.js";
import { take, LIMITS } from "../src/ratelimit.js";
import { makeEnv, seedWeek, forceLock, NOW, HOUR } from "./helpers/env.js";

/** Stand in for the Pages origin for the length of one call. */
function serving(body, status = 200) {
  const real = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    typeof body === "string" ? body : JSON.stringify(body),
    { status, headers: { "Content-Type": "application/json" } });
  return () => { globalThis.fetch = real; };
}

// One clock reading for every fixture this file builds. Each WEEK() used to
// take its own NOW(), so two fixtures in one test could straddle a second
// boundary on a slow runner — and then the SECOND import died on
// weeks_lock_monotonic (its lock_at was a second "later") before ever
// reaching the trigger the test was about. The times only need to be in the
// future and consistent, so they are read once.
const T0 = NOW();
const WEEK = (over = {}) => ({
  season: 2026, week: 4, status: "published",
  lock_at: T0 + 48 * HOUR, game_count: 2, pickable_count: 1,
  games: [
    { game_id: 901, home: "Iowa State", away: "Kansas",
      kickoff_at: T0 + 48 * HOUR, spread_x2: -13, spread_raw: -6.4, books: 5 },
    { game_id: 902, home: "TCU", away: "Utah",
      kickoff_at: T0 + 50 * HOUR, spread_x2: null },
  ],
  ...over,
});

test("a published week imports", async () => {
  const env = makeEnv();
  const stop = serving(WEEK());
  try {
    const r = await importWeek(env, 2026, 4);
    assert.equal(r.ok, true);
    assert.equal(r.games, 2);
  } finally { stop(); }

  const w = env.raw.prepare(
    "SELECT * FROM weeks WHERE season=2026 AND week=4").get();
  assert.equal(w.pickable_count, 1);
  assert.equal(env.raw.prepare(
    "SELECT COUNT(*) c FROM slate_games WHERE week=4").get().c, 2);
});

test("an unchanged file is not re-imported", async () => {
  const env = makeEnv();
  const week = WEEK();
  let stop = serving(week);
  try { await importWeek(env, 2026, 4); } finally { stop(); }
  stop = serving(week);
  try {
    const r = await importWeek(env, 2026, 4);
    assert.equal(r.unchanged, true);
  } finally { stop(); }
});

test("a line that was missing can be filled in later", async () => {
  // The Tuesday-with-no-market case: a game published unpickable becomes
  // playable by Thursday. This is the one transition the frozen trigger
  // permits, because nobody could have acted on a line that did not exist.
  const env = makeEnv();
  let stop = serving(WEEK());
  try { await importWeek(env, 2026, 4); } finally { stop(); }
  assert.equal(env.raw.prepare(
    "SELECT spread_x2 s FROM slate_games WHERE game_id=902").get().s, null);

  const later = WEEK();
  later.games[1].spread_x2 = 5;
  later.pickable_count = 2;
  stop = serving(later);
  try {
    const r = await importWeek(env, 2026, 4);
    assert.equal(r.ok, true);
  } finally { stop(); }
  assert.equal(env.raw.prepare(
    "SELECT spread_x2 s FROM slate_games WHERE game_id=902").get().s, 5);
});

test("a line that already exists cannot be moved, and takes the batch with it",
  async () => {
    const env = makeEnv();
    let stop = serving(WEEK());
    try { await importWeek(env, 2026, 4); } finally { stop(); }

    // Somebody re-publishes with the number changed. The database refuses.
    const moved = WEEK();
    moved.games[0].spread_x2 = -20;
    moved.games[1].spread_x2 = 99;   // would otherwise have been allowed
    stop = serving(moved);
    try {
      const r = await importWeek(env, 2026, 4);
      assert.equal(r.ok, false);
      assert.match(r.reason, /slate_frozen/);
    } finally { stop(); }

    assert.equal(env.raw.prepare(
      "SELECT spread_x2 s FROM slate_games WHERE game_id=901").get().s, -13);
    // A D1 batch is one transaction, so the permitted change rolled back too.
    // Half an import is worse than none.
    assert.equal(env.raw.prepare(
      "SELECT spread_x2 s FROM slate_games WHERE game_id=902").get().s, null);
  });

test("a missing or malformed file is a reason, not a crash", async () => {
  const env = makeEnv();
  for (const [body, status, why] of [
    ["", 404, /fetch_404/], ["not json", 200, /bad_json/],
    [{ season: 2026 }, 200, /bad_shape/],
  ]) {
    const stop = serving(body, status);
    try {
      const r = await importWeek(env, 2026, 4);
      assert.equal(r.ok, false);
      assert.match(r.reason, why);
    } finally { stop(); }
  }
});

test("the current week is the earliest one still open", async () => {
  const env = makeEnv();
  // Distinct ids per week: slate_game_unique(season, game_id) says a game
  // belongs to exactly one week, which is the constraint that makes joining
  // on game_id alone safe everywhere else.
  seedWeek(env, { week: 1, games: [
    { game_id: 101, home: "A", away: "B", spread_x2: -3 }] });
  seedWeek(env, { week: 2, games: [
    { game_id: 201, home: "C", away: "D", spread_x2: -3 }] });
  forceLock(env, 2026, 1);
  assert.equal(await currentWeek(env, 2026), 2);
  forceLock(env, 2026, 2);
  // Everything played: the last one, not null, so the board has something.
  assert.equal(await currentWeek(env, 2026), 2);
});

// ------------------------------------------------------------- limiter

test("a limit counts, then refuses, then resets with the window", async () => {
  const env = makeEnv();
  const now = NOW();
  for (let i = 0; i < LIMITS.rename.max; i++) {
    const r = await take(env, "rename", "u1", now);
    assert.equal(r.ok, true, `refused at attempt ${i + 1}`);
  }
  const over = await take(env, "rename", "u1", now);
  assert.equal(over.ok, false);
  assert.ok(over.retryAfter > 0);

  // The next window is a clean slate — this is a fixed window, deliberately,
  // and saying so is better than being quietly approximate.
  const next = await take(env, "rename", "u1", now + LIMITS.rename.window);
  assert.equal(next.ok, true);
});

test("limits are per key", async () => {
  const env = makeEnv();
  const now = NOW();
  for (let i = 0; i < LIMITS.rename.max; i++) await take(env, "rename", "u1", now);
  assert.equal((await take(env, "rename", "u1", now)).ok, false);
  assert.equal((await take(env, "rename", "u2", now)).ok, true);
});

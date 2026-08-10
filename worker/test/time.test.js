// The moments things change.
//
// Every other test here holds the clock still and asserts what is true at that
// instant. The deadlines are what this product IS, though — a slate that locks
// at the first kickoff, a game that gives up thirty-six hours after it should
// have finished, a session that ends after thirty days — and a boundary is
// exactly where an off-by-one lives without ever showing itself. A lock that
// fires a second early takes a pick away from somebody who made it in time; a
// second late lets one in after the information was public.
//
// Waiting thirty-six hours is not a test, so each boundary is driven instead:
// one second before, exactly on it, one second after. Where a function takes a
// clock, it is given one. Where it reads its own — the handlers all call
// Date.now() — the DATA is moved instead, which exercises the same code path
// including the default.
//
// The one that could not be tested by moving either is agreement: the handler
// checks the lock in JavaScript and the trigger checks it again in SQL, and if
// those two disagree by a second there is a window where a request is accepted
// and then aborted, or worse, accepted when it should not be.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import { isLocked } from "../src/slate.js";
import { scoreWeek } from "../src/scoring.js";
import * as session from "../src/session.js";
import { sign, unsign, SESSION_COOKIE } from "../src/cookies.js";
import { take, LIMITS } from "../src/ratelimit.js";
import { makeEnv, seedWeek, seedUser, seedPick, NOW, HOUR }
  from "./helpers/env.js";

const ORIGIN = "https://big12ology.com";
const SEASON = 2026;
const T = 1_800_000_000;          // a fixed instant, so nothing drifts

// ------------------------------------------------------------------ the lock

test("the lock fires ON the second, not around it", () => {
  const w = { lock_at: T };
  assert.equal(isLocked(w, T - 1), false, "locked a second early");
  assert.equal(isLocked(w, T), true, "not locked on the second itself");
  assert.equal(isLocked(w, T + 1), true, "not locked a second later");
});

test("a week with no lock time is never locked", () => {
  // A slate published with every kickoff still unannounced has lock_at NULL.
  // Treating that as "locked" would close a week nobody could pick; treating
  // it as an instant in 1970 would do the same.
  assert.equal(isLocked({ lock_at: null }, T), false);
  assert.equal(isLocked(null, T), false);
  assert.equal(isLocked(undefined, T), false);
});

test("the handler and the trigger agree about the same second", async () => {
  // isLocked() is `lock_at <= now` in JavaScript; picks_locked_insert is
  // `lock_at <= unixepoch()` in SQL. If those ever drift apart there is a
  // window where the cheap check waves a request through and the batch aborts
  // — or, the direction that matters, where the handler refuses and the
  // trigger would have allowed.
  for (const offset of [-2, -1, 0, 1, 2]) {
    const env = makeEnv();
    const at = NOW() + offset;
    seedWeek(env, { season: SEASON, week: 1, lockAt: at, games: [
      { game_id: 101, home: "Utah", away: "BYU", spread_x2: -7 }] });
    seedUser(env, "u", { name: "Punctual" });

    const w = env.raw.prepare(
      `SELECT lock_at FROM weeks WHERE season = ? AND week = 1`).get(SEASON);
    const handlerSays = isLocked(w, NOW());

    let triggerRefused = false;
    try {
      seedPick(env, "u", SEASON, 1, 101, "home", -7);
    } catch (e) {
      triggerRefused = /week_locked/.test(String(e.message || e));
    }
    assert.equal(handlerSays, triggerRefused,
      `at lock_at ${offset >= 0 ? "+" : ""}${offset}s the handler said ` +
      `${handlerSays ? "locked" : "open"} and the trigger ` +
      `${triggerRefused ? "refused" : "accepted"}`);
  }
});

test("a pick lands a second before the lock and is refused a second after",
  async () => {
    const env = makeEnv();
    seedWeek(env, { season: SEASON, week: 1, lockAt: NOW() + 60, games: [
      { game_id: 101, home: "Utah", away: "BYU", spread_x2: -7 }] });
    seedUser(env, "u", { name: "Punctual" });
    const s = await session.create(env, "u");
    const cookie = `${SESSION_COOKIE}=${encodeURIComponent(s.raw)}`;
    const put = () => worker.fetch(new Request(`${ORIGIN}/api/picks`, {
      method: "PUT",
      headers: { Cookie: cookie, Origin: ORIGIN,
                 "Content-Type": "application/json" },
      body: JSON.stringify({ season: SEASON, week: 1, picks: { 101: "home" } }),
    }), env, {});

    assert.equal((await put()).status, 200, "refused before the lock");

    env.raw.prepare(`UPDATE weeks SET lock_at = ? WHERE season = ? AND week = 1`)
      .run(NOW() - 1, SEASON);
    const after = await put();
    assert.equal(after.status, 409, "accepted after the lock");
    // And the card it hands back is what the database holds, so the page can
    // repaint from the truth rather than from what the reader was mid-way
    // through choosing.
    const body = await after.json();
    assert.equal(body.error, "locked");
    assert.ok(body.picks, "the 409 did not return the picks as they stood");
  });

// ------------------------------------------------------------- the 36h void

test("a game is not void at thirty-six hours, and is at thirty-six and one",
  async () => {
    // The clock is the only thing that can decide this: absence from the
    // scores file is deliberately NOT a trigger, because a truncated file
    // would void a whole week of games that had not kicked off yet.
    const kickoff = T - 40 * HOUR;
    const mk = () => {
      const env = makeEnv();
      seedWeek(env, { season: SEASON, week: 1, lockAt: kickoff, games: [
        { game_id: 101, home: "Utah", away: "BYU", spread_x2: -7,
          kickoff_at: kickoff }] });
      return env;
    };
    const at = async (now) => {
      const env = mk();
      await scoreWeek(env, SEASON, 1, { games: {} }, now);
      const r = env.raw.prepare(
        `SELECT status FROM results WHERE game_id = 101`).get();
      return r ? r.status : "no row";
    };

    assert.equal(await at(kickoff + 36 * HOUR - 1), "no row",
      "voided before the window was up");
    assert.equal(await at(kickoff + 36 * HOUR), "no row",
      "voided ON the thirty-six hour mark; the rule is strictly past it");
    assert.equal(await at(kickoff + 36 * HOUR + 1), "void",
      "still waiting a second past the window");
  });

test("a result that arrives late beats the void", async () => {
  const kickoff = T - 40 * HOUR;
  const env = makeEnv();
  seedWeek(env, { season: SEASON, week: 1, lockAt: kickoff, games: [
    { game_id: 101, home: "Utah", away: "BYU", spread_x2: -7,
      kickoff_at: kickoff }] });
  await scoreWeek(env, SEASON, 1, { games: {} }, kickoff + 37 * HOUR);
  assert.equal(env.raw.prepare(
    `SELECT status FROM results WHERE game_id = 101`).get().status, "void");

  // The scores file catches up. A void is not a tombstone.
  await scoreWeek(env, SEASON, 1, { games: { 101: [28, 10, true] } },
                  kickoff + 40 * HOUR);
  const r = env.raw.prepare(
    `SELECT status, ats, revision FROM results WHERE game_id = 101`).get();
  assert.equal(r.status, "final", "a late final did not replace the void");
  assert.equal(r.ats, "home");
  assert.ok(r.revision > 1, "the correction was not recorded as a revision");
});

// ------------------------------------------------------- the scoring guard

test("a week is never scored while it is still open", async () => {
  // The guard exists so an early final cannot reveal anything about a week
  // people are still picking — including, through the consensus, what
  // everybody else has chosen.
  const env = makeEnv();
  seedWeek(env, { season: SEASON, week: 1, lockAt: T, games: [
    { game_id: 101, home: "Utah", away: "BYU", spread_x2: -7,
      kickoff_at: T }] });
  const scores = { games: { 101: [28, 10, true] } };

  const early = await scoreWeek(env, SEASON, 1, scores, T - 1);
  assert.equal(early.skipped, "not_locked", "scored a second before the lock");
  assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM results`).get().n, 0);

  const onTime = await scoreWeek(env, SEASON, 1, scores, T);
  assert.equal(onTime.skipped, undefined, "refused to score on the second");
  assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM results`).get().n, 1);
});

// ------------------------------------------------------------ the sessions

test("a session dies on its expiry second, not around it", async () => {
  const env = makeEnv();
  seedUser(env, "u", { name: "Someone" });
  const s = await session.create(env, "u");

  // Expiry is checked as `expires_at <= now`, so the second it names is over.
  env.raw.prepare(`UPDATE sessions SET expires_at = ? WHERE sid_hash = ?`)
    .run(NOW() + 2, s.hash);
  assert.ok(await session.read(env, s.raw, { strict: true }),
    "died before its time");

  env.raw.prepare(`UPDATE sessions SET expires_at = ? WHERE sid_hash = ?`)
    .run(NOW(), s.hash);
  assert.equal(await session.read(env, s.raw, { strict: true }), null,
    "outlived its expiry second");
});

test("a session is re-issued only inside the renewal window", () => {
  // Sliding expiry on every request would spend the KV free tier's daily
  // budget on one busy Saturday, so it renews only near the end.
  const now = Math.floor(Date.now() / 1000);
  assert.equal(session.stale(now + session.RENEW_WITHIN + 60), false,
    "renewed a session with weeks left");
  assert.equal(session.stale(now + session.RENEW_WITHIN - 60), true,
    "did not renew a session about to expire");
  assert.equal(session.stale(null), false, "renewed a session with no expiry");
});

// --------------------------------------------------------- the state cookie

test("the OAuth state cookie expires from inside its own signature",
  async () => {
    // Max-Age is a request to the browser. A replayed cookie has to be
    // refused here, which is why exp is signed rather than merely set.
    const KEY = "time-test-key";
    const live = await sign(KEY, { state: "a" }, 60);
    assert.ok(await unsign(KEY, live), "a fresh state cookie was refused");
    const dead = await sign(KEY, { state: "a" }, -1);
    assert.equal(await unsign(KEY, dead), null, "an expired cookie verified");
    const edge = await sign(KEY, { state: "a" }, 0);
    assert.equal(await unsign(KEY, edge), null,
      "a cookie expiring this very second still verified");
  });

// ------------------------------------------------------- the limiter window

test("a rate limit window rolls over and forgets", async () => {
  const env = makeEnv();
  const { max, window } = LIMITS.rename;
  const start = Math.floor(T / window) * window;

  for (let i = 0; i < max; i++) {
    assert.ok((await take(env, "rename", "u", start + 1)).ok,
      `attempt ${i + 1} of ${max} was refused inside the window`);
  }
  assert.equal((await take(env, "rename", "u", start + 2)).ok, false,
    "the limit did not bite");
  // One second before the next window: still refused.
  assert.equal((await take(env, "rename", "u", start + window - 1)).ok, false,
    "the window ended early");
  // And the second it turns over: a clean slate.
  assert.equal((await take(env, "rename", "u", start + window)).ok, true,
    "the window did not roll over on its own boundary");
});

test("a refusal says how long to wait, and the wait is right", async () => {
  const env = makeEnv();
  const { max, window } = LIMITS.login;
  const start = Math.floor(T / window) * window;
  for (let i = 0; i < max; i++) await take(env, "login", "u", start + 10);
  const no = await take(env, "login", "u", start + 10);
  assert.equal(no.ok, false);
  assert.equal(no.retryAfter, window - 10,
    "retry_after does not land on the window boundary");
  assert.ok((await take(env, "login", "u", start + 10 + no.retryAfter)).ok,
    "waiting exactly as long as it asked was not enough");
});

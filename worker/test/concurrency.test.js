// Two things happening at once.
//
// A Worker is single-threaded, so nothing here is about threads: the races
// that exist are interleavings at await boundaries, where a handler reads
// something, yields, and writes based on what it read after somebody else has
// changed it. Promise.all reproduces exactly that, which makes these real
// rather than theatrical.
//
// D1 serialises writes, so a single statement is safe on its own. What is not
// automatically safe is a handler that checks a condition in one query and
// acts on it in another — the cheap lock check before the batch, the identity
// lookup before the account is created, the rate limiter's increment and its
// read. Each of those is a place where the second request can arrive between
// the two halves of the first.
//
// The failures would be quiet and specific: two accounts for one person, a
// card half from one submission and half from another, a pick landing after
// the deadline. None of them raise an error at the time.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as api from "../src/api.js";
import * as session from "../src/session.js";
import { take, LIMITS } from "../src/ratelimit.js";
import { SESSION_COOKIE } from "../src/cookies.js";
import {
  makeEnv, seedWeek, seedUser, seedSurvivorPick, forceLock, NOW, HOUR,
} from "./helpers/env.js";

const ORIGIN = "https://big12ology.com";
const SEASON = 2026;

async function signedIn(env, id) {
  const s = await session.create(env, id);
  return `${SESSION_COOKIE}=${encodeURIComponent(s.raw)}`;
}

const put = (env, path, cookie, body) => worker.fetch(
  new Request(`${ORIGIN}${path}`, {
    method: "PUT",
    headers: { Cookie: cookie, Origin: ORIGIN,
               "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }), env, {});

function week(env, { week = 1, locked = false } = {}) {
  seedWeek(env, { season: SEASON, week, games: [
    { game_id: week * 100 + 1, home: "Utah", away: "BYU", spread_x2: -7,
      b12: "both" },
    { game_id: week * 100 + 2, home: "Kansas", away: "TCU", spread_x2: 5,
      b12: "both" },
    { game_id: week * 100 + 3, home: "Baylor", away: "Houston", spread_x2: -3,
      b12: "both" },
  ] });
  if (locked) forceLock(env, SEASON, week);
}

// ------------------------------------------------------------------ picks

test("two tabs saving different cards leave one card, not a blend", async () => {
  // putPicks is a whole-slate replace in one batch precisely so this cannot
  // half-happen. Two submissions arriving together must leave the card
  // entirely as one of them meant it, never three games from one and one from
  // the other — a mixture is a card the player never made and cannot explain.
  const env = makeEnv();
  week(env);
  seedUser(env, "u", { name: "Two Tabs" });
  const cookie = await signedIn(env, "u");

  const allHome = { season: SEASON, week: 1,
    picks: { 101: "home", 102: "home", 103: "home" } };
  const allAway = { season: SEASON, week: 1,
    picks: { 101: "away", 102: "away", 103: "away" } };

  const res = await Promise.all([
    put(env, "/api/picks", cookie, allHome),
    put(env, "/api/picks", cookie, allAway),
    put(env, "/api/picks", cookie, allHome),
  ]);
  for (const r of res) assert.ok(r.status < 500, `putPicks returned ${r.status}`);

  const sides = env.raw.prepare(
    `SELECT side FROM picks WHERE user_id = 'u' AND season = ? AND week = 1`)
    .all(SEASON).map((r) => r.side);
  assert.equal(sides.length, 3, `expected one full card, got ${sides.length}`);
  assert.equal(new Set(sides).size, 1,
    `the card is a blend of two submissions: ${sides.join(",")}`);
});

test("a card racing the lock lands whole or not at all", async () => {
  // The handler checks the lock cheaply and the triggers check it again
  // inside the batch, which is the pair that matters: the cheap check can be
  // true when it runs and false by the time the write lands.
  const env = makeEnv();
  week(env);
  seedUser(env, "u", { name: "Late" });
  const cookie = await signedIn(env, "u");

  const submissions = [];
  for (let i = 0; i < 6; i++) {
    submissions.push(put(env, "/api/picks", cookie,
      { season: SEASON, week: 1, picks: { 101: "home", 102: "home", 103: "home" } }));
  }
  // Flip the lock while they are in flight.
  forceLock(env, SEASON, 1);
  const res = await Promise.all(submissions);

  for (const r of res) assert.ok(r.status < 500, `crossing the lock 500'd`);
  const n = env.raw.prepare(
    `SELECT COUNT(*) n FROM picks WHERE user_id = 'u' AND week = 1`)
    .get().n;
  assert.ok(n === 0 || n === 3,
    `a partial card survived the lock: ${n} of 3 games`);

  // And nothing was written after the deadline.
  const late = env.raw.prepare(
    `SELECT COUNT(*) n FROM picks p JOIN weeks w
       ON w.season = p.season AND w.week = p.week
      WHERE p.created_at > w.lock_at`).get().n;
  assert.equal(late.n ?? late, 0, "a pick was created after the lock");
});

test("the same team twice, submitted together, is still refused once",
  async () => {
    // survivor_no_reuse is a trigger rather than a handler check, so the
    // second write sees the first one committed however close together they
    // arrive.
    const env = makeEnv();
    week(env, { week: 1 });
    week(env, { week: 2 });
    seedUser(env, "u", { name: "Doubler" });
    const cookie = await signedIn(env, "u");

    const res = await Promise.all([
      put(env, "/api/survivor/pick", cookie,
          { week: 1, game_id: 101, team: "Utah" }),
      put(env, "/api/survivor/pick", cookie,
          { week: 2, game_id: 201, team: "Utah" }),
    ]);
    const ok = res.filter((r) => r.status === 200).length;
    assert.equal(ok, 1, `${ok} of two same-team picks were accepted`);
    assert.equal(env.raw.prepare(
      `SELECT COUNT(*) n FROM survivor_picks WHERE user_id = 'u'
        AND team = 'Utah'`).get().n, 1, "Utah was spent twice");
  });

// -------------------------------------------------------------- accounts

test("one person signing in twice at once gets one account", async () => {
  // resolveIdentity looks the identity up and creates an account when it
  // finds nothing. Two callbacks for the same subject can both look, both
  // find nothing, and both create — leaving one person with two accounts and
  // whichever session they hold pointing at an empty one.
  const env = makeEnv();
  const both = await Promise.all([
    api.resolveIdentity(env, "google", "same-subject", { ip: null }),
    api.resolveIdentity(env, "google", "same-subject", { ip: null }),
    api.resolveIdentity(env, "google", "same-subject", { ip: null }),
  ]);
  for (const r of both) assert.ok(!r.error, `resolveIdentity: ${r.error}`);

  const users = env.raw.prepare(`SELECT COUNT(*) n FROM users`).get().n;
  const ids = env.raw.prepare(`SELECT COUNT(*) n FROM identities`).get().n;
  assert.equal(users, 1, `${users} accounts created for one subject`);
  assert.equal(ids, 1, `${ids} identity rows for one subject`);
  assert.equal(new Set(both.map((b) => b.userId)).size, 1,
    "the same person was handed two different account ids");
});

test("two names claimed at once cannot both be taken", async () => {
  const env = makeEnv();
  seedUser(env, "a");
  seedUser(env, "b");
  const [ra, rb] = await Promise.all([
    api.patchMe(env, { userId: "a" }, { display_name: "Contested" }),
    api.patchMe(env, { userId: "b" }, { display_name: "Contested" }),
  ]);
  const ok = [ra, rb].filter((r) => r.status === 200).length;
  assert.equal(ok, 1, `${ok} accounts got the same display name`);
  assert.equal(env.raw.prepare(
    `SELECT COUNT(*) n FROM users WHERE display_norm = 'contested'`).get().n, 1);
});

// ------------------------------------------------------------ rate limits

test("a burst never gets more through than the limit allows", async () => {
  // take() increments in one statement and reads back in another. Arriving
  // together, several callers can read the same count — which must never
  // round in the caller's favour.
  const env = makeEnv();
  const max = LIMITS.picks.max;
  const res = await Promise.all(
    Array.from({ length: max + 25 }, () => take(env, "picks", "one-user")));
  const allowed = res.filter((r) => r.ok).length;
  assert.ok(allowed <= max,
    `${allowed} of ${max + 25} concurrent attempts were allowed, limit is ${max}`);
  // And it is not so pessimistic that it refuses everybody.
  assert.ok(allowed > 0, "the limiter refused every concurrent request");
});

// ------------------------------------------------------------ the cron

test("scoring while somebody is picking touches different weeks", async () => {
  // The cron only ever scores locked weeks and picking is only possible on
  // unlocked ones, so the two cannot meet on the same row. Worth holding: a
  // scoring pass that reached into the open week would be reading picks that
  // are still being made.
  const { scoreWeek } = await import("../src/scoring.js");
  const env = makeEnv();
  week(env, { week: 1, locked: true });
  week(env, { week: 2 });                      // open
  seedUser(env, "u", { name: "Busy" });
  const cookie = await signedIn(env, "u");

  const scores = { games: { 101: [30, 10, true], 102: [10, 30, true],
                            103: [20, 20, true] } };
  const [, picked] = await Promise.all([
    scoreWeek(env, SEASON, 1, scores),
    put(env, "/api/picks", cookie,
        { season: SEASON, week: 2, picks: { 201: "home", 202: "away" } }),
  ]);
  assert.equal(picked.status, 200, "picking failed while scoring ran");
  assert.equal(env.raw.prepare(
    `SELECT COUNT(*) n FROM picks WHERE week = 2`).get().n, 2);
  assert.equal(env.raw.prepare(
    `SELECT COUNT(*) n FROM pick_scores WHERE week = 2`).get().n, 0,
    "the open week was scored while it was still being picked");
});

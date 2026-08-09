// The survivor pool, against the real schema and the real router.
//
// The facts that decide whether anyone trusts a survivor game: a pick cannot
// move after the lock, a team cannot be spent twice, a void spares you and
// hands the team back, and the walk that declares people dead is the same
// answer every time it runs. Each gets a test that fails loudly.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE } from "../src/cookies.js";
import { scoreWeek } from "../src/scoring.js";
import {
  makeEnv, seedWeek, seedUser, seedSurvivorPick, forceLock, NOW, HOUR,
} from "./helpers/env.js";

const ORIGIN = "https://big12ology.com";

async function signedIn(env, userId) {
  const s = await session.create(env, userId);
  return `${SESSION_COOKIE}=${encodeURIComponent(s.raw)}`;
}

function req(path, { method = "GET", cookie, body, origin = ORIGIN } = {}) {
  const headers = { Accept: "application/json" };
  if (cookie) headers.Cookie = cookie;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    if (origin) headers.Origin = origin;
  }
  return new Request(`${ORIGIN}${path}`, {
    method, headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const call = (env, ...a) => worker.fetch(req(...a), env, {});

/** Two weeks of the same two matchups, so reuse has something to collide on. */
function twoWeeks(env) {
  seedWeek(env, { week: 3, games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13 },
    { game_id: 402, home: "Baylor", away: "Houston", spread_x2: 7 },
    { game_id: 403, home: "TCU", away: "Utah", spread_x2: null },
  ] });
  seedWeek(env, { week: 4, games: [
    { game_id: 411, home: "Kansas", away: "Baylor", spread_x2: -3 },
    { game_id: 412, home: "Iowa State", away: "TCU", spread_x2: -7 },
  ] });
}

// ---------------------------------------------------------------- triggers

test("a survivor pick cannot be written, moved or withdrawn after the lock",
  async () => {
    const env = makeEnv();
    twoWeeks(env);
    seedUser(env, "u1", { name: "Early" });
    seedSurvivorPick(env, "u1", 2026, 3, 401, "Iowa State");
    forceLock(env, 2026, 3);

    assert.throws(() => seedSurvivorPick(env, "u2", 2026, 3, 401, "Kansas"),
      /week_locked/, "a locked week accepted a new pick");
    assert.throws(() => env.raw.prepare(
      `UPDATE survivor_picks SET team = 'Kansas', game_id = 401
        WHERE user_id = 'u1'`).run(),
      /week_locked/, "a locked pick moved");
    assert.throws(() => env.raw.prepare(
      `DELETE FROM survivor_picks WHERE user_id = 'u1'`).run(),
      /week_locked/, "a locked pick was withdrawn");
  });

test("the team must be playing that game, and the game must have a line",
  async () => {
    const env = makeEnv();
    twoWeeks(env);
    seedUser(env, "u1", { name: "Confused" });
    assert.throws(() => seedSurvivorPick(env, "u1", 2026, 3, 401, "Baylor"),
      /survivor_not_in_game/, "a team from another game was accepted");
    assert.throws(() => seedSurvivorPick(env, "u1", 2026, 3, 403, "TCU"),
      /survivor_not_in_game/, "a lineless game was pickable");
  });

test("a team is spent the moment it is picked, and a void hands it back",
  async () => {
    const env = makeEnv();
    // The kickoff is stale from the start — 40 hours gone — because
    // slate_games_frozen forbids moving one, in tests as in production.
    seedWeek(env, { week: 3, games: [
      { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
        kickoff_at: NOW() - 40 * HOUR },
    ] });
    seedWeek(env, { week: 4, games: [
      { game_id: 411, home: "Kansas", away: "Baylor", spread_x2: -3 },
    ] });
    seedUser(env, "u1", { name: "Spender" });
    seedSurvivorPick(env, "u1", 2026, 3, 401, "Kansas");

    // Week 3 is not even locked yet: the pick is presumed live.
    assert.throws(() => seedSurvivorPick(env, "u1", 2026, 4, 411, "Kansas"),
      /survivor_team_reused/, "an unscored pick did not spend the team");

    // Week 3 voids — the game vanished — and Kansas comes back.
    forceLock(env, 2026, 3, NOW() - 41 * HOUR);
    await scoreWeek(env, 2026, 3, { games: {} });
    assert.equal(env.raw.prepare(
      `SELECT outcome FROM survivor_scores WHERE user_id = 'u1'`).get().outcome,
      "void");
    seedSurvivorPick(env, "u1", 2026, 4, 411, "Kansas");   // no throw
  });

test("changing your mind within the open week is not a reuse", async () => {
  const env = makeEnv();
  twoWeeks(env);
  seedUser(env, "u1", { name: "Waverer" });
  seedSurvivorPick(env, "u1", 2026, 3, 401, "Kansas");
  env.raw.prepare(
    `UPDATE survivor_picks SET game_id = 402, team = 'Baylor'
      WHERE user_id = 'u1' AND week = 3`).run();
  env.raw.prepare(
    `UPDATE survivor_picks SET game_id = 401, team = 'Kansas'
      WHERE user_id = 'u1' AND week = 3`).run();
});

// ----------------------------------------------------------------- grading

test("straight up, not against the spread", async () => {
  const env = makeEnv();
  twoWeeks(env);
  seedUser(env, "u1", { name: "Winner" });
  seedUser(env, "u2", { name: "Loser" });
  // Iowa State gives 6.5 and wins by 3: an ATS loss, a survivor win.
  seedSurvivorPick(env, "u1", 2026, 3, 401, "Iowa State");
  seedSurvivorPick(env, "u2", 2026, 3, 401, "Kansas");
  forceLock(env, 2026, 3);
  await scoreWeek(env, 2026, 3, { games: { "401": [24, 21, true] } });

  const got = env.raw.prepare(
    `SELECT user_id, outcome FROM survivor_scores ORDER BY user_id`).all()
    .map((r) => ({ ...r }));
  assert.deepEqual(got, [
    { user_id: "u1", outcome: "win" },
    { user_id: "u2", outcome: "loss" },
  ]);
});

test("a loss is the out, with the week and the reason on the row", async () => {
  const env = makeEnv();
  twoWeeks(env);
  seedUser(env, "u1", { name: "Doomed" });
  seedSurvivorPick(env, "u1", 2026, 3, 402, "Houston");
  forceLock(env, 2026, 3);
  await scoreWeek(env, 2026, 3, { games: {
    "401": [24, 21, true], "402": [30, 10, true] } });

  const b = env.raw.prepare(
    `SELECT wins, alive, out_week, out_reason FROM survivor_board
      WHERE user_id = 'u1'`).get();
  assert.equal(b.alive, 0);
  assert.equal(b.wins, 0);
  assert.equal(b.out_week, 3);
  assert.equal(b.out_reason, "loss");
});

test("a missed week after entry is an out; weeks before entry are not",
  async () => {
    const env = makeEnv();
    twoWeeks(env);
    seedUser(env, "u1", { name: "Forgot" });
    seedUser(env, "u2", { name: "Late" });
    // u1 entered in week 3 and let week 4 pass. u2's first pick IS week 4:
    // week 3 must not count against them.
    seedSurvivorPick(env, "u1", 2026, 3, 401, "Iowa State");
    seedSurvivorPick(env, "u2", 2026, 4, 411, "Kansas");
    forceLock(env, 2026, 3);
    await scoreWeek(env, 2026, 3, { games: { "401": [24, 21, true] } });
    forceLock(env, 2026, 4);
    await scoreWeek(env, 2026, 4, { games: { "411": [20, 10, true] } });

    const rows = Object.fromEntries(env.raw.prepare(
      `SELECT user_id, wins, alive, out_week, out_reason, entered_week
         FROM survivor_board`).all().map((r) => [r.user_id, { ...r }]));
    assert.equal(rows.u1.alive, 0);
    assert.equal(rows.u1.wins, 1, "the win before the miss was lost");
    assert.equal(rows.u1.out_week, 4);
    assert.equal(rows.u1.out_reason, "missed");
    assert.equal(rows.u2.alive, 1, "a late entrant was punished for week 3");
    assert.equal(rows.u2.wins, 1);
    assert.equal(rows.u2.entered_week, 4);
  });

test("a locked week with no result yet neither counts nor eliminates",
  async () => {
    const env = makeEnv();
    seedWeek(env, { week: 3, games: [
      { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
        kickoff_at: NOW() - HOUR },      // in play, nothing final
    ] });
    seedUser(env, "u1", { name: "Waiting" });
    seedSurvivorPick(env, "u1", 2026, 3, 401, "Iowa State");
    forceLock(env, 2026, 3);
    await scoreWeek(env, 2026, 3, { games: {} });

    const b = env.raw.prepare(
      `SELECT wins, alive FROM survivor_board WHERE user_id = 'u1'`).get();
    assert.equal(b.alive, 1, "a pending game killed its picker");
    assert.equal(b.wins, 0, "a pending game paid out early");
  });

test("the board ranks by wins, the living above the dead on a tie, and a "
   + "rerun moves nothing", async () => {
  const env = makeEnv();
  twoWeeks(env);
  seedUser(env, "a", { name: "Alive" });
  seedUser(env, "b", { name: "Dead" });
  seedUser(env, "c", { name: "Behind" });
  // a and b both win week 3; b loses week 4 while a wins it; c enters week 4.
  seedSurvivorPick(env, "a", 2026, 3, 401, "Iowa State");
  seedSurvivorPick(env, "b", 2026, 3, 402, "Baylor");
  forceLock(env, 2026, 3);
  await scoreWeek(env, 2026, 3, { games: {
    "401": [24, 21, true], "402": [20, 10, true] } });

  seedSurvivorPick(env, "a", 2026, 4, 411, "Kansas");
  seedSurvivorPick(env, "b", 2026, 4, 412, "TCU");
  seedSurvivorPick(env, "c", 2026, 4, 412, "Iowa State");
  forceLock(env, 2026, 4);
  const scores4 = { games: { "411": [20, 10, true], "412": [21, 3, true] } };
  await scoreWeek(env, 2026, 4, scores4);

  const rows = () => env.raw.prepare(
    `SELECT user_id, wins, alive, rank FROM survivor_board
      ORDER BY rank, user_id`).all().map((r) => ({ ...r }));
  assert.deepEqual(rows(), [
    { user_id: "a", wins: 2, alive: 1, rank: 1 },
    { user_id: "c", wins: 1, alive: 1, rank: 2 },
    { user_id: "b", wins: 1, alive: 0, rank: 3 },
  ]);
  // Unlike the leaderboard's tied records, alive-versus-dead on equal wins
  // is not a tie: surviving is the game, so it is part of the rank rather
  // than of the presentation order.

  const before = JSON.stringify(rows());
  await scoreWeek(env, 2026, 4, scores4);
  assert.equal(JSON.stringify(rows()), before, "a rerun moved the board");
});

test("a survivor-only week promotes a provisional account", async () => {
  const env = makeEnv();
  twoWeeks(env);
  seedUser(env, "new", { name: "Newcomer", status: "provisional" });
  seedSurvivorPick(env, "new", 2026, 3, 401, "Iowa State");
  forceLock(env, 2026, 3);
  await scoreWeek(env, 2026, 3, { games: { "401": [24, 21, true] } });
  const { promoteProvisional } = await import("../src/scoring.js");
  await promoteProvisional(env, 2026, 3);
  assert.equal(env.raw.prepare(
    `SELECT status FROM users WHERE id = 'new'`).get().status, "active");
});

// ------------------------------------------------------------------ the API

test("picking, repicking and withdrawing through the front door", async () => {
  const env = makeEnv();
  twoWeeks(env);
  seedUser(env, "u1", { name: "Player" });
  const cookie = await signedIn(env, "u1");

  let r = await call(env, "/api/survivor/pick", { method: "PUT", cookie,
    body: { week: 3, game_id: 401, team: "Iowa State" } });
  assert.equal(r.status, 200);
  assert.deepEqual((await r.json()).pick,
    { game_id: 401, team: "Iowa State" });

  // Mind changed: same week, other game entirely.
  r = await call(env, "/api/survivor/pick", { method: "PUT", cookie,
    body: { week: 3, game_id: 402, team: "Baylor" } });
  assert.equal(r.status, 200);

  r = await call(env, "/api/survivor", { cookie });
  const me = await r.json();
  assert.deepEqual(me.pick, { game_id: 402, team: "Baylor" });
  assert.equal(me.used.length, 1);

  // Withdrawn before the lock: the week is open and so is the team.
  r = await call(env, "/api/survivor/pick", { method: "PUT", cookie,
    body: { week: 3, team: null } });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).pick, null);
  assert.equal(env.raw.prepare(
    `SELECT COUNT(*) c FROM survivor_picks`).get().c, 0);
});

test("the handler translates the triggers: spent team, wrong team, the lock",
  async () => {
    const env = makeEnv();
    twoWeeks(env);
    seedUser(env, "u1", { name: "Player" });
    const cookie = await signedIn(env, "u1");
    seedSurvivorPick(env, "u1", 2026, 3, 401, "Kansas");

    let r = await call(env, "/api/survivor/pick", { method: "PUT", cookie,
      body: { week: 4, game_id: 411, team: "Kansas" } });
    assert.equal(r.status, 409);
    assert.equal((await r.json()).error, "team_used");

    r = await call(env, "/api/survivor/pick", { method: "PUT", cookie,
      body: { week: 4, game_id: 411, team: "Iowa State" } });
    assert.equal(r.status, 400);
    assert.equal((await r.json()).error, "not_in_game");

    forceLock(env, 2026, 4);
    r = await call(env, "/api/survivor/pick", { method: "PUT", cookie,
      body: { week: 4, game_id: 411, team: "Baylor" } });
    assert.equal(r.status, 409);
    assert.equal((await r.json()).error, "locked");
  });

test("the eliminated cannot keep playing", async () => {
  const env = makeEnv();
  twoWeeks(env);
  seedUser(env, "u1", { name: "Ghost" });
  seedSurvivorPick(env, "u1", 2026, 3, 402, "Houston");
  forceLock(env, 2026, 3);
  await scoreWeek(env, 2026, 3, { games: { "402": [30, 10, true] } });

  const cookie = await signedIn(env, "u1");
  const r = await call(env, "/api/survivor/pick", { method: "PUT", cookie,
    body: { week: 4, game_id: 411, team: "Kansas" } });
  assert.equal(r.status, 409);
  const b = await r.json();
  assert.equal(b.error, "eliminated");
  assert.equal(b.out_week, 3);
});

test("the public board withholds the week's picks until the lock, and "
   + "provisional players entirely", async () => {
  const env = makeEnv();
  twoWeeks(env);
  seedUser(env, "act", { name: "Shown" });
  seedUser(env, "prov", { name: "Hidden", status: "provisional" });
  seedSurvivorPick(env, "act", 2026, 3, 401, "Iowa State");
  seedSurvivorPick(env, "prov", 2026, 3, 402, "Baylor");

  // Nothing scored yet: no board rows at all, and no picks leak.
  let r = await call(env, "/api/survivor/board");
  let b = await r.json();
  assert.equal(b.rows.length, 0);

  forceLock(env, 2026, 3);
  await scoreWeek(env, 2026, 3, { games: { "401": [24, 21, true] } });

  r = await call(env, "/api/survivor/board");
  assert.match(r.headers.get("Cache-Control"), /s-maxage=60/);
  b = await r.json();
  assert.equal(b.rows.length, 1, "a provisional account reached the board");
  assert.equal(b.rows[0].display_name, "Shown");
  assert.equal(b.rows[0].wins, 1);
  // Week 3 is both locked and current (week 4 exists but 3 is the earliest
  // unfinished — locked counts as finished for currentWeek, so the current
  // week is 4 and its picks are absent: nothing to reveal there yet).
  assert.equal(b.alive, 1);
});

test("your own standing is yours even while provisional", async () => {
  const env = makeEnv();
  twoWeeks(env);
  seedUser(env, "prov", { name: "Quiet", status: "provisional" });
  seedSurvivorPick(env, "prov", 2026, 3, 401, "Iowa State");
  forceLock(env, 2026, 3);
  await scoreWeek(env, 2026, 3, { games: { "401": [24, 21, true] } });

  const cookie = await signedIn(env, "prov");
  const r = await call(env, "/api/survivor", { cookie });
  const me = await r.json();
  assert.equal(me.standing.wins, 1);
  assert.equal(me.standing.alive, 1);
  assert.equal(me.used[0].outcome, "win");
});

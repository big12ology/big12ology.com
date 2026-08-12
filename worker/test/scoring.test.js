// Scoring, against the real schema.
//
// The three things that decide whether people believe a pick'em board are a
// push not being a loss, a void not being anybody's fault, and re-running the
// scorer not moving anything. Each gets a test that would fail loudly rather
// than produce a plausible wrong number.

import test from "node:test";
import assert from "node:assert/strict";
import {
  makeEnv, seedWeek, seedUser, seedPick, forceLock, NOW, HOUR,
} from "./helpers/env.js";
import { chalk, promoteProvisional, room, scoreWeek } from "../src/scoring.js";

// Iowa State -6.5 over Kansas (spread_x2 -13); Baylor +3.5 (spread_x2 7).
const SCORES = { games: { "401": [31, 21, true], "402": [17, 24, true] } };

// Picks go in while the week is OPEN and the lock lands afterwards, because
// that is the only order the schema permits: picks_locked_insert refuses a
// pick on a locked week, which is the whole point of it. A fixture that
// seeded picks after locking was testing the trigger, not the scorer.
function open() {
  const env = makeEnv();
  return { env, w: seedWeek(env) };
}
const lock = (env) => forceLock(env, 2026, 3);

test("a week that has not locked is never scored", async () => {
  const env = makeEnv();
  seedWeek(env, { lockAt: NOW() + 48 * HOUR });
  seedUser(env, "u1", { name: "Someone" });
  seedPick(env, "u1", 2026, 3, 401, "home", -13);

  const r = await scoreWeek(env, 2026, 3, SCORES);
  assert.equal(r.skipped, "not_locked");
  const n = env.raw.prepare("SELECT COUNT(*) c FROM results").get();
  assert.equal(n.c, 0, "an open week produced results");
});

test("wins, losses and the ats sign", async () => {
  const { env } = open();
  seedUser(env, "u1", { name: "Correct" });
  seedUser(env, "u2", { name: "Wrong" });
  // 401: Iowa State 31-21, giving 6.5 -> home covers.
  seedPick(env, "u1", 2026, 3, 401, "home", -13);
  seedPick(env, "u2", 2026, 3, 401, "away", -13);
  // 402: Baylor 17-24, getting 3.5 -> away wins outright, away covers.
  seedPick(env, "u1", 2026, 3, 402, "away", 7);
  seedPick(env, "u2", 2026, 3, 402, "home", 7);
  lock(env);

  await scoreWeek(env, 2026, 3, SCORES);
  // Spread into plain objects: node:sqlite returns null-prototype rows and
  // deepEqual is strict about the prototype.
  const got = env.raw.prepare(
    `SELECT user_id, game_id, outcome FROM pick_scores
      ORDER BY user_id, game_id`).all().map((r) => ({ ...r }));
  assert.deepEqual(got, [
    { user_id: "u1", game_id: 401, outcome: "win" },
    { user_id: "u1", game_id: 402, outcome: "win" },
    { user_id: "u2", game_id: 401, outcome: "loss" },
    { user_id: "u2", game_id: 402, outcome: "loss" },
  ]);
});

test("a push is its own outcome and leaves the percentage alone", async () => {
  const env = makeEnv();
  seedWeek(env, { games: [
    // A whole-number line that lands exactly: 24-17 with the home team
    // giving 7 is a push, not a win and not a loss.
    { game_id: 501, home: "TCU", away: "BYU", spread_x2: -14 },
    { game_id: 502, home: "Utah", away: "UCF", spread_x2: -6 },
  ] });
  seedUser(env, "u1", { name: "Pusher" });
  seedPick(env, "u1", 2026, 3, 501, "home", -14);
  seedPick(env, "u1", 2026, 3, 502, "home", -6);
  lock(env);

  await scoreWeek(env, 2026, 3,
    { games: { "501": [24, 17, true], "502": [30, 20, true] } });

  const row = env.raw.prepare(
    `SELECT w, l, p, pct FROM leaderboard_season WHERE user_id = 'u1'`).get();
  assert.equal(row.w, 1);
  assert.equal(row.l, 0);
  assert.equal(row.p, 1);
  // 1-0-1 is 100%, not 50%. Folding the push into losses is the single most
  // common way a board loses its players.
  assert.equal(row.pct, 1);
});

test("a game past kickoff with no score voids, and costs nobody", async () => {
  const env = makeEnv();
  const long = NOW() - 40 * HOUR;
  seedWeek(env, { lockAt: NOW() + HOUR, games: [
    { game_id: 601, home: "Kansas", away: "Iowa", spread_x2: -3,
      kickoff_at: long },
    { game_id: 602, home: "BYU", away: "Utah", spread_x2: -7,
      kickoff_at: long },
  ] });
  seedUser(env, "u1", { name: "Unlucky" });
  seedPick(env, "u1", 2026, 3, 601, "home", -3);
  seedPick(env, "u1", 2026, 3, 602, "home", -7);
  lock(env);

  // 601 played; 602 has vanished from the file entirely.
  await scoreWeek(env, 2026, 3, { games: { "601": [28, 20, true] } });

  const row = env.raw.prepare(
    `SELECT w, l, p, v, pct FROM leaderboard_season WHERE user_id = 'u1'`).get();
  assert.equal(row.w, 1);
  assert.equal(row.l, 0);
  assert.equal(row.v, 1, "the vanished game did not void");
  assert.equal(row.pct, 1, "a void was counted in the denominator");
});

// The bug a local run found that the suite did not: a game still being played
// was voided the instant its week locked, because it was not yet in the scores
// file. Eleven voids on a week with nine unplayable games.
test("a game still to be played is not voided for being absent", async () => {
  const env = makeEnv();
  seedWeek(env, { lockAt: NOW() + HOUR, games: [
    { game_id: 1101, home: "Playing", away: "Now", spread_x2: -3,
      kickoff_at: NOW() - 2 * HOUR },       // kicked off, no score yet
    { game_id: 1102, home: "Later", away: "Today", spread_x2: -3,
      kickoff_at: NOW() + 6 * HOUR },       // has not kicked off
    { game_id: 1103, home: "Long", away: "Gone", spread_x2: -3,
      kickoff_at: NOW() - 40 * HOUR },      // no result is coming
    { game_id: 1104, home: "No", away: "Line", spread_x2: null,
      kickoff_at: NOW() - 40 * HOUR },      // never pickable
  ] });
  seedUser(env, "u1", { name: "Waiting" });
  seedPick(env, "u1", 2026, 3, 1101, "home", -3);
  lock(env);

  // A scores file listing none of them, which is what a locked week looks
  // like on a Saturday afternoon before anything has finished.
  const r = await scoreWeek(env, 2026, 3, { games: {} });
  assert.equal(r.voids, 1, "voided a game that had not finished");

  const rows = env.raw.prepare(
    "SELECT game_id, status FROM results ORDER BY game_id").all();
  assert.deepEqual(rows.map((x) => x.game_id), [1103],
    "only the 40-hours-stale game should have a result row");
  // The card says WAITING off the missing row; a void would have said the
  // game was canceled.
  assert.equal(env.raw.prepare(
    "SELECT COUNT(*) c FROM pick_scores WHERE user_id='u1'").get().c, 0);
});

test("a game with no line never gets a result row", async () => {
  const env = makeEnv();
  seedWeek(env, { games: [
    { game_id: 1201, home: "A", away: "B", spread_x2: null,
      kickoff_at: NOW() - 40 * HOUR },
  ] });
  lock(env);
  await scoreWeek(env, 2026, 3, { games: {} });
  assert.equal(env.raw.prepare("SELECT COUNT(*) c FROM results").get().c, 0,
    "an unpickable game produced a result nobody could have picked");
});

test("re-running changes nothing", async () => {
  const { env } = open();
  seedUser(env, "u1", { name: "Steady" });
  seedPick(env, "u1", 2026, 3, 401, "home", -13);
  lock(env);

  const first = await scoreWeek(env, 2026, 3, SCORES);
  assert.ok(first.changed > 0, "the first run scored nothing");
  const snap = () => JSON.stringify([
    env.raw.prepare("SELECT * FROM results ORDER BY game_id").all(),
    env.raw.prepare("SELECT * FROM pick_scores ORDER BY game_id").all(),
    env.raw.prepare("SELECT user_id,w,l,p,v,pct,rank FROM leaderboard_season").all(),
  ]);
  const before = snap();

  const second = await scoreWeek(env, 2026, 3, SCORES);
  assert.equal(second.changed, 0, "an unchanged rerun rewrote results");
  assert.equal(snap(), before, "a rerun moved the board");

  const rev = env.raw.prepare(
    "SELECT revision FROM results WHERE game_id = 401").get();
  assert.equal(rev.revision, 1, "revision bumped without a change");
});

test("a corrected score propagates and bumps the revision", async () => {
  const { env } = open();
  seedUser(env, "u1", { name: "Flipped" });
  seedPick(env, "u1", 2026, 3, 401, "away", -13);
  lock(env);

  await scoreWeek(env, 2026, 3, SCORES);
  assert.equal(env.raw.prepare(
    "SELECT outcome FROM pick_scores WHERE game_id = 401").get().outcome, "loss");

  // CFBD corrects the score; Kansas actually won by nine.
  await scoreWeek(env, 2026, 3,
    { games: { "401": [21, 30, true], "402": [17, 24, true] } });

  assert.equal(env.raw.prepare(
    "SELECT outcome FROM pick_scores WHERE game_id = 401").get().outcome, "win");
  assert.equal(env.raw.prepare(
    "SELECT revision FROM results WHERE game_id = 401").get().revision, 2);
});

test("provisional accounts are scored but not published", async () => {
  const { env } = open();
  seedUser(env, "new", { name: "Newcomer", status: "provisional" });
  seedPick(env, "new", 2026, 3, 401, "home", -13);
  lock(env);

  await scoreWeek(env, 2026, 3, SCORES);
  // The pick is graded...
  assert.equal(env.raw.prepare(
    "SELECT COUNT(*) c FROM pick_scores WHERE user_id = 'new'").get().c, 1);
  // ...and the board does not show them yet.
  assert.equal(env.raw.prepare(
    "SELECT COUNT(*) c FROM leaderboard_season WHERE user_id = 'new'").get().c, 0);

  // One completed week is the whole cost of admission.
  await promoteProvisional(env, 2026, 3);
  assert.equal(env.raw.prepare(
    "SELECT status FROM users WHERE id = 'new'").get().status, "active");
  await scoreWeek(env, 2026, 3, SCORES);
  assert.equal(env.raw.prepare(
    "SELECT COUNT(*) c FROM leaderboard_season WHERE user_id = 'new'").get().c, 1);
});

test("ranking is by wins then percentage, and ties share a rank", async () => {
  const env = makeEnv();
  seedWeek(env, { games: [
    { game_id: 701, home: "A", away: "B", spread_x2: -2 },
    { game_id: 702, home: "C", away: "D", spread_x2: -2 },
    { game_id: 703, home: "E", away: "F", spread_x2: -2 },
  ] });
  for (const u of ["a", "b", "c"]) seedUser(env, u, { name: u.toUpperCase() });
  // Home covers all three.
  for (const g of [701, 702, 703]) {
    seedPick(env, "a", 2026, 3, g, "home", -2);
  }
  seedPick(env, "b", 2026, 3, 701, "home", -2);   // 1-0
  seedPick(env, "c", 2026, 3, 702, "home", -2);   // 1-0, identical record
  lock(env);
  await scoreWeek(env, 2026, 3, { games: {
    "701": [10, 0, true], "702": [10, 0, true], "703": [10, 0, true] } });

  const rows = env.raw.prepare(
    `SELECT user_id, w, rank FROM leaderboard_season ORDER BY rank, user_id`).all();
  assert.equal(rows[0].user_id, "a");
  assert.equal(rows[0].rank, 1);
  assert.equal(rows[1].rank, 2);
  assert.equal(rows[2].rank, 2, "an identical record did not share a rank");
});

test("the room takes the majority, and abstains on a dead heat", async () => {
  const env = makeEnv();
  seedWeek(env, { games: [
    // Three voters lean home, and home covers: the room wins.
    { game_id: 901, home: "H", away: "A", spread_x2: -2 },
    // Three voters lean away, and home covers: the room loses.
    { game_id: 902, home: "H2", away: "A2", spread_x2: -2 },
    // Two each way. The room had no opinion and does not get one.
    { game_id: 903, home: "H3", away: "A3", spread_x2: -2 },
  ] });
  for (const u of ["a", "b", "c", "d"]) seedUser(env, u, { name: u.toUpperCase() });

  seedPick(env, "a", 2026, 3, 901, "home", -2);
  seedPick(env, "b", 2026, 3, 901, "home", -2);
  seedPick(env, "c", 2026, 3, 901, "away", -2);

  seedPick(env, "a", 2026, 3, 902, "away", -2);
  seedPick(env, "b", 2026, 3, 902, "away", -2);
  seedPick(env, "c", 2026, 3, 902, "home", -2);

  seedPick(env, "a", 2026, 3, 903, "home", -2);
  seedPick(env, "b", 2026, 3, 903, "home", -2);
  seedPick(env, "c", 2026, 3, 903, "away", -2);
  seedPick(env, "d", 2026, 3, 903, "away", -2);
  lock(env);

  // Home covers all three.
  await scoreWeek(env, 2026, 3, { games: {
    "901": [20, 0, true], "902": [20, 0, true], "903": [20, 0, true] } });

  const r = await room(env, 2026, 3);
  assert.equal(r.w, 1, "the room did not win the game it leaned right on");
  assert.equal(r.l, 1, "the room did not lose the game it leaned wrong on");
  assert.equal(r.split, 1, "the dead heat was not set aside");
  // The tie is in neither term: 1-1 is 50%, not 33%.
  assert.equal(r.pct, 0.5);
});

test("the room is the same population as the consensus bars", async () => {
  // Verifiability is the whole reason it counts every account: a reader can
  // add up the splits they can see and arrive at this row. Filtering to
  // active accounts here would make it unreachable from the page.
  const env = makeEnv();
  seedWeek(env, { games: [
    { game_id: 911, home: "H", away: "A", spread_x2: -2 }] });
  seedUser(env, "act", { name: "Active" });
  seedUser(env, "prov", { name: "Provisional", status: "provisional" });
  seedPick(env, "act", 2026, 3, 911, "away", -2);
  seedPick(env, "prov", 2026, 3, 911, "away", -2);
  lock(env);
  await scoreWeek(env, 2026, 3, { games: { "911": [20, 0, true] } });

  const r = await room(env, 2026, 3);
  // Both picks counted, so the room took away and lost. Counting only the
  // active account gives the same side here — the point is that neither is
  // dropped, which the split confirms.
  assert.equal(r.l, 1);
  assert.equal(r.split, 0);
  const c = env.raw.prepare(
    `SELECT SUM(side='home') h, SUM(side='away') a FROM picks
      WHERE game_id = 911`).get();
  assert.equal(c.a, 2, "the consensus bar and the room disagree on who voted");
});

test("the room is null before anything is graded", async () => {
  const env = makeEnv();
  seedWeek(env);
  assert.equal(await room(env, 2026, 3), null);
});

test("the chalk takes the favorite, and skips pick'ems", async () => {
  const env = makeEnv();
  seedWeek(env, { games: [
    { game_id: 801, home: "Fav", away: "Dog", spread_x2: -14 },   // home favored
    { game_id: 802, home: "Dog", away: "Fav", spread_x2: 14 },    // away favored
    { game_id: 803, home: "Even", away: "Even2", spread_x2: 0 },  // no favorite
  ] });
  lock(env);
  await scoreWeek(env, 2026, 3, { games: {
    "801": [30, 0, true],    // home covers -> chalk wins
    "802": [0, 30, true],    // away covers -> chalk wins
    "803": [10, 0, true],    // a pick'em: chalk has no opinion
  } });

  const c = await chalk(env, 2026, 3);
  assert.equal(c.w, 2);
  assert.equal(c.l, 0);
  assert.equal(c.pct, 1);
});

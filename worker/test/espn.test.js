// The ESPN fill, which exists to shorten the gap between a game ending and the
// publisher saying so — and must never be able to lengthen it, or to overrule
// CFBD once CFBD has spoken.

import test from "node:test";
import assert from "node:assert/strict";
import { makeEnv, seedWeek, forceLock, NOW, HOUR } from "./helpers/env.js";
import { fetchEspn, fetchEspnSchedule, sweepEspn, sweepKickoffs, withEspn,
         espnKey } from "../src/espn.js";
import { scoreWeek } from "../src/scoring.js";

// The binding the fake env does not carry, because most of the suite has no
// use for it. Same contract as helpers/env.js's: get(k, "json") parses.
class KV {
  constructor() { this.map = new Map(); this.writes = 0; }
  async get(k, type) {
    if (!this.map.has(k)) return null;
    const v = this.map.get(k);
    if (type !== "json") return v;
    try { return JSON.parse(v); } catch { return null; }
  }
  async put(k, v) { this.writes++; this.map.set(k, v); }
}

// One event, in the shape the scoreboard actually returns.
function event(id, { home, away, completed = true, homeScore, awayScore }) {
  return {
    id: String(id),
    competitions: [{
      status: { type: { completed } },
      competitors: [
        { homeAway: "home", team: { abbreviation: home }, score: homeScore },
        { homeAway: "away", team: { abbreviation: away }, score: awayScore },
      ],
    }],
  };
}

const ok = (events) => async () => ({
  ok: true, json: async () => ({ events }),
});

/** A scheduled game, which is all the kickoff sweep reads. */
function scheduled(id, at) {
  return { id: String(id), date: new Date(at * 1000).toISOString() };
}

/** Read a kickoff back out, for the assertions below. */
const kickoffOf = (env, id) => env.raw.prepare(
  "SELECT kickoff_at k FROM slate_games WHERE game_id=?").get(id).k;

test("only finished games with both scores are taken", async () => {
  const got = await fetchEspn(NOW(), NOW(), ok([
    event(11, { home: "KU", away: "ISU", homeScore: "28", awayScore: "3" }),
    event(12, { home: "BAY", away: "HOU", homeScore: "7", awayScore: "10",
                completed: false }),
    // A finished game whose score has not been filled in. Number("") is 0, and
    // a 0-0 final is a plausible-looking lie, which is the whole reason the
    // empty string is rejected rather than parsed.
    event(13, { home: "TCU", away: "UTAH", homeScore: "", awayScore: "" }),
  ]));
  assert.deepEqual(got, { "11": [28, 3, true] });
});

test("the URL asks by date with a day of slack, never by week", async () => {
  let asked = null;
  await fetchEspn(1788476400, 1788483600, async (u) => {
    asked = String(u);
    return { ok: true, json: async () => ({ events: [] }) };
  });
  // ESPN has no week 0 — week=0&seasontype=2 returns nothing, and the 2026
  // opener in Dublin is a week 0 game — so the range is the only query that
  // finds every slate game. The slack absorbs the UTC/Eastern date skew.
  assert.match(asked, /dates=20260902-20260905/);
  assert.doesNotMatch(asked, /week=/);
});

test("a kept final fills a gap and never overwrites the publisher", async () => {
  const env = makeEnv({ SCORES: new KV() });
  await env.SCORES.put(espnKey(2026), JSON.stringify({ games: {
    "11": [28, 3, true],      // the publisher has not graded this one
    "12": [1, 2, true],       // and this one it has, differently
  } }));
  const merged = await withEspn(env, 2026, { games: {
    "11": [null, null, false],
    "12": [40, 6, true],
  } });
  assert.deepEqual(merged.games["11"], [28, 3, true], "the gap was not filled");
  assert.deepEqual(merged.games["12"], [40, 6, true],
                   "CFBD is the record and was overwritten");
});

test("nothing is asked about a game the publisher has already graded", async () => {
  const env = makeEnv({ SCORES: new KV() });
  seedWeek(env, { games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: NOW() - 6 * HOUR },
  ] });
  const r = await sweepEspn(env, 2026,
    { games: { "401": [31, 21, true] } }, NOW());
  assert.equal(r.skipped, "nothing_pending");
  assert.equal(env.SCORES.writes, 0, "a quiet sweep wrote to KV");
});

test("a game still being played is left alone", async () => {
  const env = makeEnv({ SCORES: new KV() });
  seedWeek(env, { games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: NOW() - HOUR },   // inside the settle window
  ] });
  const r = await sweepEspn(env, 2026, { games: {} }, NOW());
  assert.equal(r.skipped, "nothing_pending");
});

test("a final the publisher lacks is fetched, kept, and written once", async () => {
  const env = makeEnv({ SCORES: new KV() });
  env.ESPN_FETCH = ok([
    event(401, { home: "ISU", away: "KU", homeScore: "31", awayScore: "21" }),
    event(999, { home: "OSU", away: "TEX", homeScore: "3", awayScore: "5" }),
  ]);
  seedWeek(env, { games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: NOW() - 6 * HOUR },
  ] });

  const r = await sweepEspn(env, 2026, { games: {} }, NOW());
  assert.equal(r.added, 1, "the final was not kept");
  assert.equal(env.SCORES.writes, 1);
  const held = await env.SCORES.get(espnKey(2026), "json");
  assert.deepEqual(held.games["401"], [31, 21, true]);
  assert.equal(held.games["999"], undefined, "a game off the slate was kept");

  // Same answer again. KV allows a thousand writes a day and this runs hourly
  // all season, so an unchanged sweep has to cost nothing.
  await sweepEspn(env, 2026, { games: {} }, NOW());
  assert.equal(env.SCORES.writes, 1, "an unchanged sweep wrote again");
});

test("a kept final keeps a game graded, so the void clock cannot reach it",
     async () => {
  // The failure this is really about. A result that lived only in one sweep
  // would vanish the moment the publisher wrote a file that still lacked it,
  // and thirty-six hours after kickoff scoring.js writes the game off — which
  // is exactly how three finished games came out VOID on 2026-09-05.
  const env = makeEnv({ SCORES: new KV() });
  seedWeek(env, { lockAt: NOW() + HOUR, games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: NOW() - 40 * HOUR },
  ] });
  forceLock(env, 2026, 3);
  await env.SCORES.put(espnKey(2026), JSON.stringify({
    games: { "401": [31, 21, true] } }));

  // The publisher, still silent about it.
  await scoreWeek(env, 2026, 3, { games: { "401": [null, null, false] } });

  const row = env.raw.prepare(
    `SELECT status, ats, home_points AS hp FROM results WHERE game_id = 401`).get();
  assert.equal(row.status, "final", "a game ESPN had graded was voided anyway");
  assert.equal(row.hp, 31);
  assert.equal(row.ats, "home");
});

test("the schedule reader takes e.date and drops what will not parse",
     async () => {
  // A date that does not parse would become NaN, and NaN reaches kickoff_at,
  // which is NOT NULL. Dropped here instead.
  const got = await fetchEspnSchedule(NOW(), NOW(), ok([
    { id: "11", date: "2026-09-12T17:30Z" },
    { id: "12", date: "whenever" },
    { id: "13" },
  ]));
  assert.deepEqual(got, { "11": Date.parse("2026-09-12T17:30Z") / 1000 });
});

// ---------------------------------------------------------------------------
// The kickoff correction. Same scoreboard, opposite end of the game: these run
// before kickoff and may only ever touch a game that has not started.

test("a kickoff that has not happened yet is corrected from the scoreboard",
     async () => {
  // 2026 week 2's Oklahoma State at Oregon, in miniature: the slate an hour
  // and a half early, ESPN right.
  const env = makeEnv();
  const real = NOW() + 10 * HOUR;
  seedWeek(env, { lockAt: NOW() + 8 * HOUR, games: [
    { game_id: 401, home: "Oregon", away: "Oklahoma State", spread_x2: -13,
      kickoff_at: NOW() + 8 * HOUR },
  ] });
  env.ESPN_FETCH = ok([scheduled(401, real)]);

  const r = await sweepKickoffs(env, 2026);
  assert.equal(r.moved, 1);
  assert.equal(kickoffOf(env, 401), real);
});

test("a kickoff that has already passed is never moved", async () => {
  // The trigger would refuse this anyway. The sweep must not even offer it,
  // because a refusal inside a batch takes the other corrections with it.
  const env = makeEnv();
  seedWeek(env, { lockAt: NOW() - 2 * HOUR, games: [
    { game_id: 401, home: "Baylor", away: "Houston", spread_x2: -7,
      kickoff_at: NOW() - 2 * HOUR },
  ] });
  env.ESPN_FETCH = ok([scheduled(401, NOW() + 6 * HOUR)]);

  const r = await sweepKickoffs(env, 2026);
  assert.equal(r.skipped, "nothing_upcoming");
  assert.equal(kickoffOf(env, 401), NOW() - 2 * HOUR);
});

test("a scoreboard time in the past is not a correction either", async () => {
  // The other side of the same rule: the row is still ahead of us, but the
  // time offered is behind us, and taking it would lock a card retroactively.
  const env = makeEnv();
  const ours = NOW() + 6 * HOUR;
  seedWeek(env, { lockAt: ours, games: [
    { game_id: 401, home: "TCU", away: "Utah", spread_x2: 3,
      kickoff_at: ours },
  ] });
  env.ESPN_FETCH = ok([scheduled(401, NOW() - HOUR)]);

  const r = await sweepKickoffs(env, 2026);
  assert.equal(r.moved, 0);
  assert.equal(kickoffOf(env, 401), ours);
});

test("a move big enough to be a reschedule is refused and counted", async () => {
  // A game that has genuinely moved to another date has changed week too, and
  // that is the publisher's call. This only ever fixes the hour.
  const env = makeEnv();
  const ours = NOW() + 6 * HOUR;
  seedWeek(env, { lockAt: ours, games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: ours },
  ] });
  env.ESPN_FETCH = ok([scheduled(401, ours + 8 * 24 * HOUR)]);

  const r = await sweepKickoffs(env, 2026);
  assert.equal(r.moved, 0);
  assert.equal(r.far, 1);
  assert.equal(kickoffOf(env, 401), ours);
});

test("a game the scoreboard does not carry is left exactly as it was",
     async () => {
  const env = makeEnv();
  const ours = NOW() + 6 * HOUR;
  seedWeek(env, { lockAt: ours, games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: ours },
  ] });
  env.ESPN_FETCH = ok([scheduled(999, NOW() + 7 * HOUR)]);

  const r = await sweepKickoffs(env, 2026);
  assert.equal(r.moved, 0);
  assert.equal(kickoffOf(env, 401), ours);
});

test("correcting the earliest game moves the week's lock earlier with it",
     async () => {
  // lock_at is the first kickoff of a playable game, and /api answers `locked`
  // from it. A correction that leaves it behind would report a card open past
  // the point its first game had started.
  const env = makeEnv();
  const early = NOW() + 5 * HOUR;
  seedWeek(env, { lockAt: NOW() + 8 * HOUR, games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: NOW() + 8 * HOUR },
    { game_id: 402, home: "Baylor", away: "Houston", spread_x2: 7,
      kickoff_at: NOW() + 9 * HOUR },
  ] });
  env.ESPN_FETCH = ok([scheduled(401, early)]);

  await sweepKickoffs(env, 2026);
  assert.equal(env.raw.prepare(
    "SELECT lock_at l FROM weeks WHERE season=2026 AND week=3").get().l, early);
});

test("a lock never moves later, however the kickoffs move", async () => {
  // weeks_lock_monotonic would abort the batch; the statement is written so
  // the case is a no-op instead, and the correction it came in with still
  // lands. A card that locks sooner than it must is the safe half.
  const env = makeEnv();
  const lock = NOW() + 8 * HOUR;
  seedWeek(env, { lockAt: lock, games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: lock },
  ] });
  env.ESPN_FETCH = ok([scheduled(401, lock + 3 * HOUR)]);

  const r = await sweepKickoffs(env, 2026);
  assert.equal(r.moved, 1);
  assert.equal(kickoffOf(env, 401), lock + 3 * HOUR);
  assert.equal(env.raw.prepare(
    "SELECT lock_at l FROM weeks WHERE season=2026 AND week=3").get().l, lock);
});

test("an unpickable game is corrected but does not set the lock", async () => {
  // The kickoff_tbd case end to end: the placeholder is fixed while the game
  // is still unpickable, and lock_at keeps following the playable games only,
  // exactly as the publisher computes it.
  const env = makeEnv();
  const lock = NOW() + 8 * HOUR;
  const real = NOW() + 4 * HOUR;
  seedWeek(env, { lockAt: lock, games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: lock },
    { game_id: 403, home: "TCU", away: "Utah", spread_x2: null,
      kickoff_at: NOW() + 6 * HOUR },
  ] });
  env.ESPN_FETCH = ok([scheduled(403, real)]);

  await sweepKickoffs(env, 2026);
  assert.equal(kickoffOf(env, 403), real);
  assert.equal(env.raw.prepare(
    "SELECT lock_at l FROM weeks WHERE season=2026 AND week=3").get().l, lock);
});

test("nothing upcoming means no scoreboard call at all", async () => {
  const env = makeEnv();
  let called = 0;
  seedWeek(env, { lockAt: NOW() - 2 * HOUR, games: [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13,
      kickoff_at: NOW() - 2 * HOUR },
  ] });
  env.ESPN_FETCH = async () => { called++; return { ok: true,
    json: async () => ({ events: [] }) }; };

  assert.equal((await sweepKickoffs(env, 2026)).skipped, "nothing_upcoming");
  assert.equal(called, 0);
});

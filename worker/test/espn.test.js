// The ESPN fill, which exists to shorten the gap between a game ending and the
// publisher saying so — and must never be able to lengthen it, or to overrule
// CFBD once CFBD has spoken.

import test from "node:test";
import assert from "node:assert/strict";
import { makeEnv, seedWeek, forceLock, NOW, HOUR } from "./helpers/env.js";
import { fetchEspn, sweepEspn, withEspn, espnKey } from "../src/espn.js";
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

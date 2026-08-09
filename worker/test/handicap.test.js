// The late-joiner handicap, and the ranking it makes honest.
//
// Two properties hold the survivor pool together once joining stays open all
// season. First, somebody arriving in week six must not get a fresh roster
// while the August players work around what they have spent — the chalk of
// every missed week is spent for them. Second, the board must put the living
// above the dead, which is only fair once the first property removes the
// late joiner's edge. Each is easy to get subtly wrong and invisible when it
// is, so each is pinned here.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE } from "../src/cookies.js";
import { chalkRoster, rosterFor } from "../src/handicap.js";
import { scoreWeek } from "../src/scoring.js";
import {
  makeEnv, seedWeek, seedUser, seedSurvivorPick, forceLock, NOW, HOUR,
} from "./helpers/env.js";

const ORIGIN = "https://big12ology.com";

async function signedIn(env, userId) {
  const s = await session.create(env, userId);
  return `${SESSION_COOKIE}=${encodeURIComponent(s.raw)}`;
}

function req(path, { method = "GET", cookie, body } = {}) {
  const headers = { Accept: "application/json" };
  if (cookie) headers.Cookie = cookie;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers.Origin = ORIGIN;
  }
  return new Request(`${ORIGIN}${path}`, {
    method, headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const call = (env, ...a) => worker.fetch(req(...a), env, {});

/**
 * Four weeks whose chalk is known by construction.
 *
 * Week 1's biggest favourite is Texas Tech (-28), week 2's is Utah (-24),
 * week 3's is BYU (-20). Week 4 repeats Texas Tech as the biggest, which is
 * the case the "next largest" rule exists for.
 */
function season(env) {
  seedWeek(env, { week: 1, games: [
    { game_id: 101, home: "Texas Tech", away: "Sam Houston", spread_x2: -28 },
    { game_id: 102, home: "Kansas", away: "Iowa State", spread_x2: 6 },
    { game_id: 103, home: "TCU", away: "Utah", spread_x2: null },
  ] });
  seedWeek(env, { week: 2, games: [
    { game_id: 201, home: "Utah", away: "Idaho State", spread_x2: -24 },
    { game_id: 202, home: "Baylor", away: "Houston", spread_x2: -4 },
  ] });
  seedWeek(env, { week: 3, games: [
    { game_id: 301, home: "Cincinnati", away: "BYU", spread_x2: 20 },
    { game_id: 302, home: "Arizona", away: "Colorado", spread_x2: -2 },
  ] });
  seedWeek(env, { week: 4, games: [
    { game_id: 401, home: "Texas Tech", away: "Arizona State", spread_x2: -30 },
    { game_id: 402, home: "Kansas State", away: "TCU", spread_x2: -8 },
  ] });
  seedWeek(env, { week: 5, games: [
    { game_id: 501, home: "Colorado", away: "Iowa State", spread_x2: 5 },
    { game_id: 502, home: "West Virginia", away: "UCF", spread_x2: -9 },
  ] });
}

// ---------------------------------------------------------------- the chalk

test("the burned set is the biggest favourite of every week missed",
  async () => {
    const env = makeEnv();
    season(env);
    // The sign convention is the home spread, so a positive number means the
    // AWAY team is favoured — week 3's chalk is BYU, not Cincinnati.
    assert.deepEqual((await chalkRoster(env, 2026, 4)).map((b) => b.team),
      ["Texas Tech", "Utah", "BYU"]);
    assert.deepEqual((await chalkRoster(env, 2026, 2)).map((b) => b.team),
      ["Texas Tech"]);
  });

test("week 1 entrants are handicapped not at all", async () => {
  const env = makeEnv();
  season(env);
  assert.deepEqual(await chalkRoster(env, 2026, 1), []);
});

test("a repeated favourite falls through to the next biggest", async () => {
  // Week 4's chalk is Texas Tech again, already spent by week 1. Taking the
  // next largest keeps the handicap exactly one team per week missed; without
  // it, joining after a week whose favourite repeated would be a discount.
  const env = makeEnv();
  season(env);
  const burned = (await chalkRoster(env, 2026, 5)).map((b) => b.team);
  assert.deepEqual(burned, ["Texas Tech", "Utah", "BYU", "Kansas State"]);
  assert.equal(burned.length, 4, "four weeks missed did not cost four teams");
  assert.equal((await chalkRoster(env, 2026, 6)).length, 5);
});

test("a game with no line is never the chalk", async () => {
  // TCU–Utah in week 1 has no spread. An ungraded game cannot end a run, so
  // spending its favourite would take a team for nothing.
  const env = makeEnv();
  season(env);
  const w1 = await chalkRoster(env, 2026, 2);
  assert.equal(w1.length, 1);
  assert.equal(w1[0].team, "Texas Tech");
});

// ------------------------------------------------------------- enforcement

test("a late joiner cannot pick a team the chalk already spent for them",
  async () => {
    const env = makeEnv();
    season(env);
    seedUser(env, "late", { name: "October Arrival" });
    const cookie = await signedIn(env, "late");

    const bad = await call(env, "/api/survivor/pick", {
      method: "PUT", cookie,
      body: { week: 4, game_id: 401, team: "Texas Tech" },
    });
    assert.equal(bad.status, 409);
    const body = await bad.json();
    assert.equal(body.error, "team_spent_before_entry");
    assert.equal(body.entered_week, 4);
    assert.ok(body.burned.includes("Texas Tech"));

    // The other side of the same game is untouched by the handicap.
    const ok = await call(env, "/api/survivor/pick", {
      method: "PUT", cookie,
      body: { week: 4, game_id: 401, team: "Arizona State" },
    });
    assert.equal(ok.status, 200, await ok.text());
  });

test("entering fixes the handicap; it does not grow week by week",
  async () => {
    // Someone who entered in week 2 keeps a one-team handicap forever. If the
    // burned set were recomputed against the current week instead of the
    // entry week, a loyal player would be penalised for every week they
    // played — the exact opposite of the intent.
    const env = makeEnv();
    season(env);
    seedUser(env, "u", { name: "Steady" });
    seedSurvivorPick(env, "u", 2026, 2, 202, "Baylor");

    const r = await rosterFor(env, 2026, "u", 4);
    assert.equal(r.entered_week, 2);
    assert.equal(r.joining, false);
    assert.deepEqual(r.burned.map((b) => b.team), ["Texas Tech"]);

    // Week 1's chalk is Texas Tech and stays spent — but weeks 2 and 3 are
    // hers, and their chalk is not burned however long she keeps playing.
    const cookie = await signedIn(env, "u");
    for (const team of ["Utah", "BYU"]) {
      assert.ok(!r.burned.some((b) => b.team === team),
        `${team} was burned for a player who was there for it`);
    }
    const res = await call(env, "/api/survivor/pick", {
      method: "PUT", cookie,
      body: { week: 4, game_id: 401, team: "Arizona State" },
    });
    assert.equal(res.status, 200, await res.text());
  });

test("joining closes when the handicap would leave too little to pick from",
  async () => {
    const env = makeEnv();
    seedWeek(env, { week: 1, games: [
      { game_id: 101, home: "Texas Tech", away: "Sam Houston", spread_x2: -28 },
    ] });
    seedWeek(env, { week: 2, games: [
      { game_id: 201, home: "Utah", away: "Idaho State", spread_x2: -24 },
    ] });
    // Week 3 is the last week, and both its teams are already burned by the
    // handicap — there is no season left to play.
    seedWeek(env, { week: 3, games: [
      { game_id: 301, home: "Utah", away: "Texas Tech", spread_x2: -3 },
    ] });
    seedUser(env, "toolate", { name: "Too Late" });
    const cookie = await signedIn(env, "toolate");

    const res = await call(env, "/api/survivor/pick", {
      method: "PUT", cookie,
      body: { week: 3, game_id: 301, team: "Utah" },
    });
    assert.equal(res.status, 409);
    assert.equal((await res.json()).error, "join_closed");
  });

test("the roster and the close reach the client", async () => {
  const env = makeEnv();
  season(env);
  seedUser(env, "late", { name: "October Arrival" });
  const cookie = await signedIn(env, "late");
  const r = await (await call(env, "/api/survivor?week=4", { cookie })).json();
  assert.equal(r.entered_week, 4);
  assert.equal(r.joining, true);
  assert.equal(r.join_closed, false);
  assert.deepEqual(r.burned.map((b) => b.team), ["Texas Tech", "Utah", "BYU"]);
});

// ----------------------------------------------------------- alive-first

test("the living outrank the dead however deep the dead ran", async () => {
  // The wart alive-first exists to fix: under wins-first a ten-week run that
  // ended in November was crowned over a shorter run still standing, which is
  // not what a survivor pool means.
  const env = makeEnv();
  seedWeek(env, { week: 1, games: [
    { game_id: 101, home: "Texas Tech", away: "Sam Houston", spread_x2: -28 },
    { game_id: 102, home: "Kansas", away: "Iowa State", spread_x2: -6 },
  ] });
  seedWeek(env, { week: 2, games: [
    { game_id: 201, home: "Utah", away: "Idaho State", spread_x2: -24 },
    { game_id: 202, home: "Baylor", away: "Houston", spread_x2: -4 },
  ] });

  seedUser(env, "deep", { name: "Deep Run" });
  seedUser(env, "short", { name: "Still In" });

  // Deep Run banks week 1 and then loses week 2. Still In sits out week 1 —
  // weeks before a first pick never happened — and wins week 2.
  seedSurvivorPick(env, "deep", 2026, 1, 101, "Texas Tech");
  seedSurvivorPick(env, "deep", 2026, 2, 202, "Houston");
  seedSurvivorPick(env, "short", 2026, 2, 201, "Utah");

  forceLock(env, 2026, 1);
  forceLock(env, 2026, 2);
  await scoreWeek(env, 2026, 1, { games: {
    "101": [45, 3, true], "102": [30, 20, true] } });
  // Baylor 24 Houston 10: Deep Run picked Houston, so week 2 ends the run.
  await scoreWeek(env, 2026, 2, { games: {
    "201": [50, 7, true], "202": [24, 10, true] } });

  const rows = env.raw.prepare(
    `SELECT user_id, wins, alive, rank FROM survivor_board
      WHERE season = 2026 ORDER BY rank`).all();
  assert.equal(rows[0].user_id, "short", "an eliminated run outranked a live one");
  assert.equal(rows[0].alive, 1);
  assert.equal(rows[1].user_id, "deep");
  assert.equal(rows[1].wins, 1, "the ended run lost the win it banked");
});

test("wins still order the living among themselves", async () => {
  const env = makeEnv();
  seedWeek(env, { week: 1, games: [
    { game_id: 101, home: "Texas Tech", away: "Sam Houston", spread_x2: -28 },
  ] });
  seedWeek(env, { week: 2, games: [
    { game_id: 201, home: "Utah", away: "Idaho State", spread_x2: -24 },
  ] });

  seedUser(env, "both", { name: "Two Weeks" });
  seedUser(env, "one", { name: "One Week" });
  seedSurvivorPick(env, "both", 2026, 1, 101, "Texas Tech");
  seedSurvivorPick(env, "both", 2026, 2, 201, "Utah");
  seedSurvivorPick(env, "one", 2026, 2, 201, "Utah");

  forceLock(env, 2026, 1);
  forceLock(env, 2026, 2);
  await scoreWeek(env, 2026, 1, { games: { "101": [45, 3, true] } });
  await scoreWeek(env, 2026, 2, { games: { "201": [50, 7, true] } });

  const rows = env.raw.prepare(
    `SELECT user_id, wins, rank FROM survivor_board
      WHERE season = 2026 ORDER BY rank`).all();
  assert.equal(rows[0].user_id, "both");
  assert.equal(rows[0].wins, 2);
  assert.equal(rows[1].user_id, "one");
  assert.equal(rows[1].wins, 1);
});

// ------------------------------------------------------------ eligibility

/** Weeks 1..n, one lopsided game each, so entering at any week is possible. */
function longSeason(env, n) {
  const names = ["Texas Tech", "Utah", "BYU", "Kansas State", "Iowa State",
                 "Arizona", "Baylor", "TCU", "Houston", "Colorado"];
  for (let w = 1; w <= n; w++) {
    seedWeek(env, { week: w, games: [
      { game_id: w * 100 + 1, home: names[(w - 1) % names.length],
        away: `Opponent ${w}`, spread_x2: -20 },
      { game_id: w * 100 + 2, home: `Host ${w}`, away: `Visitor ${w}`,
        spread_x2: -4 },
    ] });
  }
}

test("entering after the cutoff is allowed, and is not on the leaderboard",
  async () => {
    // The hole this closes: in a survivor pool nearly everybody is dead by
    // December, so a last-week entrant who wins once can be the only player
    // alive. Alive-first would then hand them the season on one pick.
    const env = makeEnv();
    longSeason(env, 9);

    seedUser(env, "aug", { name: "August" });
    seedUser(env, "nov", { name: "November" });
    // August enters week 1 and is eliminated immediately. November enters
    // week 8, well past the cutoff, and wins.
    seedSurvivorPick(env, "aug", 2026, 1, 102, "Visitor 1");
    seedSurvivorPick(env, "nov", 2026, 8, 802, "Host 8");
    for (let w = 1; w <= 8; w++) forceLock(env, 2026, w);
    await scoreWeek(env, 2026, 1, { games: { "101": [40, 3, true],
                                             "102": [40, 3, true] } });
    await scoreWeek(env, 2026, 8, { games: { "801": [40, 3, true],
                                             "802": [40, 3, true] } });

    const rows = env.raw.prepare(
      `SELECT user_id, alive, ranked, rank FROM survivor_board
        WHERE season = 2026`).all();
    const by = Object.fromEntries(rows.map((r) => [r.user_id, r]));

    assert.equal(by.nov.alive, 1, "the late entrant should still be playing");
    assert.equal(by.nov.ranked, 0, "a week-8 entrant made the leaderboard");
    assert.equal(by.aug.ranked, 1, "an August entrant who lost early was "
      + "dropped from the leaderboard — eligibility is about when you "
      + "entered, not how long you lasted");
    assert.equal(by.aug.rank, 1, "the only ranked player is not rank 1");
  });

test("the two groups are ranked separately, not interleaved", async () => {
  const env = makeEnv();
  longSeason(env, 9);
  seedUser(env, "a", { name: "Early A" });
  seedUser(env, "b", { name: "Early B" });
  seedUser(env, "z", { name: "Latecomer" });
  seedSurvivorPick(env, "a", 2026, 1, 101, "Texas Tech");
  seedSurvivorPick(env, "b", 2026, 1, 102, "Host 1");
  seedSurvivorPick(env, "z", 2026, 8, 802, "Host 8");
  for (let w = 1; w <= 8; w++) forceLock(env, 2026, w);
  await scoreWeek(env, 2026, 1, { games: { "101": [40, 3, true],
                                           "102": [40, 3, true] } });
  await scoreWeek(env, 2026, 8, { games: { "801": [40, 3, true],
                                           "802": [40, 3, true] } });

  const ranked = env.raw.prepare(
    `SELECT user_id, rank FROM survivor_board
      WHERE season = 2026 AND ranked = 1 ORDER BY rank`).all();
  assert.deepEqual(ranked.map((r) => r.rank), [1, 1],
    "two live ranked players on one win each should share rank 1");
  const un = env.raw.prepare(
    `SELECT user_id, rank FROM survivor_board
      WHERE season = 2026 AND ranked = 0`).all();
  assert.equal(un.length, 1);
  assert.equal(un[0].rank, 1, "unranked entrants get their own sequence");
});

test("the board tells the client who is playing for what", async () => {
  const env = makeEnv();
  longSeason(env, 9);
  seedUser(env, "z", { name: "Latecomer" });
  seedSurvivorPick(env, "z", 2026, 8, 802, "Host 8");
  for (let w = 1; w <= 8; w++) forceLock(env, 2026, w);
  await scoreWeek(env, 2026, 8, { games: { "801": [40, 3, true],
                                           "802": [40, 3, true] } });

  const b = await (await call(env, "/api/survivor/board")).json();
  assert.equal(b.unranked, 1);
  assert.equal(b.ranked_entry_by, 6);
  assert.equal(b.rows[0].ranked, 0);
});

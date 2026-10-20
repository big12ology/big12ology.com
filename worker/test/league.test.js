// The league column (migration 0014): football, men's and women's basketball
// in one set of tables, each blind to the others.
//
// Two things to hold. Football must not notice: every row the old code wrote
// is football's, and a request that names no league means what it always did.
// And a basketball row must be invisible to every football read and untouched
// by every football write, which is what lets the pools share the schema.
//
// Since 0015 league is part of every key, so two leagues can share a season
// and a week number; most of these use different weeks anyway, so that a
// leak shows up as a wrong week rather than a coincidentally right one.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE } from "../src/cookies.js";
import { scoreWeek } from "../src/scoring.js";
import * as L from "../src/league.js";
import { makeEnv, seedWeek, seedUser, forceLock, NOW, HOUR } from "./helpers/env.js";

const ORIGIN = "https://big12ology.com";
const SEASON = 2026;

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

/** A men's basketball week, written the way the import writes one. Game 901
 * tips at `lockAt` (the week's first tip), 902 a day later unless told. */
function seedHoopsWeek(env, { week = 9, lockAt = NOW() + 48 * HOUR,
                              second = null } = {}) {
  const now = NOW();
  env.raw.prepare(
    `INSERT INTO weeks (league, season, week, published_at, lock_at,
                        game_count, pickable_count)
     VALUES ('mbb', ?, ?, ?, ?, 2, 2)`).run(SEASON, week, now - HOUR, lockAt);
  for (const [id, home, away, x2, at] of [
    [901, "Kansas", "Kansas State", -15, lockAt],
    [902, "Houston", "Arizona", 4, second ?? lockAt + 24 * HOUR]]) {
    env.raw.prepare(
      `INSERT INTO slate_games (league, season, week, game_id, home, away,
                                kickoff_at, spread_x2, b12, frozen_at)
       VALUES ('mbb', ?, ?, ?, ?, ?, ?, ?, 'both', ?)`)
      .run(SEASON, week, id, home, away, at, x2, now - HOUR);
  }
}

function rawPick(env, league, week, gameId, x2) {
  const now = NOW();
  env.raw.prepare(
    `INSERT INTO picks (league, user_id, season, week, game_id, side,
                        spread_x2, created_at, updated_at)
     VALUES (?, 'u1', ?, ?, ?, 'home', ?, ?, ?)`)
    .run(league, SEASON, week, gameId, x2, now, now);
}

test("the league names are the three pools, football by default", () => {
  assert.deepEqual(L.LEAGUES, ["cfb", "mbb", "wbb"]);
  assert.equal(L.parse(null), "cfb");
  assert.equal(L.parse(""), "cfb");
  assert.equal(L.parse("MBB"), "mbb");
  assert.equal(L.parse("nba"), null);
});

test("a row written without a league is football's", () => {
  const env = makeEnv();
  seedWeek(env);
  const w = env.raw.prepare(`SELECT league FROM weeks`).get();
  const g = env.raw.prepare(`SELECT DISTINCT league FROM slate_games`).all();
  assert.equal(w.league, "cfb");
  assert.deepEqual(g.map((r) => r.league), ["cfb"]);
});

test("a league that is not one of the three is refused", async () => {
  const env = makeEnv();
  seedWeek(env);
  for (const p of ["/api/slate?league=nba", "/api/leaderboard?league=x",
                   "/api/survivor/board?league=nfl", "/api/grid?league=1"]) {
    const r = await call(env, p);
    assert.equal(r.status, 400, p);
    assert.equal((await r.json()).error, "bad_league", p);
  }
  seedUser(env, "u1", { name: "Pat" });
  const cookie = await signedIn(env, "u1");
  const r = await call(env, "/api/picks", { method: "PUT", cookie,
    body: { league: "nba", week: 3, picks: {} } });
  assert.equal(r.status, 400);
});

test("a basketball week is invisible to football", async () => {
  const env = makeEnv();
  seedWeek(env, { season: SEASON, week: 3 });
  seedHoopsWeek(env);

  const hoops = await (await call(env, "/api/slate?league=mbb&week=9")).json();
  assert.deepEqual(hoops.games.map((g) => g.game_id), [901, 902]);

  // Football's own week, by default and by name, and never the basketball one.
  const cfb = await (await call(env, "/api/slate")).json();
  assert.equal(cfb.week, 3);
  assert.ok(cfb.games.every((g) => g.game_id < 900));
  assert.equal((await call(env, "/api/slate?week=9")).status, 404);
  assert.equal((await call(env, "/api/slate?league=cfb&week=9")).status, 404);
  // And the other way: women's has nothing at all.
  assert.equal((await call(env, "/api/slate?league=wbb")).status, 404);
});

test("picks are saved under their league and read back only there", async () => {
  const env = makeEnv();
  seedWeek(env, { season: SEASON, week: 3 });
  seedHoopsWeek(env);
  seedUser(env, "u1", { name: "Pat" });
  const cookie = await signedIn(env, "u1");

  const put = await call(env, "/api/picks", { method: "PUT", cookie,
    body: { league: "mbb", week: 9, picks: { 901: "home", 902: "away" } } });
  assert.equal(put.status, 200, await put.clone().text());
  const rows = env.raw.prepare(
    `SELECT league, game_id FROM picks ORDER BY game_id`).all();
  assert.deepEqual(rows.map((r) => [r.league, r.game_id]),
                   [["mbb", 901], ["mbb", 902]]);

  const mine = await (await call(env, "/api/picks?league=mbb&week=9",
                                 { cookie })).json();
  assert.deepEqual(mine.picks, { 901: "home", 902: "away" });
  const cfb = await (await call(env, "/api/picks?week=9", { cookie })).json();
  assert.deepEqual(cfb.picks, {});

  // A football card replaced in full leaves the basketball card alone.
  const fb = await call(env, "/api/picks", { method: "PUT", cookie,
    body: { week: 3, picks: { 401: "home" } } });
  assert.equal(fb.status, 200);
  assert.equal(env.raw.prepare(
    `SELECT COUNT(*) AS n FROM picks WHERE league = 'mbb'`).get().n, 2);
});

test("a survivor pick is saved under its league", async () => {
  const env = makeEnv();
  seedHoopsWeek(env);
  seedUser(env, "u1", { name: "Pat" });
  const cookie = await signedIn(env, "u1");
  const r = await call(env, "/api/survivor/pick", { method: "PUT", cookie,
    body: { league: "mbb", week: 9, game_id: 901, team: "Kansas" } });
  assert.equal(r.status, 200, await r.clone().text());
  const row = env.raw.prepare(`SELECT league, team FROM survivor_picks`).get();
  assert.deepEqual([row.league, row.team], ["mbb", "Kansas"]);
  const me = await (await call(env, "/api/survivor?league=mbb&week=9",
                               { cookie })).json();
  assert.equal(me.pick.team, "Kansas");
});

test("grading a basketball week builds basketball boards, and only those",
  async () => {
    const env = makeEnv();
    seedWeek(env, { season: SEASON, week: 3 });
    seedHoopsWeek(env);
    seedUser(env, "u1", { name: "Pat" });
    const cookie = await signedIn(env, "u1");
    await call(env, "/api/picks", { method: "PUT", cookie,
      body: { league: "mbb", week: 9, picks: { 901: "home", 902: "away" } } });
    forceLock(env, SEASON, 9);

    // Kansas by 20 covers -7.5; Arizona wins outright as a 2-point dog.
    const scores = { games: { 901: [80, 60, true], 902: [70, 75, true] } };
    const rep = await scoreWeek(env, SEASON, 9, scores, NOW(),
                                { league: "mbb" });
    assert.equal(rep.finals, 2);
    const res = env.raw.prepare(
      `SELECT DISTINCT league FROM results`).all().map((r) => r.league);
    assert.deepEqual(res, ["mbb"]);

    const hoops = await (await call(env, "/api/leaderboard?league=mbb")).json();
    assert.equal(hoops.rows.length, 1);
    assert.equal(hoops.rows[0].w, 2);
    const cfb = await (await call(env, "/api/leaderboard")).json();
    assert.equal(cfb.rows.length, 0);
  });

test("football and basketball can share a season and a week number", () => {
  const env = makeEnv();
  seedWeek(env, { season: SEASON, week: 3 });
  seedHoopsWeek(env, { week: 3 });
  const n = env.raw.prepare(
    `SELECT league, COUNT(*) AS n FROM weeks WHERE season = ? AND week = 3
      GROUP BY league ORDER BY league`).all(SEASON);
  assert.deepEqual(n.map((r) => [r.league, r.n]), [["cfb", 1], ["mbb", 1]]);
});

test("a basketball pick locks at its own game's tip, not the week's", () => {
  const env = makeEnv();
  seedUser(env, "u1", { name: "Pat" });
  // The week's first game tipped an hour ago; the second is tomorrow.
  seedHoopsWeek(env, { week: 9, lockAt: NOW() - HOUR, second: NOW() + 24 * HOUR });
  assert.throws(() => rawPick(env, "mbb", 9, 901, -15), /game_started/);
  rawPick(env, "mbb", 9, 902, 4);
  assert.equal(env.raw.prepare(
    `SELECT COUNT(*) AS n FROM picks WHERE league = 'mbb'`).get().n, 1);
});

test("football keeps its week lock", () => {
  const env = makeEnv();
  seedUser(env, "u1", { name: "Pat" });
  seedWeek(env, { season: SEASON, week: 3, lockAt: NOW() - HOUR,
                  games: [{ game_id: 401, home: "Iowa State", away: "Kansas",
                            spread_x2: -13, kickoff_at: NOW() + 24 * HOUR }] });
  assert.throws(() => rawPick(env, "cfb", 3, 401, -13), /week_locked/);
});

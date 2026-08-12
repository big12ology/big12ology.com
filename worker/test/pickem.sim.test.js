// Ten thousand random seasons of the pick'em, checked against the arithmetic.
//
// Sibling of survivor.sim.test.js, and the same argument: the boards are one
// derived statement each, and what breaks them is not the fixture anyone
// writes but the season nobody pictures — a week that is all pushes, a player
// whose only graded game voided, eleven people tied on nine wins, a game with
// no line sitting in the middle of a slate, a provisional account whose first
// week has just been graded.
//
// Three things get their own attention here because they are the three ways a
// pick'em board loses trust:
//
//   PUSH is not a loss. It leaves both terms of the percentage alone, so 7-3-1
//   reads 70% and not 63.6%.
//   VOID is not anything. Out of W, L, P and out of the denominator.
//   A PROVISIONAL account is scored but not published, and is published the
//   moment its first week is graded.
//
// The expected values are computed here from the raw scores with a different
// formulation than the engine uses — the engine works in the doubled integer
// spread, this works in points and half-points — so the two agreeing means
// something. Deterministic: a failure prints its seed, SIM_SEED=n replays it.
//
//     SIM_RUNS=10000 node --test test/pickem.sim.test.js

import test from "node:test";
import assert from "node:assert/strict";
import { chalk, room, promoteProvisional, scoreWeek } from "../src/scoring.js";
import { makeEnv, seedWeek, seedUser, seedPick, forceLock, NOW, HOUR }
  from "./helpers/env.js";

const RUNS = Number(process.env.SIM_RUNS || 300);
const ONLY = process.env.SIM_SEED ? Number(process.env.SIM_SEED) : null;
const SEASON = 2026;

const TEAMS = ["Arizona", "Arizona State", "Baylor", "BYU", "Cincinnati",
               "Colorado", "Houston", "Iowa State", "Kansas", "Kansas State",
               "Oklahoma State", "TCU", "Texas Tech", "UCF", "Utah",
               "West Virginia", "Notre Dame", "Auburn", "Iowa", "Tulane"];

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const int = (r, lo, hi) => lo + Math.floor(r() * (hi - lo + 1));

/**
 * Who covered, worked out in points rather than in the doubled integer the
 * engine uses.
 *
 * spread_x2 is an integer, so spread_x2 / 2 is exactly representable — a whole
 * number or a half — and the margin is a whole number, so this sum is exact
 * and a push is a real equality rather than a near-miss. That it is a
 * different formulation from src/ats.js is the point: a sign error copied into
 * the test would agree with a sign error in the code.
 */
function covered(homePoints, awayPoints, spreadX2) {
  const v = (homePoints - awayPoints) + spreadX2 / 2;
  return v > 0 ? "home" : v < 0 ? "away" : "push";
}

function buildSeason(r) {
  const weeks = [];
  const nWeeks = int(r, 2, 8);
  let gid = 5000;
  for (let w = 1; w <= nWeeks; w++) {
    const pool = [...TEAMS];
    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(r() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    const games = [];
    const nGames = int(r, 1, 8);
    for (let g = 0; g < nGames && pool.length >= 2; g++) {
      // A fifth of games carry no line. They are unpickable, they never get a
      // result row, and they must not reach any total.
      const hasLine = r() > 0.2;
      // A pick'em line (0) is a real thing and has no favorite, so the chalk
      // has to leave it alone. One game in ten.
      const sx = r() < 0.1 ? 0 : int(r, -30, 30);
      games.push({
        game_id: gid++, home: pool.pop(), away: pool.pop(),
        spread_x2: hasLine ? sx : null,
        kickoff_at: r() > 0.4 ? NOW() - 40 * HOUR : NOW() + 6 * HOUR,
      });
    }
    if (games.length) weeks.push({ week: w, games });
  }
  return weeks;
}

function buildPlayers(r, weeks) {
  const players = [];
  const n = int(r, 1, 12);
  for (let p = 0; p < n; p++) {
    // A fifth arrive provisional: scored, not published, promoted on their
    // first graded week.
    const status = r() < 0.2 ? "provisional" : "active";
    const picks = [];
    for (const { week, games } of weeks) {
      if (r() < 0.15) continue;                    // sat the week out
      for (const g of games) {
        if (g.spread_x2 == null) continue;         // picks_require_line
        if (r() < 0.2) continue;                   // left a game blank
        picks.push({ week, game_id: g.game_id,
                     side: r() < 0.5 ? "home" : "away",
                     spread_x2: g.spread_x2 });
      }
    }
    players.push({ id: `p${p}`, status, picks });
  }
  return players;
}

function buildScores(r, weeks) {
  const games = {};
  for (const { games: gs } of weeks) {
    for (const g of gs) {
      if (g.spread_x2 == null) continue;
      if (r() < 0.1) continue;                     // never arrives
      // Deliberately narrow, so exact pushes actually happen rather than
      // being a case the generator never reaches.
      const hp = int(r, 0, 40);
      const swing = int(r, -20, 20);
      games[String(g.game_id)] = [hp, Math.max(0, hp - swing), true];
    }
  }
  return { games };
}

/** Every graded game, as points, keyed by id. */
function gradedGames(env) {
  const rows = env.raw.prepare(
    `SELECT g.week, g.game_id, g.spread_x2, r.status, r.home_points,
            r.away_points
       FROM slate_games g
       JOIN results r ON r.season = g.season AND r.week = g.week
                     AND r.game_id = g.game_id
      WHERE g.season = ?`).all(SEASON);
  const out = new Map();
  for (const g of rows) out.set(g.game_id, g);
  return out;
}

/** What every player's card should come to, counted here from the picks. */
function expectedCards(env, graded, activeOnly) {
  const picks = env.raw.prepare(
    `SELECT p.user_id, p.week, p.game_id, p.side, u.status
       FROM picks p JOIN users u ON u.id = p.user_id
      WHERE p.season = ?`).all(SEASON);
  const byUserWeek = new Map();
  for (const p of picks) {
    if (activeOnly && p.status !== "active") continue;
    const g = graded.get(p.game_id);
    if (!g) continue;                              // ungraded: not on the card
    let outcome;
    if (g.status === "void") outcome = "void";
    else outcome = covered(g.home_points, g.away_points, g.spread_x2) === p.side
      ? "win"
      : covered(g.home_points, g.away_points, g.spread_x2) === "push"
        ? "push" : "loss";
    const k = `${p.user_id}|${p.week}`;
    if (!byUserWeek.has(k)) {
      byUserWeek.set(k, { user_id: p.user_id, week: p.week,
                          w: 0, l: 0, p: 0, v: 0 });
    }
    const rec = byUserWeek.get(k);
    rec[outcome === "win" ? "w" : outcome === "loss" ? "l"
       : outcome === "push" ? "p" : "v"]++;
  }
  return byUserWeek;
}

const pctOf = (w, l) => (w + l === 0 ? null : w / (w + l));

/** Ranks are dense on ties and start at 1, ordered w desc then pct desc. */
function assertRanking(rows, what) {
  if (!rows.length) return;
  const sorted = [...rows].sort((a, b) =>
    b.w - a.w
    || (b.pct == null ? -1 : 0) - (a.pct == null ? -1 : 0)
    || (b.pct ?? -1) - (a.pct ?? -1));
  assert.equal(Math.min(...rows.map((x) => x.rank)), 1,
    `${what}: ranks do not start at 1`);
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1], cur = sorted[i];
    const tied = prev.w === cur.w && prev.pct === cur.pct;
    if (tied) assert.equal(cur.rank, prev.rank, `${what}: tied cards differ`);
    else {
      assert.ok(cur.rank > prev.rank,
        `${what}: ${cur.w}/${cur.pct} outranked ${prev.w}/${prev.pct}`);
    }
  }
}

function snapshot(env) {
  const strip = (rows) => rows.map((r) => {
    const { computed_at, scored_at, ...rest } = r;
    return rest;
  });
  return JSON.stringify([
    strip(env.raw.prepare(
      `SELECT * FROM leaderboard_week ORDER BY week, user_id`).all()),
    strip(env.raw.prepare(
      `SELECT * FROM leaderboard_season ORDER BY user_id`).all()),
    strip(env.raw.prepare(
      `SELECT * FROM pick_scores ORDER BY user_id, week, game_id`).all()),
  ]);
}

/**
 * What the generated seasons actually contained.
 *
 * A fuzz run that never reaches a push, a void or a tied rank passes for the
 * wrong reason, and passing for the wrong reason is worse than failing. The
 * counts are printed with the result so the claim can be checked rather than
 * taken.
 */
const cover = {
  pushes: 0, voids: 0, splits: 0, pickems: 0, ties: 0,
  nullPct: 0, promoted: 0, unlockedSeasons: 0, emptyBoards: 0,
};

async function runOne(seed) {
  const r = rng(seed);
  const env = makeEnv();
  const weeks = buildSeason(r);
  for (const w of weeks) seedWeek(env, { season: SEASON, ...w });

  const players = buildPlayers(r, weeks);
  for (const p of players) {
    seedUser(env, p.id, { name: `Player ${p.id}`, status: p.status });
    for (const pk of p.picks) {
      seedPick(env, p.id, SEASON, pk.week, pk.game_id, pk.side, pk.spread_x2);
    }
  }

  const lockThrough = int(r, 0, weeks.length);
  const locked = [];
  for (const w of weeks) {
    if (w.week <= lockThrough) {
      forceLock(env, SEASON, w.week);
      locked.push(w.week);
    }
  }

  const scores = buildScores(r, weeks);
  for (const w of weeks) await scoreWeek(env, SEASON, w.week, scores);

  // --- an open week is never graded, in either table
  const leaked = env.raw.prepare(
    `SELECT COUNT(*) n FROM pick_scores s
       JOIN weeks w ON w.season = s.season AND w.week = s.week
      WHERE s.season = ? AND (w.lock_at IS NULL OR w.lock_at > ?)`)
    .get(SEASON, NOW() + 1);
  assert.equal(leaked.n, 0, "an unlocked week was scored");

  // --- a game with no line has no result and no score
  const unlined = env.raw.prepare(
    `SELECT COUNT(*) n FROM results r
       JOIN slate_games g ON g.season = r.season AND g.week = r.week
                         AND g.game_id = r.game_id
      WHERE r.season = ? AND g.spread_x2 IS NULL`).get(SEASON);
  assert.equal(unlined.n, 0, "a game with no line got a result row");

  const graded = gradedGames(env);
  if (!locked.length) cover.unlockedSeasons++;
  for (const g of graded.values()) {
    if (g.status === "void") { cover.voids++; continue; }
    if (g.spread_x2 === 0) cover.pickems++;
    if (covered(g.home_points, g.away_points, g.spread_x2) === "push") {
      cover.pushes++;
    }
  }

  // --- every pick_scores row is the outcome the points say it is
  const rows = env.raw.prepare(
    `SELECT s.user_id, s.week, s.game_id, s.outcome, p.side
       FROM pick_scores s
       JOIN picks p ON p.user_id = s.user_id AND p.season = s.season
                   AND p.week = s.week AND p.game_id = s.game_id
      WHERE s.season = ?`).all(SEASON);
  for (const row of rows) {
    const g = graded.get(row.game_id);
    assert.ok(g, `scored game ${row.game_id} has no result`);
    let want;
    if (g.status === "void") want = "void";
    else {
      const c = covered(g.home_points, g.away_points, g.spread_x2);
      want = c === "push" ? "push" : (c === row.side ? "win" : "loss");
    }
    assert.equal(row.outcome, want,
      `game ${row.game_id}: ${g.home_points}-${g.away_points} on `
      + `${g.spread_x2 / 2} picked ${row.side}`);
  }

  // --- the weekly board is those outcomes counted, active accounts only
  const wantWeek = expectedCards(env, graded, true);
  const gotWeek = env.raw.prepare(
    `SELECT * FROM leaderboard_week WHERE season = ?`).all(SEASON);
  assert.equal(gotWeek.length, wantWeek.size, "weekly board size");
  for (const row of gotWeek) {
    const e = wantWeek.get(`${row.user_id}|${row.week}`);
    assert.ok(e, `${row.user_id} week ${row.week} is on the board unearned`);
    assert.equal(row.w, e.w, "w");
    assert.equal(row.l, e.l, "l");
    assert.equal(row.p, e.p, "p");
    assert.equal(row.v, e.v, "v");
    // Push and void touch neither term. This is the arithmetic that decides
    // whether anyone believes the table.
    assert.equal(row.pct, pctOf(e.w, e.l), "pct");
  }
  for (const w of locked) {
    assertRanking(gotWeek.filter((x) => x.week === w), `week ${w}`);
  }
  for (const row of gotWeek) if (row.pct == null) cover.nullPct++;
  const seen = new Map();
  for (const row of gotWeek) {
    const k = `${row.week}|${row.rank}`;
    seen.set(k, (seen.get(k) || 0) + 1);
  }
  for (const n of seen.values()) if (n > 1) cover.ties++;
  if (!gotWeek.length) cover.emptyBoards++;

  // --- the season board is the weeks added up
  const wantSeason = new Map();
  for (const e of wantWeek.values()) {
    if (!wantSeason.has(e.user_id)) {
      wantSeason.set(e.user_id, { w: 0, l: 0, p: 0, v: 0, weeks: new Set() });
    }
    const t = wantSeason.get(e.user_id);
    t.w += e.w; t.l += e.l; t.p += e.p; t.v += e.v; t.weeks.add(e.week);
  }
  const gotSeason = env.raw.prepare(
    `SELECT * FROM leaderboard_season WHERE season = ?`).all(SEASON);
  assert.equal(gotSeason.length, wantSeason.size, "season board size");
  for (const row of gotSeason) {
    const t = wantSeason.get(row.user_id);
    assert.ok(t, `${row.user_id} is on the season board unearned`);
    assert.equal(row.w, t.w, "season w");
    assert.equal(row.l, t.l, "season l");
    assert.equal(row.p, t.p, "season p");
    assert.equal(row.v, t.v, "season v");
    assert.equal(row.pct, pctOf(t.w, t.l), "season pct");
    assert.equal(row.weeks_played, t.weeks.size, "weeks_played");
  }
  assertRanking(gotSeason, "season");

  // --- provisional accounts are scored and not published
  const hidden = env.raw.prepare(
    `SELECT COUNT(*) n FROM leaderboard_season b
       JOIN users u ON u.id = b.user_id
      WHERE b.season = ? AND u.status <> 'active'`).get(SEASON);
  assert.equal(hidden.n, 0, "a provisional account reached the public board");

  // --- the benchmarks, counted here from the results
  for (const w of locked) {
    const ck = await chalk(env, SEASON, w);
    let cw = 0, cl = 0, cp = 0, cv = 0;
    for (const g of graded.values()) {
      if (g.week !== w || g.spread_x2 === 0) continue;
      if (g.status === "void") { cv++; continue; }
      const c = covered(g.home_points, g.away_points, g.spread_x2);
      if (c === "push") { cp++; continue; }
      // The chalk always takes the favorite: home when the home spread is
      // negative, away when it is positive.
      c === (g.spread_x2 < 0 ? "home" : "away") ? cw++ : cl++;
    }
    if (ck) {
      assert.equal(ck.w, cw, `chalk w, week ${w}`);
      assert.equal(ck.l, cl, `chalk l, week ${w}`);
      assert.equal(ck.p, cp, `chalk p, week ${w}`);
      assert.equal(ck.v, cv, `chalk v, week ${w}`);
      assert.equal(ck.pct, pctOf(cw, cl), `chalk pct, week ${w}`);
    } else {
      assert.equal(cw + cl + cp + cv, 0, `chalk was null with ${cw + cl} games`);
    }

    const rm = await room(env, SEASON, w);
    const tally = new Map();
    for (const p of env.raw.prepare(
      `SELECT week, game_id, side FROM picks WHERE season = ? AND week = ?`)
      .all(SEASON, w)) {
      const t = tally.get(p.game_id) || { home: 0, away: 0 };
      t[p.side]++;
      tally.set(p.game_id, t);
    }
    let rw = 0, rl = 0, rp = 0, split = 0;
    for (const [gid, t] of tally) {
      const g = graded.get(gid);
      if (!g) continue;
      if (t.home === t.away) { split++; continue; }
      if (g.status === "void") continue;
      const c = covered(g.home_points, g.away_points, g.spread_x2);
      if (c === "push") { rp++; continue; }
      c === (t.home > t.away ? "home" : "away") ? rw++ : rl++;
    }
    cover.splits += split;
    if (rm) {
      assert.equal(rm.w, rw, `room w, week ${w}`);
      assert.equal(rm.l, rl, `room l, week ${w}`);
      assert.equal(rm.p, rp, `room p, week ${w}`);
      assert.equal(rm.split, split, `room split, week ${w}`);
      assert.equal(rm.pct, pctOf(rw, rl), `room pct, week ${w}`);
    } else {
      assert.equal(rw + rl + rp + split, 0,
        `room was null with ${rw + rl} decided games`);
    }
  }

  // --- scoring twice changes nothing
  const before = snapshot(env);
  for (const w of weeks) await scoreWeek(env, SEASON, w.week, scores);
  assert.equal(snapshot(env), before, "a second scoring run moved the board");

  // --- a provisional account is published once its first week is graded
  const wasProvisional = env.raw.prepare(
    `SELECT DISTINCT s.user_id FROM pick_scores s
       JOIN users u ON u.id = s.user_id
      WHERE s.season = ? AND u.status = 'provisional'`).all(SEASON);
  if (wasProvisional.length) {
    cover.promoted += wasProvisional.length;
    for (const w of locked) await promoteProvisional(env, SEASON, w);
    for (const u of wasProvisional) {
      const now = env.raw.prepare(`SELECT status FROM users WHERE id = ?`)
        .get(u.user_id);
      assert.equal(now.status, "active",
        `${u.user_id} has a graded week and is still provisional`);
    }
    for (const w of weeks) await scoreWeek(env, SEASON, w.week, scores);
    const after = env.raw.prepare(
      `SELECT COUNT(*) n FROM leaderboard_season WHERE season = ?`).get(SEASON);
    assert.ok(after.n >= gotSeason.length,
      "promoting an account removed somebody from the board");
  }
  return players.length;
}

test(`${RUNS} simulated pick'em seasons hold every rule`, async (t) => {
  let seasons = 0, cards = 0;
  for (let i = 0; i < RUNS; i++) {
    const seed = ONLY != null ? ONLY : i + 1;
    try {
      cards += await runOne(seed);
      seasons++;
    } catch (e) {
      e.message = `SIM_SEED=${seed} — ${e.message}`;
      throw e;
    }
    if (ONLY != null) break;
  }
  t.diagnostic(`${seasons} seasons, ${cards} cards`);
  t.diagnostic(`reached: ${JSON.stringify(cover)}`);
  // If the generator stopped producing an edge case, this test would keep
  // passing and stop meaning anything.
  for (const k of ["pushes", "voids", "splits", "pickems", "ties",
                   "nullPct", "promoted"]) {
    assert.ok(cover[k] > 0, `no simulated season reached: ${k}`);
  }
});

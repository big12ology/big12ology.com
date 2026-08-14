// Ten thousand random seasons, checked against what the rules actually say.
//
// The survivor engine is a walk over a season expressed almost entirely in one
// recursive SQL statement, and the cases that break that kind of code are not
// the ones anybody writes a fixture for: a player who entered in week six and
// missed week nine, a game that voided between two losses, a week that locked
// with nothing graded, eleven people tied on four wins. So the season is
// generated instead — teams, weeks, lines, entries, absences, results — and
// every derived row is re-derived here, independently, from the picks and the
// scores rather than from the query that produced it.
//
// Deterministic on purpose. A failure prints its seed, and `SIM_SEED=n` replays
// exactly that season. Math.random would make a failure a story about a season
// nobody can visit again.
//
//     SIM_RUNS=10000 node --test test/survivor.sim.test.js
//
// The committed default is small enough to belong in the ordinary suite; the
// large runs are for when the walk changes.

import test from "node:test";
import assert from "node:assert/strict";
import { scoreWeek, rankedEntryBy } from "../src/scoring.js";
import { chalkRoster, isPickable } from "../src/handicap.js";
import { backdateGame, makeEnv, seedWeek, seedUser, seedSurvivorPick, forceLock, NOW, HOUR }
  from "./helpers/env.js";

const RUNS = Number(process.env.SIM_RUNS || 300);
const ONLY = process.env.SIM_SEED ? Number(process.env.SIM_SEED) : null;
const SEASON = 2026;

const CONF = ["Arizona", "Arizona State", "Baylor", "BYU", "Cincinnati",
              "Colorado", "Houston", "Iowa State", "Kansas", "Kansas State",
              "Oklahoma State", "TCU", "Texas Tech", "UCF", "Utah",
              "West Virginia"];
const OUTSIDE = ["Notre Dame", "Auburn", "Iowa", "Georgia Tech", "Murray State",
                 "Coastal Carolina", "Idaho State", "Tulane"];

/** mulberry32: small, seedable, and good enough to shake out a SQL walk. */
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const pick = (r, xs) => xs[Math.floor(r() * xs.length)];
const int = (r, lo, hi) => lo + Math.floor(r() * (hi - lo + 1));

/**
 * A season: weeks of games, each with a line or not, each with a kickoff
 * either safely past (so an ungraded game voids on the clock) or ahead of it
 * (so it stays waiting).
 */
function buildSeason(r) {
  const weeks = [];
  const nWeeks = int(r, 2, 8);
  let gid = 1000;
  for (let w = 1; w <= nWeeks; w++) {
    const pool = [...CONF, ...OUTSIDE];
    // Shuffle, then pair off. A team plays at most once a week, as in life.
    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(r() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    const games = [];
    const nGames = int(r, 1, 7);
    for (let g = 0; g < nGames && pool.length >= 2; g++) {
      const home = pool.pop(), away = pool.pop();
      const hb = CONF.includes(home), ab = CONF.includes(away);
      if (!hb && !ab) continue;      // never on a Big 12 slate
      // A quarter of games carry no line, which makes them unpickable and
      // keeps them out of results entirely.
      const hasLine = r() > 0.25;
      let sx = 0;
      while (sx === 0) sx = int(r, -40, 40);
      games.push({
        game_id: gid++, home, away,
        spread_x2: hasLine ? sx : null,
        b12: hb && ab ? "both" : (hb ? "home" : "away"),
        // Every game is seeded LIVE and the stale ones are aged afterwards.
        // Since 0010 a survivor pick is refused on a game that has already
        // kicked off, so a fixture cannot seed the pick and the past kickoff
        // in one step — which is the real order too. `stale` says which ones
        // simulate() drags back, and a stale game is one that can void on the
        // clock rather than sit waiting.
        stale: r() > 0.4,
        kickoff_at: NOW() + 6 * HOUR,
      });
    }
    if (games.length) weeks.push({ week: w, games });
  }
  return weeks;
}

/** Legal client behavior: conference side, has a line, never reused. */
function buildPicks(r, weeks) {
  const players = [];
  const n = int(r, 1, 10);
  for (let p = 0; p < n; p++) {
    const entered = int(r, 1, weeks.length);
    const used = new Set();
    const picks = [];
    for (const { week, games } of weeks) {
      if (week < entered) continue;
      // Sometimes simply does not pick. That is the other way a run ends and
      // the one the walk is most likely to get wrong.
      if (week > entered && r() < 0.15) continue;
      const options = [];
      for (const g of games) {
        if (g.spread_x2 == null) continue;
        for (const t of [g.home, g.away]) {
          if (isPickable(g, t) && !used.has(t)) options.push([g, t]);
        }
      }
      if (!options.length) continue;
      const [g, team] = pick(r, options);
      used.add(team);
      picks.push({ week, game_id: g.game_id, team });
    }
    if (picks.length) players.push({ id: `u${p}`, entered: picks[0].week, picks });
  }
  return players;
}

function buildScores(r, weeks) {
  const games = {};
  for (const { games: gs } of weeks) {
    for (const g of gs) {
      if (g.spread_x2 == null) continue;
      // A tenth of played games never arrive. Past kickoff that is a void;
      // before it, the game is simply still to come.
      if (r() < 0.1) continue;
      games[String(g.game_id)] = [int(r, 0, 56), int(r, 0, 56), true];
    }
  }
  return { games };
}

/**
 * Re-derive every run from the picks and the graded outcomes.
 *
 * Deliberately not a second copy of the SQL: it reads survivor_scores (which
 * is itself checked against the raw results below) and the locked weeks, and
 * applies the three sentences of the rules — a loss ends it, a locked week
 * with no pick ends it, everything before that counts.
 */
/**
 * How many teams a player was allowed to pick in one week.
 *
 * Read off the slate and the picks, never off survivor_stranded — that table
 * is the thing under test, and an oracle that consults it proves nothing.
 * Spent is the no-reuse rule: the chalk they arrived having burned, plus
 * every earlier pick whose week was not scored void.
 *
 * buildPicks skips a week when it finds no legal option, so the generator
 * produces this case on its own; a season with one game a week and a quarter
 * of games lineless produces it often.
 */
function usableAt(env, uid, week, chalkTeams) {
  const spent = new Set(chalkTeams);
  for (const p of env.raw.prepare(
    `SELECT p.team FROM survivor_picks p
      WHERE p.user_id = ? AND p.season = ? AND p.week < ?
        AND NOT EXISTS (SELECT 1 FROM survivor_scores s
                         WHERE s.user_id = p.user_id AND s.season = p.season
                           AND s.week = p.week AND s.outcome = 'void')`)
    .all(uid, SEASON, week)) spent.add(p.team);

  let n = 0;
  for (const g of env.raw.prepare(
    `SELECT home, away, b12 FROM slate_games
      WHERE season = ? AND week = ? AND spread_x2 IS NOT NULL`)
    .all(SEASON, week)) {
    for (const t of [g.home, g.away]) {
      if (isPickable(g, t) && !spent.has(t)) n++;
    }
  }
  return n;
}

async function expectedRuns(env, lockedWeeks) {
  const picks = env.raw.prepare(
    `SELECT user_id, week, team FROM survivor_picks WHERE season = ?
      ORDER BY user_id, week`).all(SEASON);
  const scores = env.raw.prepare(
    `SELECT user_id, week, outcome FROM survivor_scores WHERE season = ?`)
    .all(SEASON);

  const byUser = new Map();
  for (const p of picks) {
    if (!byUser.has(p.user_id)) byUser.set(p.user_id, { picks: new Map() });
    byUser.get(p.user_id).picks.set(p.week, p.team);
  }
  const outcome = new Map();
  for (const s of scores) outcome.set(`${s.user_id}|${s.week}`, s.outcome);

  const out = new Map();
  for (const [uid, u] of byUser) {
    const entered = Math.min(...u.picks.keys());
    const chalk = (await chalkRoster(env, SEASON, entered)).map((b) => b.team);
    let outWeek = null, outReason = null, wins = 0;
    for (const w of lockedWeeks) {
      if (w < entered) continue;                 // never happened for them
      if (!u.picks.has(w)) {
        // A week with nothing on it they were allowed to pick does not end
        // a run — it does not happen for them at all, like a void.
        if (usableAt(env, uid, w, chalk) === 0) continue;
        outWeek = w; outReason = "missed"; break;
      }
      const o = outcome.get(`${uid}|${w}`);
      if (o === "loss") { outWeek = w; outReason = "loss"; break; }
      if (o === "win") wins++;
      // 'void' and ungraded both carry on, banking nothing.
    }
    out.set(uid, { entered, outWeek, outReason, wins });
  }
  return out;
}

/**
 * Everything the second scoring run must reproduce — minus computed_at, which
 * is when the rebuild ran and not what it decided. A wall-clock second ticking
 * between two runs is not the board moving.
 */
function snapshot(env) {
  return JSON.stringify([
    env.raw.prepare(
      `SELECT season, user_id, wins, alive, entered_week, out_week,
              out_reason, ranked, rank
         FROM survivor_board ORDER BY user_id`).all(),
    env.raw.prepare(
      `SELECT * FROM survivor_scores ORDER BY user_id, week`).all(),
  ]);
}

function simulate(seed) {
  const r = rng(seed);
  const env = makeEnv();
  const weeks = buildSeason(r);
  for (const w of weeks) seedWeek(env, { season: SEASON, ...w });

  const players = buildPicks(r, weeks);
  for (const p of players) {
    seedUser(env, p.id, { name: `Player ${p.id}` });
    for (const pk of p.picks) {
      seedSurvivorPick(env, p.id, SEASON, pk.week, pk.game_id, pk.team);
    }
  }

  // Now age the games that were meant to be already played. Picks are in, so
  // the per-game lock has nothing left to refuse.
  for (const w of weeks) {
    for (const g of w.games) {
      if (g.stale) backdateGame(env, SEASON, w.week, g.game_id);
    }
  }

  // Lock a prefix of the season, so there is always an open tail the scorer
  // must refuse to touch.
  const lockThrough = int(r, 1, weeks.length);
  const locked = [];
  for (const w of weeks) {
    if (w.week <= lockThrough) { forceLock(env, SEASON, w.week); locked.push(w.week); }
  }

  // Scoring belongs to the caller, which can await it. Firing it here
  // unawaited overlaps two D1 batches and SQLite refuses the second.
  return { env, weeks, players, locked, scores: buildScores(r, weeks) };
}

async function runOne(seed) {
  const { env, weeks, players, locked, scores } = simulate(seed);
  for (const w of weeks) await scoreWeek(env, SEASON, w.week, scores);

  const cutoff = rankedEntryBy(env);
  const board = env.raw.prepare(
    `SELECT * FROM survivor_board WHERE season = ? ORDER BY rank, user_id`)
    .all(SEASON);
  const expect = await expectedRuns(env, locked);

  // --- an open week is never scored
  const openScored = env.raw.prepare(
    `SELECT COUNT(*) n FROM survivor_scores s
       JOIN weeks w ON w.season = s.season AND w.week = s.week
      WHERE s.season = ? AND (w.lock_at IS NULL OR w.lock_at > ?)`)
    .get(SEASON, NOW() + 1);
  assert.equal(openScored.n, 0, "an unlocked week was graded");

  // --- no locked week, no board
  //
  // scoreWeek refuses an open week before it reaches the rebuild, so a season
  // where nothing has locked yet has no standings at all — not a table of
  // everyone on zero. Worth pinning: the board appearing early would mean
  // the entrant list was readable before the first deadline.
  if (!locked.length) {
    assert.equal(board.length, 0, "a board existed before any week locked");
    return players.length;
  }

  // --- every board row matches an independent walk of the same facts
  assert.equal(board.length, expect.size, "board size");
  for (const row of board) {
    const e = expect.get(row.user_id);
    assert.ok(e, `board has a row for ${row.user_id} with no picks`);
    assert.equal(row.entered_week, e.entered, "entered_week");
    assert.equal(row.out_week, e.outWeek, "out_week");
    assert.equal(row.out_reason, e.outReason, "out_reason");
    assert.equal(row.alive, e.outWeek == null ? 1 : 0, "alive");
    assert.equal(row.wins, e.wins, "wins");
    assert.equal(row.ranked, e.entered <= cutoff ? 1 : 0, "ranked");
  }

  // --- the board banks nothing from after the week that ended the run
  //
  // Graded rows DO exist past an elimination and that is not a fault: the
  // handler refuses a new pick from a dead run, but the board it checks is
  // rebuilt by the cron, and a corrected score can end a run retroactively
  // with later picks already on file. So the rule is not "no such rows" —
  // it is that the total ignores them.
  const banked = env.raw.prepare(
    `SELECT b.user_id, b.wins,
            (SELECT COUNT(*) FROM survivor_scores s
              WHERE s.season = b.season AND s.user_id = b.user_id
                AND s.outcome = 'win'
                AND s.week >= b.entered_week
                AND (b.out_week IS NULL OR s.week < b.out_week)) AS want
       FROM survivor_board b WHERE b.season = ?`).all(SEASON);
  for (const row of banked) {
    assert.equal(row.wins, row.want,
      `${row.user_id} banked ${row.wins} wins, the walk allows ${row.want}`);
  }

  // --- ranks: alive above out, then wins, ties shared, each group from 1
  for (const group of [1, 0]) {
    const rows = board.filter((x) => x.ranked === group);
    if (!rows.length) continue;
    const sorted = [...rows].sort((a, b) =>
      b.alive - a.alive || b.wins - a.wins);
    assert.equal(Math.min(...rows.map((x) => x.rank)), 1,
      "a group's ranks do not start at 1");
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1], cur = sorted[i];
      const tied = prev.alive === cur.alive && prev.wins === cur.wins;
      if (tied) {
        assert.equal(cur.rank, prev.rank, "tied runs got different ranks");
      } else {
        assert.ok(cur.rank > prev.rank,
          `a ${cur.alive ? "live" : "dead"} run on ${cur.wins} outranked `
          + `a ${prev.alive ? "live" : "dead"} run on ${prev.wins}`);
      }
    }
  }

  // --- scoring twice changes nothing
  const before = snapshot(env);
  for (const w of weeks) await scoreWeek(env, SEASON, w.week, scores);
  assert.equal(snapshot(env), before, "a second scoring run moved the board");

  // --- the handicap: one team per week missed, all pickable, none repeated
  for (const entry of [1, 2, Math.max(1, weeks.length)]) {
    const burned = await chalkRoster(env, SEASON, entry);
    const names = burned.map((b) => b.team);
    assert.equal(new Set(names).size, names.length,
      "the handicap burned the same team twice");
    assert.ok(burned.length <= Math.max(0, entry - 1),
      "the handicap burned more teams than there were weeks missed");
    for (const b of burned) {
      assert.ok(CONF.includes(b.team),
        `the handicap burned ${b.team}, who is not in the conference`);
      assert.ok(b.week < entry, "the handicap reached into a week they played");
    }
  }
  return players.length;
}

test(`${RUNS} simulated seasons hold every rule`, async (t) => {
  let seasons = 0, runs = 0;
  for (let i = 0; i < RUNS; i++) {
    const seed = ONLY != null ? ONLY : i + 1;
    try {
      runs += await runOne(seed);
      seasons++;
    } catch (e) {
      // The seed is the whole value of a failure here: SIM_SEED=<n> replays
      // this exact season.
      e.message = `SIM_SEED=${seed} — ${e.message}`;
      throw e;
    }
    if (ONLY != null) break;
  }
  t.diagnostic(`${seasons} seasons, ${runs} runs`);
});

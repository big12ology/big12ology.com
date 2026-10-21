// A men's basketball pools season, publisher to leaderboards: the pick'em and
// the survivor pool.
//
// The slates are the real 2025-26 conference season as basketball/bb_pickem.py
// publishes it (tools/make-hoops-fixture.py writes them, lines from this
// site's ratings standing in for a market nobody archived), and the finals
// are the real ones. This test takes them the way production does: the
// Worker imports each week from the repository through importWeek, players
// pick through the API before the games, each week then passes, and the
// basketball sweep (src/hoops.js) grades it. Every player's record is then
// checked against an independent count from the raw scores.
//
// The live basketball season is not the fixture's, so the files are served
// under whichever season the Worker is asking for, and every tip is moved
// into the future so the picks can be made before the games, as they would.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE } from "../src/cookies.js";
import { sweepLeague } from "../src/hoops.js";
import * as L from "../src/league.js";
import { makeEnv, seedUser, forceLock, NOW, HOUR } from "./helpers/env.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIX = join(HERE, "fixtures", "hoops-2026");
const ORIGIN = "https://big12ology.com";
const DAY = 24 * HOUR;

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The fixture, moved: every tip shifted so the first is a day from now, and
 * labeled with the season the Worker will ask for. */
function loadFixture(season) {
  const weeks = readdirSync(FIX).filter((f) => f.startsWith("week-")).sort()
    .map((f) => JSON.parse(readFileSync(join(FIX, f), "utf8")));
  const first = Math.min(...weeks.flatMap((w) => w.games.map((g) => g.kickoff_at)));
  const shift = NOW() + DAY - first;
  for (const w of weeks) {
    w.season = season;
    w.lock_at += shift;
    for (const g of w.games) {
      g.kickoff_at += shift;
      g.kickoff = new Date(g.kickoff_at * 1000).toISOString();
    }
  }
  const scores = JSON.parse(readFileSync(join(FIX, "scores.json"), "utf8"));
  scores.season = season;
  return { weeks, scores };
}

/** Who covered, in points: a different formulation from src/ats.js on
 * purpose, so a sign error copied into one would not hide in the other. */
function cover(hp, ap, x2) {
  const v = hp - ap + x2 / 2;
  return v > 0 ? "home" : v < 0 ? "away" : "push";
}

test("a basketball pools season, imported, picked, graded and ranked",
  async () => {
    const env = makeEnv();
    const season = L.seasonOf(env, "mbb");
    const { weeks, scores } = loadFixture(season);
    const files = new Map(weeks.map((w) => [
      `${env.RAW_ORIGIN}/basketball/pickem/mbb/${season}/week-${String(w.week).padStart(2, "0")}.json`,
      JSON.stringify(w)]));
    files.set(`${env.RAW_ORIGIN}/basketball/pickem/mbb/${season}/scores.json`,
              JSON.stringify(scores));
    const real = globalThis.fetch;
    globalThis.fetch = async (url) => {
      const u = String(url);
      if (files.has(u)) return new Response(files.get(u), { status: 200 });
      return new Response("not found", { status: 404 });
    };
    // ESPN has nothing to add: the scores file is the record here.
    env.ESPN_FETCH = async () => new Response(JSON.stringify({ events: [] }));
    try {
      // The Worker learns the season from the repository.
      const first = await sweepLeague(env, "wbb");
      assert.equal(first.imported, 0, "nothing published for women's");
      const r0 = await sweepLeague(env, "mbb");
      assert.equal(r0.imported, weeks.length);
      assert.equal(env.raw.prepare(
        `SELECT COUNT(*) AS n FROM slate_games WHERE league = 'mbb'`).get().n, 144);

      // Six players, random cards, every week, through the API.
      const rand = rng(20262027);
      const players = ["u1", "u2", "u3", "u4", "u5", "u6"];
      const cookies = {};
      for (const p of players) {
        seedUser(env, p, { name: `Player ${p}` });
        const s = await session.create(env, p);
        cookies[p] = `${SESSION_COOKIE}=${encodeURIComponent(s.raw)}`;
      }
      const picked = new Map();          // `${user}|${game}` -> side
      for (const w of weeks) {
        for (const p of players) {
          const card = {};
          for (const g of w.games) {
            if (g.spread_x2 == null || rand() < 0.15) continue;   // skips some
            card[g.game_id] = rand() < 0.5 ? "home" : "away";
          }
          const r = await worker.fetch(new Request(`${ORIGIN}/api/picks`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", Origin: ORIGIN,
                       Cookie: cookies[p] },
            body: JSON.stringify({ league: "mbb", week: w.week, picks: card }),
          }), env, {});
          assert.equal(r.status, 200, await r.clone().text());
          for (const [gid, side] of Object.entries(card)) {
            picked.set(`${p}|${gid}`, side);
          }
        }
      }

      // The survivor pool, through the API, in the same weeks: one team a
      // week to win a named game, never the same team twice, and now and
      // then a week skipped, which is a miss and the end of a run. Picks are
      // all made before any game, as a player could; a pick after a run has
      // ended is simply never reached by the walk.
      // Four of the six take the biggest favorite they have not spent, as a
      // careful player does, so runs last; two pick at random, so they end.
      const svPicks = new Map();          // `${user}|${week}` -> {game, team}
      for (const [pi, p] of players.entries()) {
        const careful = pi < 4;
        const used = new Set();
        for (const w of weeks) {
          if (rand() < 0.08) continue;                       // a missed week
          const options = [];
          for (const g of w.games) {
            for (const t of [g.home, g.away]) {
              if (used.has(t)) continue;
              // The spread is home's, doubled: negative is home favored.
              const edge = g.spread_x2 == null ? 0
                : (t === g.home ? -g.spread_x2 : g.spread_x2);
              options.push([g, t, edge]);
            }
          }
          if (!options.length) continue;
          options.sort((a, b) => b[2] - a[2]);
          const [g, t] = careful ? options[0]
                                 : options[Math.floor(rand() * options.length)];
          const r = await worker.fetch(new Request(`${ORIGIN}/api/survivor/pick`, {
            method: "PUT",
            headers: { "Content-Type": "application/json", Origin: ORIGIN,
                       Cookie: cookies[p] },
            body: JSON.stringify({ league: "mbb", week: w.week,
                                   game_id: g.game_id, team: t }),
          }), env, {});
          assert.equal(r.status, 200, await r.clone().text());
          used.add(t);
          svPicks.set(`${p}|${w.week}`, { game: g.game_id, team: t,
                                          home: g.home });
        }
      }

      // Each week passes and is graded, in order.
      for (const w of weeks) {
        forceLock(env, season, w.week, NOW() - 2 * DAY);
        await sweepLeague(env, "mbb");
      }

      // The independent count.
      const want = {};
      for (const p of players) want[p] = { w: 0, l: 0, p: 0 };
      for (const w of weeks) {
        for (const g of w.games) {
          const [hp, ap, done] = scores.games[String(g.game_id)];
          if (!done || g.spread_x2 == null) continue;
          const c = cover(hp, ap, g.spread_x2);
          for (const p of players) {
            const side = picked.get(`${p}|${g.game_id}`);
            if (!side) continue;
            if (c === "push") want[p].p++;
            else if (c === side) want[p].w++;
            else want[p].l++;
          }
        }
      }
      const board = await (await worker.fetch(new Request(
        `${ORIGIN}/api/leaderboard?league=mbb`), env, {})).json();
      assert.equal(board.rows.length, players.length);
      for (const row of board.rows) {
        const exp = want[row.user_id];
        assert.deepEqual([row.w, row.l, row.p], [exp.w, exp.l, exp.p],
                         `${row.user_id}'s record`);
      }
      assert.deepEqual(board.weeks, weeks.map((w) => w.week));
      // The survivor walk, independently: from the first week a player
      // picked, every week is a win, or a loss or a missed week, which ends
      // the run; wins count only before the end.
      const sv = await (await worker.fetch(new Request(
        `${ORIGIN}/api/survivor/board?league=mbb`), env, {})).json();
      const svRows = new Map(sv.rows.map((r) => [r.user_id, r]));
      let eliminated = 0, alive = 0, missed = 0, lost = 0, longest = 0;
      for (const p of players) {
        const mine = weeks.filter((w) => svPicks.has(`${p}|${w.week}`));
        if (!mine.length) {
          assert.equal(svRows.has(p), false, `${p} never entered`);
          continue;
        }
        const entered = mine[0].week;
        let wins = 0, outWeek = null, outReason = null;
        for (const w of weeks) {
          if (w.week < entered) continue;
          const pk = svPicks.get(`${p}|${w.week}`);
          if (!pk) { outWeek = w.week; outReason = "missed"; break; }
          const [hp, ap] = scores.games[String(pk.game)];
          const homeWon = hp > ap;
          if ((pk.team === pk.home) === homeWon) wins++;
          else { outWeek = w.week; outReason = "loss"; break; }
        }
        const row = svRows.get(p);
        assert.ok(row, `${p} is on the survivor board`);
        assert.deepEqual(
          [row.entered_week, row.wins, row.alive, row.out_week, row.out_reason],
          [entered, wins, outWeek == null ? 1 : 0, outWeek, outReason],
          `${p}'s survivor run`);
        if (outWeek == null) alive++; else eliminated++;
        if (outReason === "missed") missed++;
        if (outReason === "loss") lost++;
        longest = Math.max(longest, wins);
      }
      assert.equal(sv.alive, alive);
      // The season has to have exercised the walk, or agreeing proves little:
      // runs of several weeks, ended both ways.
      assert.ok(longest >= 3, `the longest run was ${longest} wins`);
      assert.ok(lost > 0, "nobody lost a pick");
      assert.ok(missed > 0, "nobody missed a week");

      // And football's board never heard of any of it.
      const cfb = await (await worker.fetch(new Request(
        `${ORIGIN}/api/leaderboard`), env, {})).json();
      assert.equal(cfb.rows.length, 0);
      const cfbSv = await (await worker.fetch(new Request(
        `${ORIGIN}/api/survivor/board`), env, {})).json();
      assert.equal(cfbSv.rows.length, 0);
    } finally {
      globalThis.fetch = real;
    }
  });

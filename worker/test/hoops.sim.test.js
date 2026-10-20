// A men's basketball pick'em season, publisher to leaderboard.
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

test("a basketball pick'em season, imported, picked, graded and ranked",
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
      // And football's board never heard of any of it.
      const cfb = await (await worker.fetch(new Request(
        `${ORIGIN}/api/leaderboard`), env, {})).json();
      assert.equal(cfb.rows.length, 0);
    } finally {
      globalThis.fetch = real;
    }
  });

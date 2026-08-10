// The hourly cron, end to end, against a stubbed origin.
//
// Everything downstream of it has tests. It has none, and it is the only code
// on the site that runs with nobody watching — which is also why both of the
// bugs it has produced were found by hand rather than by anything here:
//
//   A Worker deployed in October imported October and nothing else, so the
//   season history was a single point and the week-by-week chart had nothing
//   to draw.
//
//   The import skipped weeks it had already seen, so when the publisher
//   started carrying the conference side of each game the whole season kept a
//   NULL column with nothing to suggest anything was wrong.
//
// Neither showed up as an error. Both are the same shape: the run reports
// success and the database quietly lacks something. So what is asserted here
// is mostly about the state after the run rather than what the run returned.
//
// The Pages origin is stubbed rather than reached. It is the same JSON the
// publisher writes — tiebreaker/pickem.py's shape, checked by
// tiebreaker/tests/test_pickem.py — so the two ends meet at a file format both
// sides have tests for.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import { makeEnv, seedWeek, NOW, HOUR } from "./helpers/env.js";

const SEASON = 2026;
const ORIGIN = "https://big12ology.github.io";

/**
 * A season the publisher could have written, and the scores file the build
 * writes beside it.
 *
 * `lockAt` is per week so a run can be placed anywhere in the season: weeks
 * before `through` are locked and played, the rest are still ahead.
 */
function publisher(weeks) {
  const slates = new Map();
  const scores = { season: SEASON, games: {} };
  for (const w of weeks) {
    slates.set(w.week, {
      season: SEASON, week: w.week,
      status: "published",
      lock_at: w.lock_at,
      game_count: w.games.length,
      pickable_count: w.games.filter((g) => g.spread_x2 != null).length,
      games: w.games.map((g) => ({
        game_id: g.game_id, home: g.home, away: g.away,
        kickoff: new Date(g.kickoff_at * 1000).toISOString(),
        kickoff_at: g.kickoff_at,
        spread_x2: g.spread_x2,
        b12: g.b12 ?? "both",
        ...(g.spread_x2 == null ? { unpickable: "no_line" } : {}),
      })),
    });
    for (const g of w.games) {
      if (g.spread_x2 == null || !g.played) continue;
      scores.games[String(g.game_id)] = [g.home_points, g.away_points, true];
    }
  }
  return { slates, scores };
}

/** Serve it, counting what was asked for. */
function serve({ slates, scores }, opts = {}) {
  const hits = { slates: new Map(), scores: 0 };
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.endsWith("/tiebreaker/pickem-scores.json")) {
      hits.scores++;
      if (opts.scoresDown) return new Response("", { status: 503 });
      return new Response(JSON.stringify(scores), { status: 200 });
    }
    const m = u.match(/\/pools\/data\/(\d+)\/week-(\d+)\.json$/);
    if (m) {
      const wk = Number(m[2]);
      hits.slates.set(wk, (hits.slates.get(wk) || 0) + 1);
      const s = slates.get(wk);
      if (!s) return new Response("not found", { status: 404 });
      return new Response(JSON.stringify(s), { status: 200 });
    }
    throw new Error(`the cron reached somewhere unexpected: ${u}`);
  };
  return hits;
}

function env0() {
  const env = makeEnv();
  env.SEASON = String(SEASON);
  env.PAGES_ORIGIN = ORIGIN;
  return env;
}

const run = (env) => worker.scheduled({}, env, { waitUntil() {} });

const rows = (env, sql, ...a) => env.raw.prepare(sql).all(...a);
const one = (env, sql, ...a) => env.raw.prepare(sql).get(...a);

/**
 * A season as the publisher would have written it by now: every week through
 * `through` locked and played, plus the one ahead whose slate exists.
 *
 * publish_slate writes the earliest week still holding an unplayed game, so
 * the files on disk are 1..current — never the whole season in August. A
 * fixture that published week fourteen in week one would be testing an import
 * against a shape the publisher cannot produce, and would freeze a lock_at
 * months before the TV windows are set.
 */
function season(through, { lastWeek = through + 1 } = {}) {
  const weeks = [];
  for (let w = 1; w <= lastWeek; w++) {
    const played = w <= through;
    const kickoff = NOW() + (played ? -1 : 1) * (24 + w) * HOUR;
    weeks.push({
      week: w,
      lock_at: kickoff,
      games: [
        { game_id: w * 10 + 1, home: "Utah", away: "BYU", spread_x2: -7,
          kickoff_at: kickoff, played, home_points: 28, away_points: 10 },
        { game_id: w * 10 + 2, home: "Kansas", away: "TCU", spread_x2: 5,
          kickoff_at: kickoff, played, home_points: 17, away_points: 24 },
        // One game a week has no line. It must never get a result row.
        { game_id: w * 10 + 3, home: "Baylor", away: "Sam Houston",
          spread_x2: null, kickoff_at: kickoff, played },
      ],
    });
  }
  return weeks;
}

// ------------------------------------------------------------------- import

test("a cold start in October imports the whole season, not just October",
  async () => {
    // The first bug this file exists for. A Worker deployed mid-season used to
    // know only the current week, which left the history a single point.
    const env = env0();
    const hits = serve(publisher(season(7)));
    await run(env);

    const have = rows(env, `SELECT week FROM weeks WHERE season = ? ORDER BY week`,
                      SEASON).map((r) => r.week);
    assert.deepEqual(have, [1, 2, 3, 4, 5, 6, 7, 8],
      "the cron did not backfill the season behind it");
    // Every locked week is scored, and the one ahead is not.
    for (const w of [1, 2, 3, 4, 5, 6, 7]) {
      const n = one(env, `SELECT COUNT(*) n FROM results WHERE season = ? AND week = ?`,
                    SEASON, w);
      assert.equal(n.n, 2, `week ${w} did not grade both lined games`);
    }
    assert.equal(
      one(env, `SELECT COUNT(*) n FROM results WHERE season = ? AND week = 8`, SEASON).n,
      0, "an unlocked week was graded");
    assert.ok(hits.scores > 0, "the scores file was never read");
  });

test("a game with no line never gets a result row", async () => {
  const env = env0();
  serve(publisher(season(4)));
  await run(env);
  const junk = one(env,
    `SELECT COUNT(*) n FROM results r
       JOIN slate_games g ON g.season = r.season AND g.week = r.week
                         AND g.game_id = r.game_id
      WHERE g.spread_x2 IS NULL`);
  assert.equal(junk.n, 0, "an unlined game was graded");
});

test("a second run changes nothing at all", async () => {
  const env = env0();
  serve(publisher(season(6)));
  await run(env);
  const snap = () => JSON.stringify([
    rows(env, `SELECT season, week, source_sha256, lock_at, game_count,
                      pickable_count FROM weeks ORDER BY week`),
    rows(env, `SELECT * FROM slate_games ORDER BY week, game_id`),
    rows(env, `SELECT season, week, game_id, home_points, away_points, status,
                      ats, source_hash, revision FROM results
                ORDER BY week, game_id`),
  ]);
  const before = snap();
  await run(env);
  assert.equal(snap(), before, "a second cron run moved something");
  // And specifically: no result was revised, which is what a spurious rewrite
  // would look like on the board a week later.
  const rev = one(env, `SELECT MAX(revision) m FROM results`);
  assert.equal(rev.m, 1, "a result was revised by an identical re-run");
});

test("one missing week does not hide every week after it", async () => {
  // The walk stops when the publisher runs out, which is how it knows where
  // the season ends. A single gap — a week nobody published, a numbering that
  // skips — must not be mistaken for the end, or the rest of the season
  // silently stops importing and nothing says so.
  const env = env0();
  const p = publisher(season(7));
  p.slates.delete(5);
  serve(p);
  await run(env);

  const have = rows(env, `SELECT week FROM weeks WHERE season = ? ORDER BY week`,
                    SEASON).map((r) => r.week);
  assert.deepEqual(have, [1, 2, 3, 4, 6, 7, 8],
    "a gap in the published weeks truncated the import");
});

test("the walk stops at the frontier instead of running to the cap",
  async () => {
    // Two consecutive misses end it. Without that the run would fetch every
    // week to MAX_WEEK every hour, which is twenty-five requests to prove
    // nothing.
    const env = env0();
    const hits = serve(publisher(season(3)));
    await run(env);
    const asked = [...hits.slates.keys()].sort((a, b) => a - b);
    // From 0: college football has a week 0 and the publisher writes one.
    // A miss there costs one fetch and does not end the walk, because the
    // counter only stops on two in a row.
    assert.deepEqual(asked, [0, 1, 2, 3, 4, 5, 6],
      "the walk did not start at zero and stop two weeks past the last one");
  });

test("a republished week that gains a field is re-read", async () => {
  // The second bug. The import used to skip weeks it already had, so a slate
  // that started carrying the conference side could never deliver it and the
  // column stayed NULL all season with nothing to show for it.
  const env = env0();
  const early = publisher(season(3));
  for (const s of early.slates.values()) {
    for (const g of s.games) delete g.b12;      // the publisher before the field
  }
  serve(early);
  await run(env);
  assert.equal(
    one(env, `SELECT COUNT(*) n FROM slate_games WHERE b12 IS NOT NULL`).n, 0,
    "the fixture did not actually start without the column");

  serve(publisher(season(3)));                  // the publisher after it
  await run(env);
  assert.equal(
    one(env, `SELECT COUNT(*) n FROM slate_games WHERE b12 IS NULL`).n, 0,
    "a re-published slate did not deliver its new field");
});

test("a frozen line is never overwritten, and the run survives the attempt",
  async () => {
    // slate_games_frozen aborts the batch. What matters is that the cron logs
    // it and carries on rather than wedging on the same week every hour.
    const env = env0();
    serve(publisher(season(2)));
    await run(env);

    const moved = publisher(season(2));
    for (const s of moved.slates.values()) {
      for (const g of s.games) if (g.spread_x2 != null) g.spread_x2 += 4;
    }
    serve(moved);
    await run(env);

    const line = one(env,
      `SELECT spread_x2 FROM slate_games WHERE season = ? AND week = 1
        AND game_id = 11`, SEASON);
    assert.equal(line.spread_x2, -7, "a frozen line moved");
  });

test("a late line fills in, because that transition is the one allowed",
  async () => {
    const env = env0();
    const tuesday = publisher(season(0, { lastWeek: 2 }));
    serve(tuesday);
    await run(env);
    assert.equal(
      one(env, `SELECT spread_x2 FROM slate_games WHERE game_id = 13`).spread_x2,
      null, "the fixture did not start unlined");

    const thursday = publisher(season(0, { lastWeek: 2 }));
    for (const s of thursday.slates.values()) {
      for (const g of s.games) if (g.spread_x2 == null) g.spread_x2 = -3;
    }
    serve(thursday);
    await run(env);
    assert.equal(
      one(env, `SELECT spread_x2 FROM slate_games WHERE game_id = 13`).spread_x2,
      -3, "a game that gained a market never became playable");
  });

test("a week 0 slate is imported like any other", async () => {
  // The 2026 season opens on August 29 with a single game, nine days before
  // Labor Day, and the publisher numbers it week 0 under the same
  // Tuesday-to-Monday rule the rest of the site uses. The cron used to start
  // at one, so that file would have been written every Tuesday and read
  // never: no slate, no picks, and nothing anywhere saying a week was
  // missing.
  const env = env0();
  const weeks = season(0, { lastWeek: 1 });
  weeks.unshift({
    week: 0,
    lock_at: NOW() - 26 * HOUR,
    games: [{ game_id: 1, home: "TCU", away: "North Carolina", spread_x2: -14,
              kickoff_at: NOW() - 26 * HOUR, played: true,
              home_points: 31, away_points: 20 }],
  });
  serve(publisher(weeks));
  await run(env);

  const have = rows(env, `SELECT week FROM weeks WHERE season = ? ORDER BY week`,
                    SEASON).map((r) => r.week);
  assert.ok(have.includes(0), "week 0 was never imported");
  assert.equal(
    one(env, `SELECT COUNT(*) n FROM slate_games WHERE season = ? AND week = 0`,
        SEASON).n, 1, "week 0's game did not land");
  // And it grades, so an opener is a real week rather than a decorative one.
  // TCU by 7, winning by 11: the favourite covers.
  assert.equal(
    one(env, `SELECT ats FROM results WHERE season = ? AND week = 0`, SEASON).ats,
    "home", "week 0 was imported but never scored");
});

// ------------------------------------------------------------------ scoring

test("the run converges: the board is the same however many times it runs",
  async () => {
    const env = env0();
    serve(publisher(season(5)));
    await run(env); await run(env); await run(env);
    const board = rows(env,
      `SELECT user_id, w, l, p, v, rank FROM leaderboard_season ORDER BY user_id`);
    // No players in this fixture, so the assertion is about the benchmark
    // tables and the weeks ledger rather than the standings.
    assert.deepEqual(board, []);
    const scored = rows(env,
      `SELECT week, scored_rev FROM weeks WHERE season = ? AND scored_at IS NOT NULL
        ORDER BY week`, SEASON);
    assert.equal(scored.length, 5, "not every locked week was scored");
    for (const s of scored) {
      assert.ok(s.scored_rev >= 1, `week ${s.week} was never marked scored`);
    }
  });

test("a scores file that is down does not corrupt what is already there",
  async () => {
    const env = env0();
    serve(publisher(season(4)));
    await run(env);
    const before = JSON.stringify(rows(env,
      `SELECT * FROM results ORDER BY week, game_id`));

    serve(publisher(season(4)), { scoresDown: true });
    await run(env);              // must not throw, must not void the season
    assert.equal(
      JSON.stringify(rows(env, `SELECT * FROM results ORDER BY week, game_id`)),
      before, "an outage at the scores file rewrote the results");
  });

test("a corrected score is a revision, and it moves the board", async () => {
  const env = env0();
  serve(publisher(season(3)));
  await run(env);
  const first = one(env,
    `SELECT home_points, revision, ats FROM results WHERE game_id = 11`);
  assert.equal(first.revision, 1);

  const fixed = publisher(season(3));
  fixed.scores.games["11"] = [3, 28, true];     // the other way round
  serve(fixed);
  await run(env);
  const after = one(env,
    `SELECT home_points, revision, ats FROM results WHERE game_id = 11`);
  assert.equal(after.home_points, 3, "a corrected score never landed");
  assert.equal(after.revision, 2, "a correction was not recorded as a revision");
  assert.notEqual(after.ats, first.ats, "the correction did not change who covered");
});

test("the cron never reaches the apex, only the Pages origin", async () => {
  // Fetching big12ology.com from inside the Worker loops a request back
  // through our own route. cert-watch.yml and compare-live.sh use the same
  // trick for the same reason, and it is the kind of thing a refactor undoes.
  const env = env0();
  const seen = [];
  const inner = serve(publisher(season(3)));
  const wrapped = globalThis.fetch;
  globalThis.fetch = async (url) => { seen.push(String(url)); return wrapped(url); };
  await run(env);
  assert.ok(seen.length > 0, "the cron fetched nothing");
  for (const u of seen) {
    assert.ok(u.startsWith(ORIGIN),
      `the cron fetched ${u}, which is not the Pages origin`);
  }
  assert.ok(inner.scores > 0);
});

// ------------------------------------------------------------- write budget

test("a quiet hour writes nothing at all", async () => {
  // The operational invariant, and the one that decides whether this survives
  // its first weekend. D1's free plan allows 100,000 rows written a day and
  // returns errors rather than throttling once that is gone, so an idle cron
  // run has to cost nothing — the crons fire 29 times on a Saturday whether
  // or not a single result has moved.
  //
  // Measured before the fix, at twenty players over fourteen weeks: an idle
  // pass wrote 9,534 rows, MORE than the 5,244 of the very first pass, because
  // the first had empty tables to delete from. 276,000 a day against an
  // allowance of 100,000.
  const env = env0();
  serve(publisher(season(6)));
  await run(env);

  let written = 0;
  const realPrepare = env.raw.prepare.bind(env.raw);
  env.raw.prepare = (sql) => {
    const st = realPrepare(sql);
    const realRun = st.run.bind(st);
    st.run = (...a) => {
      const r = realRun(...a);
      written += r.changes || 0;
      return r;
    };
    return st;
  };

  await run(env);
  assert.equal(written, 0,
    `an unchanged cron run wrote ${written} rows; it must write none`);
});

test("a result that moves rebuilds its own game, not the whole week",
  async () => {
    // The other half of the budget. Rebuilding every pick of the week because
    // one Saturday-afternoon final arrived costs players x games per run; the
    // game that moved costs players. That is the difference between a ceiling
    // around a hundred and fifty players and one above a thousand.
    const env = env0();
    serve(publisher(season(3)));
    await run(env);

    let deletes = [];
    const realPrepare = env.raw.prepare.bind(env.raw);
    env.raw.prepare = (sql) => {
      if (/DELETE FROM pick_scores/i.test(sql)) deletes.push(sql);
      return realPrepare(sql);
    };

    const fixed = publisher(season(3));
    fixed.scores.games["11"] = [3, 40, true];      // one game, corrected
    serve(fixed);
    await run(env);

    assert.ok(deletes.length > 0, "nothing was rebuilt after a correction");
    for (const sql of deletes) {
      assert.match(sql, /game_id IN/,
        "the whole week's pick_scores were deleted for one changed game");
    }
  });

// ------------------------------------------------------------------ health

test("health reports a locked week nobody has graded", async () => {
  // The signal a monitor watches. "The database answered SELECT 1" is true of
  // a Worker whose cron died a week ago, which is the failure worth catching:
  // the API keeps serving and the boards quietly stop moving.
  const env = env0();
  const weeks = season(2);
  serve(publisher(weeks));

  // Locked, never scored — the shape of a cron that is not running.
  seedWeek(env, { season: SEASON, week: 1,
                  lockAt: NOW() - 3 * HOUR,
                  games: [{ game_id: 11, home: "Utah", away: "BYU",
                            spread_x2: -7, kickoff_at: NOW() - 3 * HOUR }] });

  const before = await (await worker.fetch(
    new Request("https://big12ology.com/api/health"), env, {})).json();
  assert.equal(before.ok, true, "health should still be up");
  assert.equal(before.unscored, 1, "a locked ungraded week was not reported");
  assert.ok(before.waiting_s > 2 * 3600,
    `waiting_s was ${before.waiting_s}, expected about three hours`);

  // Once the cron has run, the signal clears.
  await run(env);
  const after = await (await worker.fetch(
    new Request("https://big12ology.com/api/health"), env, {})).json();
  assert.equal(after.unscored, 0, "still reporting an ungraded week after a run");
  assert.equal(after.waiting_s, 0);
  assert.ok(after.weeks > 0, "health does not say how many weeks it has");
});

test("health is quiet out of season", async () => {
  // Nothing locked, nothing overdue. A monitor that cried every day from
  // January to August would be turned off by February.
  const env = env0();
  const body = await (await worker.fetch(
    new Request("https://big12ology.com/api/health"), env, {})).json();
  assert.equal(body.ok, true);
  assert.equal(body.unscored, 0);
  assert.equal(body.waiting_s, 0);
});

// --------------------------------------------------------------- heartbeat

test("a finished run pings the dead man's switch", async () => {
  const env = env0();
  env.HEARTBEAT_URL = "https://kuma.example/api/push/TOKEN";
  const inner = serve(publisher(season(3)));
  const pings = [];
  const wrapped = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    if (u.startsWith("https://kuma.example/")) { pings.push(u); return new Response("ok"); }
    return wrapped(url, init);
  };

  await run(env);
  assert.equal(pings.length, 1, `${pings.length} heartbeats for one run`);
  const u = new URL(pings[0]);
  assert.equal(u.searchParams.get("status"), "up");
  assert.match(u.searchParams.get("msg") || "", /weeks/,
    "the heartbeat carried no summary");
  assert.ok(inner.scores > 0);
});

test("a run that fails stays silent", async () => {
  // The property that makes this worth having. If the cron reported its own
  // failures, every way it can die without running at all — a deleted
  // schedule, a suspended account, a Worker that will not start — would be
  // invisible. Absence has to be the alarm.
  const env = env0();
  env.HEARTBEAT_URL = "https://kuma.example/api/push/TOKEN";
  const pings = [];
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.startsWith("https://kuma.example/")) { pings.push(u); return new Response("ok"); }
    throw new Error("the origin is down");
  };

  await run(env);                       // must not throw
  assert.equal(pings.length, 0,
    "a failed run still told the monitor it was fine");
});

test("no push URL configured is not an error", async () => {
  const env = env0();               // HEARTBEAT_URL unset
  serve(publisher(season(2)));
  await run(env);
  assert.ok(rows(env, `SELECT week FROM weeks WHERE season = ?`, SEASON).length > 0,
    "the run did not complete without a monitor configured");
});

test("a monitor that is down never breaks scoring", async () => {
  const env = env0();
  env.HEARTBEAT_URL = "https://kuma.example/api/push/TOKEN";
  const inner = serve(publisher(season(3)));
  const wrapped = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    if (String(url).startsWith("https://kuma.example/")) {
      throw new Error("monitoring host unreachable");
    }
    return wrapped(url, init);
  };

  await run(env);
  assert.equal(
    one(env, `SELECT COUNT(*) n FROM results WHERE season = ?`, SEASON).n > 0,
    true, "scoring did not survive the monitoring host being down");
  void inner;
});

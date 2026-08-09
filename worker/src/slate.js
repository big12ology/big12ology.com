// Getting a week out of git and into D1, and reading it back out again.
//
// The published slate is a committed file — tiebreaker/pickem/<year>/week-NN.json,
// written by the Tuesday build and deployed with the site. That is the durable
// record, and D1 is a serving cache of it. Stated as a property: losing the
// database entirely costs the picks and nothing else, because every line, every
// kickoff and every lock time can be re-imported from the repository.
//
// The file is fetched from the PAGES ORIGIN, not from big12ology.com. Two
// reasons, both practical: the apex is behind our own Worker route, so a
// subrequest to it is a loop through ourselves; and the Pages origin is not
// cached by the edge, so a slate published four minutes ago is visible. It is
// the same trick cert-watch.yml and compare-live.sh already use.

import { ats } from "./ats.js";

/** Where the week lives once assemble.sh has copied it. */
export function slateUrl(env, season, week) {
  const nn = String(week).padStart(2, "0");
  return `${env.PAGES_ORIGIN}/pools/data/${season}/week-${nn}.json`;
}

export function scoresUrl(env, season) {
  return `${env.PAGES_ORIGIN}/tiebreaker/pickem-scores.json`;
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256",
                                         new TextEncoder().encode(text));
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Import a published week.
 *
 * Insert-only for the games, by design. The frozen-line trigger will abort any
 * attempt to move a spread that already has one, so this does not need to be
 * careful — it needs to be honest, and let the database refuse. The one update
 * it does perform, filling a NULL spread in, is the transition the trigger
 * explicitly permits: a game published on Tuesday with no market becoming
 * playable on Thursday.
 *
 * Returns what changed, so the cron log says something useful.
 */
export async function importWeek(env, season, week) {
  const r = await fetch(slateUrl(env, season, week), {
    headers: { Accept: "application/json" },
    cf: { cacheTtl: 0 },
  });
  if (!r.ok) return { ok: false, reason: `fetch_${r.status}` };
  const text = await r.text();
  const hash = await sha256Hex(text);

  let slate;
  try { slate = JSON.parse(text); } catch { return { ok: false, reason: "bad_json" }; }
  if (!slate || !Array.isArray(slate.games)) {
    return { ok: false, reason: "bad_shape" };
  }

  const existing = await env.DB.prepare(
    `SELECT source_sha256, lock_at FROM weeks WHERE season = ? AND week = ?`)
    .bind(season, week).first();
  if (existing && existing.source_sha256 === hash) {
    return { ok: true, unchanged: true, games: 0 };
  }

  const now = Math.floor(Date.now() / 1000);
  const stmts = [];

  // The week row. lock_at is guarded by weeks_lock_monotonic, so an upstream
  // file that somehow pushed the lock later will abort the batch rather than
  // reopen a week that has already started.
  stmts.push(env.DB.prepare(
    `INSERT INTO weeks (season, week, status, published_at, lock_at,
                        game_count, pickable_count, source_sha256)
     VALUES (?, ?, 'published', ?, ?, ?, ?, ?)
     ON CONFLICT(season, week) DO UPDATE SET
       lock_at        = excluded.lock_at,
       game_count     = excluded.game_count,
       pickable_count = excluded.pickable_count,
       source_sha256  = excluded.source_sha256`)
    .bind(season, week, now, slate.lock_at ?? null,
          slate.game_count ?? slate.games.length,
          slate.pickable_count ??
            slate.games.filter((g) => g.spread_x2 != null).length,
          hash));

  for (const g of slate.games) {
    stmts.push(env.DB.prepare(
      `INSERT INTO slate_games (season, week, game_id, home, away, kickoff_at,
                                spread_x2, spread_raw, books, frozen_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(season, week, game_id) DO UPDATE SET
         -- The COALESCE argument order is the whole behaviour here, so:
         --
         --   ours NULL, theirs 5   -> 5, and the trigger stays quiet because
         --                            nobody could have acted on a line that
         --                            did not exist. This is the Tuesday-
         --                            with-no-market game becoming playable.
         --   ours -13, theirs -13  -> unchanged, so a re-import is a no-op.
         --   ours -13, theirs -20  -> the write is attempted and
         --                            slate_games_frozen ABORTS THE BATCH.
         --                            Written this way round on purpose: with
         --                            the arguments reversed, ours won
         --                            silently and a republished file that
         --                            disagreed about the number produced no
         --                            signal at all. The line was safe and
         --                            the disagreement was invisible, which
         --                            is the half of the property that
         --                            actually needed the trigger.
         --   ours -13, theirs NULL -> ours, quietly. An upstream that drops a
         --                            line must not wedge the cron into
         --                            failing the same import every hour.
         spread_x2  = COALESCE(excluded.spread_x2, slate_games.spread_x2),
         -- Audit columns keep what they first saw: spread_raw is the
         -- un-rounded mean that PRODUCED the frozen number, and it drifts
         -- between fetches even when the rounded line does not.
         spread_raw = COALESCE(slate_games.spread_raw, excluded.spread_raw),
         books      = COALESCE(slate_games.books, excluded.books)`)
      .bind(season, week, g.game_id, g.home, g.away, g.kickoff_at,
            g.spread_x2 ?? null, g.spread_raw ?? null, g.books ?? null, now));
  }

  // A D1 batch is one implicit transaction: a trigger abort rolls back
  // everything, so a bad file cannot half-import a week.
  try {
    await env.DB.batch(stmts);
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
  return { ok: true, games: slate.games.length, hash };
}

export function isLocked(week, now = Math.floor(Date.now() / 1000)) {
  return week && week.lock_at != null && week.lock_at <= now;
}

/**
 * The week a reader should see by default: the earliest published week that
 * has not finished, else the latest there is.
 */
export async function currentWeek(env, season) {
  const soon = await env.DB.prepare(
    `SELECT week FROM weeks
      WHERE season = ? AND (lock_at IS NULL OR lock_at > ?)
      ORDER BY week LIMIT 1`).bind(season, Math.floor(Date.now() / 1000)).first();
  if (soon) return soon.week;
  const last = await env.DB.prepare(
    `SELECT week FROM weeks WHERE season = ? ORDER BY week DESC LIMIT 1`)
    .bind(season).first();
  return last ? last.week : null;
}

/**
 * A week, shaped exactly as tiebreaker/site_pools/app.js expects it.
 *
 * The consensus is attached HERE and only when the week is locked. It is the
 * one number on the site that would change how people play if it leaked
 * early: a late picker could simply follow the room, and the whole reason the
 * slate locks at once is that nobody picks on more information than anybody
 * else. Withholding it in the client would not be withholding it.
 */
export async function readSlate(env, season, week) {
  const w = await env.DB.prepare(
    `SELECT * FROM weeks WHERE season = ? AND week = ?`).bind(season, week).first();
  if (!w) return null;

  const { results: games } = await env.DB.prepare(
    `SELECT g.game_id, g.home, g.away, g.kickoff_at, g.spread_x2,
            r.home_points, r.away_points, r.status AS rstatus, r.ats
       FROM slate_games g
       LEFT JOIN results r
         ON r.season = g.season AND r.week = g.week AND r.game_id = g.game_id
      WHERE g.season = ? AND g.week = ?
      ORDER BY g.kickoff_at, g.game_id`).bind(season, week).all();

  const locked = isLocked(w);
  let consensus = {};
  if (locked) {
    const { results } = await env.DB.prepare(
      `SELECT game_id,
              SUM(side = 'home') AS home,
              SUM(side = 'away') AS away
         FROM picks WHERE season = ? AND week = ?
        GROUP BY game_id`).bind(season, week).all();
    for (const c of results || []) {
      consensus[c.game_id] = { home: c.home || 0, away: c.away || 0 };
    }
  }

  return {
    season, week: w.week, status: w.status, lock_at: w.lock_at, locked,
    game_count: w.game_count, pickable_count: w.pickable_count,
    games: (games || []).map((g) => {
      const out = {
        game_id: g.game_id, home: g.home, away: g.away,
        kickoff: new Date(g.kickoff_at * 1000).toISOString(),
        kickoff_at: g.kickoff_at,
        spread_x2: g.spread_x2,
      };
      if (g.spread_x2 == null) out.unpickable = "no_line";
      if (g.rstatus === "final" || g.rstatus === "void") {
        out.result = { home_points: g.home_points, away_points: g.away_points,
                       ats: g.ats };
      }
      if (locked && consensus[g.game_id]) out.consensus = consensus[g.game_id];
      return out;
    }),
  };
}

/** Grade one game without touching the database. Used by scoring and tests. */
export function grade(homePoints, awayPoints, spreadX2) {
  if (spreadX2 == null) return "void";
  return ats(homePoints, awayPoints, spreadX2);
}

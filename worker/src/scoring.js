// Scoring, as a pure recompute.
//
// Nothing in here is incremental. Every run rebuilds `pick_scores` and both
// leaderboards from `picks ⋈ slate_games ⋈ results`, which makes re-running it
// definitionally a no-op and makes a corrected score propagate on its own. The
// alternative — adjusting totals as results land — is how a board drifts from
// the picks it claims to summarise, and a board nobody trusts is worse than no
// board.
//
// Three rules that decide whether people believe the numbers:
//
//   PUSH is its own column. Not a loss. It is excluded from both terms of the
//   percentage, so 7-3-1 reads 70% and not 63.6%. Folding pushes into losses
//   is the most reliable way for a pick'em to lose its players — they know
//   they did not lose, and the table says they did.
//
//   VOID is a game that stopped existing: it vanished from the scores file, or
//   it is thirty-six hours past kickoff with no result. Excluded from W, L, P
//   and from the denominator. Nobody is punished for a cancellation.
//
//   A WEEK THAT HAS NOT LOCKED IS NEVER SCORED. An early final on an open week
//   would let a player infer a result before the deadline, and worse, the
//   consensus would become visible. The guard is unconditional.

import { ats } from "./ats.js";

const VOID_AFTER = 36 * 3600;

async function hashOf(obj) {
  const buf = await crypto.subtle.digest("SHA-256",
    new TextEncoder().encode(JSON.stringify(obj)));
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Pull the scores file the tiebreaker build writes on every run.
 *
 * Shape: {season, games: {"401756846": [home, away, completed], ...}}.
 * The Worker holds no CFBD key and never will; this file is the only score
 * source, and it costs the project nothing because the build already had the
 * data in hand.
 */
export async function fetchScores(env) {
  const r = await fetch(`${env.PAGES_ORIGIN}/tiebreaker/pickem-scores.json`, {
    headers: { Accept: "application/json" }, cf: { cacheTtl: 0 },
  });
  if (!r.ok) throw new Error(`scores_${r.status}`);
  return r.json();
}

/**
 * Write results for one locked week, then rebuild everything downstream.
 * Returns a small report; the cron logs it.
 */
export async function scoreWeek(env, season, week, scores,
                                now = Math.floor(Date.now() / 1000)) {
  const w = await env.DB.prepare(
    `SELECT lock_at FROM weeks WHERE season = ? AND week = ?`)
    .bind(season, week).first();
  if (!w) return { skipped: "no_such_week" };
  // The guard. Never scores an open week, whatever the caller thinks.
  if (w.lock_at == null || w.lock_at > now) return { skipped: "not_locked" };

  const { results: games } = await env.DB.prepare(
    `SELECT game_id, kickoff_at, spread_x2 FROM slate_games
      WHERE season = ? AND week = ?`).bind(season, week).all();

  const stmts = [];
  let finals = 0, voids = 0, changed = 0;

  for (const g of games || []) {
    const raw = scores.games ? scores.games[String(g.game_id)] : null;
    const [hp, ap, completed] = raw || [null, null, false];

    let status, atsValue, home = null, away = null;
    if (completed && hp != null && ap != null && g.spread_x2 != null) {
      status = "final";
      home = hp; away = ap;
      atsValue = ats(hp, ap, g.spread_x2);
      finals++;
    } else if (!raw || now > g.kickoff_at + VOID_AFTER) {
      // Gone from the file, or so far past kickoff that no result is coming.
      // A game with no line is void too: there was nothing to be right about.
      status = "void"; atsValue = "void";
      voids++;
    } else {
      continue;   // still to come; no row, so the card says "waiting"
    }

    const src = await hashOf([status, home, away, atsValue, g.spread_x2]);
    const prev = await env.DB.prepare(
      `SELECT source_hash FROM results
        WHERE season = ? AND week = ? AND game_id = ?`)
      .bind(season, week, g.game_id).first();
    if (prev && prev.source_hash === src) continue;   // genuinely unchanged
    changed++;

    stmts.push(env.DB.prepare(
      `INSERT INTO results (season, week, game_id, home_points, away_points,
                            status, ats, source_hash, revision, scored_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
       ON CONFLICT(season, week, game_id) DO UPDATE SET
         home_points = excluded.home_points,
         away_points = excluded.away_points,
         status      = excluded.status,
         ats         = excluded.ats,
         source_hash = excluded.source_hash,
         -- A corrected score is a revision, not a silent movement: the board
         -- moving after the fact is then explicable from this column rather
         -- than merely mysterious.
         revision    = results.revision + 1,
         scored_at   = excluded.scored_at`)
      .bind(season, week, g.game_id, home, away, status, atsValue, src, now));
  }

  if (stmts.length) await env.DB.batch(stmts);

  await rebuildPickScores(env, season, week);
  await rebuildWeekBoard(env, season, week, now);
  await rebuildSeasonBoard(env, season, now);

  await env.DB.prepare(
    `UPDATE weeks SET scored_at = ?, scored_rev = scored_rev + 1
      WHERE season = ? AND week = ?`).bind(now, season, week).run();

  return { finals, voids, changed };
}

/**
 * One statement, derived. `pick_scores` is never written by a handler and
 * never adjusted — it is deleted and recreated from the join, so it cannot
 * disagree with the picks it summarises.
 */
async function rebuildPickScores(env, season, week) {
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM pick_scores WHERE season = ? AND week = ?`)
      .bind(season, week),
    env.DB.prepare(
      `INSERT INTO pick_scores (user_id, season, week, game_id, outcome)
       SELECT p.user_id, p.season, p.week, p.game_id,
              CASE WHEN r.ats = 'void' THEN 'void'
                   WHEN r.ats = 'push' THEN 'push'
                   WHEN r.ats = p.side THEN 'win'
                   ELSE 'loss' END
         FROM picks p
         JOIN results r ON r.season = p.season AND r.week = p.week
                       AND r.game_id = p.game_id
        WHERE p.season = ? AND p.week = ?`).bind(season, week),
  ]);
}

// Only 'active' players appear. A provisional account's picks are scored and
// counted towards becoming active — they are simply not published yet.
const PUBLISHABLE = `u.status = 'active'`;

async function rebuildWeekBoard(env, season, week, now) {
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM leaderboard_week WHERE season = ? AND week = ?`)
      .bind(season, week),
    env.DB.prepare(
      `INSERT INTO leaderboard_week
         (season, week, user_id, w, l, p, v, pct, rank, computed_at)
       SELECT season, week, user_id, w, l, p, v, pct,
              -- No user_id in the ordering. It is a unique column, so
              -- including it makes every row a distinct rank and two
              -- identical records come back 2nd and 3rd. Presentation order
              -- among ties is the reading query's job, not the rank's.
              RANK() OVER (ORDER BY w DESC, pct DESC NULLS LAST),
              ?
         FROM (
           SELECT s.season, s.week, s.user_id,
                  SUM(s.outcome = 'win')  AS w,
                  SUM(s.outcome = 'loss') AS l,
                  SUM(s.outcome = 'push') AS p,
                  SUM(s.outcome = 'void') AS v,
                  CASE WHEN SUM(s.outcome IN ('win','loss')) = 0 THEN NULL
                       ELSE 1.0 * SUM(s.outcome = 'win')
                            / SUM(s.outcome IN ('win','loss')) END AS pct
             FROM pick_scores s
             JOIN users u ON u.id = s.user_id
            WHERE s.season = ? AND s.week = ? AND ${PUBLISHABLE}
            GROUP BY s.user_id)`).bind(now, season, week),
  ]);
}

async function rebuildSeasonBoard(env, season, now) {
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM leaderboard_season WHERE season = ?`).bind(season),
    env.DB.prepare(
      `INSERT INTO leaderboard_season
         (season, user_id, w, l, p, v, pct, weeks_played, rank, computed_at)
       SELECT season, user_id, w, l, p, v, pct, weeks_played,
              RANK() OVER (ORDER BY w DESC, pct DESC NULLS LAST),
              ?
         FROM (
           SELECT s.season, s.user_id,
                  SUM(s.outcome = 'win')  AS w,
                  SUM(s.outcome = 'loss') AS l,
                  SUM(s.outcome = 'push') AS p,
                  SUM(s.outcome = 'void') AS v,
                  COUNT(DISTINCT s.week)  AS weeks_played,
                  CASE WHEN SUM(s.outcome IN ('win','loss')) = 0 THEN NULL
                       ELSE 1.0 * SUM(s.outcome = 'win')
                            / SUM(s.outcome IN ('win','loss')) END AS pct
             FROM pick_scores s
             JOIN users u ON u.id = s.user_id
            WHERE s.season = ? AND ${PUBLISHABLE}
            GROUP BY s.user_id)`).bind(now, season),
  ]);
}

/**
 * Promote anyone who has finished a scored week.
 *
 * This is the whole of the anti-throwaway design and it is deliberately mild:
 * it does not stop anybody signing up, it removes the payoff for doing it
 * twenty times. A real new player waits one week; a board-spammer never
 * appears at all. Anonymous OAuth cannot do better than this and pretending
 * otherwise would be the mistake.
 */
export async function promoteProvisional(env, season, week) {
  await env.DB.prepare(
    `UPDATE users SET status = 'active'
      WHERE status = 'provisional'
        AND id IN (SELECT DISTINCT user_id FROM pick_scores
                    WHERE season = ? AND week = ?)`).bind(season, week).run();
}

/**
 * The chalk: what always taking the favourite would have scored.
 *
 * A benchmark row, computed from the same results everyone else is measured
 * on. tiebreaker/scorecard.py already makes this comparison for the models;
 * this is the same idea for the players. A pick'em board with no baseline
 * flatters everyone above .500 and tells you nothing.
 */
export async function chalk(env, season, week = null) {
  const sql = `
    SELECT SUM(CASE WHEN r.ats = 'push' THEN 0
                    WHEN r.ats = 'void' THEN 0
                    WHEN (g.spread_x2 < 0 AND r.ats = 'home')
                      OR (g.spread_x2 > 0 AND r.ats = 'away') THEN 1
                    ELSE 0 END) AS w,
           SUM(CASE WHEN r.ats IN ('push','void') THEN 0
                    WHEN (g.spread_x2 < 0 AND r.ats = 'home')
                      OR (g.spread_x2 > 0 AND r.ats = 'away') THEN 0
                    ELSE 1 END) AS l,
           SUM(r.ats = 'push') AS p,
           SUM(r.ats = 'void') AS v
      FROM results r
      JOIN slate_games g ON g.season = r.season AND g.week = r.week
                        AND g.game_id = r.game_id
     WHERE r.season = ? AND g.spread_x2 IS NOT NULL AND g.spread_x2 <> 0
       ${week == null ? "" : "AND r.week = ?"}`;
  const bind = week == null ? [season] : [season, week];
  const row = await env.DB.prepare(sql).bind(...bind).first();
  if (!row || (row.w == null && row.l == null)) return null;
  const w = row.w || 0, l = row.l || 0;
  return { w, l, p: row.p || 0, v: row.v || 0,
           pct: w + l === 0 ? null : w / (w + l) };
}

/** Every locked week of a season, scored oldest first. */
export async function scoreAll(env, season, now = Math.floor(Date.now() / 1000)) {
  const scores = await fetchScores(env);
  const { results } = await env.DB.prepare(
    `SELECT week FROM weeks
      WHERE season = ? AND lock_at IS NOT NULL AND lock_at <= ?
      ORDER BY week`).bind(season, now).all();
  const report = [];
  for (const { week } of results || []) {
    report.push({ week, ...(await scoreWeek(env, season, week, scores, now)) });
    await promoteProvisional(env, season, week);
  }
  return report;
}

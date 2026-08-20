// Scoring, as a pure recompute.
//
// Nothing in here is incremental. Every run rebuilds `pick_scores` and both
// leaderboards from `picks ⋈ slate_games ⋈ results`, which makes re-running it
// definitionally a no-op and makes a corrected score propagate on its own. The
// alternative — adjusting totals as results land — is how a board drifts from
// the picks it claims to summarize, and a board nobody trusts is worse than no
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
import { strandedWeeks } from "./handicap.js";
import { scoresUrl } from "./slate.js";

const VOID_AFTER = 36 * 3600;

// Enter by this week to be in the running for the season.
//
// Not a join deadline — entry stays open, under the chalk handicap, for as
// long as there is a season left to play. This is only about the leaderboard,
// and it exists because in a survivor pool almost everybody is dead by
// December: without it, one person entering in the last week and winning once
// could be the only player still alive and take the title on a single pick.
//
// Six of a roughly fourteen-week season leaves nine weeks to survive, which is
// enough of the pool to have actually played it.
// A season shorter or longer than usual wants a different number, so it is
// configurable — but it is CONFIG, not a secret and not a per-request input:
// changing it mid-season moves people on and off the leaderboard, which is why
// it is a declared var rather than something a handler can be talked into.
export const RANKED_ENTRY_BY = 6;

export function rankedEntryBy(env) {
  const v = Number(env && env.SURVIVOR_RANKED_ENTRY_BY);
  return Number.isInteger(v) && v > 0 ? v : RANKED_ENTRY_BY;
}

async function hashOf(obj) {
  const buf = await crypto.subtle.digest("SHA-256",
    new TextEncoder().encode(JSON.stringify(obj)));
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Where a season's scores live in KV. The publisher and the reader agree here. */
export function scoresKey(season) {
  return `scores:${season}`;
}

/**
 * Pull the scores the tiebreaker build produced.
 *
 * Shape: {season, games: {"401756846": [home, away, completed], ...}}.
 * The Worker holds no CFBD key and never will; this is the only score source,
 * and it costs the project nothing because the build already had the data in
 * hand.
 *
 * TWO CHANNELS, AND THE ORDER MATTERS. Scores used to arrive only as a file on
 * the Pages origin, which meant publishing one changed number was a rebuild
 * and redeploy of all 415 files on the domain — the reason pages.yml's cron
 * had to stay clear of Pages' deployment rate limiting, and the reason a final
 * could sit an hour before it was graded. Now the publisher POSTs them to
 * /api/ingest/scores, which writes them here.
 *
 * The file fetch stays as a fallback and is not dead code: it is what runs if
 * the ingest secret is ever unset, if a KV namespace is lost, or on the very
 * first deploy after this change, when nothing has been posted yet. It is also
 * what the tests exercise, since makeEnv binds no SCORES namespace. When both
 * channels have been live for a season and the logs show no fallback, this
 * half can go, and RAW_ORIGIN with it if the slate import no longer wants it.
 */
export async function fetchScores(env, season) {
  if (env.SCORES && season != null) {
    // "json" rather than parsing here: the runtime does it, and a value that
    // somehow is not JSON comes back null instead of throwing, which lands on
    // the fallback below rather than failing the whole sweep.
    const cached = await env.SCORES.get(scoresKey(season), "json");
    if (cached && cached.games) return cached;
    console.log(`scores: KV miss for ${scoresKey(season)}, asking the repo`);
  }
  const r = await fetch(scoresUrl(env), {
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
                                now = Math.floor(Date.now() / 1000),
                                { force = false } = {}) {
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
  const touched = [];          // the games whose result actually moved
  let finals = 0, voids = 0, changed = 0;

  for (const g of games || []) {
    // A game with no line was never pickable, so there is nothing to be right
    // or wrong about and no pick can exist on it. It gets no result row at
    // all — writing one made nine junk voids a week and put a "void" chip on
    // a card row that only ever said "No Spread Available".
    if (g.spread_x2 == null) continue;

    const raw = scores.games ? scores.games[String(g.game_id)] : null;
    const [hp, ap, completed] = raw || [null, null, false];

    let status, atsValue, home = null, away = null;
    if (completed && hp != null && ap != null) {
      status = "final";
      home = hp; away = ap;
      atsValue = ats(hp, ap, g.spread_x2);
      finals++;
    } else if (now > g.kickoff_at + VOID_AFTER) {
      // Thirty-six hours past kickoff with no final: no result is coming,
      // whether the game was canceled or simply dropped out of the file.
      //
      // Absence from the file is NOT its own trigger, though the plan said it
      // could be. The scores file lists every game of the season, finished or
      // not, so absence should mean "vanished" — but if it were ever
      // truncated or written half-way, treating absence as a void would void
      // an entire locked week of games that had not kicked off yet. The clock
      // says the same thing about a genuinely canceled game a day and a half
      // later, and cannot be wrong about one still to be played.
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
    touched.push(g.game_id);

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

  // NOTHING CHANGED, SO NOTHING IS REBUILT.
  //
  // The five rebuilds below are deletes-and-reinserts of every derived row
  // for this week plus both season boards, and they used to run on every
  // call whether or not a single result had moved. That is the difference
  // between a quiet hour costing nothing and costing more than a busy one:
  // measured at twenty players over fourteen weeks, an idle pass wrote 9,534
  // rows where the very first pass wrote 5,244, because the first had empty
  // tables to delete from. Twenty-nine cron runs on a Saturday made that
  // 276,000 rows a day against a free-tier allowance of 100,000 — and D1
  // does not throttle when the allowance is gone, it returns errors, so the
  // pick'em would have gone dark on the first weekend it had players.
  //
  // Skipping is sound rather than merely cheap: this week is locked, picks
  // cannot be written to a locked week, and no result changed. The join the
  // rebuilds read from has identical inputs, so it would produce identical
  // rows. The one thing that CAN change underneath is board membership, when
  // a provisional account is published — scoreAll forces a pass for that.
  // The membership check is not a formality. Relying on the caller to say
  // "a provisional account was published, rebuild anyway" makes the skip
  // correct only when it is called correctly, and scoreWeek is called
  // directly from several places. Comparing who SHOULD be on this week's
  // board against who is costs two counted reads and makes the skip prove
  // itself instead.
  const state = await env.DB.prepare(
    `SELECT
       (SELECT scored_at FROM weeks WHERE season = ? AND week = ?) AS scored_at,
       (SELECT COUNT(DISTINCT s.user_id) FROM pick_scores s
          JOIN users u ON u.id = s.user_id
         WHERE s.season = ? AND s.week = ? AND u.status = 'active') AS want,
       (SELECT COUNT(*) FROM leaderboard_week
         WHERE season = ? AND week = ?) AS have`)
    .bind(season, week, season, week, season, week).first();
  if (!force && changed === 0 && state && state.scored_at != null
      && state.want === state.have) {
    return { finals, voids, changed: 0, skipped: "unchanged" };
  }

  await rebuildPickScores(env, season, week, touched);
  await rebuildWeekBoard(env, season, week, now);
  await rebuildSeasonBoard(env, season, now);
  await rebuildSurvivorScores(env, season, week);
  // Before the board, and after the scores: a week scored void hands its team
  // back, which changes what a player was holding, which is what decides
  // whether a later empty week was a choice or a dead end.
  await rebuildSurvivorStranded(env, season, now);
  await rebuildSurvivorBoard(env, season, now);

  await env.DB.prepare(
    `UPDATE weeks SET scored_at = ?, scored_rev = scored_rev + 1
      WHERE season = ? AND week = ?`).bind(now, season, week).run();

  return { finals, voids, changed };
}

/**
 * One statement, derived. `pick_scores` is never written by a handler and
 * never adjusted — it is deleted and recreated from the join, so it cannot
 * disagree with the picks it summarizes.
 */
/**
 * @param {number[]|null} games only these games, or all of the week's when
 *   empty — the first scoring of a week, or a forced pass.
 *
 * Scoped because the whole week is players x games rows, and rebuilding all
 * of it because one Saturday afternoon final arrived is what puts a busy day
 * over D1's write allowance: at 200 players, 3,400 rows a run rather than 600.
 * Still a pure recompute, just of the rows whose inputs moved — a game nobody
 * re-scored has the same pick, the same line and the same result, so its
 * outcome cannot have changed.
 */
async function rebuildPickScores(env, season, week, games = null) {
  const some = Array.isArray(games) && games.length;
  const holes = some ? games.map(() => "?").join(",") : "";
  await env.DB.batch([
    env.DB.prepare(
      `DELETE FROM pick_scores WHERE season = ? AND week = ?` +
      (some ? ` AND game_id IN (${holes})` : ""))
      .bind(season, week, ...(some ? games : [])),
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
        WHERE p.season = ? AND p.week = ?` +
       (some ? ` AND p.game_id IN (${holes})` : ""))
      .bind(season, week, ...(some ? games : [])),
  ]);
}

/**
 * Straight-up outcomes for the survivor pool. Same shape as
 * rebuildPickScores: derived, deleted and recreated, never adjusted.
 *
 * The spread is not consulted. A survivor pick is a team to win the game,
 * so the grading is points against points — with two escapes that both land
 * on 'void': a game scoring voided (canceled, or vanished past the
 * thirty-six-hour clock), and the theoretical tie, which modern overtime
 * rules should make impossible but which must not default to somebody's
 * elimination if it ever happens.
 */
async function rebuildSurvivorScores(env, season, week) {
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM survivor_scores WHERE season = ? AND week = ?`)
      .bind(season, week),
    env.DB.prepare(
      `INSERT INTO survivor_scores (user_id, season, week, game_id, outcome)
       SELECT p.user_id, p.season, p.week, p.game_id,
              CASE WHEN r.status = 'void' THEN 'void'
                   WHEN r.home_points = r.away_points THEN 'void'
                   WHEN (p.team = g.home AND r.home_points > r.away_points)
                     OR (p.team = g.away AND r.away_points > r.home_points)
                     THEN 'win'
                   ELSE 'loss' END
         FROM survivor_picks p
         JOIN slate_games g ON g.season = p.season AND g.week = p.week
                           AND g.game_id = p.game_id
         JOIN results r ON r.season = p.season AND r.week = p.week
                       AND r.game_id = p.game_id
        WHERE p.season = ? AND p.week = ?`).bind(season, week),
  ]);
}

/**
 * The weeks nobody could have played, rebuilt wholesale like everything else
 * here. Almost always empty, and cheap when it is: the driving query only
 * returns weeks somebody actually skipped.
 */
async function rebuildSurvivorStranded(env, season, now) {
  const rows = await strandedWeeks(env, season);
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM survivor_stranded WHERE season = ?`)
      .bind(season),
    ...rows.map((r) => env.DB.prepare(
      `INSERT INTO survivor_stranded (season, user_id, week, usable,
                                      computed_at)
       VALUES (?, ?, ?, 0, ?)`).bind(season, r.user_id, r.week, now)),
  ]);
}

/**
 * The survivor standings, season-wide, in one INSERT.
 *
 * The walk: for every entrant, every LOCKED week from their first pick
 * onward is one of four things — a win, a loss, a void, or (no pick at all)
 * a miss. The first loss or miss is the out; wins are counted strictly
 * before it. A locked week whose game has no result yet is pending: it
 * neither counts nor eliminates, and the next recompute settles it.
 *
 * Locked weeks, not scored weeks, and the difference is deliberate: the
 * moment a week locks, "no pick" is a permanent fact — there is nothing to
 * wait for — so a player who let it pass is out on the next recompute
 * rather than in limbo until the games finish.
 *
 * Every entrant lands in the table, whatever their account status: the read
 * side filters the public board to active accounts, but a provisional or
 * banned player's own standing — and the pick handler's "you are out" —
 * still has to come from somewhere.
 */
async function rebuildSurvivorBoard(env, season, now) {
  const cutoff = rankedEntryBy(env);
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM survivor_board WHERE season = ?`).bind(season),
    env.DB.prepare(
      `INSERT INTO survivor_board
         (season, user_id, wins, alive, entered_week, out_week, out_reason,
          ranked, rank, computed_at)
       SELECT ?, user_id, wins,
              CASE WHEN out_week IS NULL THEN 1 ELSE 0 END,
              entered_week, out_week, out_reason,
              -- Eligibility is about WHEN you entered, not how long you
              -- lasted. Someone who joined in August and lost in week two
              -- played the season and belongs on the board at the bottom of
              -- it; someone who joined in November did not.
              CASE WHEN entered_week <= ? THEN 1 ELSE 0 END,
              -- Alive first, THEN wins. This is what a survivor pool means:
              -- the last one standing wins it, and no eliminated player
              -- outranks somebody still in it however deep their run was.
              -- Wins order each group, so the deepest run leads the living
              -- and the deepest run leads the dead.
              --
              -- Wins-first would let a 10-week run that ended be crowned over
              -- a shorter run still alive in December. It is also what made
              -- joining late pointless; with the chalk handicap pricing the
              -- roster instead, alive-first is the honest order.
              --
              -- Partitioned so the ranked players' numbers are theirs alone.
              -- Unranked entrants get their own 1..n, which nothing displays —
              -- that keeps rank NOT NULL for everyone rather than making the
              -- column nullable for the sake of a group that has no rank.
              RANK() OVER (
                PARTITION BY CASE WHEN entered_week <= ? THEN 1 ELSE 0 END
                ORDER BY CASE WHEN out_week IS NULL THEN 1 ELSE 0 END DESC,
                         wins DESC),
              ?
         FROM (
           -- Plain ? throughout, bound in text order: the node:sqlite shim
           -- the tests run on cannot bind ?N numbered parameters
           -- positionally, and a query only D1 can execute is a query the
           -- suite cannot check.
           -- A SURVIVOR WEEK CLOSES AT ITS LAST KICKOFF, not its first.
           --
           -- This walk is what decides a missed week, and a miss is an
           -- elimination. Survivor locks per game now (0010), so a player
           -- with no pick on Thursday may still have until Friday afternoon —
           -- judging the week at lock_at would put them out while they could
           -- still legally pick, which is the one direction this must never
           -- get wrong.
           --
           -- Pickable games only, matching what may be picked at all. A week
           -- whose last game has no line closes when its last PICKABLE game
           -- kicks off, because the lineless one was never on the card.
           -- BOTH conditions, not just the kickoffs. lock_at is what says a
           -- week is under way at all; the last kickoff is what says nobody
           -- can still be choosing. In production they cannot disagree —
           -- lock_at IS the first kickoff — but dropping the lock_at test
           -- would let a week that was never locked be walked the moment its
           -- games' clocks passed, and eliminate somebody on a week the pool
           -- had not started.
           WITH locked AS (
             SELECT w.week FROM weeks w
              WHERE w.season = ?
                AND w.lock_at IS NOT NULL AND w.lock_at <= ?
                AND (SELECT MAX(g.kickoff_at) FROM slate_games g
                      WHERE g.season = w.season AND g.week = w.week
                        AND g.spread_x2 IS NOT NULL) <= ?),
           entrants AS (
             SELECT user_id, MIN(week) AS entered_week
               FROM survivor_picks WHERE season = ? GROUP BY user_id),
           walk AS (
             SELECT e.user_id, e.entered_week, l.week,
                    -- No pick is a miss unless there was nothing to pick.
                    -- 'stranded' is in neither the out list nor the win
                    -- count, so the week simply does not happen for that
                    -- player — the same shape a void already has, and for
                    -- the same reason: the pool must not eliminate somebody
                    -- for a board it emptied itself.
                    CASE WHEN p.user_id IS NULL
                           THEN CASE WHEN st.user_id IS NULL THEN 'missed'
                                     ELSE 'stranded' END
                         ELSE COALESCE(s.outcome, 'pending') END AS outcome
               FROM entrants e
               JOIN locked l ON l.week >= e.entered_week
               LEFT JOIN survivor_picks p
                 ON p.user_id = e.user_id AND p.season = ? AND p.week = l.week
               LEFT JOIN survivor_scores s
                 ON s.user_id = e.user_id AND s.season = ? AND s.week = l.week
               LEFT JOIN survivor_stranded st
                 ON st.user_id = e.user_id AND st.season = ?
                AND st.week = l.week)
           SELECT e.user_id, e.entered_week,
                  (SELECT MIN(w.week) FROM walk w
                    WHERE w.user_id = e.user_id
                      AND w.outcome IN ('loss', 'missed')) AS out_week,
                  (SELECT CASE w2.outcome WHEN 'missed' THEN 'missed'
                                          ELSE 'loss' END
                     FROM walk w2
                    WHERE w2.user_id = e.user_id
                      AND w2.week = (SELECT MIN(w3.week) FROM walk w3
                                      WHERE w3.user_id = e.user_id
                                        AND w3.outcome IN ('loss', 'missed')))
                    AS out_reason,
                  (SELECT COUNT(*) FROM walk w4
                    WHERE w4.user_id = e.user_id AND w4.outcome = 'win'
                      AND w4.week < COALESCE(
                            (SELECT MIN(w5.week) FROM walk w5
                              WHERE w5.user_id = e.user_id
                                AND w5.outcome IN ('loss', 'missed')),
                            1000000)) AS wins
             FROM entrants e)`)
      // Text order, twice for the cutoff: the ranked flag and the partition
      // that ranks within it have to agree, so they read the same binding.
      // The trailing seasons are, in text order: locked, entrants, picks,
      // scores, stranded.
      //
      // `now` appears twice inside locked since 0010 — once for the week's own
      // deadline and once for its last kickoff. Positional binding means a
      // missing one silently shifts every parameter after it, so the count
      // here and the ? count in the query have to be read together.
      .bind(season, cutoff, cutoff, now,
            season, now, now, season, season, season, season),
  ]);
}

// Only 'active' players appear. A provisional account's picks are scored and
// counted toward becoming active — they are simply not published yet.
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
  // Either game counts. A survivor-only player has completed a scored week
  // in exactly the sense the gate cares about — they played a week that has
  // since been graded — and holding them provisional forever because they
  // never filled in a spread card would be the rule outliving its reason.
  const r = await env.DB.prepare(
    `UPDATE users SET status = 'active'
      WHERE status = 'provisional'
        AND (id IN (SELECT DISTINCT user_id FROM pick_scores
                     WHERE season = ? AND week = ?)
          OR id IN (SELECT DISTINCT user_id FROM survivor_scores
                     WHERE season = ? AND week = ?))`)
    .bind(season, week, season, week).run();
  // How many were published, so the caller knows whether the boards it is
  // about to skip rebuilding have changed membership underneath it.
  return (r && (r.meta ? r.meta.changes : r.changes)) || 0;
}

/**
 * The chalk: what always taking the favorite would have scored.
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

/**
 * The room: the majority pick on every game, scored as if one player made
 * them all.
 *
 * The second benchmark, and it answers a different question from the chalk.
 * The chalk asks whether you beat the market. This asks whether you beat
 * everybody else put together — which is a real question, because a crowd is
 * often better than the median member of it and occasionally much worse.
 *
 * Three decisions worth stating, because each could defensibly go the other
 * way:
 *
 *   EVERY pick counts, from every account, exactly as the consensus bars on
 *   the slate count them. That is the point: a reader can add up the bars
 *   they can see and arrive at this row. Filtering to active accounts here
 *   would make the number correct by some measure and unverifiable by any.
 *
 *   A DEAD HEAT IS NOT A PICK. Split exactly down the middle, the room had no
 *   opinion, and inventing one — by falling back to the favorite, or to the
 *   home side — would quietly turn this into a worse copy of the chalk. Those
 *   games sit outside the record entirely and are counted separately.
 *
 *   NO MINIMUM. The consensus BAR needs ten cards before it will draw,
 *   because a bar is a claim about a crowd. This is arithmetic on whoever
 *   actually played, and a threshold would make the row appear and disappear
 *   early in a season in a way that needs more explaining than it is worth.
 */
export async function room(env, season, week = null) {
  const sql = `
    WITH majority AS (
      SELECT p.season, p.week, p.game_id,
             SUM(p.side = 'home') AS h,
             SUM(p.side = 'away') AS a
        FROM picks p
       WHERE p.season = ? ${week == null ? "" : "AND p.week = ?"}
       GROUP BY p.season, p.week, p.game_id)
    SELECT
      SUM(CASE WHEN r.ats IN ('push','void') OR m.h = m.a THEN 0
               WHEN (m.h > m.a AND r.ats = 'home')
                 OR (m.a > m.h AND r.ats = 'away') THEN 1
               ELSE 0 END) AS w,
      SUM(CASE WHEN r.ats IN ('push','void') OR m.h = m.a THEN 0
               WHEN (m.h > m.a AND r.ats = 'home')
                 OR (m.a > m.h AND r.ats = 'away') THEN 0
               ELSE 1 END) AS l,
      SUM(CASE WHEN r.ats = 'push' AND m.h <> m.a THEN 1 ELSE 0 END) AS p,
      SUM(CASE WHEN m.h = m.a THEN 1 ELSE 0 END) AS split
      FROM majority m
      JOIN results r ON r.season = m.season AND r.week = m.week
                    AND r.game_id = m.game_id`;
  const bind = week == null ? [season] : [season, week];
  const row = await env.DB.prepare(sql).bind(...bind).first();
  if (!row || (row.w == null && row.l == null)) return null;
  const w = row.w || 0, l = row.l || 0;
  if (w + l === 0 && !row.p && !row.split) return null;   // nothing to show yet
  return { w, l, p: row.p || 0, split: row.split || 0,
           pct: w + l === 0 ? null : w / (w + l) };
}

/**
 * The season week by week: where everybody stood after each one.
 *
 * Season-to-date, not per-week. A week in isolation is fourteen coin flips
 * and reads as noise; the cumulative figure is the one the board shows and
 * the one that can be compared to the chalk and the room without converting
 * anything in your head.
 *
 * The percentiles are computed here rather than in SQL because SQLite has no
 * percentile function and the alternative is a correlated subquery per week
 * per cut. The row count is players × weeks — a few thousand at the size this
 * will ever be — so the arithmetic is free and the query stays legible.
 */
export async function history(env, season) {
  const { results: rows } = await env.DB.prepare(
    `WITH per AS (
       SELECT s.user_id, s.week,
              SUM(s.outcome = 'win')  AS w,
              SUM(s.outcome = 'loss') AS l
         FROM pick_scores s
         JOIN users u ON u.id = s.user_id
        WHERE s.season = ? AND u.status = 'active'
        GROUP BY s.user_id, s.week)
     SELECT user_id, week,
            SUM(w) OVER (PARTITION BY user_id ORDER BY week) AS cw,
            SUM(l) OVER (PARTITION BY user_id ORDER BY week) AS cl
       FROM per
      ORDER BY week, user_id`).bind(season).all();
  if (!rows || !rows.length) return null;

  const weeks = [...new Set(rows.map((r) => r.week))].sort((a, b) => a - b);
  const byWeek = new Map(weeks.map((w) => [w, []]));
  const byUser = new Map();
  for (const r of rows) {
    const pct = r.cw + r.cl === 0 ? null : r.cw / (r.cw + r.cl);
    const point = { week: r.week, pct, w: r.cw, l: r.cl, user_id: r.user_id };
    byWeek.get(r.week).push(point);
    if (!byUser.has(r.user_id)) byUser.set(r.user_id, []);
    byUser.get(r.user_id).push(point);
  }

  // Rank within each week, sharing a rank on a tie, the way the board does.
  const field = [];
  for (const w of weeks) {
    const col = byWeek.get(w).filter((p) => p.pct != null)
      .sort((a, b) => b.pct - a.pct);
    col.forEach((p, i) => {
      p.rank = i > 0 && col[i - 1].pct === p.pct ? col[i - 1].rank : i + 1;
    });
    const v = col.map((p) => p.pct);
    const at = (q) => v.length ? v[Math.min(v.length - 1,
      Math.round((1 - q) * (v.length - 1)))] : null;
    field.push({ week: w, n: v.length,
                 p10: at(0.10), p25: at(0.25), p50: at(0.50),
                 p75: at(0.75), p90: at(0.90) });
  }

  const line = (id) => (byUser.get(id) || []).map(
    (p) => ({ week: p.week, pct: p.pct, rank: p.rank, w: p.w, l: p.l }));

  const top = await env.DB.prepare(
    `SELECT b.user_id, u.display_name, u.team FROM leaderboard_season b
       JOIN users u ON u.id = b.user_id
      WHERE b.season = ? ORDER BY b.rank, u.display_name LIMIT 1`)
    .bind(season).first();

  return { season, weeks, field,
           leader: top ? { user_id: top.user_id, display_name: top.display_name,
                           team: top.team, rows: line(top.user_id) } : null,
           room: await roomHistory(env, season, weeks),
           chalk: await chalkHistory(env, season, weeks),
           _line: line };
}

/** The chalk's cumulative record after each week, to match the room's. */
async function chalkHistory(env, season, weeks) {
  const { results } = await env.DB.prepare(
    `SELECT r.week,
            SUM(CASE WHEN r.ats IN ('push','void') THEN 0
                     WHEN (g.spread_x2 < 0 AND r.ats = 'home')
                       OR (g.spread_x2 > 0 AND r.ats = 'away') THEN 1
                     ELSE 0 END) AS w,
            SUM(CASE WHEN r.ats IN ('push','void') THEN 0
                     WHEN (g.spread_x2 < 0 AND r.ats = 'home')
                       OR (g.spread_x2 > 0 AND r.ats = 'away') THEN 0
                     ELSE 1 END) AS l
       FROM results r
       JOIN slate_games g ON g.season = r.season AND g.week = r.week
                         AND g.game_id = r.game_id
      WHERE r.season = ? AND g.spread_x2 IS NOT NULL AND g.spread_x2 <> 0
      GROUP BY r.week ORDER BY r.week`).bind(season).all();
  let cw = 0, cl = 0;
  const by = new Map((results || []).map((r) => [r.week, r]));
  return weeks.map((week) => {
    const r = by.get(week);
    if (r) { cw += r.w || 0; cl += r.l || 0; }
    return { week, pct: cw + cl === 0 ? null : cw / (cw + cl), w: cw, l: cl };
  });
}

/** The room's cumulative record after each week. */
async function roomHistory(env, season, weeks) {
  const { results } = await env.DB.prepare(
    `WITH majority AS (
       SELECT p.season, p.week, p.game_id,
              SUM(p.side = 'home') AS h, SUM(p.side = 'away') AS a
         FROM picks p WHERE p.season = ?
        GROUP BY p.season, p.week, p.game_id)
     SELECT m.week,
            SUM(CASE WHEN r.ats IN ('push','void') OR m.h = m.a THEN 0
                     WHEN (m.h > m.a AND r.ats = 'home')
                       OR (m.a > m.h AND r.ats = 'away') THEN 1
                     ELSE 0 END) AS w,
            SUM(CASE WHEN r.ats IN ('push','void') OR m.h = m.a THEN 0
                     WHEN (m.h > m.a AND r.ats = 'home')
                       OR (m.a > m.h AND r.ats = 'away') THEN 0
                     ELSE 1 END) AS l
       FROM majority m
       JOIN results r ON r.season = m.season AND r.week = m.week
                     AND r.game_id = m.game_id
      GROUP BY m.week ORDER BY m.week`).bind(season).all();
  let cw = 0, cl = 0;
  const by = new Map((results || []).map((r) => [r.week, r]));
  return weeks.map((week) => {
    const r = by.get(week);
    if (r) { cw += r.w || 0; cl += r.l || 0; }
    return { week, pct: cw + cl === 0 ? null : cw / (cw + cl), w: cw, l: cl };
  });
}

/**
 * Every locked week of a season, scored oldest first.
 *
 * `scores` may be supplied by the caller, and the ingest endpoint does supply
 * it: it has just been handed the file and grades from the bytes it verified
 * rather than reading back what it wrote. KV is eventually consistent, so a
 * read-back inside the same request can legitimately return the PREVIOUS
 * value — which would grade the new final against the old numbers and report
 * success. The cron passes nothing and reads normally, which is correct for
 * it, because by then the write has had a whole cron interval to propagate.
 */
export async function scoreAll(env, season, now = Math.floor(Date.now() / 1000),
                               { scores: given = null } = {}) {
  const scores = given || await fetchScores(env, season);
  const { results } = await env.DB.prepare(
    `SELECT week FROM weeks
      WHERE season = ? AND lock_at IS NOT NULL AND lock_at <= ?
      ORDER BY week`).bind(season, now).all();
  // Promotions first, and all of them, before anything is scored. A newly
  // published account changes who belongs on the boards, and the boards are
  // now only rebuilt when something moved — so this has to be known before
  // that decision rather than after it.
  let promoted = 0;
  for (const { week } of results || []) {
    promoted += await promoteProvisional(env, season, week);
  }
  const report = [];
  for (const { week } of results || []) {
    report.push({ week, ...(await scoreWeek(env, season, week, scores, now,
                                            { force: promoted > 0 })) });
  }
  return report;
}

// The Kansas City bracket challenge: import, check, score, serve.
//
// One bracket per player per basketball league, all fifteen games picked
// before the first-round tip (migration 0016 holds the lock). The bracket is
// the publisher's (basketball/pickem/<league>/<season>/bracket.json), and the
// game shape is engine.js's: four first-round games among seeds 9 to 16, the
// winners meeting 5 to 8, those meeting 1 to 4, the semifinals, the final.
// Each game is a pair whose sides are seeds (numbers) or earlier games
// ("g<index>").
//
// A PICK MUST BE A TEAM THAT CAN BE IN THAT GAME, given the seeds and the
// player's own earlier picks: a first-round game is between its two seeds, a
// later one between whoever the player sent through. A bracket may be saved
// half done, so a pick may be null; a team whose opponent is not yet picked
// may still be picked to win (the 8 seed waits in the second round whoever
// comes through), which is what the page offers.
// The check is here, where the refusal can say why; the database holds only
// the lock.
//
// SCORING is on read, because it is fifteen comparisons a player and the
// field is a few hundred at most: points for every game picked right, plus
// what is still possible (the points of every unplayed game whose picked
// winner is still alive), the number a bracket pool is read by.

import { seasonOf } from "./league.js";

export const BRACKET_LEAGUES = ["mbb", "wbb"];

export function bracketUrl(env, season, league) {
  return `${env.RAW_ORIGIN}/basketball/pickem/${league}/${season}/bracket.json`;
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256",
                                         new TextEncoder().encode(text));
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The two teams that can meet in game `gi`, given the seeds and a set of
 * picks (a team name, or null where that side is not yet decided). */
export function sides(b, picks, gi) {
  return b.games[gi].map((x) => (typeof x === "number" ? b.seeds[x - 1]
                                 : picks[Number(String(x).slice(1))] ?? null));
}

/** Null when the bracket is coherent, else the first problem in words. */
export function invalid(b, picks) {
  if (!Array.isArray(picks) || picks.length !== b.games.length) {
    return `a bracket has ${b.games.length} games`;
  }
  for (let gi = 0; gi < b.games.length; gi++) {
    const p = picks[gi];
    if (p == null) continue;
    const [a, c] = sides(b, picks, gi);
    if (p !== a && p !== c) return `${p} cannot be in game ${gi + 1}`;
  }
  return null;
}

/** {score, possible, perfect}: points won, points still reachable, and the
 * most the bracket could have scored. */
export function score(b, picks, results) {
  let won = 0, possible = 0, perfect = 0;
  const out = new Set();          // teams that have lost a game
  for (const v of Object.keys(results)) {
    const gi = Number(v), w = results[v];
    const pair = b.games[gi].map((x) => (typeof x === "number" ? b.seeds[x - 1]
                                         : results[String(x).slice(1)]));
    for (const t of pair) if (t && t !== w) out.add(t);
  }
  b.points.forEach((pts, gi) => {
    perfect += pts;
    const p = picks[gi];
    if (p == null) return;
    const r = results[String(gi)];
    if (r != null) {
      if (r === p) won += pts;
    } else if (!out.has(p)) {
      possible += pts;
    }
  });
  return { score: won, possible: won + possible, perfect };
}

function parse(row) {
  return {
    season: row.season, league: row.league, lock_at: row.lock_at,
    seeds: JSON.parse(row.seeds), games: JSON.parse(row.games),
    points: JSON.parse(row.points), results: JSON.parse(row.results || "{}"),
  };
}

export async function readBracket(env, league, season) {
  const row = await env.DB.prepare(
    `SELECT * FROM brackets WHERE league = ? AND season = ?`)
    .bind(league, season).first();
  return row ? parse(row) : null;
}

/** Bring the published bracket in, or refresh its results. Never fatal. */
export async function importBracket(env, league, season) {
  const r = await fetch(bracketUrl(env, season, league), {
    headers: { Accept: "application/json" }, cf: { cacheTtl: 0 },
  });
  if (!r.ok) return { ok: false, reason: `fetch_${r.status}` };
  const text = await r.text();
  const hash = await sha256Hex(text);
  let b;
  try { b = JSON.parse(text); } catch { return { ok: false, reason: "bad_json" }; }
  if (!b || !Array.isArray(b.seeds) || !Array.isArray(b.games)
      || !Array.isArray(b.points) || b.games.length !== b.points.length
      || !Number.isInteger(b.lock_at)) {
    return { ok: false, reason: "bad_shape" };
  }
  const have = await env.DB.prepare(
    `SELECT source_sha256 FROM brackets WHERE league = ? AND season = ?`)
    .bind(league, season).first();
  if (have && have.source_sha256 === hash) return { ok: true, unchanged: true };
  try {
    await env.DB.prepare(
      `INSERT INTO brackets (league, season, published_at, lock_at, seeds,
                             games, points, results, source_sha256)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(league, season) DO UPDATE SET
         -- The lock may only move earlier (brackets_lock_monotonic); a file
         -- that moved it later is ignored on that one field.
         lock_at = MIN(brackets.lock_at, excluded.lock_at),
         seeds = excluded.seeds, games = excluded.games,
         points = excluded.points, results = excluded.results,
         source_sha256 = excluded.source_sha256`)
      .bind(league, season, Math.floor(Date.now() / 1000), b.lock_at,
            JSON.stringify(b.seeds), JSON.stringify(b.games),
            JSON.stringify(b.points), JSON.stringify(b.results || {}), hash)
      .run();
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
  return { ok: true, results: Object.keys(b.results || {}).length };
}

// ---------------------------------------------------------------- handlers

const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8",
               "Cache-Control": "no-store", Vary: "Cookie",
               "X-Robots-Tag": "noindex", ...headers },
  });
const fail = (error, status = 400, extra = {}) => json({ error, ...extra }, status);

function leagueOf(raw) {
  const v = String(raw || "").toLowerCase();
  return BRACKET_LEAGUES.includes(v) ? v : null;
}

/** GET /api/bracket?league=: the bracket, and your entry if signed in. */
export async function getBracket(env, user, url) {
  const lg = leagueOf(url.searchParams.get("league"));
  if (!lg) return fail("bad_league", 400);
  const season = seasonOf(env, lg);
  const b = await readBracket(env, lg, season);
  if (!b) return fail("no_bracket", 404);
  const now = Math.floor(Date.now() / 1000);
  let entry = null;
  if (user) {
    const row = await env.DB.prepare(
      `SELECT picks, updated_at FROM bracket_entries
        WHERE league = ? AND season = ? AND user_id = ?`)
      .bind(lg, season, user.userId).first();
    if (row) {
      const picks = JSON.parse(row.picks);
      entry = { picks, updated_at: row.updated_at,
                ...score(b, picks, b.results) };
    }
  }
  return json({ ...b, locked: b.lock_at <= now, entry });
}

/** PUT /api/bracket {league, picks}: save a whole bracket, before the lock. */
export async function putBracket(env, user, body, take) {
  if (!user) return fail("unauthenticated", 401);
  const who = await env.DB.prepare(
    `SELECT display_name FROM users WHERE id = ?`).bind(user.userId).first();
  if (!who || !who.display_name) return fail("no_display_name", 403);
  const lg = leagueOf(body.league);
  if (!lg) return fail("bad_league", 400);
  const season = seasonOf(env, lg);
  const b = await readBracket(env, lg, season);
  if (!b) return fail("no_bracket", 404);
  const now = Math.floor(Date.now() / 1000);
  if (b.lock_at <= now) return fail("locked", 409);
  const why = invalid(b, body.picks);
  if (why) return fail("bad_bracket", 400, { why });
  const rl = await take(env, "picks", user.userId);
  if (!rl.ok) return fail("rate_limited", 429, { retry_after: rl.retryAfter });
  try {
    await env.DB.prepare(
      `INSERT INTO bracket_entries (league, season, user_id, picks, created_at,
                                    updated_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(league, season, user_id) DO UPDATE SET
         picks = excluded.picks, updated_at = excluded.updated_at`)
      .bind(lg, season, user.userId, JSON.stringify(body.picks), now, now).run();
  } catch (e) {
    if (/bracket_locked/.test(String(e.message || e))) return fail("locked", 409);
    throw e;
  }
  return json({ saved: true, picked: body.picks.filter((p) => p != null).length });
}

/** GET /api/bracket/board?league=: before the lock, how many have entered;
 * after it, every bracket, scored and ranked. Active accounts only, as the
 * other boards. */
export async function getBracketBoard(env, url) {
  const lg = leagueOf(url.searchParams.get("league"));
  if (!lg) return fail("bad_league", 400);
  const season = seasonOf(env, lg);
  const b = await readBracket(env, lg, season);
  if (!b) return fail("no_bracket", 404);
  const now = Math.floor(Date.now() / 1000);
  const { results } = await env.DB.prepare(
    `SELECT e.user_id, e.picks, u.display_name, u.team
       FROM bracket_entries e JOIN users u ON u.id = e.user_id
      WHERE e.league = ? AND e.season = ? AND u.status = 'active'`)
    .bind(lg, season).all();
  const rows = results || [];
  const cache = { "Cache-Control": "public, max-age=0, s-maxage=60", Vary: "" };
  if (b.lock_at > now) {
    // Before the lock a bracket is exactly what the lock withholds.
    return json({ season, league: lg, locked: false, lock_at: b.lock_at,
                  entrants: rows.length, rows: [] }, 200, cache);
  }
  const scored = rows.map((r) => {
    const picks = JSON.parse(r.picks);
    return { user_id: r.user_id, display_name: r.display_name, team: r.team,
             champion: picks[picks.length - 1] ?? null, picks,
             ...score(b, picks, b.results) };
  }).sort((x, y) => y.score - x.score || y.possible - x.possible
                 || String(x.display_name).localeCompare(String(y.display_name)));
  scored.forEach((r, i) => {
    r.rank = i > 0 && scored[i - 1].score === r.score ? scored[i - 1].rank : i + 1;
  });
  return json({ season, league: lg, locked: true, lock_at: b.lock_at,
                results: b.results, entrants: scored.length, rows: scored },
              200, cache);
}

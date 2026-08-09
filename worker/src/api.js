// The handlers. One file, because they share a small amount of context and
// splitting eight of them across eight files buys nothing but imports.
//
// Every response shape here is fixed by tiebreaker/site_pickem/app.js, which
// was built and verified against a stub of exactly these endpoints. Where a
// field looks redundant it is usually because the client reads it — `weeks` on
// the leaderboard is the count to enumerate, distinct from `week`, which is
// the one this response is for.

import { normalize, validate } from "./names.js";
import { ulid, hmac } from "./crypto.js";
import * as session from "./session.js";
import * as ratelimit from "./ratelimit.js";
import { currentWeek, isLocked, readSlate } from "./slate.js";
import { chalk, room } from "./scoring.js";

export const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      // Everything here is per-viewer or per-second. The one exception sets
      // its own header below.
      "Cache-Control": "no-store",
      Vary: "Cookie",
      // None of this belongs in an index. The pages that describe the game
      // are static and indexable; the JSON behind them is not.
      "X-Robots-Tag": "noindex",
      ...headers,
    },
  });

export const fail = (error, status = 400, extra = {}) =>
  json({ error, ...extra }, status);

const RENAME_DAYS = 30;

function season(env) {
  return Number(env.SEASON || new Date().getUTCFullYear());
}

/** A week from the query string, or the current one. */
async function weekParam(env, url, s) {
  const raw = url.searchParams.get("week");
  if (raw != null && raw !== "") {
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 1 || n > 25) return null;
    return n;
  }
  return currentWeek(env, s);
}

// ------------------------------------------------------------------- me

export async function getMe(env, user) {
  if (!user) return fail("unauthenticated", 401);
  const row = await env.DB.prepare(
    `SELECT id, display_name, status, team FROM users WHERE id = ?`)
    .bind(user.userId).first();
  if (!row) return fail("unauthenticated", 401);
  const { results } = await env.DB.prepare(
    `SELECT provider, linked_at FROM identities WHERE user_id = ?`)
    .bind(user.userId).all();
  return json({
    user_id: row.id,
    display_name: row.display_name,
    // The client shows the name form on this alone, so it must be a boolean
    // about the account rather than about this request.
    needs_name: !row.display_name,
    status: row.status,
    team: row.team || null,
    identities: results || [],
  });
}

export async function patchMe(env, user, body) {
  if (!user) return fail("unauthenticated", 401);

  // Team is a display preference, not an identity: no cooldown, no history,
  // no uniqueness. It only tints your own rows.
  if ("team" in body && !("display_name" in body)) {
    const t = body.team == null || body.team === "" ? null : String(body.team);
    if (t && t.length > 60) return fail("team_unknown", 400);
    await env.DB.prepare(`UPDATE users SET team = ? WHERE id = ?`)
      .bind(t, user.userId).run();
    return json({ team: t });
  }

  const v = validate(body.display_name);
  if (!v.ok) return fail(v.error, 400);

  const row = await env.DB.prepare(
    `SELECT display_name, name_changed_at FROM users WHERE id = ?`)
    .bind(user.userId).first();
  const now = Math.floor(Date.now() / 1000);

  // The cooldown applies to a CHANGE, never to choosing a name for the first
  // time — an account that cannot be named is an account that cannot play.
  if (row && row.display_name && row.name_changed_at &&
      now - row.name_changed_at < RENAME_DAYS * 86400) {
    return fail("rename_cooldown", 429,
                { retry_after: row.name_changed_at + RENAME_DAYS * 86400 - now });
  }

  const rl = await ratelimit.take(env, "rename", user.userId, now);
  if (!rl.ok) return fail("rename_cooldown", 429, { retry_after: rl.retryAfter });

  try {
    await env.DB.batch([
      env.DB.prepare(
        `UPDATE users SET display_name = ?, display_norm = ?,
                          name_changed_at = ? WHERE id = ?`)
        .bind(v.display, v.norm, now, user.userId),
      // The trail, so a banned name cannot be laundered through a rename and
      // quietly reclaimed later.
      env.DB.prepare(
        `INSERT OR REPLACE INTO name_history
           (user_id, display_norm, display_name, set_at)
         VALUES (?, ?, ?, ?)`).bind(user.userId, v.norm, v.display, now),
    ]);
  } catch (e) {
    // The unique index on display_norm is what actually decides this, not a
    // SELECT beforehand — two people can pass the same check at the same
    // time and both see the name free.
    //
    // Matched on the column SQLite actually names — "UNIQUE constraint
    // failed: users.display_norm" — and not on /constraint/ at large. The
    // broad version reported "name taken" for every constraint in the batch,
    // which is how a NOT NULL failure elsewhere in the same statement spent
    // a while convincingly impersonating a collision.
    if (/UNIQUE.*users\.display_norm/i.test(String(e.message || e))) {
      return fail("name_taken", 409);
    }
    throw e;
  }
  return json({ display_name: v.display });
}

/**
 * Delete an account.
 *
 * Identities and sessions go; the picks stay, attached to a user renamed
 * `deleted-<suffix>`. A public board that develops holes where people left is
 * worse than one with pseudonymous rows, and every other player's rank depends
 * on those games having been picked. This is a real limit on erasure and it is
 * stated plainly in privacy.html rather than left to be discovered.
 */
export async function deleteMe(env, user) {
  if (!user) return fail("unauthenticated", 401);
  const now = Math.floor(Date.now() / 1000);
  const anon = `deleted-${user.userId.slice(-6).toLowerCase()}`;
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM identities WHERE user_id = ?`).bind(user.userId),
    env.DB.prepare(
      `UPDATE users SET display_name = ?, display_norm = ?, status = 'active',
                        signup_ip_hash = NULL, signup_asn = NULL
        WHERE id = ?`).bind(anon, anon, user.userId),
    env.DB.prepare(
      `INSERT INTO audit_log (at, actor, action, subject)
       VALUES (?, ?, 'account_deleted', ?)`)
      .bind(now, user.userId, user.userId),
  ]);
  await session.revokeAll(env, user.userId);
  return json({ deleted: true, display_name: anon });
}

// ---------------------------------------------------------------- slate

export async function getSlate(env, url) {
  const s = season(env);
  const week = await weekParam(env, url, s);
  if (week == null) return fail("no_slate", 404);
  const slate = await readSlate(env, s, week);
  if (!slate) return fail("no_slate", 404);
  return json(slate);
}

// ---------------------------------------------------------------- picks

export async function getPicks(env, user, url) {
  if (!user) return fail("unauthenticated", 401);
  const s = season(env);
  const week = await weekParam(env, url, s);
  if (week == null) return fail("no_slate", 404);

  const w = await env.DB.prepare(
    `SELECT lock_at FROM weeks WHERE season = ? AND week = ?`)
    .bind(s, week).first();
  const { results } = await env.DB.prepare(
    `SELECT game_id, side FROM picks
      WHERE user_id = ? AND season = ? AND week = ?`)
    .bind(user.userId, s, week).all();

  const picks = {};
  for (const r of results || []) picks[r.game_id] = r.side;
  return json({ season: s, week, locked: isLocked(w),
                lock_at: w ? w.lock_at : null, picks });
}

/**
 * Replace the whole card, in one transaction.
 *
 * Not a per-game PATCH, and the difference matters more than it looks. A
 * whole-slate replace is idempotent, it cannot leave half a card saved when
 * the lock lands mid-request, and "what did you submit" is a single row-set
 * rather than a sequence to be reconstructed.
 *
 * The lock is checked twice on purpose: cheaply here, so the ordinary case is
 * a clean 409 before anything is touched, and again by the triggers inside the
 * batch, which is what catches a request that crosses the boundary in flight.
 */
export async function putPicks(env, user, body) {
  if (!user) return fail("unauthenticated", 401);

  // A pick is only worth storing if it can appear on the board with a name
  // beside it, and the board is the whole product. Enforced here rather than
  // trusted to the client, which is the half of it that can be skipped.
  const who = await env.DB.prepare(
    `SELECT display_name FROM users WHERE id = ?`).bind(user.userId).first();
  if (!who || !who.display_name) return fail("no_display_name", 403);

  const s = season(env);
  const week = Number(body.week);
  if (!Number.isInteger(week)) return fail("bad_week", 400);
  if (body.season != null && Number(body.season) !== s) {
    return fail("bad_season", 400);
  }
  const picks = body.picks;
  if (!picks || typeof picks !== "object" || Array.isArray(picks)) {
    return fail("bad_picks", 400);
  }
  if (Object.keys(picks).length > 40) return fail("too_many_picks", 400);

  const rl = await ratelimit.take(env, "picks", user.userId);
  if (!rl.ok) return fail("rate_limited", 429, { retry_after: rl.retryAfter });

  const w = await env.DB.prepare(
    `SELECT lock_at FROM weeks WHERE season = ? AND week = ?`)
    .bind(s, week).first();
  if (!w) return fail("no_slate", 404);
  if (isLocked(w)) return lockedNow(env, user, s, week);

  const { results: games } = await env.DB.prepare(
    `SELECT game_id, spread_x2 FROM slate_games
      WHERE season = ? AND week = ? AND spread_x2 IS NOT NULL`)
    .bind(s, week).all();
  const line = new Map((games || []).map((g) => [String(g.game_id), g.spread_x2]));

  const now = Math.floor(Date.now() / 1000);
  const stmts = [env.DB.prepare(
    `DELETE FROM picks WHERE user_id = ? AND season = ? AND week = ?`)
    .bind(user.userId, s, week)];

  for (const [gid, side] of Object.entries(picks)) {
    if (side !== "home" && side !== "away") return fail("bad_side", 400);
    // A game with no line is not an error to send — the client renders the
    // whole slate — but it is never stored.
    if (!line.has(gid)) continue;
    stmts.push(env.DB.prepare(
      `INSERT INTO picks (user_id, season, week, game_id, side, spread_x2,
                          created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
      .bind(user.userId, s, week, Number(gid), side, line.get(gid), now, now));
  }

  try {
    await env.DB.batch(stmts);
  } catch (e) {
    const msg = String(e.message || e);
    // The triggers speak in these words. Mapping them back to 409 rather than
    // letting them surface as a 500 is what makes the client's "the week
    // locked while you were picking" path work.
    if (/week_locked/.test(msg)) return lockedNow(env, user, s, week);
    if (/unpickable_or_stale_line/.test(msg)) return fail("stale_line", 409);
    throw e;
  }
  return json({ saved: true, count: stmts.length - 1 });
}

/**
 * The 409 the client is written to handle: it repaints every radio from
 * `picks` here, because its own optimistic state is now a lie.
 */
async function lockedNow(env, user, s, week) {
  const { results } = await env.DB.prepare(
    `SELECT game_id, side FROM picks
      WHERE user_id = ? AND season = ? AND week = ?`)
    .bind(user.userId, s, week).all();
  const picks = {};
  for (const r of results || []) picks[r.game_id] = r.side;
  return fail("locked", 409, { picks });
}

// ---------------------------------------------------------- leaderboard

export async function getBoard(env, url) {
  const s = season(env);
  const raw = url.searchParams.get("week");
  const week = raw ? Number(raw) : null;
  if (raw && !Number.isInteger(week)) return fail("bad_week", 400);

  const table = week == null ? "leaderboard_season" : "leaderboard_week";
  const where = week == null ? `season = ?` : `season = ? AND week = ?`;
  const bind = week == null ? [s] : [s, week];

  const { results } = await env.DB.prepare(
    `SELECT b.rank, b.user_id, u.display_name, u.team,
            b.w, b.l, b.p, b.v, b.pct
       FROM ${table} b JOIN users u ON u.id = b.user_id
      WHERE ${where}
      -- Ties share a rank, so the name is what makes the order among them
      -- stable across requests rather than whatever the planner returns.
      ORDER BY b.rank, u.display_name LIMIT 500`).bind(...bind).all();

  // How many weeks there are to CHOOSE from, which is not the same as which
  // one this response is for. The season view is for no week at all, and a
  // client reading `week` there has nothing to enumerate.
  const wk = await env.DB.prepare(
    `SELECT MAX(week) AS n FROM leaderboard_week WHERE season = ?`)
    .bind(s).first();

  return json({
    season: s, week, weeks: (wk && wk.n) || 0,
    computed_at: Math.floor(Date.now() / 1000),
    chalk: await chalk(env, s, week),
    room: await room(env, s, week),
    rows: results || [],
  }, 200, {
    // The one cacheable endpoint on the API: identical for every reader, and
    // recomputed on a cron rather than on demand.
    "Cache-Control": "public, max-age=0, s-maxage=60",
    Vary: "",
  });
}

// ------------------------------------------------------------ consensus

/**
 * What the room did — and only once nobody can act on it.
 *
 * Served for public pages (a game preview, the schedule cards), so the
 * unlocked case must return nothing at all rather than an empty-looking
 * something. The SQL filters on the week's lock, not on the caller.
 */
export async function getConsensus(env, url) {
  const s = season(env);
  const ids = (url.searchParams.get("games") || "")
    .split(",").map((x) => Number(x.trim()))
    .filter((n) => Number.isInteger(n) && n > 0).slice(0, 40);
  if (!ids.length) return json({ games: {} });

  const now = Math.floor(Date.now() / 1000);
  const holes = ids.map(() => "?").join(",");
  const { results } = await env.DB.prepare(
    `SELECT p.game_id,
            SUM(p.side = 'home') AS home,
            SUM(p.side = 'away') AS away
       FROM picks p
       JOIN weeks w ON w.season = p.season AND w.week = p.week
      WHERE p.season = ? AND w.lock_at IS NOT NULL AND w.lock_at <= ?
        AND p.game_id IN (${holes})
      GROUP BY p.game_id`).bind(s, now, ...ids).all();

  const games = {};
  for (const r of results || []) {
    games[r.game_id] = { home: r.home || 0, away: r.away || 0 };
  }
  return json({ games }, 200, {
    "Cache-Control": "public, max-age=0, s-maxage=120", Vary: "",
  });
}

// ------------------------------------------------------------ public odds

export async function getUserPicks(env, url, userId) {
  const s = season(env);
  const raw = url.searchParams.get("week");
  const week = raw ? Number(raw) : await currentWeek(env, s);
  if (!Number.isInteger(week)) return fail("bad_week", 400);

  const w = await env.DB.prepare(
    `SELECT lock_at FROM weeks WHERE season = ? AND week = ?`)
    .bind(s, week).first();
  // Before the lock, one player's card is exactly the information the lock
  // exists to withhold.
  if (!isLocked(w)) return fail("not_yet_public", 403);

  const u = await env.DB.prepare(
    `SELECT display_name, status FROM users WHERE id = ?`).bind(userId).first();
  if (!u || u.status === "banned") return fail("no_such_user", 404);

  const { results } = await env.DB.prepare(
    `SELECT p.game_id, p.side, p.spread_x2, s.outcome
       FROM picks p
       LEFT JOIN pick_scores s ON s.user_id = p.user_id AND s.season = p.season
                              AND s.week = p.week AND s.game_id = p.game_id
      WHERE p.user_id = ? AND p.season = ? AND p.week = ?`)
    .bind(userId, s, week).all();

  return json({ user_id: userId, display_name: u.display_name,
                season: s, week, picks: results || [] });
}

export async function getSeasonCurrent(env) {
  const s = season(env);
  return json({ season: s, week: await currentWeek(env, s) });
}

/** Enough to tell a deploy from an outage, and nothing a probe can mine. */
export async function getHealth(env) {
  let db = "down";
  try {
    await env.DB.prepare("SELECT 1").first();
    db = "ok";
  } catch { /* reported as down */ }
  return json({ ok: db === "ok", db, at: Math.floor(Date.now() / 1000) });
}

// ------------------------------------------------------------- accounts

/**
 * Find or make the account behind a verified subject hash.
 *
 * The interesting case is `mode: "link"`: an authenticated player adding a
 * second provider. If that identity already belongs to somebody else it is a
 * 409, and a merge is offered ONLY when the other account has no picks.
 * Merging two accounts that have both played is not a hard problem, it is an
 * unanswerable one — you would have to choose whose week survives, and the
 * public board rests on not having to.
 */
export async function resolveIdentity(env, provider, subjectHash,
                                      { linkTo = null, ip = null } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const found = await env.DB.prepare(
    `SELECT user_id FROM identities WHERE provider = ? AND subject_hash = ?`)
    .bind(provider, subjectHash).first();

  if (linkTo) {
    if (found && found.user_id !== linkTo) {
      const other = await env.DB.prepare(
        `SELECT COUNT(*) AS n FROM picks WHERE user_id = ?`)
        .bind(found.user_id).first();
      if (other && other.n > 0) return { error: "identity_in_use" };
      await env.DB.batch([
        env.DB.prepare(`DELETE FROM identities WHERE user_id = ?`)
          .bind(found.user_id),
        env.DB.prepare(`DELETE FROM users WHERE id = ?`).bind(found.user_id),
        env.DB.prepare(
          `INSERT INTO identities (provider, subject_hash, user_id, linked_at)
           VALUES (?, ?, ?, ?)`).bind(provider, subjectHash, linkTo, now),
      ]);
      return { userId: linkTo, linked: true, absorbed: found.user_id };
    }
    if (!found) {
      await env.DB.prepare(
        `INSERT INTO identities (provider, subject_hash, user_id, linked_at)
         VALUES (?, ?, ?, ?)`).bind(provider, subjectHash, linkTo, now).run();
    }
    return { userId: linkTo, linked: true };
  }

  if (found) return { userId: found.user_id, created: false };

  const id = ulid();
  const ipHash = ip ? await hmac(env.IDENTITY_PEPPER, `ip|${ip}`) : null;
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO users (id, status, created_at, signup_ip_hash)
       VALUES (?, 'provisional', ?, ?)`).bind(id, now, ipHash),
    env.DB.prepare(
      `INSERT INTO identities (provider, subject_hash, user_id, linked_at)
       VALUES (?, ?, ?, ?)`).bind(provider, subjectHash, id, now),
    env.DB.prepare(
      `INSERT INTO audit_log (at, actor, action, subject, detail)
       VALUES (?, ?, 'signup', ?, ?)`).bind(now, id, id, provider),
  ]);
  return { userId: id, created: true };
}

export async function unlink(env, user, provider) {
  if (!user) return fail("unauthenticated", 401);
  const { results } = await env.DB.prepare(
    `SELECT provider FROM identities WHERE user_id = ?`).bind(user.userId).all();
  if ((results || []).length <= 1) {
    // With no email on file there is no recovery path. Removing the last
    // identity would not delete the account, it would strand it.
    return fail("last_identity", 409);
  }
  await env.DB.prepare(
    `DELETE FROM identities WHERE user_id = ? AND provider = ?`)
    .bind(user.userId, provider).run();
  return json({ unlinked: provider });
}

// Sessions: KV for the read, D1 for the truth.
//
// Every authenticated request looks a session up, so the lookup has to be the
// cheap one — that is KV. But KV cannot answer "sign me out everywhere", it
// cannot be audited, and its consistency model means a delete is visible when
// it is visible. So the row in D1 is the system of record and KV is a cache
// with a TTL, and the two are read together only where it matters.
//
// The consequence, stated rather than discovered: signing out can take up to
// about a minute to propagate to another edge location. That is acceptable
// for a pick'em and it is not acceptable silently, so it is in privacy.html.

import { b64url, randomBytes, sha256 } from "./crypto.js";

export const TTL_DAYS = 30;
export const TTL = TTL_DAYS * 86400;
// Re-issued only inside this window. A sliding expiry that wrote on every
// request would spend the KV free tier's daily budget on a busy Saturday.
export const RENEW_WITHIN = 7 * 86400;

const kvKey = (h) => `sess:${h}`;

/**
 * Mint a session. Returns the raw cookie value, which is the only time it
 * exists in plaintext anywhere — only its hash is stored, so a database dump
 * cannot be used to impersonate anyone.
 */
export async function create(env, userId, { ua, ip } = {}) {
  const raw = b64url(randomBytes(32));
  const h = await sha256(raw);
  const now = Math.floor(Date.now() / 1000);
  const expires = now + TTL;

  await env.DB.prepare(
    `INSERT INTO sessions (sid_hash, user_id, created_at, expires_at,
                           ua_hash, ip_hash)
     VALUES (?, ?, ?, ?, ?, ?)`)
    .bind(h, userId, now, expires, ua || null, ip || null).run();
  await env.SESSIONS.put(kvKey(h), userId, { expirationTtl: TTL });

  return { raw, hash: h, expires };
}

/**
 * Who is asking, or null.
 *
 * The KV hit answers the common case in one read. It is deliberately NOT
 * trusted on its own for anything destructive — see `strict` — because a
 * revoked session lingers in KV for as long as propagation takes.
 */
export async function read(env, raw, { strict = false } = {}) {
  if (!raw) return null;
  const h = await sha256(raw);

  if (!strict) {
    const cached = await env.SESSIONS.get(kvKey(h));
    if (cached) return { userId: cached, hash: h };
  }

  const row = await env.DB.prepare(
    `SELECT user_id, expires_at, revoked_at FROM sessions WHERE sid_hash = ?`)
    .bind(h).first();
  if (!row || row.revoked_at) return null;
  if (row.expires_at <= Math.floor(Date.now() / 1000)) return null;
  return { userId: row.user_id, hash: h, expiresAt: row.expires_at };
}

/** True when the cookie should be re-issued on this response. */
export function stale(expiresAt) {
  return expiresAt != null &&
         expiresAt - Math.floor(Date.now() / 1000) < RENEW_WITHIN;
}

export async function extend(env, hash) {
  const expires = Math.floor(Date.now() / 1000) + TTL;
  await env.DB.prepare(`UPDATE sessions SET expires_at = ? WHERE sid_hash = ?`)
    .bind(expires, hash).run();
  const uid = await env.DB.prepare(
    `SELECT user_id FROM sessions WHERE sid_hash = ?`).bind(hash).first();
  if (uid) await env.SESSIONS.put(kvKey(hash), uid.user_id,
                                  { expirationTtl: TTL });
  return expires;
}

export async function revoke(env, hash) {
  await env.DB.prepare(
    `UPDATE sessions SET revoked_at = ? WHERE sid_hash = ? AND revoked_at IS NULL`)
    .bind(Math.floor(Date.now() / 1000), hash).run();
  await env.SESSIONS.delete(kvKey(hash));
}

/** Sign out everywhere. Used by account deletion and by unlinking. */
export async function revokeAll(env, userId) {
  const { results } = await env.DB.prepare(
    `SELECT sid_hash FROM sessions
      WHERE user_id = ? AND revoked_at IS NULL`).bind(userId).all();
  const now = Math.floor(Date.now() / 1000);
  await env.DB.prepare(
    `UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL`)
    .bind(now, userId).run();
  await Promise.all((results || []).map((r) =>
    env.SESSIONS.delete(kvKey(r.sid_hash))));
}

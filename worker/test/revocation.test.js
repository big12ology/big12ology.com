// Signing out, and the sixty seconds afterwards.
//
// Sessions are read from KV because every authenticated request reads one and
// KV is the cheap lookup. KV is also eventually consistent, so a session
// revoked at one edge can still be served from another for up to about a
// minute. That lag is a deliberate, documented trade — it is in privacy.html —
// and it is fine for reading a slate.
//
// It is not fine for everything. session.read takes `strict`, which skips the
// cache and asks D1, and the comment on it says the cached answer is "NOT
// trusted on its own for anything destructive". The point of these tests is
// that the code agrees with that sentence, because the gap between them is
// silent: during the lag window a signed-out cookie still works, and the only
// question is what it is allowed to do.
//
// The KV in the test harness is a Map and therefore instantly consistent, so
// the lag is staged rather than waited for: revoke in D1, leave the cache
// entry behind, and that is exactly what another edge sees.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE } from "../src/cookies.js";
import { makeEnv, seedUser, seedWeek, NOW } from "./helpers/env.js";

const ORIGIN = "https://big12ology.com";

const cookieOf = (raw) => `${SESSION_COOKIE}=${encodeURIComponent(raw)}`;
const call = (env, path, opts = {}) => worker.fetch(
  new Request(`${ORIGIN}${path}`, {
    method: opts.method || "GET",
    headers: {
      Cookie: opts.cookie || "",
      ...(opts.method && opts.method !== "GET"
        ? { Origin: ORIGIN, "Content-Type": "application/json" } : {}),
    },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  }), env, {});

async function signedIn(env, id = "u") {
  seedUser(env, id, { name: "Someone" });
  const s = await session.create(env, id);
  return s;
}

/** Revoke in D1 only, leaving the cache as another edge would still have it. */
async function staleCache(env, s) {
  await env.DB.prepare(
    `UPDATE sessions SET revoked_at = ? WHERE sid_hash = ?`)
    .bind(NOW(), s.hash).run();
  // KV untouched on purpose: this is the propagation window.
}

test("a revoked session is gone the moment KV catches up", async () => {
  const env = makeEnv();
  const s = await signedIn(env);
  await session.revoke(env, s.hash);
  assert.equal(await session.read(env, s.raw), null,
    "a fully revoked session still read");
  assert.equal((await call(env, "/api/me", { cookie: cookieOf(s.raw) })).status,
    401);
});

test("during the propagation window the cookie still reads, by design",
  async () => {
    // Not a bug being pinned as correct — a documented consequence being
    // pinned so that if it ever stops being true, it is because somebody
    // decided so rather than because a cache line moved.
    const env = makeEnv();
    const s = await signedIn(env);
    await staleCache(env, s);

    assert.ok(await session.read(env, s.raw),
      "the cached read should still succeed inside the lag window");
    assert.equal(await session.read(env, s.raw, { strict: true }), null,
      "a strict read must ask D1 and see the revocation");
  });

test("signing out again inside the window still revokes", async () => {
  // Logout reads strictly, so it cannot be satisfied by a cache entry that a
  // previous revocation has not finished propagating away from.
  const env = makeEnv();
  const s = await signedIn(env);
  await staleCache(env, s);
  const res = await call(env, "/api/auth/logout",
                         { method: "POST", cookie: cookieOf(s.raw), body: {} });
  assert.equal(res.status, 204);
  assert.equal(await session.read(env, s.raw), null,
    "the cache entry outlived a second sign-out");
});

// ------------------------------------------------- the destructive ones

test("a signed-out cookie cannot delete the account", async () => {
  // The one that matters. deleteMe removes every identity and anonymises the
  // row, and with no email on file there is no way back. Reading it from a
  // stale cache means: sign out on a shared machine, and for the length of
  // the propagation window that cookie can still end the account.
  const env = makeEnv();
  const s = await signedIn(env);
  await staleCache(env, s);

  const res = await call(env, "/api/me",
                         { method: "DELETE", cookie: cookieOf(s.raw) });
  assert.equal(res.status, 401,
    "a revoked cookie deleted an account from a stale cache entry");
  const still = env.raw.prepare(
    `SELECT display_name FROM users WHERE id = 'u'`).get();
  assert.equal(still.display_name, "Someone", "the account was anonymised");
});

test("a signed-out cookie cannot unlink the last identity", async () => {
  // Same shape: unlinking revokes every session and can leave an account with
  // no way to sign in at all.
  const env = makeEnv();
  const s = await signedIn(env);
  env.raw.prepare(
    `INSERT INTO identities (provider, subject_hash, user_id, linked_at)
     VALUES ('google', 'hash', 'u', ?)`).run(NOW());
  await staleCache(env, s);

  const res = await call(env, "/api/auth/identities/google",
                         { method: "DELETE", cookie: cookieOf(s.raw) });
  assert.equal(res.status, 401,
    "a revoked cookie unlinked an identity from a stale cache entry");
  assert.equal(env.raw.prepare(
    `SELECT COUNT(*) n FROM identities WHERE user_id = 'u'`).get().n, 1);
});

test("the ordinary reads stay cheap and keep using the cache", async () => {
  // The counterweight. Making everything strict would put a D1 query on every
  // authenticated request, which is the cost the cache exists to avoid.
  const env = makeEnv();
  const s = await signedIn(env);
  seedWeek(env, { season: 2026, week: 1, games: [
    { game_id: 101, home: "Utah", away: "BYU", spread_x2: -7 }] });

  let d1Reads = 0;
  const realPrepare = env.DB.prepare.bind(env.DB);
  env.DB.prepare = (sql) => {
    if (/FROM sessions/i.test(sql)) d1Reads++;
    return realPrepare(sql);
  };

  await call(env, "/api/me", { cookie: cookieOf(s.raw) });
  await call(env, "/api/picks", { cookie: cookieOf(s.raw) });
  assert.equal(d1Reads, 0,
    `${d1Reads} session lookups hit D1 on ordinary reads; the cache should ` +
    `answer them`);
});

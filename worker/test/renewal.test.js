// The sliding expiry, which for a long time did not slide.
//
// index.js re-issues a cookie when the session is close to expiring, guarded
// on `user.expiresAt`. session.read answers most requests out of KV, because
// every authenticated request reads a session and D1 on each would be the
// expense the cache exists to avoid — and the cached path used to return only
// the user id. So the guard was never true, `extend` never ran, and somebody
// using the pick'em every day was signed out at thirty days flat.
//
// Nothing failed when that happened. The session worked, the tests passed,
// and the only symptom was a logout a month later that looked like a browser
// clearing cookies. Pinned here because a renewal that silently does not
// happen is invisible in exactly the way this suite exists to prevent.

import test from "node:test";
import assert from "node:assert/strict";
import * as session from "../src/session.js";
import { makeEnv, seedUser, NOW } from "./helpers/env.js";

test("a cached read carries the expiry, so the renewal can see it", async () => {
  const env = makeEnv();
  seedUser(env, "U1");
  const { raw } = await session.create(env, "U1");

  const cached = await session.read(env, raw);
  assert.equal(cached.userId, "U1");
  assert.ok(cached.expiresAt, "the cached path must answer with an expiry");
  assert.equal(session.stale(cached.expiresAt), false,
    "a fresh session is not due for renewal");
});

test("a session inside the window is renewed, and only then", async () => {
  const env = makeEnv();
  seedUser(env, "U2");
  const { raw, hash } = await session.create(env, "U2");

  // Age it into the renewal window in D1 and in the cache together, which is
  // what a month of use looks like from here.
  const soon = NOW() + session.RENEW_WITHIN - 3600;
  env.raw.prepare(`UPDATE sessions SET expires_at = ? WHERE sid_hash = ?`)
    .run(soon, hash);
  await env.SESSIONS.put(`sess:${hash}`, `U2:${soon}`);

  const near = await session.read(env, raw);
  assert.equal(session.stale(near.expiresAt), true,
    "a session an hour inside the window is due");

  const extended = await session.extend(env, near.hash);
  assert.ok(extended > soon, "extend pushes the expiry out");

  const after = await session.read(env, raw);
  assert.equal(session.stale(after.expiresAt), false,
    "and the renewed session is not immediately due again");
  assert.equal(after.userId, "U2", "the cache still knows whose it is");
});

test("entries written before the expiry was cached still authenticate", async () => {
  const env = makeEnv();
  seedUser(env, "U3");
  const { raw, hash } = await session.create(env, "U3");
  // The old format: a bare user id, no delimiter.
  await env.SESSIONS.put(`sess:${hash}`, "U3");

  const got = await session.read(env, raw);
  assert.equal(got.userId, "U3", "the id still reads back");
  assert.equal(got.expiresAt, null, "with no expiry, which is what it had");
  assert.equal(session.stale(got.expiresAt), false,
    "and an unknown expiry is not treated as due");
});

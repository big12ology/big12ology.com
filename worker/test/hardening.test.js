// Four things that were wrong, pinned so they stay fixed.
//
// Each of these came out of a read of the whole Worker rather than from a
// failure in the field, which is exactly why they need tests: nothing was
// visibly broken, so nothing would have noticed them coming back.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE, parseCookies } from "../src/cookies.js";
import { hmac } from "../src/crypto.js";
import { makeEnv, seedUser } from "./helpers/env.js";

const ORIGIN = "https://big12ology.com";

async function signedIn(env, userId) {
  const s = await session.create(env, userId);
  return `${SESSION_COOKIE}=${encodeURIComponent(s.raw)}`;
}

function req(path, { method = "GET", cookie, body, origin = ORIGIN } = {}) {
  const headers = { Accept: "application/json" };
  if (cookie) headers.Cookie = cookie;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    if (origin) headers.Origin = origin;
  }
  return new Request(`${ORIGIN}${path}`, {
    method, headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const call = (env, ...a) => worker.fetch(req(...a), env, {});

// ------------------------------------------------- the address is not stored

test("a session stores a keyed hash of the address, never the address", async () => {
  const env = makeEnv();
  seedUser(env, "U1", { name: "Someone" });
  const ip = "203.0.113.9";
  await session.create(env, "U1", { ip });

  const row = env.raw.prepare(
    `SELECT ip_hash FROM sessions WHERE user_id = 'U1'`).get();
  assert.ok(row.ip_hash, "nothing was stored at all");
  assert.notEqual(row.ip_hash, ip, "the raw address was stored");
  assert.ok(!row.ip_hash.includes("203.0.113"),
    "the address survives inside the stored value");
  assert.equal(row.ip_hash, await hmac(env.IDENTITY_PEPPER, `ip|${ip}`),
    "not the keyed hash the rest of the Worker uses");
});

test("the same address under a different pepper does not match", async () => {
  // The pepper is what makes a dump of this table link to nobody. If the hash
  // did not depend on it, two databases could be joined on the column.
  const a = makeEnv();
  // `test-` prefixed on purpose: tools/hooks/pre-commit treats a secret-shaped
  // value as a leak unless it announces itself as fake, and helpers/env.js
  // already follows the same convention.
  const b = makeEnv({ IDENTITY_PEPPER: "test-a-different-pepper" });
  seedUser(a, "U1"); seedUser(b, "U1");
  await session.create(a, "U1", { ip: "203.0.113.9" });
  await session.create(b, "U1", { ip: "203.0.113.9" });
  assert.notEqual(
    a.raw.prepare(`SELECT ip_hash FROM sessions`).get().ip_hash,
    b.raw.prepare(`SELECT ip_hash FROM sessions`).get().ip_hash);
});

test("deleting an account clears the session address hashes too", async () => {
  const env = makeEnv();
  seedUser(env, "U1", { name: "Someone" });
  const cookie = await signedIn(env, "U1");
  env.raw.prepare(
    `UPDATE sessions SET ip_hash = 'x', ua_hash = 'y' WHERE user_id = 'U1'`).run();

  const r = await call(env, "/api/me", { method: "DELETE", cookie, body: {} });
  assert.equal(r.status, 200);

  const rows = env.raw.prepare(
    `SELECT ip_hash, ua_hash FROM sessions WHERE user_id = 'U1'`).all();
  assert.ok(rows.length, "the session rows were expected to survive deletion");
  for (const row of rows) {
    assert.equal(row.ip_hash, null, "an address hash survived deletion");
    assert.equal(row.ua_hash, null, "a UA hash survived deletion");
  }
});

// ------------------------------------------------------ a bad cookie is inert

test("a cookie with a malformed escape does not throw", () => {
  // decodeURIComponent("%zz") is a URIError, and this ran before the router
  // matched anything — so one junk cookie made every /api/ path 500.
  const out = parseCookies("junk=%zz; other=fine");
  assert.equal(out.other, "fine");
  assert.equal(out["junk"], "%zz", "the undecodable value should be kept raw");
});

test("a malformed cookie does not take the whole API down", async () => {
  const env = makeEnv();
  const r = await worker.fetch(
    new Request(`${ORIGIN}/api/health`, { headers: { Cookie: "cf_junk=%zz" } }),
    env, {});
  assert.equal(r.status, 200, "health answered 500 because of an unrelated cookie");
  const body = await r.json();
  assert.equal(body.db, "ok");
});

test("a malformed cookie alongside a real session still authenticates", async () => {
  const env = makeEnv();
  seedUser(env, "U1", { name: "Someone" });
  const cookie = await signedIn(env, "U1");
  const r = await call(env, "/api/me", { cookie: `bad=%E0%A4%A; ${cookie}` });
  assert.equal(r.status, 200, "a junk cookie beside the session broke the read");
  assert.equal((await r.json()).user_id, "U1");
});

// --------------------------------------------------- the team write is metered

test("the team-only PATCH is rate limited like every other write", async () => {
  const env = makeEnv();
  seedUser(env, "U1", { name: "Someone" });
  const cookie = await signedIn(env, "U1");

  let refusedAt = null;
  for (let i = 0; i < 70; i++) {
    const r = await call(env, "/api/me",
      { method: "PATCH", cookie, body: { team: `T${i}` } });
    if (r.status === 429) { refusedAt = i; break; }
    assert.equal(r.status, 200, `unexpected ${r.status} on attempt ${i}`);
  }
  assert.ok(refusedAt !== null, "unlimited team writes are still accepted");
  assert.equal(refusedAt, 60, "refused somewhere other than the stated limit");
});

test("changing a team does not spend the rename budget", async () => {
  // Both are PATCH /api/me. Sharing a bucket would mean somebody trying
  // colours on could not then choose a name, which is the worse failure.
  const env = makeEnv();
  seedUser(env, "U1");
  const cookie = await signedIn(env, "U1");
  for (let i = 0; i < 10; i++) {
    const r = await call(env, "/api/me",
      { method: "PATCH", cookie, body: { team: `T${i}` } });
    assert.equal(r.status, 200);
  }
  const named = await call(env, "/api/me",
    { method: "PATCH", cookie, body: { display_name: "Perfectly Fine" } });
  assert.equal(named.status, 200, "the team writes ate the rename allowance");
});

// ------------------------------------------- linking reads D1, not the cache

test("linking a provider refuses a session revoked at another edge", async () => {
  // The KV cache still answers for a revoked session for about a minute, and
  // the link callback can DELETE an account. deleteMe and unlink already read
  // strictly; this is the third destructive path.
  const env = makeEnv();
  seedUser(env, "U1", { name: "Someone" });
  const s = await session.create(env, "U1");
  const cookie = `${SESSION_COOKIE}=${encodeURIComponent(s.raw)}`;

  // Revoke in D1 only — exactly the window KV leaves open.
  env.raw.prepare(
    `UPDATE sessions SET revoked_at = 1 WHERE sid_hash = ?`).run(s.hash);
  assert.ok(await session.read(env, s.raw),
    "the cache was expected to still answer — the test's premise is gone");

  const r = await call(env, "/api/auth/link/github", { cookie });
  assert.equal(r.status, 302);
  assert.match(r.headers.get("Location") || "", /auth_error=unauthenticated/,
    "a revoked session was allowed to start a link");
});

// ------------------------------------------------- a week that is not a contest

// 2026 opens with one game — North Carolina at TCU, in Dublin — of which only
// TCU is a Big 12 side. Survivor is a choice about which team to spend, and one
// legal team is not a choice: it is a toll on whoever does not already know
// that skipping is free. Since entered_week is MIN(week) over a player's own
// picks and the scoring walk starts there, a week nobody can pick in is a week
// nobody is judged on — so refusing the pick is the whole fix.

import { seedWeek } from "./helpers/env.js";

/** A week whose only Big 12 side is the home team of a single game. */
function seedLoneGameWeek(env, week = 0) {
  return seedWeek(env, {
    week,
    games: [{ game_id: 900, home: "TCU", away: "North Carolina",
              spread_x2: -14, b12: "home" }],
  });
}

test("a week with one pickable team refuses the pick, and says why", async () => {
  const env = makeEnv();
  seedUser(env, "U1", { name: "Someone" });
  const cookie = await signedIn(env, "U1");
  seedLoneGameWeek(env);

  const r = await call(env, "/api/survivor/pick", {
    method: "PUT", cookie,
    body: { week: 0, game_id: 900, team: "TCU" },
  });
  assert.equal(r.status, 409);
  const b = await r.json();
  assert.equal(b.error, "no_contest");
  assert.equal(b.pickable_teams, 1);
  assert.equal(b.min_teams, 2);

  assert.equal(env.raw.prepare(
    `SELECT COUNT(*) n FROM survivor_picks`).get().n, 0,
    "a team was spent on a week that offered no choice");
});

test("the week reports itself as no contest before anybody tries", async () => {
  const env = makeEnv();
  seedUser(env, "U1", { name: "Someone" });
  const cookie = await signedIn(env, "U1");
  seedLoneGameWeek(env);

  const b = await (await call(env, "/api/survivor?week=0", { cookie })).json();
  assert.equal(b.no_contest, true);
  assert.equal(b.pickable_teams, 1);
});

test("two pickable teams is a contest, and is allowed", async () => {
  // The boundary, from the other side: two is where a decision starts to
  // exist, so it must not be swept up by the same guard.
  const env = makeEnv();
  seedUser(env, "U1", { name: "Someone" });
  const cookie = await signedIn(env, "U1");
  seedWeek(env, {
    week: 0,
    games: [{ game_id: 900, home: "TCU", away: "North Carolina",
              spread_x2: -14, b12: "home" },
            { game_id: 901, home: "Baylor", away: "Sam Houston",
              spread_x2: -20, b12: "home" }],
  });
  // A season has to remain, or entry is refused by a different rule: joining
  // closes when fewer than MIN_USABLE teams are left to pick from across the
  // rest of the year, and a fixture with one week has two.
  seedWeek(env, { week: 1 });

  const b = await (await call(env, "/api/survivor?week=0", { cookie })).json();
  assert.equal(b.no_contest, false);
  assert.equal(b.pickable_teams, 2);

  const r = await call(env, "/api/survivor/pick", {
    method: "PUT", cookie,
    body: { week: 0, game_id: 900, team: "TCU" },
  });
  assert.equal(r.status, 200, JSON.stringify(await r.clone().json()));
  assert.equal((await r.json()).pick.team, "TCU");
});

test("skipping a no-contest week costs nothing: entry is the first pick", async () => {
  // The property the whole fix rests on. A player who cannot pick in week 0
  // enters at week 1, and the scoring walk never visits a week before that —
  // so there is no miss to record and no elimination to explain.
  const env = makeEnv();
  seedUser(env, "U1", { name: "Someone" });
  const cookie = await signedIn(env, "U1");
  seedLoneGameWeek(env, 0);
  seedWeek(env, { week: 1 });

  const r = await call(env, "/api/survivor/pick", {
    method: "PUT", cookie, body: { week: 1, game_id: 401, team: "Iowa State" },
  });
  assert.equal(r.status, 200, JSON.stringify(await r.clone().json()));

  const row = env.raw.prepare(
    `SELECT MIN(week) w FROM survivor_picks WHERE user_id = 'U1'`).get();
  assert.equal(row.w, 1, "week 0 leaked into this player's entry week");
});

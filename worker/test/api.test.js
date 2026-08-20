// The API, driven through the real router.
//
// These go in the front door — `worker.fetch(new Request(...), env)` — rather
// than calling handlers directly, because half of what is worth testing lives
// in the router: the CSRF checks, the method dispatch, the session cookie, the
// 404 for everything outside /api/. A test that called putPicks() straight
// would prove the handler works and nothing about whether it is reachable, or
// reachable by the wrong person.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE } from "../src/cookies.js";
import {
  makeEnv, seedWeek, seedUser, seedPick, seedSurvivorPick, forceLock,
  NOW, HOUR,
} from "./helpers/env.js";

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

// --------------------------------------------------------------- routing

test("nothing outside /api/ is answered", async () => {
  const env = makeEnv();
  for (const p of ["/", "/pools/", "/tiebreaker/index.html"]) {
    const r = await call(env, p);
    assert.equal(r.status, 404, `${p} was handled`);
  }
});

test("bare /api is a JSON 404, not an HTML one", async () => {
  const env = makeEnv();
  const r = await call(env, "/api");
  assert.equal(r.status, 404);
  assert.match(r.headers.get("Content-Type"), /application\/json/);
});

test("health reports the database", async () => {
  const env = makeEnv();
  const r = await call(env, "/api/health");
  const b = await r.json();
  assert.equal(b.ok, true);
  assert.equal(b.db, "ok");
});

test("health counts who signed up and who is actually in each game", async () => {
  const env = makeEnv();
  seedWeek(env);
  // Three accounts, three standings: a provisional signup is a real person
  // who has not finished a scored week yet, and a banned one is not in any
  // public number no matter how much it picked.
  seedUser(env, "u-new", { status: "provisional" });
  seedUser(env, "u-vet", { status: "active" });
  seedUser(env, "u-out", { status: "banned" });
  seedPick(env, "u-new", 2026, 3, 401, "home", -13);
  seedPick(env, "u-new", 2026, 3, 402, "away", 7);   // twice, counted once
  seedPick(env, "u-vet", 2026, 3, 401, "away", -13);
  seedPick(env, "u-out", 2026, 3, 401, "home", -13);
  seedSurvivorPick(env, "u-new", 2026, 3, 401, "Iowa State");
  seedSurvivorPick(env, "u-out", 2026, 3, 402, "Baylor");

  const b = await (await call(env, "/api/health")).json();
  assert.equal(b.registered, 2, "signed up = provisional + active");
  assert.equal(b.pickem_players, 2, "picked at least one game this season");
  assert.equal(b.survivor_players, 1, "picked at least one survivor week");
  assert.equal(b.players, 1, "players still means a completed scored week");
});

test("every response is noindex and uncacheable unless it says otherwise",
  async () => {
    const env = makeEnv();
    seedWeek(env);
    const slate = await call(env, "/api/slate");
    assert.equal(slate.headers.get("Cache-Control"), "no-store");
    assert.equal(slate.headers.get("X-Robots-Tag"), "noindex");
    // The board is the one endpoint identical for every reader.
    const board = await call(env, "/api/leaderboard");
    assert.match(board.headers.get("Cache-Control"), /s-maxage=60/);
  });

// ------------------------------------------------------------------ CSRF

test("a state-changing request needs our Origin and a JSON content type",
  async () => {
    const env = makeEnv();
    seedUser(env, "u1", { name: "Player" });
    const cookie = await signedIn(env, "u1");

    // Right session, wrong origin.
    const evil = new Request(`${ORIGIN}/api/me`, {
      method: "PATCH",
      headers: { Cookie: cookie, Origin: "https://evil.example",
                 "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: "Hacked" }),
    });
    assert.equal((await worker.fetch(evil, env, {})).status, 403);

    // Right origin, form content type — the shape a cross-site <form> can
    // actually produce without a preflight.
    const form = new Request(`${ORIGIN}/api/me`, {
      method: "PATCH",
      headers: { Cookie: cookie, Origin: ORIGIN,
                 "Content-Type": "application/x-www-form-urlencoded" },
      body: "display_name=Hacked",
    });
    assert.equal((await worker.fetch(form, env, {})).status, 403);

    assert.equal(env.raw.prepare(
      "SELECT display_name n FROM users WHERE id='u1'").get().n, "Player");
  });

// -------------------------------------------------------------------- me

test("me is 401 with no session and the account with one", async () => {
  const env = makeEnv();
  assert.equal((await call(env, "/api/me")).status, 401);

  seedUser(env, "u1", { name: "Player" });
  const r = await call(env, "/api/me", { cookie: await signedIn(env, "u1") });
  const b = await r.json();
  assert.equal(b.user_id, "u1");
  assert.equal(b.display_name, "Player");
  assert.equal(b.needs_name, false);
  assert.deepEqual(b.identities, []);
});

test("a fabricated session cookie is not a session", async () => {
  const env = makeEnv();
  seedUser(env, "u1", { name: "Player" });
  const r = await call(env, "/api/me",
                       { cookie: `${SESSION_COOKIE}=not-a-real-session` });
  assert.equal(r.status, 401);
});

test("a name can be set once, then not again for a month", async () => {
  const env = makeEnv();
  seedUser(env, "u1");
  const cookie = await signedIn(env, "u1");

  const first = await call(env, "/api/me",
    { method: "PATCH", cookie, body: { display_name: "Cyclone Fan" } });
  assert.equal(first.status, 200);
  assert.equal((await first.json()).display_name, "Cyclone Fan");

  const again = await call(env, "/api/me",
    { method: "PATCH", cookie, body: { display_name: "Second Thoughts" } });
  assert.equal(again.status, 429);
  assert.equal((await again.json()).error, "rename_cooldown");
});

test("two people cannot hold names that normalize the same", async () => {
  const env = makeEnv();
  seedUser(env, "u1"); seedUser(env, "u2");
  const a = await signedIn(env, "u1"), b = await signedIn(env, "u2");

  assert.equal((await call(env, "/api/me",
    { method: "PATCH", cookie: a, body: { display_name: "Cyclone" } })).status, 200);
  // A Cyrillic 'о'. Renders identically, and on a public board that is the
  // whole attack.
  const r = await call(env, "/api/me",
    { method: "PATCH", cookie: b, body: { display_name: "Cyclоne" } });
  assert.equal(r.status, 409);
  assert.equal((await r.json()).error, "name_taken");
});

test("team is a preference, with no cooldown attached", async () => {
  const env = makeEnv();
  seedUser(env, "u1", { name: "Player" });
  const cookie = await signedIn(env, "u1");
  for (const t of ["Iowa State", "__big12", ""]) {
    const r = await call(env, "/api/me",
      { method: "PATCH", cookie, body: { team: t } });
    assert.equal(r.status, 200, `team ${t} refused`);
  }
  assert.equal(env.raw.prepare(
    "SELECT team FROM users WHERE id='u1'").get().team, null);
});

// ----------------------------------------------------------------- picks

test("a whole card saves, and replaces rather than merges", async () => {
  const env = makeEnv();
  seedWeek(env);
  seedUser(env, "u1", { name: "Player" });
  const cookie = await signedIn(env, "u1");

  const r = await call(env, "/api/picks", { method: "PUT", cookie,
    body: { season: 2026, week: 3, picks: { 401: "home", 402: "away" } } });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).count, 2);

  // Fewer picks the second time means fewer picks, not a merge of the two.
  await call(env, "/api/picks", { method: "PUT", cookie,
    body: { season: 2026, week: 3, picks: { 401: "away" } } });
  const got = await (await call(env, "/api/picks?week=3", { cookie })).json();
  assert.deepEqual(got.picks, { 401: "away" });
});

test("a game with no line is dropped, not rejected", async () => {
  // The client renders the whole slate, so it may well send one. Refusing the
  // request would lose the fourteen good picks alongside it.
  const env = makeEnv();
  seedWeek(env);
  seedUser(env, "u1", { name: "Player" });
  const cookie = await signedIn(env, "u1");
  const r = await call(env, "/api/picks", { method: "PUT", cookie,
    body: { week: 3, picks: { 401: "home", 403: "home" } } });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).count, 1);
});

// Signing in used to drop a brand-new account straight back on the slate,
// where the only sign anything had happened was a chip reading "Choose a
// name". These are the two halves of making that impossible.
test("an account with no name cannot pick", async () => {
  const env = makeEnv();
  seedWeek(env);
  seedUser(env, "u1");                       // signed in, never named
  const cookie = await signedIn(env, "u1");

  const r = await call(env, "/api/picks", { method: "PUT", cookie,
    body: { week: 3, picks: { 401: "home" } } });
  assert.equal(r.status, 403);
  assert.equal((await r.json()).error, "no_display_name");
  assert.equal(env.raw.prepare("SELECT COUNT(*) c FROM picks").get().c, 0);

  // And the moment it has one, it can.
  await call(env, "/api/me",
    { method: "PATCH", cookie, body: { display_name: "Newcomer" } });
  const ok = await call(env, "/api/picks", { method: "PUT", cookie,
    body: { week: 3, picks: { 401: "home" } } });
  assert.equal(ok.status, 200);
});

test("me reports needs_name, which is what the pages branch on", async () => {
  const env = makeEnv();
  seedUser(env, "u1");
  const cookie = await signedIn(env, "u1");
  assert.equal((await (await call(env, "/api/me", { cookie })).json()).needs_name,
               true);
  await call(env, "/api/me",
    { method: "PATCH", cookie, body: { display_name: "Named Now" } });
  assert.equal((await (await call(env, "/api/me", { cookie })).json()).needs_name,
               false);
});

test("the lock is a 409 that hands back the server's picks", async () => {
  const env = makeEnv();
  seedWeek(env);
  seedUser(env, "u1", { name: "Player" });
  const cookie = await signedIn(env, "u1");
  await call(env, "/api/picks", { method: "PUT", cookie,
    body: { week: 3, picks: { 401: "home" } } });

  forceLock(env, 2026, 3);
  const r = await call(env, "/api/picks", { method: "PUT", cookie,
    body: { week: 3, picks: { 401: "away", 402: "home" } } });
  assert.equal(r.status, 409);
  const b = await r.json();
  assert.equal(b.error, "locked");
  // The client repaints every radio from this, because its own optimistic
  // state is now a lie.
  assert.deepEqual(b.picks, { 401: "home" });
  assert.equal(env.raw.prepare(
    "SELECT COUNT(*) c FROM picks WHERE user_id='u1'").get().c, 1);
});

test("picks need a session", async () => {
  const env = makeEnv();
  seedWeek(env);
  assert.equal((await call(env, "/api/picks")).status, 401);
  assert.equal((await call(env, "/api/picks",
    { method: "PUT", body: { week: 3, picks: {} } })).status, 401);
});

// ------------------------------------------------------- what leaks when

test("the consensus is withheld until the week locks", async () => {
  const env = makeEnv();
  seedWeek(env);
  for (const u of ["a", "b", "c"]) {
    seedUser(env, u, { name: u });
    seedPick(env, u, 2026, 3, 401, "home", -13);
  }

  const open = await (await call(env, "/api/consensus?games=401")).json();
  assert.deepEqual(open.games, {},
    "the crowd was visible while people could still act on it");

  const slateOpen = await (await call(env, "/api/slate?week=3")).json();
  assert.equal(slateOpen.games.find((g) => g.game_id === 401).consensus,
               undefined);

  forceLock(env, 2026, 3);
  const shut = await (await call(env, "/api/consensus?games=401")).json();
  assert.deepEqual(shut.games["401"], { home: 3, away: 0 });
  const slateShut = await (await call(env, "/api/slate?week=3")).json();
  assert.deepEqual(slateShut.games.find((g) => g.game_id === 401).consensus,
                   { home: 3, away: 0 });
});

test("one player's card is not public until the week locks", async () => {
  const env = makeEnv();
  seedWeek(env);
  seedUser(env, "u1", { name: "Player" });
  seedPick(env, "u1", 2026, 3, 401, "home", -13);

  const early = await call(env, "/api/users/u1/picks?week=3");
  assert.equal(early.status, 403);
  assert.equal((await early.json()).error, "not_yet_public");

  forceLock(env, 2026, 3);
  const late = await call(env, "/api/users/u1/picks?week=3");
  assert.equal(late.status, 200);
  assert.equal((await late.json()).picks.length, 1);
});

// ----------------------------------------------------------------- slate

test("the slate is shaped the way the client reads it", async () => {
  const env = makeEnv();
  seedWeek(env);
  const s = await (await call(env, "/api/slate?week=3")).json();
  assert.equal(s.season, 2026);
  assert.equal(s.week, 3);
  assert.equal(s.locked, false);
  assert.equal(s.games.length, 3);
  const g = s.games[0];
  for (const k of ["game_id", "home", "away", "kickoff", "kickoff_at",
                   "spread_x2"]) {
    assert.ok(k in g, `slate game is missing ${k}`);
  }
  // ISO with a Z, which is what the client hands to Date and to <time>.
  assert.match(g.kickoff, /^\d{4}-\d\d-\d\dT.*Z$/);
  assert.equal(s.games.find((x) => x.game_id === 403).unpickable, "no_line");
});

test("an unknown week is a 404, not an empty slate", async () => {
  const env = makeEnv();
  seedWeek(env);
  assert.equal((await call(env, "/api/slate?week=9")).status, 404);
  assert.equal((await call(env, "/api/slate?week=abc")).status, 404);
});

// ------------------------------------------------------------- logout

test("logging out ends the session everywhere it is checked", async () => {
  const env = makeEnv();
  seedUser(env, "u1", { name: "Player" });
  const cookie = await signedIn(env, "u1");
  assert.equal((await call(env, "/api/me", { cookie })).status, 200);

  const out = await worker.fetch(new Request(`${ORIGIN}/api/auth/logout`, {
    method: "POST",
    headers: { Cookie: cookie, Origin: ORIGIN,
               "Content-Type": "application/json" },
  }), env, {});
  assert.equal(out.status, 204);
  assert.match(out.headers.get("Set-Cookie"), /Max-Age=0/);
  assert.equal((await call(env, "/api/me", { cookie })).status, 401);
});

test("logout is refused from another origin", async () => {
  const env = makeEnv();
  seedUser(env, "u1", { name: "Player" });
  const cookie = await signedIn(env, "u1");
  const r = await worker.fetch(new Request(`${ORIGIN}/api/auth/logout`, {
    method: "POST",
    headers: { Cookie: cookie, Origin: "https://evil.example",
               "Content-Type": "application/json" },
  }), env, {});
  assert.equal(r.status, 403);
  assert.equal((await call(env, "/api/me", { cookie })).status, 200);
});

// ----------------------------------------------------------- identities

test("the last identity cannot be unlinked", async () => {
  const env = makeEnv();
  seedUser(env, "u1", { name: "Player" });
  env.raw.prepare(
    `INSERT INTO identities (provider, subject_hash, user_id, linked_at)
     VALUES ('google', 'h1', 'u1', ?)`).run(NOW());
  const cookie = await signedIn(env, "u1");

  const r = await worker.fetch(new Request(
    `${ORIGIN}/api/auth/identities/google`,
    { method: "DELETE", headers: { Cookie: cookie, Origin: ORIGIN,
                                   "Content-Type": "application/json" } }), env, {});
  assert.equal(r.status, 409);
  // With no email on file there is no recovery path, so this would strand the
  // account rather than delete it.
  assert.equal((await r.json()).error, "last_identity");
});

test("deleting an account keeps the picks and loses the identity", async () => {
  const env = makeEnv();
  seedWeek(env);
  seedUser(env, "u1", { name: "Departing" });
  seedPick(env, "u1", 2026, 3, 401, "home", -13);
  env.raw.prepare(
    `INSERT INTO identities (provider, subject_hash, user_id, linked_at)
     VALUES ('google', 'h1', 'u1', ?)`).run(NOW());
  const cookie = await signedIn(env, "u1");

  const r = await worker.fetch(new Request(`${ORIGIN}/api/me`,
    { method: "DELETE", headers: { Cookie: cookie, Origin: ORIGIN,
                                   "Content-Type": "application/json" } }), env, {});
  assert.equal(r.status, 200);
  assert.match((await r.json()).display_name, /^deleted-/);

  assert.equal(env.raw.prepare(
    "SELECT COUNT(*) c FROM identities WHERE user_id='u1'").get().c, 0);
  // The board would otherwise develop holes, and every other player's rank
  // depends on these games having been picked.
  assert.equal(env.raw.prepare(
    "SELECT COUNT(*) c FROM picks WHERE user_id='u1'").get().c, 1);
  assert.equal((await call(env, "/api/me", { cookie })).status, 401);
});

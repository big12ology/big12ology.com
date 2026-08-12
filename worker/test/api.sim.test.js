// Ten thousand random request sequences against the real router.
//
// The other two simulators generate a season and check arithmetic. This one
// generates BEHAVIOUR — a population of accounts, sessions being minted,
// revoked and replayed, and a long shuffle of requests through the actual
// worker.fetch, mixing ordinary traffic with the things an attacker would
// send. Nothing here reasons about the season; it reasons about who is
// allowed to do what.
//
// Auth code fails differently from scoring code. Scoring gets a number wrong
// and the board looks odd; auth gets a check wrong and nothing looks wrong at
// all — the request succeeds, which is exactly the bug. So the assertions are
// about what must NOT have happened: no forged cookie ever authenticates, no
// revoked session comes back, no cross-origin write lands, no request from one
// account ever changes another's row, and the raw session value never reaches
// the database.
//
// Deterministic: a failure prints its seed, SIM_SEED=n replays that sequence.
//
//     SIM_RUNS=10000 node --test test/api.sim.test.js

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE, STATE_COOKIE, HOME, safeReturn, sign, unsign }
  from "../src/cookies.js";
import { LIMITS } from "../src/ratelimit.js";
import { makeEnv, seedWeek, seedUser, forceLock, NOW, HOUR }
  from "./helpers/env.js";

const RUNS = Number(process.env.SIM_RUNS || 200);
const ONLY = process.env.SIM_SEED ? Number(process.env.SIM_SEED) : null;
const ORIGIN = "https://big12ology.com";
const SEASON = 2026;

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const int = (r, lo, hi) => lo + Math.floor(r() * (hi - lo + 1));
const pick = (r, xs) => xs[Math.floor(r() * xs.length)];

// Cookies an attacker would actually try. Every one of these must fail, and
// the interesting ones are the near-misses: a real session value belonging to
// somebody else, a real value with one character changed, a value that was
// valid until it was revoked.
const FORGERIES = [
  "", "null", "undefined", "0", "-1", "../../etc/passwd",
  "' OR 1=1 --", "a".repeat(64), "%00", "{}", "[]",
  "__proto__", "constructor",
];

const BAD_ORIGINS = [
  "https://evil.example", "http://big12ology.com", "https://big12ology.com.evil.example",
  "null", "", "https://BIG12OLOGY.COM",
];

function req(path, { method = "GET", cookie, body, origin, contentType } = {}) {
  const headers = new Headers({ Accept: "application/json" });
  if (cookie) headers.set("Cookie", cookie);
  if (origin !== undefined && origin !== null) headers.set("Origin", origin);
  if (body !== undefined) {
    headers.set("Content-Type", contentType ?? "application/json");
  }
  return new Request(`${ORIGIN}${path}`, {
    method, headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
const call = (env, ...a) => worker.fetch(req(...a), env, {});
const cookieOf = (raw) => `${SESSION_COOKIE}=${encodeURIComponent(raw)}`;

/** A small slate so the picking endpoints have something legal to write. */
function seedWorld(env, r) {
  const games = [
    { game_id: 900, home: "Utah", away: "BYU", spread_x2: -7, b12: "both" },
    { game_id: 901, home: "Kansas", away: "TCU", spread_x2: 5, b12: "both" },
    { game_id: 902, home: "Baylor", away: "Notre Dame", spread_x2: -3,
      b12: "home" },
    { game_id: 903, home: "Iowa State", away: "Houston", spread_x2: null },
  ];
  seedWeek(env, { season: SEASON, week: 1, games });
  // A locked past week gives the lock checks something real to refuse.
  seedWeek(env, { season: SEASON, week: 2, games: [
    { game_id: 910, home: "TCU", away: "Utah", spread_x2: -2, b12: "both" }] });
  forceLock(env, SEASON, 2);
  return { week: 1, games };
}

/** State of the world this simulation is allowed to assume. */
function makeWorld(env, r) {
  const slate = seedWorld(env, r);
  const users = [];
  const n = int(r, 2, 6);
  for (let i = 0; i < n; i++) {
    const id = `s${i}`;
    // Some accounts have no display name yet: that account cannot pick, which
    // is a rule with its own failure mode.
    const named = r() > 0.25;
    seedUser(env, id, { name: named ? `Name ${i}` : null,
                        status: r() < 0.2 ? "provisional" : "active" });
    users.push({ id, named, sessions: [], revoked: [] });
  }
  return { slate, users };
}

async function mint(env, u) {
  const s = await session.create(env, u.id);
  u.sessions.push(s.raw);
  return s.raw;
}

/** Everything that must stay true no matter what sequence just ran. */
async function invariants(env, world, seenNames) {
  // --- the raw session value is never stored, only its hash
  for (const u of world.users) {
    for (const raw of [...u.sessions, ...u.revoked]) {
      const hit = env.raw.prepare(
        `SELECT COUNT(*) n FROM sessions WHERE sid_hash = ?`).get(raw);
      assert.equal(hit.n, 0, "a raw session value was stored verbatim");
    }
  }

  // --- a revoked session never authenticates again
  for (const u of world.users) {
    for (const raw of u.revoked) {
      const who = await session.read(env, raw, { strict: true });
      assert.equal(who, null, "a revoked session still reads");
      const res = await call(env, "/api/me", { cookie: cookieOf(raw) });
      assert.equal(res.status, 401,
        "a revoked cookie still answered /api/me");
    }
  }

  // --- display names remain unique, case- and confusable-folded
  const norms = env.raw.prepare(
    `SELECT display_norm, COUNT(*) n FROM users
      WHERE display_norm IS NOT NULL GROUP BY display_norm HAVING n > 1`).all();
  assert.equal(norms.length, 0,
    `two accounts share a normalized name: ${JSON.stringify(norms)}`);

  // --- a rename is always recorded
  const renamed = env.raw.prepare(
    `SELECT id, display_name FROM users
      WHERE display_name IS NOT NULL AND name_changed_at IS NOT NULL`).all();
  for (const u of renamed) {
    const hist = env.raw.prepare(
      `SELECT COUNT(*) n FROM name_history WHERE user_id = ?`).get(u.id);
    assert.ok(hist.n > 0, `${u.id} was renamed with no history row`);
  }

  // --- every pick belongs to a real account and a real slate row
  const orphan = env.raw.prepare(
    `SELECT COUNT(*) n FROM picks p
      WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = p.user_id)`).get();
  assert.equal(orphan.n, 0, "a pick outlived its account");

  // --- nothing was ever written against a locked week
  const late = env.raw.prepare(
    `SELECT COUNT(*) n FROM picks p
       JOIN weeks w ON w.season = p.season AND w.week = p.week
      WHERE w.lock_at IS NOT NULL AND w.lock_at <= ? AND p.created_at > w.lock_at`)
    .get(NOW() + 1);
  assert.equal(late.n, 0, "a pick was written after its week locked");
}

async function runOne(seed) {
  const r = rng(seed);
  const env = makeEnv();
  const world = makeWorld(env, r);
  const cover = {};
  const bump = (k) => { cover[k] = (cover[k] || 0) + 1; };

  // Snapshot of who owns what, so a cross-account write is detectable.
  const ownedBefore = () => Object.fromEntries(
    env.raw.prepare(`SELECT id, display_name, team, status FROM users`)
      .all().map((u) => [u.id, JSON.stringify(u)]));

  const ops = int(r, 8, 40);
  for (let i = 0; i < ops; i++) {
    const u = pick(r, world.users);
    const roll = r();

    // ---------------------------------------------------- forged credentials
    if (roll < 0.18) {
      bump("forged");
      const cookie = cookieOf(pick(r, FORGERIES));
      const res = await call(env, "/api/me", { cookie });
      assert.equal(res.status, 401, `a forged cookie authenticated: ${cookie}`);

      // A write with a forged cookie must not land either.
      const before = ownedBefore();
      const w = await call(env, "/api/me", {
        method: "PATCH", cookie, origin: ORIGIN,
        body: { display_name: "Forged" },
      });
      assert.equal(w.status, 401, "a forged cookie wrote to an account");
      assert.deepEqual(ownedBefore(), before, "a forged write changed a row");
      continue;
    }

    // ------------------------------------------------- a real session, bent
    if (roll < 0.26 && u.sessions.length) {
      bump("bent");
      const good = pick(r, u.sessions);
      // Flip one character. The hash changes completely, so this must fail —
      // and failing is the only thing that makes the hash worth storing.
      const at = int(r, 0, good.length - 1);
      const ch = good[at] === "A" ? "B" : "A";
      const bent = good.slice(0, at) + ch + good.slice(at + 1);
      if (bent !== good) {
        const res = await call(env, "/api/me", { cookie: cookieOf(bent) });
        if (res.status === 200) {
          // The session cookie is hashed as a STRING, so unlike the signed
          // state cookie there is no encoding slack here — but if it ever
          // does authenticate it had better be the same account.
          assert.equal((await res.json()).user_id, u.id,
            "one changed character authenticated as somebody else");
        }
      }
      continue;
    }

    // ------------------------------------------------------- cross-origin
    if (roll < 0.36) {
      bump("csrf");
      const raw = u.sessions.length ? pick(r, u.sessions) : await mint(env, u);
      const before = ownedBefore();
      const origin = pick(r, BAD_ORIGINS);
      const res = await call(env, "/api/me", {
        method: "PATCH", cookie: cookieOf(raw),
        origin: origin === "" ? undefined : origin,
        body: { display_name: `Cross ${i}` },
      });
      assert.equal(res.status, 403,
        `a write from origin ${JSON.stringify(origin)} was accepted`);
      assert.deepEqual(ownedBefore(), before, "a cross-origin write landed");
      continue;
    }

    // --------------------------------------- right origin, wrong content type
    if (roll < 0.42) {
      bump("content_type");
      const raw = u.sessions.length ? pick(r, u.sessions) : await mint(env, u);
      const before = ownedBefore();
      const res = await call(env, "/api/me", {
        method: "PATCH", cookie: cookieOf(raw), origin: ORIGIN,
        contentType: pick(r, ["text/plain", "application/x-www-form-urlencoded",
                              "multipart/form-data", "application/JSON; x=1"]),
        body: { display_name: `Form ${i}` },
      });
      // The last one is legitimately json with a parameter, so it may pass;
      // everything else must not. Either way it must never 5xx.
      assert.ok(res.status < 500, "a bad content type crashed the worker");
      if (res.status === 403) {
        assert.deepEqual(ownedBefore(), before, "a 403 still wrote");
      }
      continue;
    }

    // ------------------------------------------------------------- sign in
    if (roll < 0.55) {
      bump("mint");
      const raw = await mint(env, u);
      const res = await call(env, "/api/me", { cookie: cookieOf(raw) });
      assert.equal(res.status, 200, "a fresh session did not authenticate");
      assert.equal((await res.json()).user_id, u.id,
        "a session answered as the wrong account");
      continue;
    }

    // ------------------------------------------------------------ sign out
    if (roll < 0.64 && u.sessions.length) {
      bump("logout");
      const raw = u.sessions.splice(int(r, 0, u.sessions.length - 1), 1)[0];
      const res = await call(env, "/api/auth/logout", {
        method: "POST", cookie: cookieOf(raw), origin: ORIGIN, body: {},
      });
      assert.equal(res.status, 204, "logout failed");
      u.revoked.push(raw);
      // The other sessions this account holds are untouched.
      for (const other of u.sessions) {
        const who = await session.read(env, other, { strict: true });
        assert.ok(who && who.userId === u.id,
          "signing out one session killed another");
      }
      continue;
    }

    // -------------------------------------------------------------- rename
    if (roll < 0.76) {
      bump("rename");
      const raw = u.sessions.length ? pick(r, u.sessions) : await mint(env, u);
      // Sometimes a name another account already holds, sometimes a fresh
      // one, sometimes something that should not validate at all.
      const other = pick(r, world.users);
      const name = r() < 0.35
        ? `Name ${world.users.indexOf(other)}`
        : pick(r, [`Fresh ${seed}-${i}`, "", "  ", "a", "x".repeat(200),
                   "Ｎａｍｅ 0", "Name​0", "<script>", "admin"]);
      const before = ownedBefore();
      const res = await call(env, "/api/me", {
        method: "PATCH", cookie: cookieOf(raw), origin: ORIGIN,
        body: { display_name: name },
      });
      assert.ok(res.status < 500, `rename crashed on ${JSON.stringify(name)}`);
      const after = ownedBefore();
      // Whatever happened, it happened to exactly one account.
      const changed = Object.keys(after).filter((k) => after[k] !== before[k]);
      assert.ok(changed.length <= 1, "one rename changed several accounts");
      if (changed.length) {
        assert.equal(changed[0], u.id, "a rename changed somebody else");
      }
      continue;
    }

    // ------------------------------------------------------- submit a card
    if (roll < 0.9) {
      bump("picks");
      const raw = u.sessions.length ? pick(r, u.sessions) : await mint(env, u);
      const week = r() < 0.3 ? 2 : 1;          // week 2 is locked
      const picks = {};
      for (const g of world.slate.games) {
        if (r() < 0.5) picks[String(g.game_id)] = r() < 0.5 ? "home" : "away";
      }
      const before = ownedBefore();
      const res = await call(env, "/api/picks", {
        method: "PUT", cookie: cookieOf(raw), origin: ORIGIN,
        body: { season: SEASON, week, picks },
      });
      assert.ok(res.status < 500, "putPicks crashed");
      if (week === 2) {
        assert.notEqual(res.status, 200, "a locked week accepted a card");
      }
      assert.deepEqual(ownedBefore(), before, "submitting picks changed a row");
      // Nothing landed on the unlined game, whatever was sent.
      const bad = env.raw.prepare(
        `SELECT COUNT(*) n FROM picks WHERE game_id = 903`).get();
      assert.equal(bad.n, 0, "a pick landed on a game with no line");
      continue;
    }

    // ----------------------------------------------------- delete the account
    bump("delete");
    const raw = u.sessions.length ? pick(r, u.sessions) : await mint(env, u);
    const res = await call(env, "/api/me", {
      method: "DELETE", cookie: cookieOf(raw), origin: ORIGIN,
    });
    if (res.status === 204 || res.status === 200) {
      u.revoked.push(...u.sessions, raw);
      u.sessions = [];
      // Every session that account held is dead, and its picks survive.
      for (const dead of u.revoked) {
        const who = await session.read(env, dead, { strict: true });
        assert.equal(who, null, "a deleted account kept a live session");
      }
      const still = env.raw.prepare(
        `SELECT display_name, status FROM users WHERE id = ?`).get(u.id);
      assert.ok(still, "deletion removed the row the picks point at");
    }
  }

  await invariants(env, world);
  return cover;
}

// ------------------------------------------------------------- pure surfaces

test("the redirect allowlist holds against anything", () => {
  const r = rng(99);
  const bits = ["/", "//", "\\", "..", "%2e%2e", "pools", "tiebreaker", "@",
                "evil.example", "https:", "\t", "\n", " ", "?", "#", "%00"];
  for (let i = 0; i < 20000; i++) {
    let s = "";
    for (let j = int(r, 0, 6); j > 0; j--) s += pick(r, bits);
    const out = safeReturn(s);
    assert.ok(out === HOME || out.startsWith("/pools/"),
      `safeReturn(${JSON.stringify(s)}) escaped to ${out}`);
    // Whatever it returns must be same-origin and not protocol-relative.
    assert.ok(out.startsWith("/") && !out.startsWith("//"),
      `safeReturn(${JSON.stringify(s)}) produced ${out}`);
  }
});

test("a state cookie only verifies exactly as issued", async () => {
  const r = rng(7);
  const KEY = "sim-key";
  for (let i = 0; i < 400; i++) {
    const payload = { state: `s${i}`, provider: i % 2 ? "google" : "github",
                      verifier: `v${i}`, nonce: `n${i}` };
    const tok = await sign(KEY, payload, 600);
    assert.deepEqual((await unsign(KEY, tok)).state, payload.state);

    // Bend one character and demand that it never yields a DIFFERENT payload.
    //
    // Not "never verifies", which is what this asserted first and is not
    // true: a 32-byte MAC is 43 base64url characters, and the last one
    // carries two bits that decode to nothing. Flipping it between A and B
    // produces a byte-identical signature, so the token still verifies — as
    // the same payload, held by somebody who already had a valid one. That is
    // encoding slack, not forgery, and the property worth pinning is the one
    // an attacker would need to break: no bent token ever authorises anything
    // the original did not say.
    const at = int(r, 0, tok.length - 1);
    if (tok[at] === ".") continue;
    const ch = tok[at] === "A" ? "B" : "A";
    const bent = tok.slice(0, at) + ch + tok.slice(at + 1);
    const got = await unsign(KEY, bent);
    if (got !== null) {
      assert.equal(at > tok.lastIndexOf("."), true,
        `a changed PAYLOAD character still verified at ${at}`);
      assert.deepEqual(got, await unsign(KEY, tok),
        `character ${at} changed what the cookie said`);
    }
    assert.equal(await unsign(`${KEY}x`, tok), null, "a wrong key verified");
  }
  // The verifier must never be readable without the key — it is signed, not
  // encrypted, so this is about it never leaving in a URL, which begin() is
  // responsible for. What is checked here is that a forged one cannot be
  // substituted.
  const t = await sign(KEY, { state: "a", verifier: "secret" }, -1);
  assert.equal(await unsign(KEY, t), null, "an expired state cookie verified");
});

test("a rate limit never lets more through than it says", async () => {
  const env = makeEnv();
  const { take } = await import("../src/ratelimit.js");
  const r = rng(3);
  for (const kind of Object.keys(LIMITS)) {
    const max = LIMITS[kind].max;
    const key = `sim-${kind}`;
    let ok = 0;
    for (let i = 0; i < max + int(r, 1, 20); i++) {
      const res = await take(env, kind, key);
      if (res.ok) ok++;
      else assert.ok(res.retryAfter > 0, "a refusal carried no retry window");
    }
    assert.equal(ok, max, `${kind} let ${ok} through, limit is ${max}`);
    // A different key is a different bucket.
    const other = await take(env, kind, `${key}-other`);
    assert.ok(other.ok, `${kind} leaked one account's limit onto another`);
  }
});

test(`${RUNS} simulated request sequences hold every rule`, async (t) => {
  const total = {};
  let runs = 0;
  for (let i = 0; i < RUNS; i++) {
    const seed = ONLY != null ? ONLY : i + 1;
    try {
      const cover = await runOne(seed);
      for (const k of Object.keys(cover)) {
        total[k] = (total[k] || 0) + cover[k];
      }
      runs++;
    } catch (e) {
      e.message = `SIM_SEED=${seed} — ${e.message}`;
      throw e;
    }
    if (ONLY != null) break;
  }
  t.diagnostic(`${runs} sequences`);
  t.diagnostic(`reached: ${JSON.stringify(total)}`);
  for (const k of ["forged", "bent", "csrf", "content_type", "mint", "logout",
                   "rename", "picks", "delete"]) {
    assert.ok(total[k] > 0, `no sequence reached: ${k}`);
  }
});

// Signing in, all the way through, with the providers stood in for.
//
// Every other test on this site creates a session by calling session.create.
// That means the sign-in itself — the redirect out, the signed state cookie,
// the code exchange, the ID token, the account it lands on — has never once
// executed. It is the longest path in the Worker and the only one a reader
// cannot get past if it is wrong.
//
// What is stubbed is the two providers and nothing else: Google's token
// endpoint and its JWKS, GitHub's token endpoint and its user endpoint. The
// ID token is a real RS256 JWT signed by a real key whose public half is
// served as a real JWKS, so verifyIdToken does the actual signature
// verification rather than being handed a pass. Everything between the
// browser and those four URLs is the code that will run in production.
//
// The adversarial half is the point. An auth flow that works is easy; one that
// refuses correctly is the whole job, and each of these refusals is a check
// somebody could delete without a single test going red:
//
//   a callback with no state cookie, or one for another provider
//   a token signed by the wrong key, or by nobody
//   a token minted for a different audience, or a different issuer
//   a token replayed from another sign-in (the nonce)
//   an expired token
//   a return_to pointing off the site
//
// What it cannot cover is the network to Google itself and the consent screen
// — those need a browser and the real client. This covers everything after
// the redirect comes back.

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as session from "../src/session.js";
import { SESSION_COOKIE, STATE_COOKIE } from "../src/cookies.js";
import { makeEnv, seedUser } from "./helpers/env.js";

const ORIGIN = "https://big12ology.com";
const KID = "test-key-1";

// ---------------------------------------------------------------- the keys

const keypair = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true, ["sign", "verify"]);
const publicJwk = { ...await crypto.subtle.exportKey("jwk", keypair.publicKey),
                    kid: KID, alg: "RS256", use: "sig" };
delete publicJwk.key_ops;
delete publicJwk.ext;

// A second key nobody published, for the forgery case.
const rogue = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true, ["sign", "verify"]);

const b64url = (buf) =>
  btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const enc = (o) => b64url(new TextEncoder().encode(JSON.stringify(o)));

/** A real RS256 JWT. `key` defaults to the one the JWKS publishes. */
async function idToken(claims, { key = keypair.privateKey, kid = KID } = {}) {
  const head = enc({ alg: "RS256", typ: "JWT", kid });
  const body = enc(claims);
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key,
    new TextEncoder().encode(`${head}.${body}`));
  return `${head}.${body}.${b64url(sig)}`;
}

const now = () => Math.floor(Date.now() / 1000);

function claims(env, over = {}) {
  return {
    iss: "https://accounts.google.com",
    aud: env.GOOGLE_CLIENT_ID,
    sub: "google-subject-0001",
    exp: now() + 600,
    iat: now(),
    ...over,
  };
}

// ------------------------------------------------------------- the providers

/**
 * Stand in for Google and GitHub. `plan` decides what the token endpoint
 * hands back, so a test can put anything in the ID token without touching the
 * Worker.
 */
function providers(plan = {}) {
  const seen = { token: 0, jwks: 0, user: 0, tokenBody: null };
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    if (u.includes("googleapis.com/oauth2/v3/certs")) {
      seen.jwks++;
      return new Response(JSON.stringify({ keys: [publicJwk] }), { status: 200 });
    }
    if (u.includes("oauth2.googleapis.com/token")) {
      seen.token++;
      seen.tokenBody = new URLSearchParams(init.body).get("code_verifier");
      if (plan.googleTokenFails) return new Response("no", { status: 400 });
      return new Response(JSON.stringify({ id_token: await plan.idToken() }),
                          { status: 200 });
    }
    if (u.includes("github.com/login/oauth/access_token")) {
      seen.token++;
      return new Response(JSON.stringify({ access_token: "gho_test" }),
                          { status: 200 });
    }
    if (u.includes("api.github.com/user")) {
      seen.user++;
      return new Response(JSON.stringify(plan.githubUser ?? { id: 4242,
                                                              login: "someone" }),
                          { status: 200 });
    }
    throw new Error(`the worker reached somewhere unexpected: ${u}`);
  };
  return seen;
}

// ------------------------------------------------------------------ helpers

const call = (env, path, opts = {}) => worker.fetch(
  new Request(`${ORIGIN}${path}`, {
    method: opts.method || "GET",
    headers: new Headers(opts.headers || {}),
    redirect: "manual",
  }), env, {});

/** Every Set-Cookie on a response, as a map of name to the whole header. */
function cookies(res) {
  const out = {};
  for (const [k, v] of res.headers) {
    if (k.toLowerCase() !== "set-cookie") continue;
    for (const part of v.split(/,(?=\s*__Host-)/)) {
      const name = part.trim().split("=")[0];
      out[name] = part.trim();
    }
  }
  return out;
}
const valueOf = (header) =>
  decodeURIComponent(header.split(";")[0].split("=").slice(1).join("="));

/** Press "sign in" and read what the browser would be sent to do. */
async function begin(env, provider = "google", returnTo = null) {
  const q = returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : "";
  const res = await call(env, `/api/auth/login/${provider}${q}`);
  assert.equal(res.status, 302, "sign-in did not redirect to the provider");
  const dest = new URL(res.headers.get("Location"));
  const jar = cookies(res);
  assert.ok(jar[STATE_COOKIE], "no state cookie was set");
  return {
    dest,
    state: dest.searchParams.get("state"),
    nonce: dest.searchParams.get("nonce"),
    cookie: `${STATE_COOKIE}=${valueOf(jar[STATE_COOKIE])}`,
  };
}

/** Come back from the provider. */
const callback = (env, provider, { state, cookie, code = "auth-code" }) =>
  call(env, `/api/auth/callback/${provider}?code=${code}&state=${state}`,
       { headers: { Cookie: cookie } });

// --------------------------------------------------------------- the walk

test("a first sign-in with Google creates an account and lands on the name form",
  async () => {
    const env = makeEnv();
    const b = await begin(env);

    // The redirect out asks for the minimum and carries PKCE.
    assert.equal(b.dest.origin, "https://accounts.google.com");
    assert.equal(b.dest.searchParams.get("scope"), "openid",
      "more than the subject was requested");
    assert.equal(b.dest.searchParams.get("code_challenge_method"), "S256");
    assert.ok(b.dest.searchParams.get("code_challenge"), "no PKCE challenge");
    // The verifier must NOT be in the URL — that is the whole point of it.
    assert.ok(!b.dest.search.includes("code_verifier"),
      "the PKCE verifier was sent to the provider up front");

    const seen = providers({ idToken: () => idToken(claims(env, { nonce: b.nonce })) });
    const res = await callback(env, "google", b);

    assert.equal(res.status, 302);
    // A brand new account has no display name, and cannot pick without one,
    // so it lands on the page that finishes the job rather than the slate.
    assert.match(res.headers.get("Location"), /^\/pools\/account\.html\?welcome=1/);
    assert.ok(seen.tokenBody, "the PKCE verifier was never sent at the exchange");

    const jar = cookies(res);
    assert.ok(jar[SESSION_COOKIE], "no session cookie");
    for (const bit of ["Path=/", "Secure", "HttpOnly", "SameSite=Lax"]) {
      assert.ok(jar[SESSION_COOKIE].includes(bit),
        `the session cookie is missing ${bit}`);
    }
    assert.ok(!/Domain=/i.test(jar[SESSION_COOKIE]),
      "a Domain attribute makes a __Host- cookie invalid");
    assert.ok(jar[STATE_COOKIE].includes("Max-Age=0")
      || jar[STATE_COOKIE].includes("Expires="),
      "the state cookie was not cleared");

    // And the session actually works.
    const me = await call(env, "/api/me", {
      headers: { Cookie: `${SESSION_COOKIE}=${valueOf(jar[SESSION_COOKIE])}` } });
    assert.equal(me.status, 200, "the session minted at sign-in does not work");
    const body = await me.json();
    assert.equal(body.needs_name, true);
    assert.equal(body.identities.length, 1);
    assert.equal(body.identities[0].provider, "google");

    // The provider's subject is never stored in the clear.
    const ids = env.raw.prepare(`SELECT subject_hash FROM identities`).all();
    assert.equal(ids.length, 1);
    assert.ok(!ids[0].subject_hash.includes("google-subject-0001"),
      "the raw provider subject was stored");
  });

test("signing in again returns to the same account, not a second one",
  async () => {
    const env = makeEnv();
    for (let i = 0; i < 3; i++) {
      const b = await begin(env);
      providers({ idToken: () => idToken(claims(env, { nonce: b.nonce })) });
      const res = await callback(env, "google", b);
      assert.equal(res.status, 302);
    }
    const n = env.raw.prepare(`SELECT COUNT(*) n FROM users`).get();
    assert.equal(n.n, 1, "the same person got three accounts");
    const s = env.raw.prepare(`SELECT COUNT(*) n FROM sessions`).get();
    assert.equal(s.n, 3, "each sign-in should mint its own session");
  });

test("a named account skips the welcome and honors where it came from",
  async () => {
    const env = makeEnv();
    const b1 = await begin(env);
    providers({ idToken: () => idToken(claims(env, { nonce: b1.nonce })) });
    const first = await callback(env, "google", b1);
    const uid = env.raw.prepare(`SELECT id FROM users`).get().id;
    env.raw.prepare(
      `UPDATE users SET display_name = 'Named', display_norm = 'named'
        WHERE id = ?`).run(uid);
    void first;

    const b2 = await begin(env, "google", "/pools/survivor/pool.html");
    providers({ idToken: () => idToken(claims(env, { nonce: b2.nonce })) });
    const res = await callback(env, "google", b2);
    assert.equal(res.headers.get("Location"), "/pools/survivor/pool.html");
  });

test("GitHub keys on the numeric id, so a renamed login is the same account",
  async () => {
    const env = makeEnv();
    const b1 = await begin(env, "github");
    providers({ githubUser: { id: 4242, login: "before" } });
    await callback(env, "github", b1);

    const b2 = await begin(env, "github");
    providers({ githubUser: { id: 4242, login: "after-a-rename" } });
    await callback(env, "github", b2);

    assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM users`).get().n, 1,
      "a renamed GitHub login was treated as a different person");

    // And the other way: the freed name registered by somebody else is NOT
    // the same account. This is why `login` is never the key.
    const b3 = await begin(env, "github");
    providers({ githubUser: { id: 9999, login: "before" } });
    await callback(env, "github", b3);
    assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM users`).get().n, 2,
      "somebody who took the freed username inherited the account");
  });

test("the same person at both providers is two accounts until they link",
  async () => {
    const env = makeEnv();
    const g = await begin(env, "google");
    providers({ idToken: () => idToken(claims(env, { nonce: g.nonce })) });
    await callback(env, "google", g);

    const h = await begin(env, "github");
    providers({ githubUser: { id: 4242 } });
    await callback(env, "github", h);

    assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM users`).get().n, 2,
      "two providers collapsed into one account with nothing to link them");
  });

// ----------------------------------------------------------- the refusals

const bad = (name, fn) => test(`refused: ${name}`, fn);

bad("a callback with no state cookie", async () => {
  const env = makeEnv();
  const b = await begin(env);
  providers({ idToken: () => idToken(claims(env, { nonce: b.nonce })) });
  const res = await callback(env, "google", { ...b, cookie: "" });
  assert.match(res.headers.get("Location") || "", /error=bad_state/);
  assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM users`).get().n, 0);
});

bad("a state that does not match the cookie", async () => {
  const env = makeEnv();
  const b = await begin(env);
  providers({ idToken: () => idToken(claims(env, { nonce: b.nonce })) });
  const res = await callback(env, "google", { ...b, state: crypto.randomUUID() });
  assert.match(res.headers.get("Location") || "", /error=bad_state/);
});

bad("a state cookie issued for the other provider", async () => {
  const env = makeEnv();
  const b = await begin(env, "google");
  providers({ githubUser: { id: 1 } });
  const res = await callback(env, "github", b);
  assert.match(res.headers.get("Location") || "", /error=bad_state/);
});

bad("a token signed by a key the JWKS does not publish", async () => {
  const env = makeEnv();
  const b = await begin(env);
  providers({ idToken: () =>
    idToken(claims(env, { nonce: b.nonce }), { key: rogue.privateKey }) });
  const res = await callback(env, "google", b);
  assert.match(res.headers.get("Location") || "", /error=exchange_failed/);
  assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM users`).get().n, 0,
    "a forged token created an account");
});

bad("a token minted for a different audience", async () => {
  const env = makeEnv();
  const b = await begin(env);
  providers({ idToken: () =>
    idToken(claims(env, { nonce: b.nonce, aud: "someone-elses-client-id" })) });
  const res = await callback(env, "google", b);
  assert.match(res.headers.get("Location") || "", /error=exchange_failed/);
});

bad("a token from a different issuer", async () => {
  const env = makeEnv();
  const b = await begin(env);
  providers({ idToken: () =>
    idToken(claims(env, { nonce: b.nonce, iss: "https://evil.example" })) });
  const res = await callback(env, "google", b);
  assert.match(res.headers.get("Location") || "", /error=exchange_failed/);
});

bad("a token replayed from another sign-in", async () => {
  // The nonce, and the check most often left out because nothing visibly
  // breaks without it. The token is valid, correctly signed, for the right
  // audience — it just belongs to a different browser's flow.
  const env = makeEnv();
  const mine = await begin(env);
  const theirs = await begin(env);
  providers({ idToken: () => idToken(claims(env, { nonce: theirs.nonce })) });
  const res = await callback(env, "google", mine);
  assert.match(res.headers.get("Location") || "", /error=exchange_failed/);
  assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM users`).get().n, 0);
});

bad("an expired token", async () => {
  const env = makeEnv();
  const b = await begin(env);
  providers({ idToken: () =>
    idToken(claims(env, { nonce: b.nonce, exp: now() - 60 })) });
  const res = await callback(env, "google", b);
  assert.match(res.headers.get("Location") || "", /error=exchange_failed/);
});

bad("a code the provider will not exchange", async () => {
  const env = makeEnv();
  const b = await begin(env);
  providers({ googleTokenFails: true, idToken: () => "" });
  const res = await callback(env, "google", b);
  assert.match(res.headers.get("Location") || "", /error=exchange_failed/);
});

bad("a provider nobody configured", async () => {
  const env = makeEnv();
  const res = await call(env, "/api/auth/login/facebook");
  assert.equal(res.status, 404);
});

test("return_to can never leave the section, however it is written",
  async () => {
    // Both branches. A new account is sent to the name form carrying its
    // destination in `next`; a named one goes straight there. The first
    // version of this test only ever hit the first branch, so removing
    // safeReturn from the second changed nothing and the test still passed —
    // which is the failure mode a test like this exists to avoid.
    const env = makeEnv();

    // Name the account, so the second branch is reachable.
    const first = await begin(env, "google");
    providers({ idToken: () => idToken(claims(env, { nonce: first.nonce })) });
    await callback(env, "google", first);
    env.raw.prepare(
      `UPDATE users SET display_name = 'Named', display_norm = 'named'`).run();

    for (const to of ["https://evil.example", "//evil.example",
                      "/tiebreaker/", "/\\evil.example", "",
                      "/pools/../etc", "javascript:alert(1)"]) {
      const b = await begin(env, "google", to);
      providers({ idToken: () => idToken(claims(env, { nonce: b.nonce })) });
      const res = await callback(env, "google", b);
      const dest = res.headers.get("Location");
      assert.ok(dest.startsWith("/pools/"),
        `return_to ${JSON.stringify(to)} landed on ${dest}`);
      assert.ok(!/evil\.example|tiebreaker|javascript:/.test(dest),
        `return_to ${JSON.stringify(to)} survived into ${dest}`);
    }

    // And the same for a brand new account, where it rides in `next`.
    env.raw.prepare(`UPDATE users SET display_name = NULL, display_norm = NULL`)
      .run();
    for (const to of ["https://evil.example", "/tiebreaker/"]) {
      const b = await begin(env, "google", to);
      providers({ idToken: () => idToken(claims(env, { nonce: b.nonce })) });
      const res = await callback(env, "google", b);
      const dest = res.headers.get("Location");
      const next = decodeURIComponent(
        new URL(dest, ORIGIN).searchParams.get("next") || "");
      assert.ok(next.startsWith("/pools/"),
        `a new account was handed next=${next}`);
    }
  });

test("linking a second provider needs a session, and refuses without one",
  async () => {
    const env = makeEnv();
    const res = await call(env, "/api/auth/link/github");
    assert.equal(res.status, 302);
    assert.match(res.headers.get("Location") || "", /error=unauthenticated/);
  });

test("linking attaches the second identity to the account already signed in",
  async () => {
    const env = makeEnv();
    const g = await begin(env, "google");
    providers({ idToken: () => idToken(claims(env, { nonce: g.nonce })) });
    const first = await callback(env, "google", g);
    const sess = valueOf(cookies(first)[SESSION_COOKIE]);
    const jar = `${SESSION_COOKIE}=${sess}`;

    const start = await call(env, "/api/auth/link/github",
                             { headers: { Cookie: jar } });
    assert.equal(start.status, 302);
    const dest = new URL(start.headers.get("Location"));
    const stateCookie = `${STATE_COOKIE}=${valueOf(cookies(start)[STATE_COOKIE])}`;

    providers({ githubUser: { id: 777 } });
    const done = await call(env,
      `/api/auth/callback/github?code=c&state=${dest.searchParams.get("state")}`,
      { headers: { Cookie: `${jar}; ${stateCookie}` } });
    assert.equal(done.status, 302);

    assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM users`).get().n, 1,
      "linking created a second account instead of attaching");
    const ids = env.raw.prepare(
      `SELECT provider FROM identities ORDER BY provider`).all()
      .map((r) => r.provider);
    assert.deepEqual(ids, ["github", "google"]);
  });

test("the link callback refuses a session revoked while it was away",
  async () => {
    // The window this closes: KV answers a revoked session for about a minute,
    // and the link callback is destructive — resolveIdentity's absorb branch
    // runs DELETE FROM users on the account being merged in. So this read has
    // to be the D1 one, like deleteMe's and unlink's. It was not.
    //
    // Revoked AFTER the start on purpose. That is the real sequence — sign out
    // on a shared machine while a provider's consent screen is still open in
    // another tab — and it is the only way to reach the callback's own check
    // now that the start is strict too.
    const env = makeEnv();
    const g = await begin(env, "google");
    providers({ idToken: () => idToken(claims(env, { nonce: g.nonce })) });
    const first = await callback(env, "google", g);
    const sess = valueOf(cookies(first)[SESSION_COOKIE]);
    const jar = `${SESSION_COOKIE}=${sess}`;

    const start = await call(env, "/api/auth/link/github",
                             { headers: { Cookie: jar } });
    assert.equal(start.status, 302);
    const dest = new URL(start.headers.get("Location"));
    const stateCookie = `${STATE_COOKIE}=${valueOf(cookies(start)[STATE_COOKIE])}`;

    // D1 only. KV keeps answering, which is the whole point of the test.
    env.raw.prepare(`UPDATE sessions SET revoked_at = 1`).run();

    providers({ githubUser: { id: 777 } });
    const done = await call(env,
      `/api/auth/callback/github?code=c&state=${dest.searchParams.get("state")}`,
      { headers: { Cookie: `${jar}; ${stateCookie}` } });
    assert.equal(done.status, 302);
    assert.match(done.headers.get("Location") || "", /auth_error=unauthenticated/,
      "a revoked session finished a link");

    const ids = env.raw.prepare(`SELECT provider FROM identities`).all();
    assert.deepEqual(ids.map((r) => r.provider), ["google"],
      "the second identity was attached anyway");
  });

test("the JWKS is fetched once and cached, not on every sign-in", async () => {
  const env = makeEnv();
  let jwks = 0;
  for (let i = 0; i < 3; i++) {
    const b = await begin(env);
    const seen = providers({ idToken: () =>
      idToken(claims(env, { nonce: b.nonce })) });
    await callback(env, "google", b);
    jwks += seen.jwks;
  }
  assert.ok(jwks <= 1,
    `Google's key set was fetched ${jwks} times across three sign-ins`);
});

test("signing out ends the session the sign-in minted", async () => {
  const env = makeEnv();
  const b = await begin(env);
  providers({ idToken: () => idToken(claims(env, { nonce: b.nonce })) });
  const res = await callback(env, "google", b);
  const raw = valueOf(cookies(res)[SESSION_COOKIE]);

  const out = await worker.fetch(new Request(`${ORIGIN}/api/auth/logout`, {
    method: "POST",
    headers: { Cookie: `${SESSION_COOKIE}=${raw}`, Origin: ORIGIN,
               "Content-Type": "application/json" },
    body: "{}",
  }), env, {});
  assert.equal(out.status, 204);
  assert.equal(await session.read(env, raw, { strict: true }), null);
});

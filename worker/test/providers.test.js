// The two providers added after the registry refactor, and the machinery
// that decides whether a provider exists at all.
//
// oauth.e2e.test.js already walks Google and GitHub end to end against stubbed
// providers, and that file is the one to read for how the flow works. This one
// covers what the table made possible and what it made newly easy to get
// wrong:
//
//   * a provider whose credentials are absent must be a 404, not a redirect
//     into a consent screen that cannot come back
//   * Microsoft, whose only real difference from Google is which tenant's
//     issuer is acceptable — and accepting the wrong one is invisible
//   * Amazon, whose scope string is the only thing standing between this site
//     and an email address it promises never to receive
//   * the JWKS cache, which was one object and is now one per provider

import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import * as oauth from "../src/oauth.js";
import { STATE_COOKIE } from "../src/cookies.js";
import { makeEnv } from "./helpers/env.js";

const ORIGIN = "https://big12ology.com";
const MS_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad";
const MS_ISS = `https://login.microsoftonline.com/${MS_TENANT}/v2.0`;

// --------------------------------------------------------------- the keys

const KID = "test-key-1";
const keypair = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true, ["sign", "verify"]);
const publicJwk = { ...await crypto.subtle.exportKey("jwk", keypair.publicKey),
                    kid: KID, alg: "RS256", use: "sig" };
delete publicJwk.key_ops;
delete publicJwk.ext;

const b64url = (buf) =>
  btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const seg = (o) => b64url(new TextEncoder().encode(JSON.stringify(o)));
const now = () => Math.floor(Date.now() / 1000);

async function idToken(claims) {
  const body = `${seg({ alg: "RS256", typ: "JWT", kid: KID })}.${seg(claims)}`;
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", keypair.privateKey,
    new TextEncoder().encode(body));
  return `${body}.${b64url(sig)}`;
}

// ------------------------------------------------------------- the harness

/** An env with every provider's credentials present. */
const fullEnv = (over = {}) => makeEnv({
  MICROSOFT_CLIENT_ID: "ms-client-id",
  MICROSOFT_CLIENT_SECRET: "ms-secret",
  AMAZON_CLIENT_ID: "amzn-client-id",
  AMAZON_CLIENT_SECRET: "amzn-secret",
  ...over,
});

const call = (env, path, opts = {}) => worker.fetch(
  new Request(`${ORIGIN}${path}`, {
    method: opts.method || "GET",
    headers: new Headers(opts.headers || {}),
    redirect: "manual",
  }), env, {});

function cookies(res) {
  const out = {};
  for (const [k, v] of res.headers) {
    if (k.toLowerCase() !== "set-cookie") continue;
    for (const part of v.split(/,(?=\s*__Host-)/)) {
      out[part.trim().split("=")[0]] = part.trim();
    }
  }
  return out;
}
const valueOf = (h) =>
  decodeURIComponent(h.split(";")[0].split("=").slice(1).join("="));

/**
 * Stand in for whichever provider is being exercised. `plan.token` decides
 * what the token endpoint returns; `seen` records what we were sent, which is
 * how the scope and the PKCE verifier get asserted on.
 */
function stub(plan = {}) {
  const seen = { tokenBody: null, userAuth: null, jwks: 0, hits: [] };
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    seen.hits.push(u);
    if (u.includes("/discovery/v2.0/keys") || u.includes("oauth2/v3/certs")) {
      seen.jwks++;
      return new Response(JSON.stringify({ keys: [publicJwk] }), { status: 200 });
    }
    // GitHub's is /login/oauth/access_token, which does NOT contain "/token"
    // — matching on that alone sent it to the throw below and surfaced as a
    // plain exchange_failed, which looks exactly like a real refusal.
    if (u.includes("/token") || u.includes("access_token")) {
      seen.tokenBody = new URLSearchParams(init.body);
      return new Response(JSON.stringify(await plan.token()), { status: 200 });
    }
    if (u.includes("api.amazon.com/user/profile")) {
      seen.userAuth = init.headers.Authorization;
      return new Response(JSON.stringify(plan.profile ?? { user_id: "amzn1.account.ABC" }),
                          { status: 200 });
    }
    if (u.includes("api.github.com/user")) {
      seen.userAuth = init.headers.Authorization;
      return new Response(JSON.stringify(plan.githubUser ?? { id: 4242 }),
                          { status: 200 });
    }
    throw new Error(`unexpected fetch: ${u}`);
  };
  return seen;
}

async function begin(env, provider) {
  const res = await call(env, `/api/auth/login/${provider}`);
  assert.equal(res.status, 302, `${provider} sign-in did not redirect`);
  const dest = new URL(res.headers.get("Location"));
  const jar = cookies(res);
  return { dest, state: dest.searchParams.get("state"),
           nonce: dest.searchParams.get("nonce"),
           cookie: `${STATE_COOKIE}=${valueOf(jar[STATE_COOKIE])}` };
}

const callback = (env, provider, b) =>
  call(env, `/api/auth/callback/${provider}?code=auth-code&state=${b.state}`,
       { headers: { Cookie: b.cookie } });

// ------------------------------------------------- configured or it does not exist

test("a provider with no credentials is not offered and cannot be reached",
  async () => {
    const env = makeEnv();   // google + github only
    assert.deepEqual(oauth.enabled(env), ["google", "github"]);

    for (const p of ["microsoft", "amazon"]) {
      const login = await call(env, `/api/auth/login/${p}`);
      assert.equal(login.status, 404, `${p} login was reachable unconfigured`);
      const cb = await call(env, `/api/auth/callback/${p}?code=x&state=y`);
      assert.equal(cb.status, 404, `${p} callback was reachable unconfigured`);
    }

    // A name the registry has never heard of takes the same path as one that
    // is merely unconfigured — nothing about which providers exist leaks.
    assert.equal((await call(env, "/api/auth/login/apple")).status, 404);
    assert.equal((await call(env, "/api/auth/login/nonsense")).status, 404);
  });

test("credentials are what turn a provider on — no deploy, no code change",
  async () => {
    assert.deepEqual(oauth.enabled(fullEnv()),
      ["google", "microsoft", "github", "amazon"]);
  });

test("a half-configured provider reads as off, not as broken", async () => {
  // The state a first setup lands in: the client id is pasted, the secret is
  // still in the other tab. It must read as OFF — a 404 and no button — rather
  // than redirecting somebody to Microsoft and failing at the exchange with
  // something that looks like Microsoft's fault.
  const env = fullEnv({ MICROSOFT_CLIENT_SECRET: undefined });
  assert.ok(!oauth.enabled(env).includes("microsoft"));
  assert.equal((await call(env, "/api/auth/login/microsoft")).status, 404);
});

test("/api/auth/providers reports exactly what is usable", async () => {
  const res = await call(fullEnv(), "/api/auth/providers");
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.deepEqual(body.providers,
    ["google", "microsoft", "github", "amazon"]);
  // Public and identical for everyone, so it must not be Vary: Cookie.
  assert.equal(res.headers.get("Vary"), "");
  assert.match(res.headers.get("Cache-Control"), /s-maxage/);
});

// --------------------------------------------------------------- Microsoft

test("Microsoft: asks for openid only, carries PKCE, and signs in", async () => {
  const env = fullEnv();
  oauth._resetJwksCache();
  const b = await begin(env, "microsoft");
  assert.equal(b.dest.origin, "https://login.microsoftonline.com");
  assert.match(b.dest.pathname, /\/consumers\//,
    "not the consumers tenant — the issuer would not be a fixed string");
  assert.equal(b.dest.searchParams.get("scope"), "openid",
    "more than the subject was requested");
  assert.equal(b.dest.searchParams.get("code_challenge_method"), "S256");
  assert.ok(b.nonce, "no nonce — a token from another sign-in could be replayed");

  const seen = stub({ token: async () => ({
    id_token: await idToken({ iss: MS_ISS, aud: "ms-client-id",
                              sub: "ms-subject-1", exp: now() + 600,
                              nonce: b.nonce }) }) });
  const done = await callback(env, "microsoft", b);
  assert.equal(done.status, 302);
  assert.doesNotMatch(done.headers.get("Location"), /auth_error/,
    `sign-in failed: ${done.headers.get("Location")}`);
  assert.ok(seen.tokenBody.get("code_verifier"), "PKCE verifier was not sent");

  const row = env.raw.prepare(
    `SELECT provider FROM identities`).get();
  assert.equal(row.provider, "microsoft");
});

test("Microsoft: a token from the wrong tenant is refused", async () => {
  // The failure this exists for is silent. `common` accepts work and school
  // accounts and stamps the caller's own tenant into the issuer, so a token
  // from any tenant would verify if the issuer were not pinned.
  const env = fullEnv();
  oauth._resetJwksCache();
  const b = await begin(env, "microsoft");
  stub({ token: async () => ({
    id_token: await idToken({
      iss: "https://login.microsoftonline.com/some-other-tenant/v2.0",
      aud: "ms-client-id", sub: "ms-subject-2", exp: now() + 600,
      nonce: b.nonce }) }) });
  const done = await callback(env, "microsoft", b);
  assert.match(done.headers.get("Location"), /auth_error=exchange_failed/);
  assert.equal(env.raw.prepare(`SELECT COUNT(*) n FROM identities`).get().n, 0);
});

// ------------------------------------------------------------------ Amazon

test("Amazon: asks only for the user id, and stores only that", async () => {
  const env = fullEnv();
  const b = await begin(env, "amazon");
  assert.equal(b.dest.origin, "https://www.amazon.com");
  assert.equal(b.dest.searchParams.get("scope"), "profile:user_id",
    "the wider `profile` scope would return an email address");

  const seen = stub({ token: async () => ({ access_token: "Atza|test" }),
                      profile: { user_id: "amzn1.account.XYZ" } });
  const done = await callback(env, "amazon", b);
  assert.equal(done.status, 302);
  assert.doesNotMatch(done.headers.get("Location"), /auth_error/,
    `sign-in failed: ${done.headers.get("Location")}`);
  assert.equal(seen.userAuth, "Bearer Atza|test");

  // Nothing but a peppered hash lands in the table.
  const row = env.raw.prepare(`SELECT * FROM identities`).get();
  assert.equal(row.provider, "amazon");
  assert.ok(!JSON.stringify(row).includes("amzn1.account.XYZ"),
    "the raw subject was stored");
});

test("Amazon: a profile with no user_id is refused rather than guessed at",
  async () => {
    const env = fullEnv();
    const b = await begin(env, "amazon");
    stub({ token: async () => ({ access_token: "t" }), profile: { name: "Someone" } });
    const done = await callback(env, "amazon", b);
    assert.match(done.headers.get("Location"), /auth_error=exchange_failed/);
  });

// ------------------------------------------------------------- the cache

test("the JWKS cache is per provider, not one bucket they evict each other from",
  async () => {
    // One shared module-level cache was correct while Google was the only
    // provider verifying tokens locally. With two it becomes an intermittent
    // unknown_kid, and the symptom is a sign-in that fails only when the OTHER
    // provider was used just before it — which is close to unreproducible.
    const env = fullEnv();
    oauth._resetJwksCache();

    const ms = await begin(env, "microsoft");
    let seen = stub({ token: async () => ({
      id_token: await idToken({ iss: MS_ISS, aud: "ms-client-id",
                                sub: "ms-1", exp: now() + 600, nonce: ms.nonce }) }) });
    assert.doesNotMatch((await callback(env, "microsoft", ms))
      .headers.get("Location"), /auth_error/);
    assert.equal(seen.jwks, 1, "Microsoft's keys were not fetched");

    // Google next: a different JWKS url, so it must fetch its own rather than
    // being served Microsoft's.
    const gg = await begin(env, "google");
    seen = stub({ token: async () => ({
      id_token: await idToken({ iss: "https://accounts.google.com",
                                aud: env.GOOGLE_CLIENT_ID, sub: "gg-1",
                                exp: now() + 600, nonce: gg.nonce }) }) });
    assert.doesNotMatch((await callback(env, "google", gg))
      .headers.get("Location"), /auth_error/);
    assert.equal(seen.jwks, 1, "Google's keys were not fetched separately");

    // And back to Microsoft, whose entry must still be warm.
    const ms2 = await begin(env, "microsoft");
    seen = stub({ token: async () => ({
      id_token: await idToken({ iss: MS_ISS, aud: "ms-client-id",
                                sub: "ms-2", exp: now() + 600, nonce: ms2.nonce }) }) });
    assert.doesNotMatch((await callback(env, "microsoft", ms2))
      .headers.get("Location"), /auth_error/);
    assert.equal(seen.jwks, 0,
      "Microsoft's keys were refetched — Google evicted them");
  });

// ------------------------------------------------- the id space does not collide

test("the same subject string at two providers is two different identities",
  async () => {
    // With five providers the chance of two of them handing back the same
    // opaque string stops being hypothetical — they are all just strings. If
    // the hash did not mix the provider name in, a GitHub user whose numeric
    // id matched somebody's Amazon id would land on their account.
    //
    // Driven through the real callback rather than by calling subjectHash,
    // because what matters is the value that reaches the identities table.
    const env = fullEnv();
    const SAME = "1234567890";

    const gh = await begin(env, "github");
    stub({ token: async () => ({ access_token: "gho_x" }),
           githubUser: { id: Number(SAME) } });
    assert.doesNotMatch((await callback(env, "github", gh))
      .headers.get("Location"), /auth_error/);

    const az = await begin(env, "amazon");
    stub({ token: async () => ({ access_token: "Atza|x" }),
           profile: { user_id: SAME } });
    assert.doesNotMatch((await callback(env, "amazon", az))
      .headers.get("Location"), /auth_error/);

    const rows = env.raw.prepare(
      `SELECT provider, subject_hash, user_id FROM identities ORDER BY provider`).all();
    assert.equal(rows.length, 2, "the two sign-ins collapsed into one identity");
    assert.notEqual(rows[0].subject_hash, rows[1].subject_hash,
      "same subject at two providers produced the same hash");
    assert.notEqual(rows[0].user_id, rows[1].user_id,
      "two strangers were put on one account");
  });

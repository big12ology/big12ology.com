// Signing in, with as many providers as are configured.
//
// ONE RULE SHARED BY ALL OF THEM, and it is the important one: we take the
// provider's IMMUTABLE OPAQUE SUBJECT, never a username or an email. A GitHub
// `login` can be renamed and then registered by somebody else, and keying on
// it would let a stranger inherit an account. `id` cannot be transferred.
//
// THE SECOND RULE: we ask for the minimum scope every provider allows, which
// in every case below means we never receive an email address. privacy.html
// promises that in the first person, and each entry in the table says which
// string is load-bearing for it.
//
// TWO SHAPES, NOT ONE PER COMPANY.
//
//   oidc    — there is an ID token, we verify it locally against the
//             provider's published JWKS, and PKCE + nonce are available.
//             Google, Microsoft.
//   oauth2  — no ID token. Exchange the code for an access token, spend it on
//             exactly one request to a userinfo endpoint, and throw it away.
//             GitHub, Amazon.
//
// This used to be an if/else in `begin` and a ternary in `finish`, with
// Google's issuer and audience hardcoded inside verifyIdToken. That was fine
// for two providers and is the reason a third was a day's work instead of an
// hour's. It is a table now: a provider is a row, not a branch.
//
// A PROVIDER IS ONLY OFFERED WHEN IT IS CONFIGURED. `enabled(env)` filters on
// the credentials actually present, so a half-set-up provider is a 404 rather
// than a redirect into a broken consent screen, and a new one goes live when
// its secrets land rather than when the next deploy happens.

import { pkce, subjectHash } from "./crypto.js";
import { safeReturn, sign, unsign } from "./cookies.js";

export const STATE_TTL = 600;

// GitHub's API rejects a request with no User-Agent outright. Not a
// suggestion — it 403s. Harmless everywhere else, so it is sent to all of them.
const UA = "big12ology-pickem (+https://big12ology.com)";

/**
 * The table. Adding a provider is an entry here and a button in account.html.
 *
 * `id` and `secret` are functions of env rather than names so that a missing
 * one reads as "not configured" rather than as `undefined` reaching a fetch.
 */
export const REGISTRY = {
  // OpenID Connect. `openid` and nothing else — not email, not profile — so
  // the ID token carries a subject and essentially nothing about the person.
  //
  // THE SCOPE IS ALSO WHAT DECIDES WHETHER ANYONE SEES GOOGLE'S "hasn't
  // verified this app" SCREEN. openid, email and profile are non-sensitive and
  // need no review; anything beyond them puts the consent screen behind a
  // Google review and brings the interstitial back for every reader until it
  // passes. test/oauth.e2e.test.js pins this string for that reason.
  google: {
    kind: "oidc",
    label: "Google",
    auth: "https://accounts.google.com/o/oauth2/v2/auth",
    token: "https://oauth2.googleapis.com/token",
    jwks: "https://www.googleapis.com/oauth2/v3/certs",
    // Google is the one provider that does not agree with itself about its own
    // issuer, so both spellings are accepted and nothing else is.
    issuers: ["https://accounts.google.com", "accounts.google.com"],
    scope: "openid",
    pkce: true,
    nonce: true,
    extra: { prompt: "select_account" },
    id: (env) => env.GOOGLE_CLIENT_ID,
    secret: (env) => env.GOOGLE_CLIENT_SECRET,
  },

  // OIDC, and the closest thing to a drop-in second Google.
  //
  // THE `consumers` TENANT, DELIBERATELY. The `common` endpoint accepts work
  // and school accounts as well, and its issuer contains the caller's tenant
  // id — which means the issuer is not a fixed string and cannot be compared
  // to one. Restricting to personal Microsoft accounts makes the issuer
  // constant and verifiable, and personal accounts are the entire audience for
  // a college football pool anyway.
  microsoft: {
    kind: "oidc",
    label: "Microsoft",
    auth: "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize",
    token: "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
    jwks: "https://login.microsoftonline.com/consumers/discovery/v2.0/keys",
    // The fixed tenant id for personal Microsoft accounts. It is a published
    // constant, not an account-specific value.
    issuers: [
      "https://login.microsoftonline.com/" +
      "9188040d-6c67-4c5b-b112-36a304b66dad/v2.0",
    ],
    scope: "openid",
    pkce: true,
    nonce: true,
    id: (env) => env.MICROSOFT_CLIENT_ID,
    secret: (env) => env.MICROSOFT_CLIENT_SECRET,
  },

  // NOT OIDC. No ID token, no nonce, and no PKCE support — and pretending
  // otherwise in a comment would be worse than saying so. What it has is an
  // authorization code exchanged with a client secret, which makes this a
  // confidential client; for a confidential client PKCE is defense in depth
  // rather than the load-bearing part, and the signed `state` cookie still
  // binds the callback to the browser that started it.
  github: {
    kind: "oauth2",
    label: "GitHub",
    auth: "https://github.com/login/oauth/authorize",
    token: "https://github.com/login/oauth/access_token",
    userinfo: "https://api.github.com/user",
    scope: "",                 // empty: we want the id and nothing more
    extra: { allow_signup: "true" },
    // The numeric id. Never `login`: it is renameable, and the freed name can
    // be registered by anybody.
    subjectFrom: (me) =>
      typeof me.id === "number" ? String(me.id) : null,
    id: (env) => env.GITHUB_CLIENT_ID,
    secret: (env) => env.GITHUB_CLIENT_SECRET,
  },

  // OAuth 2.0, same shape as GitHub.
  //
  // `profile:user_id` IS THE WHOLE POINT OF THE ENTRY. Amazon's plain
  // `profile` scope returns name and email whether they are wanted or not;
  // this narrower one returns the opaque account id and nothing else, which is
  // exactly what this site is willing to receive. Widening it would quietly
  // start collecting addresses that privacy.html says are never collected.
  amazon: {
    kind: "oauth2",
    label: "Amazon",
    auth: "https://www.amazon.com/ap/oa",
    token: "https://api.amazon.com/auth/o2/token",
    userinfo: "https://api.amazon.com/user/profile",
    scope: "profile:user_id",
    subjectFrom: (me) =>
      typeof me.user_id === "string" && me.user_id ? me.user_id : null,
    id: (env) => env.AMAZON_CLIENT_ID,
    secret: (env) => env.AMAZON_CLIENT_SECRET,
  },
};

/** Every provider this code knows how to talk to, configured or not. */
export const PROVIDERS = Object.keys(REGISTRY);

/** Whether this deploy actually holds what `p` needs to work. */
export function configured(env, p) {
  const spec = REGISTRY[p];
  if (!spec) return false;
  return !!spec.id(env) && !!spec.secret(env);
}

/**
 * The providers a reader may actually use, in a fixed order.
 *
 * The router asks this rather than PROVIDERS, so a provider that is known to
 * the code but missing its credentials is a 404 at /api/auth/login/<name> —
 * not a redirect into a consent screen that cannot come back.
 */
export function enabled(env) {
  return PROVIDERS.filter((p) => configured(env, p));
}

export function redirectUri(env, provider) {
  return `${env.SITE_ORIGIN}/api/auth/callback/${provider}`;
}

/**
 * Begin. Returns the provider URL to bounce to and the state cookie that
 * remembers what we sent, including the PKCE verifier — which must never
 * reach the provider until the exchange.
 */
export async function begin(env, provider, returnTo, mode = "login") {
  const spec = REGISTRY[provider];
  if (!spec) throw new Error(`unknown provider: ${provider}`);

  const state = crypto.randomUUID();
  const cookie = { provider, state, mode, return_to: safeReturn(returnTo) };

  const params = {
    client_id: spec.id(env),
    redirect_uri: redirectUri(env, provider),
    response_type: "code",
    state,
    ...(spec.extra || {}),
  };
  // Sent only when there is one. GitHub's is deliberately empty — the id and
  // nothing more — and an empty `scope=` parameter is not the same thing as
  // its absence to every provider, so the omission has to be real.
  if (spec.scope) params.scope = spec.scope;

  if (spec.pkce) {
    const { verifier, challenge } = await pkce();
    cookie.verifier = verifier;
    params.code_challenge = challenge;
    params.code_challenge_method = "S256";
  }
  if (spec.nonce) {
    cookie.nonce = crypto.randomUUID();
    params.nonce = cookie.nonce;
  }

  return {
    url: `${spec.auth}?` + new URLSearchParams(params),
    cookie: await sign(env.STATE_SIGNING_KEY, cookie, STATE_TTL),
  };
}

/** Read back what `begin` remembered, and check it against what came back. */
export async function readState(env, cookieValue, provider, state) {
  const data = await unsign(env.STATE_SIGNING_KEY, cookieValue);
  if (!data) return null;
  if (data.provider !== provider) return null;
  if (!state || data.state !== state) return null;
  return data;
}

// ------------------------------------------------------------------ OIDC

/**
 * JWKS, cached an hour, PER PROVIDER.
 *
 * A single module-level cache was correct while Google was the only provider
 * verifying tokens locally. With three, one cache would be evicted and refilled
 * by whichever provider signed in last, and the symptom would be intermittent
 * `unknown_kid` failures under load that nobody could reproduce.
 */
const jwksCache = new Map();

async function keysFor(spec) {
  const now = Date.now();
  const hit = jwksCache.get(spec.jwks);
  if (hit && now - hit.at < 3600_000) return hit.keys;
  const r = await fetch(spec.jwks);
  if (!r.ok) throw new Error("jwks_unavailable");
  const { keys } = await r.json();
  jwksCache.set(spec.jwks, { at: now, keys });
  return keys;
}

/** Exposed for the tests, which need a clean cache between providers. */
export function _resetJwksCache() {
  jwksCache.clear();
}

function b64urlToBytes(s) {
  const t = s.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(t + "=".repeat((4 - (t.length % 4)) % 4));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/**
 * Verify an ID token properly: signature, issuer, audience, expiry, nonce.
 *
 * Skipping any one of these turns the whole flow into theatre. The nonce in
 * particular is what stops a token minted for a different session being
 * replayed into this one, and it is the check most often left out because
 * nothing visibly breaks without it.
 *
 * The issuer and audience come from the registry row rather than from a
 * constant, which is the only change three providers required — and the reason
 * `issuers` is a list is Google, which uses two spellings of its own name.
 */
async function verifyIdToken(env, spec, idToken, nonce) {
  const [h, p, s] = String(idToken).split(".");
  if (!h || !p || !s) throw new Error("bad_id_token");
  const header = JSON.parse(new TextDecoder().decode(b64urlToBytes(h)));
  const claims = JSON.parse(new TextDecoder().decode(b64urlToBytes(p)));

  const jwk = (await keysFor(spec)).find((k) => k.kid === header.kid);
  if (!jwk) throw new Error("unknown_kid");
  // Both OIDC providers sign with RS256 today. Asserted rather than
  // assumed: a token whose header says something else must not be verified
  // with the wrong algorithm quietly.
  if (header.alg !== "RS256") throw new Error("bad_alg");
  const key = await crypto.subtle.importKey("jwk", jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key,
    b64urlToBytes(s), new TextEncoder().encode(`${h}.${p}`));
  if (!ok) throw new Error("bad_signature");

  const now = Math.floor(Date.now() / 1000);
  if (!spec.issuers.includes(claims.iss)) throw new Error("bad_issuer");
  if (claims.aud !== spec.id(env)) throw new Error("bad_audience");
  if (typeof claims.exp !== "number" || claims.exp <= now) {
    throw new Error("expired_id_token");
  }
  if (nonce && claims.nonce !== nonce) throw new Error("bad_nonce");
  if (!claims.sub) throw new Error("no_subject");
  return claims.sub;
}

async function oidcSubject(env, spec, code, state) {
  const body = {
    code,
    client_id: spec.id(env),
    client_secret: await spec.secret(env),
    redirect_uri: redirectUri(env, spec.name),
    grant_type: "authorization_code",
  };
  if (state.verifier) body.code_verifier = state.verifier;

  const r = await fetch(spec.token, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded",
               Accept: "application/json", "User-Agent": UA },
    body: new URLSearchParams(body),
  });
  if (!r.ok) throw new Error("token_exchange_failed");
  const tok = await r.json();
  if (!tok.id_token) throw new Error("no_id_token");
  return verifyIdToken(env, spec, tok.id_token, state.nonce);
}

// ---------------------------------------------------------------- OAuth 2

async function oauth2Subject(env, spec) {
  const { code, state } = spec._call;
  const r = await fetch(spec.token, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded",
               Accept: "application/json", "User-Agent": UA },
    body: new URLSearchParams({
      code,
      client_id: spec.id(env),
      client_secret: await spec.secret(env),
      redirect_uri: redirectUri(env, spec.name),
      grant_type: "authorization_code",
      ...(state.verifier ? { code_verifier: state.verifier } : {}),
    }),
  });
  if (!r.ok) throw new Error("token_exchange_failed");
  const tok = await r.json();
  if (!tok.access_token) throw new Error("no_access_token");

  const u = await fetch(spec.userinfo, {
    headers: { Authorization: `Bearer ${tok.access_token}`,
               Accept: "application/json", "User-Agent": UA },
  });
  if (!u.ok) throw new Error("user_lookup_failed");
  const sub = spec.subjectFrom(await u.json());
  if (!sub) throw new Error("no_subject");
  return sub;
  // The access token is deliberately not stored anywhere. We needed it for
  // exactly one request and it goes out of scope here.
}

/** Finish the dance and return the peppered subject hash. */
export async function finish(env, provider, code, state) {
  const base = REGISTRY[provider];
  if (!base) throw new Error(`unknown provider: ${provider}`);
  // `name` so the shared helpers can build the redirect_uri, and `_call` so
  // the oauth2 path has the code without a fourth positional argument.
  const spec = { ...base, name: provider, _call: { code, state } };

  const sub = spec.kind === "oidc"
    ? await oidcSubject(env, spec, code, state)
    : await oauth2Subject(env, spec);
  return subjectHash(env.IDENTITY_PEPPER, provider, sub);
}

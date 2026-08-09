// Signing in, twice, because the two providers are not the same shape.
//
// GOOGLE is OpenID Connect. We ask for `openid` and nothing else — not
// `email`, not `profile` — so the ID token we get back carries a subject and
// essentially nothing about the person. It is verified locally against
// Google's published JWKS rather than by calling a tokeninfo endpoint, which
// is both faster and the thing the spec actually asks for.
//
// GITHUB IS NOT OIDC. It has no ID token, no nonce, and no PKCE support, and
// pretending otherwise in the docs would be worse than saying so. What it has
// is an authorization code exchanged with a client secret, which makes this a
// confidential client, and for a confidential client PKCE is defence in depth
// rather than the load-bearing part. The `state` cookie still binds the
// callback to the browser that started it.
//
// One rule shared by both, and it is the important one: we take the provider's
// IMMUTABLE NUMERIC/OPAQUE SUBJECT, never a username. A GitHub `login` can be
// renamed and then registered by somebody else, and keying on it would let a
// stranger inherit an account. `id` cannot be transferred.

import { pkce, subjectHash } from "./crypto.js";
import { safeReturn, sign, unsign } from "./cookies.js";

export const PROVIDERS = ["google", "github"];
export const STATE_TTL = 600;

const GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_TOKEN = "https://oauth2.googleapis.com/token";
const GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs";
const GITHUB_AUTH = "https://github.com/login/oauth/authorize";
const GITHUB_TOKEN = "https://github.com/login/oauth/access_token";
const GITHUB_USER = "https://api.github.com/user";

// GitHub's API rejects a request with no User-Agent outright. Not a
// suggestion — it 403s.
const UA = "big12ology-pickem (+https://big12ology.com)";

export function redirectUri(env, provider) {
  return `${env.SITE_ORIGIN}/api/auth/callback/${provider}`;
}

/**
 * Begin. Returns the provider URL to bounce to and the state cookie that
 * remembers what we sent, including the PKCE verifier — which must never
 * reach the provider until the exchange.
 */
export async function begin(env, provider, returnTo, mode = "login") {
  const state = crypto.randomUUID();
  const cookie = { provider, state, mode, return_to: safeReturn(returnTo) };
  let url;

  if (provider === "google") {
    const { verifier, challenge } = await pkce();
    const nonce = crypto.randomUUID();
    cookie.verifier = verifier;
    cookie.nonce = nonce;
    url = `${GOOGLE_AUTH}?` + new URLSearchParams({
      client_id: env.GOOGLE_CLIENT_ID,
      redirect_uri: redirectUri(env, "google"),
      response_type: "code",
      scope: "openid",          // deliberately the minimum. No email, no name.
      state, nonce,
      code_challenge: challenge,
      code_challenge_method: "S256",
      prompt: "select_account",
    });
  } else {
    url = `${GITHUB_AUTH}?` + new URLSearchParams({
      client_id: env.GITHUB_CLIENT_ID,
      redirect_uri: redirectUri(env, "github"),
      state,
      scope: "",                // empty: we want the id and nothing more
      allow_signup: "true",
    });
  }

  return { url, cookie: await sign(env.STATE_SIGNING_KEY, cookie, STATE_TTL) };
}

/** Read back what `begin` remembered, and check it against what came back. */
export async function readState(env, cookieValue, provider, state) {
  const data = await unsign(env.STATE_SIGNING_KEY, cookieValue);
  if (!data) return null;
  if (data.provider !== provider) return null;
  if (!state || data.state !== state) return null;
  return data;
}

// --------------------------------------------------------------- Google

let jwksCache = { at: 0, keys: null };

async function googleKeys() {
  const now = Date.now();
  if (jwksCache.keys && now - jwksCache.at < 3600_000) return jwksCache.keys;
  const r = await fetch(GOOGLE_JWKS);
  if (!r.ok) throw new Error("jwks_unavailable");
  const { keys } = await r.json();
  jwksCache = { at: now, keys };
  return keys;
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
 */
async function verifyIdToken(env, idToken, nonce) {
  const [h, p, s] = String(idToken).split(".");
  if (!h || !p || !s) throw new Error("bad_id_token");
  const header = JSON.parse(new TextDecoder().decode(b64urlToBytes(h)));
  const claims = JSON.parse(new TextDecoder().decode(b64urlToBytes(p)));

  const jwk = (await googleKeys()).find((k) => k.kid === header.kid);
  if (!jwk) throw new Error("unknown_kid");
  const key = await crypto.subtle.importKey("jwk", jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key,
    b64urlToBytes(s), new TextEncoder().encode(`${h}.${p}`));
  if (!ok) throw new Error("bad_signature");

  const now = Math.floor(Date.now() / 1000);
  if (claims.iss !== "https://accounts.google.com" &&
      claims.iss !== "accounts.google.com") throw new Error("bad_issuer");
  if (claims.aud !== env.GOOGLE_CLIENT_ID) throw new Error("bad_audience");
  if (typeof claims.exp !== "number" || claims.exp <= now) {
    throw new Error("expired_id_token");
  }
  if (nonce && claims.nonce !== nonce) throw new Error("bad_nonce");
  if (!claims.sub) throw new Error("no_subject");
  return claims.sub;
}

async function googleSubject(env, code, state) {
  const r = await fetch(GOOGLE_TOKEN, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: env.GOOGLE_CLIENT_ID,
      client_secret: env.GOOGLE_CLIENT_SECRET,
      redirect_uri: redirectUri(env, "google"),
      grant_type: "authorization_code",
      code_verifier: state.verifier,
    }),
  });
  if (!r.ok) throw new Error("token_exchange_failed");
  const tok = await r.json();
  if (!tok.id_token) throw new Error("no_id_token");
  return verifyIdToken(env, tok.id_token, state.nonce);
}

// --------------------------------------------------------------- GitHub

async function githubSubject(env, code) {
  const r = await fetch(GITHUB_TOKEN, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded",
               Accept: "application/json", "User-Agent": UA },
    body: new URLSearchParams({
      code,
      client_id: env.GITHUB_CLIENT_ID,
      client_secret: env.GITHUB_CLIENT_SECRET,
      redirect_uri: redirectUri(env, "github"),
    }),
  });
  if (!r.ok) throw new Error("token_exchange_failed");
  const tok = await r.json();
  if (!tok.access_token) throw new Error("no_access_token");

  const u = await fetch(GITHUB_USER, {
    headers: { Authorization: `Bearer ${tok.access_token}`,
               Accept: "application/vnd.github+json", "User-Agent": UA },
  });
  if (!u.ok) throw new Error("user_lookup_failed");
  const me = await u.json();
  // The numeric id. Never `login`: it is renameable, and the freed name can
  // be registered by anybody.
  if (typeof me.id !== "number") throw new Error("no_subject");
  return String(me.id);
  // The access token is deliberately not stored anywhere. We needed it for
  // exactly one request and it goes out of scope here.
}

/** Finish the dance and return the peppered subject hash. */
export async function finish(env, provider, code, state) {
  const sub = provider === "google"
    ? await googleSubject(env, code, state)
    : await githubSubject(env, code);
  return subjectHash(env.IDENTITY_PEPPER, provider, sub);
}

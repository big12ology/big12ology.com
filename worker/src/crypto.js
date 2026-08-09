// The small cryptographic pieces, in one place so there is one of each.
//
// Everything here is WebCrypto, which the Workers runtime provides natively —
// no dependency, and no hand-rolled anything. The only judgement calls are
// which primitive to use where, and they are written down beside each one.

/** Constant-length URL-safe base64, no padding. Cookie- and header-safe. */
export function b64url(bytes) {
  let s = "";
  const a = new Uint8Array(bytes);
  for (let i = 0; i < a.length; i++) s += String.fromCharCode(a[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function unb64url(str) {
  const s = str.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const enc = new TextEncoder();

export function randomBytes(n) {
  return crypto.getRandomValues(new Uint8Array(n));
}

export async function sha256(text) {
  return b64url(await crypto.subtle.digest("SHA-256", enc.encode(text)));
}

async function hmacKey(secret) {
  return crypto.subtle.importKey("raw", enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export async function hmac(secret, message) {
  const k = await hmacKey(secret);
  return b64url(await crypto.subtle.sign("HMAC", k, enc.encode(message)));
}

/**
 * Verify without leaking where the mismatch was.
 *
 * crypto.subtle.verify is constant-time; comparing two base64 strings with
 * === is not, and the difference is a timing oracle on a MAC. It costs
 * nothing to use the right one.
 */
export async function hmacVerify(secret, message, sig) {
  const k = await hmacKey(secret);
  try {
    return await crypto.subtle.verify("HMAC", k, unb64url(sig),
                                      enc.encode(message));
  } catch {
    return false;   // malformed base64 is a failed verification, not a crash
  }
}

/**
 * The stored form of an OAuth subject.
 *
 * A dump of `identities` without IDENTITY_PEPPER links to nobody: the
 * provider's `sub` is not recoverable from the hash, and the same person at
 * Google and at GitHub does not collide. THE PEPPER IS PERMANENT — rotating
 * it orphans every account on the site, because there is no email to
 * re-associate them by.
 */
export function subjectHash(pepper, provider, sub) {
  return hmac(pepper, `${provider}|${sub}`);
}

/**
 * ULID: sortable by creation time, and short enough to sit in a URL.
 *
 * Crockford base32, 48 bits of millisecond timestamp then 80 bits of
 * randomness. Chosen over a UUID because user ids appear in
 * /api/users/:id/picks and a v4 UUID is 36 characters of nothing.
 */
const B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
export function ulid(now = Date.now()) {
  let t = "";
  let ms = now;
  for (let i = 0; i < 10; i++) { t = B32[ms % 32] + t; ms = Math.floor(ms / 32); }
  const r = randomBytes(16);
  let s = "";
  for (let i = 0; i < 16; i++) s += B32[r[i] % 32];
  return t + s;
}

/** PKCE S256. The verifier is high-entropy and never leaves our cookie. */
export async function pkce() {
  const verifier = b64url(randomBytes(32));
  const challenge = b64url(
    await crypto.subtle.digest("SHA-256", enc.encode(verifier)));
  return { verifier, challenge };
}

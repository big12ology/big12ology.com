// Two cookies, both `__Host-`, and the reasoning for every attribute.
//
// The `__Host-` prefix is not decoration. A browser refuses to store one
// unless it is Secure, Path=/, and carries NO Domain attribute — which makes
// the cookie host-only and, crucially, means no subdomain can overwrite it.
// Without it, anything that could get a page onto a big12ology.com subdomain
// could set a session cookie the apex would then honor.
//
// SameSite is Lax on both, and Strict would be a bug rather than an upgrade:
// the OAuth callback is a cross-site top-level GET back from Google, and
// Strict withholds the cookie on exactly that navigation. The state cookie
// would not arrive, and the flow could never complete.

import { hmac, hmacVerify } from "./crypto.js";

export const SESSION_COOKIE = "__Host-b12s";
export const STATE_COOKIE = "__Host-b12oauth";

/**
 * Parse a Cookie header. Returns {} for anything unparseable.
 *
 * THE try/catch IS THE WHOLE POINT OF THIS FUNCTION'S SECOND DRAFT.
 * decodeURIComponent throws URIError on a malformed escape — "%zz" is enough —
 * and this runs at the top of index.js before any route is matched, so one bad
 * cookie in the header made EVERY /api/ request answer 500. Not the request
 * that carried it: all of them, from that browser, including /api/health,
 * which is what the monitoring reads. It would have looked like the Worker
 * being down and been invisible from every other machine.
 *
 * A cookie we cannot decode is a cookie that is not ours — nothing this file
 * writes contains a percent sign — so the raw value is kept and the caller's
 * own check fails it. Skipping the entry would work equally well; keeping it
 * means one less way for a value to silently disappear.
 */
export function parseCookies(header) {
  const out = {};
  if (!header) return out;
  for (const part of header.split(";")) {
    const i = part.indexOf("=");
    if (i < 1) continue;
    const k = part.slice(0, i).trim();
    if (!k) continue;
    const v = part.slice(i + 1).trim();
    try {
      out[k] = decodeURIComponent(v);
    } catch {
      out[k] = v;
    }
  }
  return out;
}

export function serialize(name, value, { maxAge, httpOnly = true } = {}) {
  const bits = [`${name}=${encodeURIComponent(value)}`, "Path=/", "Secure",
                "SameSite=Lax"];
  if (httpOnly) bits.push("HttpOnly");
  if (maxAge != null) bits.push(`Max-Age=${maxAge}`);
  return bits.join("; ");
}

export function clear(name) {
  return `${name}=; Path=/; Secure; SameSite=Lax; HttpOnly; Max-Age=0`;
}

/**
 * A signed JSON cookie.
 *
 * Used for the OAuth handoff instead of a KV entry, for two reasons. KV's free
 * tier allows a thousand writes a day and every sign-in attempt would spend
 * one; and a ten-minute object read back seconds after it was written is the
 * case KV's eventual consistency handles worst, so the read could miss and the
 * login would fail for no visible reason. A signed cookie has neither problem
 * and needs no storage at all.
 *
 * The expiry is inside the signed payload, not only in Max-Age: Max-Age is a
 * request to the browser, and a replayed cookie would otherwise be valid
 * forever.
 */
export async function sign(secret, payload, ttlSeconds) {
  const body = JSON.stringify({ ...payload,
                                exp: Math.floor(Date.now() / 1000) + ttlSeconds });
  const b = btoa(body).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b}.${await hmac(secret, b)}`;
}

export async function unsign(secret, token) {
  if (!token) return null;
  const dot = token.lastIndexOf(".");
  if (dot < 1) return null;
  const b = token.slice(0, dot);
  if (!(await hmacVerify(secret, b, token.slice(dot + 1)))) return null;
  let data;
  try {
    const s = b.replace(/-/g, "+").replace(/_/g, "/");
    data = JSON.parse(atob(s + "=".repeat((4 - (s.length % 4)) % 4)));
  } catch {
    return null;
  }
  if (!data || typeof data.exp !== "number") return null;
  if (data.exp <= Math.floor(Date.now() / 1000)) return null;
  return data;
}

/**
 * Where to send someone after they sign in.
 *
 * An unchecked return_to on an OAuth callback is an open redirect, and an
 * open redirect on the host that just issued a session cookie is a phishing
 * primitive rather than a nuisance. Only same-site absolute paths under the
 * section are allowed; anything else silently becomes the section root.
 *
 * "//evil.com" is the case worth naming: it is a valid protocol-relative URL
 * that starts with a slash, so a naive `startsWith("/")` waves it through.
 */
/** Where a sign-in lands when there is nowhere better, and the prefix the
 *  allowlist trusts. Both games and the account they share live under it. */
export const HOME = "/pools/pickem/";

export function safeReturn(to) {
  if (typeof to !== "string") return HOME;
  if (!to.startsWith("/") || to.startsWith("//") || to.startsWith("/\\")) {
    return HOME;
  }
  return to.startsWith("/pools/") ? to : HOME;
}

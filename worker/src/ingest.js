// The one way in from the publisher.
//
// pages.yml learns scores from CFBD and has, until now, handed them to this
// Worker by writing a file into a 415-file static deploy and letting the cron
// notice. This is the direct channel: a signed POST, verified here, written to
// KV, and graded in the same request.
//
// WHY A SIGNATURE AND NOT A BEARER TOKEN. Two reasons, and the second is the
// one that decided it.
//
// A bearer token has to be compared, and comparing two strings with === in
// JavaScript is a timing oracle on a secret. There is no timingSafeEqual to
// reach for in this runtime that is worth depending on, whereas crypto.js
// already has hmacVerify built on crypto.subtle.verify, which is constant-time
// by construction and is the same primitive the OAuth state cookie trusts.
//
// More importantly, a bearer token authenticates the CALLER and nothing else.
// An HMAC over the body authenticates the PAYLOAD: a truncated upload, a
// half-written file, or a proxy that helpfully re-encoded something all fail
// verification instead of being written to KV and graded as though they were
// the season's results. The scores file is the input to every board on the
// site, and "it came from the right place" is a weaker claim than "these are
// the exact bytes that were signed".
//
// THE SECRET IS NOT A CLOUDFLARE TOKEN, on purpose. worker.yml's header
// explains why pages.yml must not hold one: that workflow runs about three
// hundred times a season, and a Cloudflare API token would let every one of
// those runs deploy the Worker, read D1, or drop a namespace. This secret
// does exactly one thing — publish scores for grading. It cannot deploy,
// cannot migrate, cannot read a pick.

import { hmacVerify } from "./crypto.js";
import { scoresKey } from "./scoring.js";

/**
 * How stale a signed request may be, in seconds.
 *
 * The signature covers the timestamp, so this is what stops a captured POST
 * from being replayed later. Five minutes is loose enough for a slow runner
 * and a retried step, and short enough that a replay window is not a place to
 * park an old set of scores — replaying a stale file would un-grade games that
 * have since finished.
 */
export const MAX_SKEW = 300;

/** The headers the publisher sends. Named, so both ends have one spelling. */
export const TS_HEADER = "X-B12-Timestamp";
export const SIG_HEADER = "X-B12-Signature";

/**
 * Check a scores POST and, if it is good, store it.
 *
 * Returns {ok: true, scores, season} or {ok: false, error, status}. It does
 * not grade — index.js does that, so the routing and the scoring stay where
 * the rest of the routing and scoring are.
 */
export async function receiveScores(req, env,
                                    now = Math.floor(Date.now() / 1000)) {
  // Refuse rather than fall open. A Worker deployed before the secret is set
  // should not accept unsigned scores from anybody who finds the path.
  if (!env.SCORES_INGEST_KEY) return { ok: false, error: "not_configured", status: 503 };
  if (!env.SCORES) return { ok: false, error: "no_scores_namespace", status: 503 };

  const ts = req.headers.get(TS_HEADER);
  const sig = req.headers.get(SIG_HEADER);
  if (!ts || !sig) return { ok: false, error: "unsigned", status: 401 };

  const at = Number(ts);
  if (!Number.isInteger(at)) return { ok: false, error: "bad_timestamp", status: 401 };
  // Both directions. A clock ahead of ours is as much a sign of something
  // wrong as one behind, and allowing the future would make the replay window
  // unbounded for anyone who can set a header.
  if (Math.abs(now - at) > MAX_SKEW) {
    return { ok: false, error: "stale", status: 401 };
  }

  // The exact bytes, before any parsing. Verifying a re-serialized object
  // would verify our own JSON.stringify rather than what was sent, and the
  // two do not have to agree on key order or number formatting.
  const raw = await req.text();
  if (!await hmacVerify(env.SCORES_INGEST_KEY, `${at}.${raw}`, sig)) {
    return { ok: false, error: "bad_signature", status: 403 };
  }

  let doc;
  try { doc = JSON.parse(raw); } catch { doc = null; }
  if (!doc || typeof doc !== "object") {
    return { ok: false, error: "bad_json", status: 400 };
  }

  // Shape, checked here rather than trusted. A signed but malformed file is
  // possible — the publisher is a shell script piping a build artifact — and
  // scoreWeek would read `scores.games[id]` off undefined and mark a whole
  // week's worth of finals as having no result.
  const season = Number(doc.season);
  if (!Number.isInteger(season) || season < 2000 || season > 2100) {
    return { ok: false, error: "bad_season", status: 400 };
  }
  if (!doc.games || typeof doc.games !== "object" || Array.isArray(doc.games)) {
    return { ok: false, error: "bad_games", status: 400 };
  }

  // Stored as the raw text, so what a later read parses is exactly what was
  // signed — not this Worker's re-encoding of it.
  await env.SCORES.put(scoresKey(season), raw);

  return { ok: true, scores: doc, season, games: Object.keys(doc.games).length };
}

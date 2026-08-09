// Fixed-window counters, in D1.
//
// Not KV: the free tier allows a thousand writes a day and a counter writes on
// every attempt, so a KV-backed limiter is a self-inflicted outage on the
// first busy Saturday. D1 has no such cap and the row is tiny.
//
// This is the SECOND line, not the first. Under an actual flood the thing that
// helps is the Cloudflare WAF rate-limit rule on /api/*, which runs at the
// edge before the Worker is even invoked. What this stops is the patient
// abuse a WAF rule is too blunt for: renaming forty times an hour, grinding
// sign-ins, replaying pick submissions.

/** Windows, in seconds, and what may happen in one. */
export const LIMITS = {
  picks:  { max: 30, window: 3600 },
  rename: { max: 5,  window: 86400 },
  login:  { max: 20, window: 3600 },
  signup: { max: 5,  window: 86400 },
};

/**
 * Count one attempt. Returns {ok, remaining, retryAfter}.
 *
 * Fixed window rather than sliding: a sliding window needs either a sorted set
 * per key or a second table, and the failure mode it fixes — twice the limit
 * across a boundary — does not matter for any of these actions. Being clear
 * about which one this is beats being quietly approximate.
 */
export async function take(env, kind, key, now = Math.floor(Date.now() / 1000)) {
  const limit = LIMITS[kind];
  if (!limit) throw new Error(`unknown rate limit: ${kind}`);
  const bucket = `${kind}:${key}`;
  const start = now - (now % limit.window);

  // One statement, so two concurrent requests cannot both read 0 and both
  // write 1. ON CONFLICT resets the count when the row belongs to an older
  // window, which is also how old rows are recycled rather than accumulated.
  await env.DB.prepare(
    `INSERT INTO rate_limits (bucket, window_start, count) VALUES (?, ?, 1)
     ON CONFLICT(bucket) DO UPDATE SET
       count = CASE WHEN rate_limits.window_start = excluded.window_start
                    THEN rate_limits.count + 1 ELSE 1 END,
       window_start = excluded.window_start`)
    .bind(bucket, start).run();

  const row = await env.DB.prepare(
    `SELECT count FROM rate_limits WHERE bucket = ?`).bind(bucket).first();
  const count = row ? row.count : 1;

  return count <= limit.max
    ? { ok: true, remaining: limit.max - count }
    : { ok: false, remaining: 0, retryAfter: start + limit.window - now };
}

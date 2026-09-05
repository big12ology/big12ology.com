/**
 * Finals from ESPN, for the gap between a game ending and CFBD saying so.
 *
 * WHY THIS EXISTS. The board grades from pickem-scores.json, which build.py
 * writes from CFBD and a GitHub workflow publishes. CFBD does catch up -- the
 * 2026 week 1 Thursday games were in games_2026.json within an hour of the
 * last one ending -- so this is not a correctness backstop. It is a latency
 * one. The delay that shows on the site is mostly the publisher's clock, not
 * CFBD's ingest: on 2026-09-05 three games finished around 23:00 UTC and were
 * still ungraded at 23:32, because the run that would have fetched them was
 * not due yet and GitHub's cron had been dropping runs all day.
 *
 * ESPN's scoreboard answers in about a second, needs no key, has no quota, and
 * carries the same ESPN game ids the slate is keyed by. Measured against
 * games_2026.json on 2026-09-05: agreement on all thirteen games the repo had,
 * plus three finals it did not have yet.
 *
 * CFBD REMAINS THE RECORD. Nothing here overwrites a completed CFBD result,
 * ever. These entries only fill games the published file has not graded, so
 * the worst a bad ESPN read can do is grade a game early -- and the next
 * publish, which is authoritative, corrects it through the ordinary revision
 * path with a row in results_history to show for it.
 *
 * AND THEY ARE KEPT, not applied once. A final that only lived in one sweep
 * would be lost the moment the publisher wrote a file that still lacked it,
 * and a game that goes back to ungraded is a game the void clock can reach
 * thirty-six hours after kickoff. So the finals accumulate in KV and are
 * overlaid on every grading pass until CFBD carries them itself.
 */

const SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football"
                 + "/college-football/scoreboard";

// FBS. Every Big 12 slate game appears here even when the opponent does not
// play in it: an FCS visitor is still on the FBS team's scoreboard, which is
// why Bethune-Cookman, Long Island, SEMO, Nicholls and Abilene Christian all
// resolved in the week 1 check.
const GROUPS = 80;

// Long enough that a game asked about is almost certainly over, short enough
// to beat the publisher. Nothing breaks if it is asked early; an unfinished
// game simply is not completed yet and nothing is written.
const SETTLE = 3 * 3600;

export function espnKey(season) {
  return `espn:${season}`;
}

function yyyymmdd(ts) {
  return new Date(ts * 1000).toISOString().slice(0, 10).replace(/-/g, "");
}

/**
 * The scoreboard, as {game_id: [home, away, completed]}, for finals only.
 *
 * DATES RATHER THAN WEEKS. ESPN's week numbering has no week 0 --
 * `week=0&seasontype=2` returns an empty event list, and the 2026 opener in
 * Dublin is a week 0 game -- while a date range finds it. A day of slack on
 * each end covers the difference between the UTC timestamps the slate is
 * stored in and the Eastern dates ESPN files games under: a 01:00 UTC kickoff
 * is the previous evening's game to them.
 */
export async function fetchEspn(from, to, fetchImpl = fetch) {
  const url = `${SCOREBOARD}?dates=${yyyymmdd(from - 86400)}`
            + `-${yyyymmdd(to + 86400)}&groups=${GROUPS}&limit=400`;
  const r = await fetchImpl(url, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`espn_${r.status}`);
  const doc = await r.json();
  const out = {};
  for (const e of doc.events || []) {
    const c = (e.competitions || [])[0];
    if (!c || !c.status || !c.status.type || !c.status.type.completed) continue;
    let home = null, away = null;
    for (const t of c.competitors || []) {
      // Scores arrive as strings. Number("") is 0, which would post a shutout
      // for a game whose score has not been filled in, so the empty case is
      // rejected before it can be parsed into a plausible lie.
      const raw = t.score;
      const v = raw === "" || raw == null ? null : Number(raw);
      if (!Number.isInteger(v)) continue;
      if (t.homeAway === "home") home = v;
      else if (t.homeAway === "away") away = v;
    }
    if (home == null || away == null) continue;
    out[String(e.id)] = [home, away, true];
  }
  return out;
}

/**
 * Ask ESPN about slate games the published file has not graded, and keep what
 * comes back. Returns a small report; the cron logs it.
 */
export async function sweepEspn(env, season, base, now = Math.floor(Date.now() / 1000)) {
  // Same guard withEspn carries. Without the binding there is nowhere to keep
  // a final, and a fetch whose answer cannot be kept is just a slower way of
  // grading nothing.
  if (!env.SCORES) return { skipped: "no_kv" };
  const { results: games } = await env.DB.prepare(
    `SELECT game_id, kickoff_at FROM slate_games WHERE season = ?`)
    .bind(season).all();

  const published = (base && base.games) || {};
  const pending = (games || []).filter((g) => {
    if (g.kickoff_at == null || g.kickoff_at + SETTLE > now) return false;
    const v = published[String(g.game_id)];
    // Completed with both scores is the only shape that counts as graded, and
    // it is the same test scoreWeek applies. A [null, null, false] entry is
    // the publisher saying nothing, which is exactly what this is here for.
    return !(Array.isArray(v) && v[2] && v[0] != null && v[1] != null);
  });
  if (!pending.length) return { skipped: "nothing_pending" };

  const kicks = pending.map((g) => g.kickoff_at);
  const found = await fetchEspn(Math.min(...kicks), Math.max(...kicks),
                               env.ESPN_FETCH || fetch);

  // Slate games only. The scoreboard carries the whole of FBS and none of the
  // rest of it is any of this Worker's business.
  const wanted = new Set(pending.map((g) => String(g.game_id)));
  const held = (await env.SCORES.get(espnKey(season), "json")) || { games: {} };
  let added = 0;
  for (const [id, triple] of Object.entries(found)) {
    if (!wanted.has(id)) continue;
    const was = held.games[id];
    if (was && was[0] === triple[0] && was[1] === triple[1]) continue;
    held.games[id] = triple;
    added++;
  }
  // KV writes are capped at a thousand a day on this plan and the sweep runs
  // hourly all season, so a quiet pass must not write. Only a new final does.
  if (added) await env.SCORES.put(espnKey(season), JSON.stringify(held));
  return { pending: pending.length, found: Object.keys(found).length, added };
}

/**
 * The published file with kept ESPN finals filling its gaps.
 *
 * Applied at grading time rather than at publish time, so it covers both ways
 * scores reach the grader: the ingest endpoint handing over a fresh file, and
 * the cron reading the last one out of KV.
 */
export async function withEspn(env, season, base) {
  if (!env.SCORES) return base;
  const held = await env.SCORES.get(espnKey(season), "json");
  if (!held || !held.games) return base;
  const games = { ...(base.games || {}) };
  let filled = 0;
  for (const [id, triple] of Object.entries(held.games)) {
    const v = games[id];
    if (Array.isArray(v) && v[2] && v[0] != null && v[1] != null) continue;
    games[id] = triple;
    filled++;
  }
  if (filled) console.log(`espn: filled ${filled} game(s) the publisher lacks`);
  return { ...base, games };
}

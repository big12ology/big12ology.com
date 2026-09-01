// The pick'em, end to end: a real Worker, a real D1, the built client's own
// API, and one origin between them.
//
// WHAT THIS COVERS THAT worker/test/*.test.js CANNOT. Those run the handlers
// directly, with a Request they construct themselves. Everything between the
// browser and the handler is therefore assumed: that the Origin the client
// sends is the one SITE_ORIGIN expects, that the session cookie survives the
// round trip, that the slate the publisher writes is the shape importWeek
// parses, that a lock arriving mid-week reaches the player as a 409 rather than
// a 500. Each of those has been wrong at least once, and none of them is
// visible to a unit test.
//
// ASSERTED THROUGH THE API, never by reading the database. Two reasons. The
// running Worker holds the SQLite file, so an outside reader gets "unable to
// open database file" as soon as there is contention. And the API is the
// contract the client is written against: a pick that is in the table but does
// not come back from GET /api/picks is not saved as far as anything that
// matters is concerned.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const BASE = process.env.E2E_BASE   || "http://localhost:8799";
const WORKER = process.env.E2E_WORKER || "http://127.0.0.1:8798";
const SITE = process.env.E2E_SITE;      // the served tree, for slate rewrites
const RUN = process.env.E2E_RUN;        // the run directory
const SEASON = Number(process.env.E2E_SEASON || 2026);

const COOKIE = `__Host-b12s=${readFileSync(join(RUN, "cookie.txt"), "utf8").trim()}`;
const ORIGIN = BASE;

let pass = 0;
const failures = [];
const skipped = [];

function check(name, ok, detail) {
  if (ok) { pass++; return true; }
  failures.push(`${name}${detail ? `\n      ${detail}` : ""}`);
  return false;
}

// A check that did not run is not a check that passed. Several of these depend
// on the committed fixture still having a shape to test against — a week with
// an unlined game, a conference team playing twice — and a season's data can
// stop providing one. Silence there would read as a clean run, which is the
// failure this file exists to make impossible, so a skip is reported and the
// tally states it.
function skip(name, why) { skipped.push(`${name} (${why})`); }

/** A request as the browser would make it, or deliberately not. */
async function call(method, path, { origin = ORIGIN, cookie = COOKIE, body } = {}) {
  const headers = {};
  if (origin) headers.Origin = origin;
  if (cookie) headers.Cookie = cookie;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(BASE + path, {
    method, headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* not every body is JSON */ }
  return { status: r.status, json, text };
}

const cron = () =>
  fetch(`${WORKER}/__scheduled?cron=30+7-23+*+8-12+3`).then((r) => r.status);

// The repo-layout path the import reads through RAW_ORIGIN, not the
// /pools/data/ path the website serves; see pickem-e2e.sh's fixture step.
const slatePath = (week) =>
  join(SITE, "tiebreaker", "pickem", String(SEASON),
       `week-${String(week).padStart(2, "0")}.json`);

/** Rewrite a published week in the served tree, then re-import it. */
async function republish(week, mutate) {
  const p = slatePath(week);
  const slate = JSON.parse(readFileSync(p, "utf8"));
  mutate(slate);
  slate.pickable_count =
    slate.games.filter((g) => g.spread_x2 != null).length;
  writeFileSync(p, JSON.stringify(slate, null, 1));
  await cron();
  return slate;
}

// ---------------------------------------------------------------- the import

const health = (await call("GET", "/api/health", { cookie: null })).json;
check("the cron imported the published weeks",
      health && health.weeks >= 3, `weeks=${health && health.weeks}`);

const slate0 = (await call("GET", "/api/slate?week=0", { cookie: null })).json;
const slate1 = (await call("GET", "/api/slate?week=1", { cookie: null })).json;
check("a slate comes back with its games",
      slate1 && Array.isArray(slate1.games) && slate1.games.length > 0);

const pickable = (slate1?.games || []).filter((g) => g.spread_x2 != null);
check("some of week 1 carries a line", pickable.length >= 2,
      `${pickable.length} pickable`);
const game = pickable[0];
const unlined = (slate1?.games || []).find((g) => g.spread_x2 == null);

// ------------------------------------------------------------------- the door

check("an anonymous reader is not signed in",
      (await call("GET", "/api/me", { cookie: null })).status === 401);

const me = await call("GET", "/api/me");
check("the session cookie signs a player in",
      me.status === 200 && me.json?.display_name === "E2E Player",
      JSON.stringify(me.json));

// The CSRF matrix. Every one of these has to hold for the browser's own write
// to be the only shape that works.
const card = { season: SEASON, week: 1, picks: { [game.game_id]: "home" } };
check("a write with no Origin is refused",
      (await call("PUT", "/api/picks", { origin: null, body: card })).status === 403);
check("a write from another origin is refused",
      (await call("PUT", "/api/picks",
                  { origin: "https://evil.example", body: card })).status === 403);
check("a write with no session is refused",
      (await call("PUT", "/api/picks", { cookie: null, body: card })).status === 401);
check("the browser's own write is accepted",
      (await call("PUT", "/api/picks", { body: card })).status === 200);

// ------------------------------------------------------------------- the card

let picks = (await call("GET", "/api/picks?week=1")).json;
check("the pick reads back", picks?.picks?.[game.game_id] === "home",
      JSON.stringify(picks?.picks));

await call("PUT", "/api/picks",
           { body: { ...card, picks: { [game.game_id]: "away" } } });
picks = (await call("GET", "/api/picks?week=1")).json;
check("a pick can be changed before the lock",
      picks?.picks?.[game.game_id] === "away");

// The whole-card replace, which is the semantic the client depends on and the
// one most likely to be misread as a partial update.
await call("PUT", "/api/picks", { body: { ...card, picks: {} } });
picks = (await call("GET", "/api/picks?week=1")).json;
check("an empty card clears the week",
      Object.keys(picks?.picks || {}).length === 0);

const bad = await call("PUT", "/api/picks",
  { body: { ...card, picks: { [game.game_id]: "sideways" } } });
check("a side that is not home or away is refused",
      bad.status === 400 && bad.json?.error === "bad_side");

if (unlined) {
  await call("PUT", "/api/picks",
    { body: { ...card, picks: { [unlined.game_id]: "home" } } });
  picks = (await call("GET", "/api/picks?week=1")).json;
  check("a game with no line is never stored",
        !(String(unlined.game_id) in (picks?.picks || {})));
} else {
  skip("a game with no line is never stored", "week 1 has no unlined game");
}

// Put a real pick back, so the lock has something to protect.
await call("PUT", "/api/picks", { body: card });

// ------------------------------------------------------------------- the lock

const week0game = (slate0?.games || []).find((g) => g.spread_x2 != null);
await call("PUT", "/api/picks",
  { body: { season: SEASON, week: 0, picks: { [week0game.game_id]: "home" } } });

// A lock may move EARLIER — weeks_lock_monotonic only refuses one that moves
// later — so this is the transition a real week actually makes, not a poke at
// the database.
await republish(0, (s) => { s.lock_at = Math.floor(Date.now() / 1000) - 60; });

const locked = (await call("GET", "/api/picks?week=0")).json;
check("the week reads as locked", locked?.locked === true);
check("the pick survived the lock",
      locked?.picks?.[week0game.game_id] === "home");

for (const [what, body] of [
  ["changed", { [week0game.game_id]: "away" }],
  ["cleared", {}],
  ["added to", { [week0game.game_id]: "home", 999999: "away" }],
]) {
  const r = await call("PUT", "/api/picks",
                       { body: { season: SEASON, week: 0, picks: body } });
  check(`a locked card cannot be ${what}`,
        r.status === 409 && r.json?.error === "locked",
        `${r.status} ${r.text.slice(0, 80)}`);
  // The 409 carries the true card, because the client repaints from it.
  check(`the refusal to be ${what} states the real card`,
        r.json?.picks?.[week0game.game_id] === "home");
}

const still = (await call("GET", "/api/picks?week=0")).json;
check("the locked pick is still exactly what it was",
      still?.picks?.[week0game.game_id] === "home");

// --------------------------------------------------------------- the survivor

const sv = (await call("GET", "/api/survivor?week=1")).json;
check("survivor reports the week", sv?.week === 1);

const conf = pickable.find((g) => g.b12 === "home" || g.b12 === "away");
const mine = conf.b12 === "home" ? conf.home : conf.away;
const theirs = conf.b12 === "home" ? conf.away : conf.home;

const sp = (body) => call("PUT", "/api/survivor/pick", { body });
check("a Big 12 team can be spent",
      (await sp({ season: SEASON, week: 1, game_id: conf.game_id, team: mine })).status === 200);

if (conf.b12 !== "both") {
  const r = await sp({ season: SEASON, week: 1, game_id: conf.game_id, team: theirs });
  check("the non-conference side of a game cannot be spent",
        r.status === 400 && r.json?.error === "not_in_conference",
        JSON.stringify(r.json));
} else {
  skip("the non-conference side of a game cannot be spent",
       "the chosen game is Big 12 on both sides");
}
{
  const r = await sp({ season: SEASON, week: 1, game_id: conf.game_id,
                       team: "Not A Real Team" });
  check("a team that is not in the game is refused",
        r.status === 400 && r.json?.error === "not_in_game");
}
if (unlined) {
  const r = await sp({ season: SEASON, week: 1, game_id: unlined.game_id,
                       team: unlined.home });
  check("a game with no line cannot be spent",
        r.status === 400 && r.json?.error === "unpickable");
} else {
  skip("a game with no line cannot be spent", "week 1 has no unlined game");
}

const after = (await call("GET", "/api/survivor?week=1")).json;
check("the spent team is on the run",
      (after?.used || []).some((u) => u.team === mine),
      JSON.stringify(after?.used));

// The rule the whole pool is: never the same team twice. Week 2 is republished
// with that team playing again and a line on it, plus enough other lined games
// that the week is a contest at all.
const w2 = JSON.parse(readFileSync(slatePath(2), "utf8"));
const again = w2.games.find((g) => g.home === mine || g.away === mine);
if (again) {
  await republish(2, (s) => {
    s.lock_at = Math.floor(Date.now() / 1000) + 7 * 86400;
    let lined = 0;
    for (const g of s.games) {
      if (g.game_id === again.game_id || (g.b12 && g.spread_x2 == null && lined < 2)) {
        if (g.game_id !== again.game_id) lined++;
        g.spread_x2 = -12;
        delete g.unpickable;
      }
    }
  });
  const r = await sp({ season: SEASON, week: 2, game_id: again.game_id, team: mine });
  check("a team already spent cannot be spent again",
        r.status === 409 && r.json?.error === "team_used",
        `${r.status} ${r.text.slice(0, 80)}`);
} else {
  skip("a team already spent cannot be spent again",
       `${mine} does not play again in week 2`);
}

// ---------------------------------------------------------------- the scoring

// The path scores.yml uses: signed bytes to /api/ingest/scores, which verifies,
// stores and grades in the same request. Nothing here touches the cron.
const KEY = process.env.E2E_INGEST_KEY;
if (!KEY) skip("the scoring path", "E2E_INGEST_KEY not set");
if (KEY && week0game) {
  // A result the pick covers: the home side wins by more than the line.
  const margin = Math.ceil(Math.abs(week0game.spread_x2) / 2) + 4;
  const body = JSON.stringify({
    season: SEASON,
    games: { [String(week0game.game_id)]: [20 + margin, 20, true] },
  });
  const ts = Math.floor(Date.now() / 1000);
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(KEY),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  // base64url, matching crypto.js's b64url and what publish-scores.sh sends.
  // Hex verifies against nothing: the Worker feeds the header to unb64url.
  const sig = Buffer.from(await crypto.subtle.sign(
    "HMAC", key, new TextEncoder().encode(`${ts}.${body}`)))
    .toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

  const r = await fetch(`${BASE}/api/ingest/scores`, {
    method: "POST",
    headers: { "Content-Type": "application/json",
               "X-B12-Timestamp": String(ts), "X-B12-Signature": sig },
    body,
  });
  const out = await r.json().catch(() => null);
  check("a signed score is accepted and graded",
        r.status === 200 && out?.ok === true, `${r.status} ${JSON.stringify(out)}`);

  const board = (await call("GET", "/api/leaderboard", { cookie: null })).json;
  const row = (board?.rows || []).find((x) => x.display_name === "E2E Player");
  check("the graded pick reaches the board", !!row && row.w === 1,
        JSON.stringify(board?.rows));

  const badsig = await fetch(`${BASE}/api/ingest/scores`, {
    method: "POST",
    headers: { "Content-Type": "application/json",
               "X-B12-Timestamp": String(ts),
               "X-B12-Signature": "A".repeat(43) },
    body,
  });
  check("an unsigned score is refused", badsig.status >= 400,
        `status ${badsig.status}`);
}

// ------------------------------------------------------------------- the tally

for (const f of failures) console.log(`  FAIL  ${f}`);
for (const s of skipped) console.log(`  SKIP  ${s}`);

const tail = skipped.length ? `, ${skipped.length} skipped` : "";
console.log(failures.length
  ? `\npick'em e2e: ${pass} passed, ${failures.length} FAILED${tail}`
  : `pick'em e2e: ${pass} checks passed against a live Worker${tail}`);
process.exit(failures.length ? 1 : 0);

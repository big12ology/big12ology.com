// What each tick is for, and that the answer does not depend on the triggers.
//
// src/schedule.js decides a tick's jobs from the time it was scheduled for.
// Two arrangements of triggers have to get the same behavior out of it:
//
//   the four cron strings in wrangler.toml, which is what delivers ticks now
//   one trigger every ten minutes, which is what replaces them
//
// So the first half of this file expands the four strings by Cloudflare's own
// rules (1 = Sunday) and checks them against the table in both directions.
// Every firing must be a tick with the jobs the old arrangement gave it, or a
// sweep is silently lost the day this deploys. And every ten-minute tick with
// a job must be one of those firings, or the single trigger would start doing
// work at times nobody chose.
//
// The second half is the one new job: asking for the weekly refresh when the
// pools week has not been opened.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import worker from "../src/index.js";
import { jobsAt, anyJob, weekNeedsOpening, REFRESH_HOURS } from "../src/schedule.js";
import { makeEnv, seedWeek } from "./helpers/env.js";

// THE FOUR, WRITTEN OUT. These are the schedules the table was derived from
// and has to keep answering for, whatever wrangler.toml says by the time
// this is read: once a single ten-minute trigger replaces them, this is the
// only place they are still written down, and the comparison below is what
// says the replacement changed nothing. Cloudflare's calendar, 1 = Sunday.
const crons = [
  "30 * * 8-12 1,7",        // hourly Saturday and Sunday: the score sweep
  "30 0-8/2 * 8-12 2,6,7",  // small hours Monday, Friday, Saturday
  "30 7-23 * 8-12 3",       // Tuesday: the slate import
  "0 12 * * *",             // noon, all year: the heartbeat
];

// The two that also dispatched a deploy, by the env var that used to name
// them. Written out here because the var is gone and this is the record of
// what it said.
const DEPLOYED = new Set(["30 * * 8-12 1,7", "30 0-8/2 * 8-12 2,6,7"]);

// scores.yml's own schedule, read from the workflow so the two cannot drift
// apart quietly. GitHub's calendar, which is the ordinary one: 0 = Sunday.
const scoresYml = fs.readFileSync(
  new URL("../../.github/workflows/scores.yml", import.meta.url), "utf8");
const scoreCrons = [...scoresYml.matchAll(/^\s*- cron: "([^"]+)"/gm)]
  .map((m) => m[1].trim());

/** One cron field as a predicate. Lists, ranges, steps and the star. */
function field(expr) {
  if (expr === "*") return () => true;
  const parts = expr.split(",").map((p) => {
    const m = p.match(/^(\d+)(?:-(\d+))?(?:\/(\d+))?$/);
    assert.ok(m, `cannot read cron field ${expr}`);
    const lo = Number(m[1]), hi = m[2] === undefined ? lo : Number(m[2]);
    const step = m[3] === undefined ? 1 : Number(m[3]);
    return (v) => v >= lo && v <= hi && (v - lo) % step === 0;
  });
  return (v) => parts.some((f) => f(v));
}

/**
 * Whether `cron` fires at `ms`. `sunday` is the number its calendar gives
 * Sunday: 1 on Cloudflare, 0 on GitHub.
 */
function fires(cron, ms, sunday = 1) {
  const [mi, ho, dom, mo, dw] = cron.replace("*/20", "0-59/20").split(/\s+/);
  const d = new Date(ms);
  return field(mi)(d.getUTCMinutes()) && field(ho)(d.getUTCHours())
    && field(dom)(d.getUTCDate()) && field(mo)(d.getUTCMonth() + 1)
    && field(dw)(d.getUTCDay() + sunday);
}

const MIN = 60 * 1000;
/** Every ten-minute tick of a span of days starting at 00:00 UTC. */
function* ticks(startIso, days) {
  const t0 = Date.parse(startIso);
  for (let i = 0; i < days * 144; i++) yield t0 + i * 10 * MIN;
}

// Two full weeks in season, the turn of the season at both ends, and a week
// in the dead of winter.
const SPANS = [["2026-10-11T00:00:00Z", 14], ["2026-07-29T00:00:00Z", 7],
               ["2026-12-28T00:00:00Z", 7], ["2027-02-07T00:00:00Z", 7]];

test("whatever delivers the ticks, the table has a job for each of them", () => {
  // wrangler.toml as it stands: the four above, or the one that replaces
  // them. Either way no trigger may fire at a time the table calls idle
  // more often than it has to, and the four must still be covered.
  const toml = fs.readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  const live = [...toml.matchAll(/^\s*"([^"]*\*[^"]*)",/gm)].map((m) => m[1].trim());
  assert.ok(live.length >= 1, "no triggers at all");
  const four = live.length === 4 && crons.every((c) => live.includes(c));
  const one = live.length === 1 && live[0] === "*/10 * * * *";
  assert.ok(four || one,
    `wrangler.toml has ${JSON.stringify(live)}: neither the four schedules ` +
    `this file knows nor the single ten-minute trigger`);
});

test("every firing of the four triggers is a tick with the same jobs", () => {
  let seen = 0;
  for (const [start, days] of SPANS) {
    for (const t of ticks(start, days)) {
      const firing = crons.filter((c) => fires(c, t));
      if (!firing.length) continue;
      seen++;
      const at = new Date(t).toISOString();
      const jobs = jobsAt(t);
      assert.ok(jobs.sweep, `${firing} fires at ${at} and the table has no sweep`);
      assert.equal(jobs.deploy, firing.some((c) => DEPLOYED.has(c)),
        `${at}: deploy is ${jobs.deploy}, the triggers firing are ${firing}`);
    }
  }
  assert.ok(seen > 200, `only ${seen} firings checked`);
});

test("every sweep, deploy and refresh is a firing of one of the four", () => {
  // The other direction, and the one that makes a single ten-minute trigger
  // safe: it would deliver 144 ticks a day, and none of the work the four
  // did may turn up at a time nobody chose.
  for (const [start, days] of SPANS) {
    for (const t of ticks(start, days)) {
      const j = jobsAt(t);
      if (!(j.sweep || j.deploy || j.refresh)) continue;
      assert.ok(crons.some((c) => fires(c, t)),
        `${new Date(t).toISOString()} has a job and no trigger fires then`);
    }
  }
});

test("the score channel is asked for exactly when scores.yml schedules itself",
  () => {
    assert.equal(scoreCrons.length, 3, `scores.yml has ${scoreCrons}`);
    let asked = 0;
    for (const [start, days] of SPANS) {
      for (const t of ticks(start, days)) {
        const want = scoreCrons.some((c) => fires(c, t, 0));
        assert.equal(jobsAt(t).scores, want,
          `${new Date(t).toISOString()}: the table says ${jobsAt(t).scores} ` +
          `and scores.yml's own crons say ${want}`);
        if (want) asked++;
      }
    }
    // 72 a day on each weekend day, 27 on each small-hours night, one a day.
    assert.ok(asked > 400, `only ${asked} score ticks in the spans checked`);
  });

test("and an idle tick is still most of them", () => {
  let idle = 0, all = 0;
  for (const t of ticks("2026-10-11T00:00:00Z", 7)) {
    all++;
    if (!anyJob(jobsAt(t))) idle++;
  }
  assert.ok(idle > all / 2, `${idle} of ${all} ticks in a week are idle`);
});

test("out of season the only job is the noon sweep", () => {
  for (const t of ticks("2027-02-07T00:00:00Z", 7)) {
    const d = new Date(t), jobs = jobsAt(t);
    const noon = d.getUTCHours() === 12 && d.getUTCMinutes() === 0;
    assert.deepEqual(jobs, { sweep: noon, deploy: false, refresh: false,
                             scores: false }, `${d.toISOString()}`);
  }
});

test("the refresh is asked for on three Tuesday ticks and no others", () => {
  const asked = [];
  for (const t of ticks("2026-10-11T00:00:00Z", 7)) {
    if (jobsAt(t).refresh) asked.push(new Date(t).toISOString());
  }
  assert.deepEqual(asked, REFRESH_HOURS.map((h) =>
    `2026-10-13T${String(h).padStart(2, "0")}:30:00.000Z`));
  // Two hours apart at least: a retry is for a refresh that failed, and one
  // an hour later could land on a run that is still going.
  for (let i = 1; i < REFRESH_HOURS.length; i++) {
    assert.ok(REFRESH_HOURS[i] - REFRESH_HOURS[i - 1] >= 2);
  }
  // And the first is early enough that the next sweep imports what it
  // published before 09:00 UTC, which is what the pools pages promise.
  assert.ok(REFRESH_HOURS[0] + 1 < 9);
});

// ------------------------------------------------------------ the handler

const SEASON = 2026;
const TUE_0730 = Date.parse("2026-10-13T07:30:00Z");
const HOUR_S = 3600;

/** An env whose origin has no slates to import, and can dispatch. */
function env0() {
  const env = makeEnv();
  env.SEASON = String(SEASON);
  env.RAW_ORIGIN = "https://big12ology.github.io";
  env.GITHUB_REPO = "big12ology/big12ology.com";
  env.GITHUB_DISPATCH_TOKEN = "test-token";
  return env;
}

/** Stub the network; returns what was dispatched to GitHub and asked of it. */
function net({ github = 204 } = {}) {
  const seen = { dispatches: [], urls: [] };
  globalThis.fetch = async (url, init) => {
    const u = String(url);
    seen.urls.push(u);
    if (u.startsWith("https://api.github.com/")) {
      seen.dispatches.push({ url: u, body: JSON.parse(init.body),
                             auth: init.headers.Authorization });
      return new Response(null, { status: github });
    }
    // The scores file has to answer, or the sweep stops where a real one
    // would with the publisher down, and nothing after it is exercised.
    if (u.endsWith("/tiebreaker/site/pickem-scores.json")) {
      return new Response(JSON.stringify({ season: SEASON, games: {} }),
                          { status: 200 });
    }
    if (u.startsWith("https://kuma.example/")) return new Response("ok");
    return new Response("not found", { status: 404 });
  };
  return seen;
}

/** Run a tick as Cloudflare would, at `slot`, with the clock set to match. */
async function tick(env, slot) {
  const realNow = Date.now;
  Date.now = () => slot + 2000;          // two seconds late, like a real one
  try {
    await worker.scheduled({ scheduledTime: slot, cron: "30 7-23 * 8-12 3" },
                           env, { waitUntil() {} });
  } finally {
    Date.now = realNow;
  }
}

const week = (env, n, lockAtS) => seedWeek(env, {
  season: SEASON, week: n, lockAt: lockAtS,
  games: [{ game_id: n * 10 + 1, home: "Utah", away: "BYU", spread_x2: -7,
            kickoff_at: lockAtS }] });

const SAT = Math.floor(Date.parse("2026-10-10T16:00:00Z") / 1000);

test("Tuesday 07:30 with last week locked asks for the refresh", async () => {
  const env = env0();
  week(env, 6, SAT);
  const seen = net();
  await tick(env, TUE_0730);
  assert.equal(seen.dispatches.length, 1, "no refresh was dispatched");
  const d = seen.dispatches[0];
  assert.match(d.url, /\/actions\/workflows\/pages\.yml\/dispatches$/);
  assert.deepEqual(d.body, { ref: "main",
                             inputs: { deploy: "true", refresh: "true" } });
  assert.equal(d.auth, "Bearer test-token");
});

test("once the new week is in, nothing is asked for", async () => {
  const env = env0();
  week(env, 6, SAT);
  week(env, 7, SAT + 7 * 24 * HOUR_S);     // imported, not yet locked
  const seen = net();
  await tick(env, Date.parse("2026-10-13T09:30:00Z"));
  assert.equal(seen.dispatches.length, 0, "asked for a week that is open");
});

test("a Tuesday tick that is not one of the three never asks", async () => {
  const env = env0();
  week(env, 6, SAT);
  const seen = net();
  await tick(env, Date.parse("2026-10-13T08:30:00Z"));
  assert.equal(seen.dispatches.length, 0);
});

test("a week that locked long ago is a bye or the end, and is left alone",
  async () => {
    const env = env0();
    week(env, 6, SAT - 9 * 24 * HOUR_S);
    const seen = net();
    await tick(env, TUE_0730);
    assert.equal(seen.dispatches.length, 0,
      "asked for a refresh nine days after the last week locked");
  });

test("an empty season is not this Worker's to open", async () => {
  const env = env0();
  const seen = net();
  await tick(env, TUE_0730);
  assert.equal(seen.dispatches.length, 0);
  assert.equal(await weekNeedsOpening(env, SEASON, SAT), false);
});

test("a refused dispatch does not stop the sweep under it", async () => {
  const env = env0();
  week(env, 6, SAT);
  env.HEARTBEAT_URL = "https://kuma.example/api/push/TOKEN";
  const seen = net({ github: 401 });
  await tick(env, TUE_0730);
  assert.equal(seen.dispatches.length, 1);
  assert.ok(seen.urls.some((u) => u.startsWith("https://kuma.example/")),
    "the run did not reach its heartbeat after GitHub refused the dispatch");
});

test("a weekend tick dispatches a plain deploy, and never a refresh",
  async () => {
    const env = env0();
    week(env, 6, SAT - 6 * HOUR_S);
    const seen = net();
    await tick(env, Date.parse("2026-10-10T22:30:00Z"));
    assert.deepEqual(seen.dispatches.map((d) => d.body.inputs),
                     [{ deploy: "true" }]);
  });

test("a score tick dispatches scores.yml, with no inputs, and nothing else",
  async () => {
    const env = env0();
    week(env, 6, SAT - 6 * HOUR_S);
    const seen = net();
    await tick(env, Date.parse("2026-10-10T22:20:00Z"));
    assert.equal(seen.dispatches.length, 1);
    assert.match(seen.dispatches[0].url,
                 /\/actions\/workflows\/scores\.yml\/dispatches$/);
    // scores.yml declares no inputs, and GitHub refuses a dispatch that
    // sends one to a workflow that did not ask for it.
    assert.deepEqual(seen.dispatches[0].body, { ref: "main" });
    assert.equal(seen.urls.length, 1, "a score tick ran the sweep as well");
    const row = env.raw.prepare(
      "SELECT detail FROM audit_log WHERE action = 'cron.tick'").get();
    assert.deepEqual(JSON.parse(row.detail).did, ["scores:dispatched"]);
  });

test("an idle tick costs nothing: no fetch, no read, no write", async () => {
  const env = env0();
  week(env, 6, SAT);
  const seen = net();
  let touched = 0;
  const real = env.DB.prepare.bind(env.DB);
  env.DB.prepare = (sql) => { touched++; return real(sql); };
  await tick(env, Date.parse("2026-10-14T03:10:00Z"));
  assert.deepEqual(jobsAt(Date.parse("2026-10-14T03:10:00Z")),
    { sweep: false, deploy: false, refresh: false, scores: false });
  assert.equal(seen.urls.length, 0, `an idle tick fetched ${seen.urls}`);
  assert.equal(touched, 0, "an idle tick touched the database");
});

test("a tick with a job is written down, and health reads it back",
  async () => {
    const env = env0();
    week(env, 6, SAT);
    net();
    const before = await (await worker.fetch(
      new Request("https://big12ology.com/api/health"), env, {})).json();
    assert.equal(before.last_tick_at, null);
    assert.equal(before.last_tick, null);

    await tick(env, TUE_0730);
    const after = await (await worker.fetch(
      new Request("https://big12ology.com/api/health"), env, {})).json();
    assert.equal(after.last_tick.slot, "2026-10-13T07:30:00.000Z");
    assert.equal(after.last_tick.drift_s, 2);
    assert.ok(after.last_tick.did.includes("refresh:dispatched"),
      `the tick's account of itself was ${JSON.stringify(after.last_tick.did)}`);
    assert.equal(typeof after.last_tick_at, "number");
  });

test("a run with no slot is a sweep, and leaves no tick behind", async () => {
  // What the rest of the suite and `wrangler dev --test-scheduled` send. It
  // has to keep sweeping, and it has no slot to decide a dispatch from.
  const env = env0();
  week(env, 6, SAT);
  const seen = net();
  await worker.scheduled({}, env, { waitUntil() {} });
  assert.equal(seen.dispatches.length, 0);
  assert.ok(seen.urls.length > 0, "the sweep did not run");
  const n = env.raw.prepare(
    "SELECT COUNT(*) AS n FROM audit_log WHERE action = 'cron.tick'").get().n;
  assert.equal(n, 0);
});

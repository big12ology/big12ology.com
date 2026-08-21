// The cron strings, against Cloudflare's calendar rather than everyone else's.
//
// Cloudflare numbers the week 1 = Sunday to 7 = Saturday, following Quartz.
// Ordinary cron numbers it 0 = Sunday to 6 = Saturday. Every schedule in this
// project was first written in the ordinary convention, which made all three
// fire a day early: the Tuesday import ran on Monday, ahead of the publish it
// exists to read.
//
// Only one of the three failed the deploy, and only because it contained a 0,
// which is out of range. The other two were valid syntax with the wrong
// meaning — a whole season scored a day early with nothing to say so. That is
// the failure this file exists to prevent, because the natural instinct when
// reading "1,7" is to "fix" it back to "0,6".

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const toml = fs.readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
const crons = [...toml.matchAll(/^\s*"([^"]*\*[^"]*)",/gm)].map((m) => m[1]);

/** 1 = Sunday, per Cloudflare. */
const DAY = ["", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function daysOf(expr) {
  const dow = expr.trim().split(/\s+/)[4];
  if (dow === "*") return [1, 2, 3, 4, 5, 6, 7];
  return dow.split(",").flatMap((part) => {
    const m = part.match(/^(\d+)-(\d+)$/);
    if (!m) return [Number(part)];
    const out = [];
    for (let i = Number(m[1]); i <= Number(m[2]); i++) out.push(i);
    return out;
  });
}

test("every cron parses and has five fields", () => {
  assert.equal(crons.length, 4, "expected four schedules");
  for (const c of crons) {
    assert.equal(c.trim().split(/\s+/).length, 5, `not five fields: ${c}`);
  }
});

test("no day-of-week is 0 — Cloudflare's week starts at 1", () => {
  // The one that failed the deploy. It fails here first now.
  for (const c of crons) {
    for (const d of daysOf(c)) {
      assert.ok(d >= 1 && d <= 7,
        `${c} uses day ${d}; Cloudflare accepts 1 (Sunday) to 7 (Saturday)`);
    }
  }
});

test("the schedules land on the days the comments claim", () => {
  // Written out rather than computed, so the assertion is a statement of
  // intent that a future edit has to disagree with out loud.
  const want = {
    "30 * * 8-12 1,7": ["Sun", "Sat"],          // the weekend score sweep
    "30 0-8/2 * 8-12 2,6,7": ["Mon", "Fri", "Sat"], // US night finals, UTC
    "30 7-12 * 8-12 3": ["Tue"],               // the slate import sweep
    "0 12 * * *": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
  };
  for (const c of crons) {
    const named = daysOf(c).map((d) => DAY[d]).sort();
    assert.ok(want[c.trim()], `unrecognised schedule ${c} — update this test`);
    assert.deepEqual(named, [...want[c.trim()]].sort(),
      `${c} runs on ${named.join(", ")}`);
  }
});

test("the score sweeps run after the publisher, not alongside it", () => {
  // Same failure as the Tuesday import, an hour at a time instead of a day.
  // This Worker holds no CFBD key: it grades from pickem-scores.json, which
  // pages.yml publishes on its own hourly cron at :00. A sweep at :00 reads
  // whatever the publisher wrote an HOUR ago, so a result could sit two
  // hours behind the television. The minute is the whole fix, and it is
  // worth an assertion because ":00 like everything else" is exactly what a
  // future tidy-up would restore.
  const sweeps = crons.filter((c) => daysOf(c).length > 1
                                  && c.trim() !== "0 12 * * *");
  assert.ok(sweeps.length >= 2, "expected the weekend and night sweeps");
  for (const c of sweeps) {
    const minute = Number(c.trim().split(/\s+/)[0]);
    assert.ok(minute > 0 && minute < 60,
      `${c} sweeps at :${minute} — it must run after pages.yml's :00 publish`);
  }
});

test("the import sweeps hourly behind the publish and beats the promise", () => {
  // pages.yml publishes the slate at 07:00 UTC Tuesday, GitHub's scheduler
  // drifts that by up to ~75 observed minutes, and the pages promise the
  // week is rolling by 09:00 UTC (5am ET). One import after the publish was
  // a bet on the drift, and losing it cost a day — the next walk is
  // Wednesday's heartbeat. So the import is a Tuesday-morning SWEEP: it
  // must start at or after the publish hour, repeat hourly so a drifted
  // publish is picked up within the hour, run past noon as backstop, and
  // fire at :30 so each pass reads a publish that finished, not one mid-run.
  const imp = crons.find((c) => daysOf(c).length === 1
                              && c.trim() !== "0 12 * * *");
  assert.ok(imp, "no single-day schedule found");
  assert.deepEqual(daysOf(imp).map((d) => DAY[d]), ["Tue"]);
  const [minute, hours] = imp.trim().split(/\s+/);
  assert.equal(Number(minute), 30, `sweeps at :${minute}, not after the :00 publish`);
  const m = hours.match(/^(\d+)-(\d+)$/);
  assert.ok(m, `${hours} is not an hourly range — one import is a bet on drift`);
  assert.ok(Number(m[1]) >= 7, `first sweep ${m[1]}:30 runs before the 07:00 publish`);
  assert.ok(Number(m[1]) <= 8, `first sweep ${m[1]}:30 misses the 09:00 UTC live-by`);
  assert.ok(Number(m[2]) >= 12, `last sweep ${m[2]}:30 leaves the afternoon uncovered`);
});

test("only the heartbeat runs outside the season", () => {
  // The three working schedules are August to December. The fourth has to run
  // all year, because it pings a dead man's switch — and a switch that stops
  // being pinged every January pages somebody daily for seven months, after
  // which it gets muted, and a muted alarm is not an alarm.
  const allYear = crons.filter((c) => c.trim().split(/\s+/)[3] === "*");
  assert.equal(allYear.length, 1,
    `${allYear.length} schedules run all year; only the heartbeat should`);
  assert.equal(allYear[0].trim(), "0 12 * * *");
  for (const c of crons) {
    assert.ok(c.trim().split(/\s+/)[3] === "8-12" || c.trim() === "0 12 * * *",
      `${c} runs outside August–December and is not the heartbeat`);
  }
});

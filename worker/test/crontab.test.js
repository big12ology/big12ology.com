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
  assert.equal(crons.length, 3, "expected three schedules");
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
    "0 * * 8-12 1,7": ["Sun", "Sat"],          // the weekend score sweep
    "0 0-8/2 * 8-12 2,6,7": ["Mon", "Fri", "Sat"], // US night finals, UTC
    "0 13 * 8-12 3": ["Tue"],                  // the slate import
  };
  for (const c of crons) {
    const named = daysOf(c).map((d) => DAY[d]).sort();
    assert.ok(want[c.trim()], `unrecognised schedule ${c} — update this test`);
    assert.deepEqual(named, [...want[c.trim()]].sort(),
      `${c} runs on ${named.join(", ")}`);
  }
});

test("the import runs after the publish, not before it", () => {
  // pages.yml refreshes and publishes the slate at 12:00 UTC on Tuesday. An
  // import that runs earlier in the day reads last week's file, which is
  // exactly what the ordinary-cron numbering caused.
  const imp = crons.find((c) => daysOf(c).length === 1);
  assert.ok(imp, "no single-day schedule found");
  assert.deepEqual(daysOf(imp).map((d) => DAY[d]), ["Tue"]);
  assert.ok(Number(imp.trim().split(/\s+/)[1]) > 12,
    `the slate import runs at ${imp.trim().split(/\s+/)[1]}:00 UTC, at or `
    + `before the 12:00 publish it is meant to read`);
});

test("nothing is scheduled outside the season", () => {
  for (const c of crons) {
    assert.equal(c.trim().split(/\s+/)[3], "8-12",
      `${c} runs outside August–December`);
  }
});

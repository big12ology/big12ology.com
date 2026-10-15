// The trigger, and that it stays as plain as it is.
//
// There used to be four cron strings here, each a statement about days and
// hours in Cloudflare's calendar, which numbers the week 1 = Sunday where
// ordinary cron says 0. All of them were first written the ordinary way and
// fired a day early: the Tuesday import ran on Monday, ahead of the publish it
// exists to read. This file was the guard against "fixing" 1,7 back to 0,6.
//
// The decision moved into src/schedule.js, which works in JavaScript's days
// and is tested for every tick of the week in schedule.test.js. What is left
// in wrangler.toml is one trigger that only delivers ticks, and what this
// file guards now is that it stays that way: a second trigger, or a
// day-of-week field on this one, is the old mistake being let back in.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const toml = fs.readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
const crons = [...toml.matchAll(/^\s*"([^"]*\*[^"]*)",/gm)].map((m) => m[1].trim());

test("there is one trigger", () => {
  assert.deepEqual(crons, ["*/10 * * * *"],
    "schedule.js decides what a tick does; add a job there, not a trigger here");
});

test("it runs every ten minutes, all day, all year", () => {
  const [minute, hour, dom, month, dow] = crons[0].split(/\s+/);
  // Ten minutes is the grain schedule.js is written to: jobs sit on :00,
  // :20, :30 and :40, and a coarser tick would skip some of them.
  assert.equal(minute, "*/10");
  assert.equal(hour, "*");
  assert.equal(dom, "*");
  // All year, because the noon tick pings a dead man's switch, and a switch
  // that stops being pinged every January gets muted.
  assert.equal(month, "*");
  // And no day-of-week, which is where 1 = Sunday used to bite.
  assert.equal(dow, "*");
});

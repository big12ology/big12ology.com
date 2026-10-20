// The bracket picker's two rules, held to the Worker's.
//
// The page offers, for each game, the two teams that can be in it given the
// player's earlier picks (bkSides), and clears whatever a change orphans
// (bkPrune). The Worker refuses any bracket that breaks the same rule
// (src/bracket.js invalid). This clicks at random through the real 2025-26
// bracket, thousands of times, and after every click asks the Worker whether
// the page's bracket is one it would accept: the two must never disagree.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { installDOM } from "./helpers/dom.js";
import { invalid } from "../src/bracket.js";

const APP = new URL("../../tiebreaker/site_pools/app.js", import.meta.url);
const GAUGE = new URL("../../tiebreaker/site/gauge.js", import.meta.url);
const REAL = JSON.parse(fs.readFileSync(
  new URL("./fixtures/hoops-2026/bracket.json", import.meta.url), "utf8"));

installDOM();
vm.runInThisContext(fs.readFileSync(GAUGE, "utf8"), { filename: "gauge.js" });
vm.runInThisContext(fs.readFileSync(APP, "utf8"), { filename: "app.js" });
const P = globalThis.window.B12POOLS;

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

test("every bracket the page can make is one the Worker accepts", () => {
  const rand = rng(1234);
  let clicks = 0, complete = 0;
  for (let run = 0; run < 200; run++) {
    const picks = REAL.games.map(() => null);
    for (let i = 0; i < 40; i++) {
      const gi = Math.floor(rand() * REAL.games.length);
      const sd = P.bkSides(REAL, picks, gi);
      const t = sd[rand() < 0.5 ? 0 : 1];
      if (t == null) continue;                 // the page shows this disabled
      picks[gi] = picks[gi] === t ? null : t;  // a second click un-picks
      P.bkPrune(REAL, picks);
      clicks++;
      assert.equal(invalid(REAL, picks), null,
                   `run ${run}, click ${i}: ${JSON.stringify(picks)}`);
    }
    // Then finish it the way a player would, game by game in order.
    for (let gi = 0; gi < REAL.games.length; gi++) {
      if (picks[gi] != null) continue;
      const sd = P.bkSides(REAL, picks, gi).filter((t) => t != null);
      picks[gi] = sd[Math.floor(rand() * sd.length)];
      P.bkPrune(REAL, picks);
    }
    assert.equal(invalid(REAL, picks), null, `run ${run} finished`);
    if (picks.every((p) => p != null)) complete++;
  }
  assert.ok(clicks > 4000, `only ${clicks} clicks`);
  assert.equal(complete, 200, "every run ends in a whole bracket");
});

test("changing an early pick clears what depended on it, and only that", () => {
  const picks = REAL.games.map((_, gi) => REAL.results[String(gi)]);
  // Game 0's winner went on to game 4 (the 8 seed's game). Flip game 0.
  const [a, b] = P.bkSides(REAL, picks, 0);
  picks[0] = picks[0] === a ? b : a;
  P.bkPrune(REAL, picks);
  const real = REAL.games.map((_, gi) => REAL.results[String(gi)]);
  // Everything not downstream of game 0 is as it was.
  for (const gi of [1, 2, 3, 5, 6, 7, 9, 10, 11]) {
    assert.equal(picks[gi], real[gi], `game ${gi} should not have moved`);
  }
  assert.equal(invalid(REAL, picks), null);
});

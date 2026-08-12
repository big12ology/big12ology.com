import test from "node:test";
import assert from "node:assert/strict";
import { ats, outcome, displaySpread, pct } from "../src/ats.js";

// Read these as sentences, because a sign error here is invisible in
// production: it grades every game backwards and nothing crashes.
//
// spreadX2 is the HOME spread doubled. -14 means the home team is laying 7.

test("the favorite covers only by more than the number", () => {
  // Home laying 7 (spreadX2 -14), wins by 10 -> covered.
  assert.equal(ats(31, 21, -14), "home");
  // Wins by 7 exactly -> push, not a win.
  assert.equal(ats(28, 21, -14), "push");
  // Wins by 3 -> won the game, lost the bet.
  assert.equal(ats(24, 21, -14), "away");
});

test("the sign follows the home team, both directions", () => {
  // Home GETTING 7 (spreadX2 +14): losing by 3 still covers.
  assert.equal(ats(21, 24, 14), "home");
  assert.equal(ats(21, 28, 14), "push");
  assert.equal(ats(21, 31, 14), "away");
});

test("a pick'em is decided by the winner", () => {
  assert.equal(ats(21, 20, 0), "home");
  assert.equal(ats(20, 21, 0), "away");
  assert.equal(ats(21, 21, 0), "push");
});

test("a half point cannot push", () => {
  // -13 is home laying 6.5. No integer margin lands on it.
  for (let margin = -40; margin <= 40; margin++) {
    assert.notEqual(ats(20 + margin, 20, -13), "push",
      `margin ${margin} pushed against a half-point line`);
  }
});

test("a whole number pushes exactly once", () => {
  const pushes = [];
  for (let margin = -40; margin <= 40; margin++) {
    if (ats(20 + margin, 20, -14) === "push") pushes.push(margin);
  }
  assert.deepEqual(pushes, [7]);
});

test("no result yet is void, not a loss", () => {
  assert.equal(ats(null, null, -14), "void");
  assert.equal(ats(21, null, -14), "void");
  assert.equal(ats(null, 21, -14), "void");
  assert.equal(ats(0, 0, -14), "away");   // 0-0 is a played game, not a missing one
});

test("a non-integer spread is a bug, not a rounding problem", () => {
  // The doubling exists so this cannot happen silently. -6.5 here would mean
  // someone passed the human-readable line instead of the stored one.
  assert.throws(() => ats(21, 14, -6.5), TypeError);
});

test("a push is its own outcome", () => {
  assert.equal(outcome("home", "home"), "win");
  assert.equal(outcome("home", "away"), "loss");
  assert.equal(outcome("away", "away"), "win");
  assert.equal(outcome("home", "push"), "push");
  assert.equal(outcome("away", "push"), "push");
  assert.equal(outcome("home", "void"), "void");
});

test("each side sees its own number", () => {
  assert.equal(displaySpread(-13, "home"), -6.5);
  assert.equal(displaySpread(-13, "away"), 6.5);
  assert.equal(displaySpread(0, "home"), 0);
  assert.equal(displaySpread(14, "home"), 7);
});

test("pushes and voids leave the percentage alone", () => {
  assert.equal(pct({ w: 6, l: 4 }), 0.6);
  // Ten decided games and three pushes is still 60%, not 46%.
  assert.equal(pct({ w: 6, l: 4, p: 3, v: 1 }), 0.6);
  // Nothing decided is unknown, not zero.
  assert.equal(pct({ w: 0, l: 0 }), null);
  assert.equal(pct({ w: 0, l: 3 }), 0);
});

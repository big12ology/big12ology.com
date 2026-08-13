// Nothing from the feed becomes markup.
//
// Opponent names, venues, cities and conference labels arrive from CFBD
// through scripts/fetch_attendance.py and are committed by a scheduled
// workflow. No step in that pipeline escapes anything, and none should — the
// escaping belongs at the point of rendering, and this is the test that says
// so out loud.
//
// The bug this pins was not hypothetical: app.js escaped `info.opponent` into
// a `title=` attribute and interpolated the SAME VALUE raw into the cell text
// three lines below it, and charts.js had sixteen innerHTML assignments and no
// escape function in the file at all. So the assertion is not "the helper
// exists" — it is "feed a hostile name through the real renderers and find no
// live markup in what they produced".
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { esc, escUrl } from "../site/html.js";

// The payload, and why this shape: a quote to close an attribute, a bracket to
// open a tag, and an event handler that needs neither if the first one lands.
const NASTY = `<img src=x onerror=alert(1)>" onmouseover="alert(2)`;

/**
 * Assert that a payload came out inert.
 *
 * NOT a search for the word "onerror". Escaped output still contains it —
 * `&lt;img src=x onerror=alert(1)&gt;` is a string, not a tag — so asserting on
 * the keyword fails against correct code and would be "fixed" by weakening the
 * escape. What actually decides this is whether the payload's own `<` opened a
 * tag and whether its `"` closed an attribute, so those are what is checked.
 *
 * `<img src=x` rather than `<img`: charts.js legitimately emits
 * `<img class="team-logo" …` for a real logo, and a check that cannot tell the
 * two apart is a check that has to be disabled the first time it fires.
 *
 * The last assertion is the one that keeps the rest honest: a renderer that
 * silently dropped the field would satisfy every negative check above while
 * proving nothing, so the escaped form has to be present.
 */
function assertInert(html, where) {
  assert.ok(!html.includes("<img src=x"), `an unescaped tag reached ${where}`);
  assert.ok(!html.includes('" onmouseover='), `an attribute break reached ${where}`);
  assert.ok(!html.includes("onerror=alert(1)>"), `an unescaped tag close reached ${where}`);
  assert.ok(html.includes("&lt;img src=x"),
    `the payload never reached ${where} at all — the test proved nothing`);
}

test("esc neutralizes brackets and both quote characters", () => {
  const out = esc(NASTY);
  assert.ok(!out.includes("<"), "a < survived");
  assert.ok(!out.includes(">"), "a > survived");
  assert.ok(!out.includes('"'), "a double quote survived");
  assert.ok(!out.includes("'"), "a single quote survived");
  // Ampersand first, or the entities this produces get double-escaped.
  assert.equal(esc("a & b"), "a &amp; b");
  assert.equal(esc(null), "");
  assert.equal(esc(undefined), "");
});

test("escUrl drops schemes that are not http(s), keeps relative paths", () => {
  assert.equal(escUrl("javascript:alert(1)"), "");
  assert.equal(escUrl("  JaVaScRiPt:alert(1)"), "");
  assert.equal(escUrl("data:text/html,<script>alert(1)</script>"), "");
  assert.equal(escUrl("assets/logos/ksu.svg"), "assets/logos/ksu.svg");
  assert.equal(escUrl("/schedule/game/x.html"), "/schedule/game/x.html");
  assert.equal(escUrl("https://example.com/a.svg"), "https://example.com/a.svg");
  // Allowed through, but still escaped — the scheme check is not the whole job.
  assert.ok(!escUrl('https://e.com/a.svg" onload="x').includes('"'));
});

// ---- the renderers themselves ---------------------------------------------

function stubEl() {
  return {
    children: [], className: "", style: {}, dataset: {},
    classList: { add() {}, toggle() {} },
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); },
    addEventListener() {}, setAttribute() {},
    set innerHTML(v) { this._html = String(v); }, get innerHTML() { return this._html || ""; },
    set textContent(v) { this._t = String(v); }, get textContent() { return this._t || ""; },
    querySelector: () => null, querySelectorAll: () => [],
  };
}
global.window = { matchMedia: () => ({ matches: false }), scrollX: 0, scrollY: 0 };
global.document = {
  createElement: stubEl, createElementNS: stubEl,
  createTextNode: (t) => ({ nodeValue: t }),
  querySelector: () => null, body: stubEl(),
  documentElement: { clientWidth: 1000 },
};

/** Every innerHTML string this render tree produced, however deeply nested. */
function collectHTML(node, out = []) {
  if (!node || typeof node !== "object") return out;
  if (node._html) out.push(node._html);
  for (const c of node.children || []) collectHTML(c, out);
  return out;
}

const { gameTooltipHTML } = await import("../site/gametip.js");
const charts = await import("../site/charts.js");

test("the game tooltip escapes opponent, venue and city", () => {
  const html = gameTooltipHTML({
    game: {
      opponent: NASTY, venue: NASTY, city: NASTY, state: "TX",
      role: "home", date: "2025-09-06", time: "14:00",
      attendance: 50000, attendanceSource: NASTY,
      espnId: '1" onload="alert(3)',
    },
    weekLabel: NASTY,
    prefix: NASTY,
  });
  assertInert(html, "the tooltip");
  // The espnId lands inside an href, so it is encoded rather than escaped.
  assert.ok(!html.includes('gameId/1" onload'), "the espnId broke out of its href");
  // The card still says what it is for — escaping is not blanking.
  assert.ok(html.includes("Attendance"), "the tooltip lost its content");
});

test("a hostile opponent name renders inert through the chart tree", () => {
  const teams = JSON.parse(
    fs.readFileSync(new URL("../data/teams.json", import.meta.url)));
  const idx = JSON.parse(
    fs.readFileSync(new URL("../data/seasons/index.json", import.meta.url)));
  const seasons = {};
  for (const y of idx.seasons) {
    seasons[y] = JSON.parse(
      fs.readFileSync(new URL(`../data/seasons/${y}.json`, import.meta.url)));
  }
  const year = [...idx.seasons].reverse()
    .find((y) => seasons[y].games.some((g) => g.attendance != null)) ?? idx.default;

  // Poison the copy, not the archive: one played home game gets a name and a
  // venue no feed would send, which is exactly the point.
  const poisoned = structuredClone(seasons);
  let hit = 0;
  for (const g of poisoned[year].games) {
    if (g.attendance == null) continue;
    g.opponent = NASTY;
    g.venue = NASTY;
    hit += 1;
  }
  assert.ok(hit > 0, "no played home game to poison — fixture assumption broke");

  const root = stubEl();
  charts.renderSeasonCharts(root, teams, poisoned, year);
  charts.renderAllTimeCharts(root, teams, poisoned);

  const all = collectHTML(root).join("\n");
  assert.ok(all.length > 0, "the renderers produced no markup to check");
  assertInert(all, "innerHTML");
});

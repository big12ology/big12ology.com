// Smoke test: every chart renderer must run against real season data.
// Twice now a bad string-replace has left a call site pointing at a
// function that no longer existed, which silently blanked every chart on
// the page. This catches that class of failure in CI.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

// minimal DOM good enough for the SVG builders
function stubEl() {
  return {
    children: [], className: "", style: {},
    classList: { add() {}, toggle() {} },
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); },
    addEventListener() {}, setAttribute() {},
    set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html || ""; },
    set textContent(v) { this._t = v; }, get textContent() { return this._t || ""; },
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

const charts = await import("../site/charts.js");
const teams = JSON.parse(fs.readFileSync(new URL("../data/teams.json", import.meta.url)));
const idx = JSON.parse(fs.readFileSync(new URL("../data/seasons/index.json", import.meta.url)));
const seasons = {};
for (const y of idx.seasons) {
  seasons[y] = JSON.parse(fs.readFileSync(new URL(`../data/seasons/${y}.json`, import.meta.url)));
}
const latestPlayed = [...idx.seasons].reverse()
  .find((y) => seasons[y].games.some((g) => g.attendance != null)) ?? idx.default;

test("season charts render", () => {
  const root = stubEl();
  charts.renderSeasonCharts(root, teams, seasons, latestPlayed);
  assert.ok(root.children.length >= 3, "expected season chart cards");
});

test("all-time charts render", () => {
  const root = stubEl();
  charts.renderAllTimeCharts(root, teams, seasons);
  assert.ok(root.children.length >= 4, "expected all-time cards");
});

test("team charts render for a selection", () => {
  const root = stubEl();
  const sel = new Set(["Kansas State", "Texas Tech"]);
  charts.renderTeamCharts(root, teams, seasons, latestPlayed, sel);
  assert.ok(root.children.length >= 3, "expected team comparison cards");
});

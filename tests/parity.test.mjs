// Parity test: the site's stats module must reproduce every number the audited
// 2025 Google Sheet produced. Fixture holds the sheet's own cached values.
// Run: node --test tests/
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { seasonSummary } from "../site/stats.js";

const read = (p) => JSON.parse(readFileSync(new URL(p, import.meta.url)));
const { teams } = read("../data/teams.json");
const { games } = read("../data/seasons/2025.json");
const fixture = read("./fixture-2025-sheet.json");

const summary = seasonSummary(teams, games);

test("per-team season totals match the sheet", () => {
  for (const row of summary.rows) {
    const expected = fixture.teams[row.team];
    assert.equal(row.games, expected.games, `${row.team} games`);
    assert.equal(row.total, expected.total, `${row.team} total`);
    assert.ok(Math.abs(row.pct - expected.pct) < 1e-9, `${row.team} pct`);
  }
});

test("weekly conference totals match the sheet", () => {
  for (const wk of summary.weeks) {
    const expected = fixture.weekly[wk.week];
    assert.equal(wk.attendance, expected.attendance, `week ${wk.week} attendance`);
    assert.equal(wk.capacity, expected.capacity, `week ${wk.week} capacity`);
    assert.equal(wk.games, expected.games, `week ${wk.week} games`);
  }
});

test("season rollup matches the sheet", () => {
  assert.equal(summary.totals.attendance, fixture.season.attendance);
  assert.equal(summary.totals.capacity, fixture.season.capacity);
  assert.equal(summary.totals.games, fixture.season.games);
  assert.ok(Math.abs(summary.totals.pct - fixture.season.pct) < 1e-9);
});

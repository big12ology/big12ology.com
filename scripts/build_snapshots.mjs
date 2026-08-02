#!/usr/bin/env node
// Build cumulative weekly snapshots for a season: for each week that has
// reported attendance, compute the standings as of that week and write
// data/snapshots/<year>/week-NN.json. Reuses the site's stats module so the
// snapshot math and the live site math can never diverge.
//
// Usage: node scripts/build_snapshots.mjs 2025
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { seasonSummary, teamsForSeason } from "../site/stats.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const year = process.argv[2] ?? "2025";

const teamsData = JSON.parse(readFileSync(join(ROOT, "data/teams.json")));
const teams = teamsForSeason(teamsData, Number(year));
const season = JSON.parse(readFileSync(join(ROOT, `data/seasons/${year}.json`)));
const reported = season.games.filter((g) => !g.role && g.attendance != null);
const numWeeks = season.weekLabels.length;

const outDir = join(ROOT, "data/snapshots", String(year));
mkdirSync(outDir, { recursive: true });

const weeksWithData = [...new Set(reported.map((g) => g.week))].sort((a, b) => a - b);
const index = [];
for (const week of weeksWithData) {
  const through = reported.filter((g) => g.week <= week);
  const summary = seasonSummary(teams, through, numWeeks);
  const file = `week-${String(week).padStart(2, "0")}.json`;
  writeFileSync(
    join(outDir, file),
    JSON.stringify({ season: Number(year), throughWeek: week, ...summary }, null, 2) + "\n"
  );
  index.push({ week, file, games: through.length });
}
writeFileSync(join(outDir, "index.json"), JSON.stringify(index, null, 2) + "\n");
console.log(`${year}: wrote ${index.length} snapshots to data/snapshots/${year}/`);

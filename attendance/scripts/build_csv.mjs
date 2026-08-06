// One flat CSV of every home game we have attendance for, all seasons.
//
//     node scripts/build_csv.mjs
//
// Why: the page renders client-side, so a fetcher that does not run JS —
// social scrapers, most LLM tools — sees a 4KB shell and no data. The
// season JSON has always been served and is perfectly readable, but nothing
// advertised it and there was no flat form. This is the flat form.
//
// The numbers come from site/stats.js, the same module the page computes
// with. Percent-of-capacity in particular is not recomputed here: a game's
// own capacity beating the team's stadium capacity is the kind of detail
// that drifts the moment it exists in two places.
import { readFileSync, writeFileSync } from "node:fs";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { teamsForSeason, teamSeason } from "../site/stats.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const teamsData = JSON.parse(readFileSync(join(ROOT, "data/teams.json")));

const seasons = readdirSync(join(ROOT, "data/seasons"))
  .filter((f) => /^\d{4}\.json$/.test(f))
  .map((f) => Number(f.slice(0, 4)))
  .sort((a, b) => a - b);

const q = (v) =>
  v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replaceAll('"', '""')}"` : String(v);

// conference_game is Big 12 conference play specifically, not "both teams are
// in the Big 12 now" — the archive starts in 2012, when most of the sixteen
// were in the Pac-12 and the AAC, and even since 2024 the sixteen have met
// out of conference most years. Without the column a reader cannot tell a
// September cupcake from a league game, which is most of what fill means.
const rows = [[
  "season", "team", "week", "date", "opponent", "conference_game", "venue",
  "attendance", "capacity", "pct_of_capacity", "capacity_estimated",
].join(",")];

let n = 0;
for (const season of seasons) {
  const data = JSON.parse(readFileSync(join(ROOT, `data/seasons/${season}.json`)));
  const teams = teamsForSeason(teamsData, season);
  for (const team of teams) {
    const s = teamSeason(team, data.games);
    for (const w of s.weeks) {
      // Join back to the source row for the things the summary drops.
      const g = data.games.find(
        (x) => x.team === team.team && x.week === w.week && !x.role
      );
      // Capacity read, not reconstructed from attendance/pct — same rule
      // stats.js uses: a game's own capacity beats the team's stadium.
      rows.push([
        season, team.team, w.week, g?.date, g?.opponent,
        // Empty, not false, when the source row is missing: an absent join is
        // not evidence of a non-conference game.
        g ? String(!!g.conferenceGame) : null,
        w.venue ?? s.stadium,
        w.attendance, g?.capacity ?? team.capacity,
        w.pct.toFixed(4),
        s.capacityEstimate ? "true" : "false",
      ].map(q).join(","));
      n++;
    }
  }
}

writeFileSync(join(ROOT, "data/attendance.csv"), rows.join("\n") + "\n");
console.log(`wrote data/attendance.csv — ${n} games, ${seasons.length} seasons `
  + `(${seasons[0]}–${seasons[seasons.length - 1]})`);

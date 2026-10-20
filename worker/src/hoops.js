// The basketball pools' sweep: import, correct, finals, grade, for one league.
//
// The football sweep in index.js, on basketball's terms. Slates come from
// basketball/pickem/<league>/<season>/week-NN.json (bb_pickem.py), numbered
// from 1 across the conference season; finals from the basketball build's
// scores file and ESPN's Big 12 scoreboard; the grading is football's
// scoreAll, which locks game by game for these leagues since 0015.
//
// Run on schedule.js's `hoops` ticks, the nights basketball games finish, and
// on the noon heartbeat. Never fatal to anything else: a league with nothing
// published costs two 404s and stops.

import { importWeek } from "./slate.js";
import { fetchScores, scoreAll } from "./scoring.js";
import { sweepEspn, sweepKickoffs } from "./espn.js";
import { seasonOf } from "./league.js";

export const HOOPS_LEAGUES = ["mbb", "wbb"];

// A conference season is about ten weeks plus Kansas City; 20 is room enough.
const MAX_WEEK = 20;

export async function sweepLeague(env, league, now = Math.floor(Date.now() / 1000)) {
  const season = seasonOf(env, league, now * 1000);
  let misses = 0, imported = 0;
  // From 1, with one missing week tolerated, as the football walk does.
  for (let w = 1; w <= MAX_WEEK && misses < 2; w++) {
    const r = await importWeek(env, season, w, league);
    if (!r.ok) {
      misses++;
      if (!/^fetch_404$/.test(r.reason)) {
        console.log(`import ${league} ${season} w${w}: ${r.reason}`);
      }
      continue;
    }
    misses = 0;
    imported++;
    if (!r.unchanged) console.log(`import ${league} ${season} w${w}: ${r.games} games`);
  }
  if (!imported) return { league, season, imported: 0, changed: 0 };

  try {
    const k = await sweepKickoffs(env, season, now, league);
    if (k.moved || k.far) console.log(`kickoffs ${league}: ${JSON.stringify(k)}`);
  } catch (e) {
    console.log(`kickoffs ${league}: ${e && (e.message || e)}`);
  }
  // The build's scores file is the record; until it is published, ESPN's
  // finals fill it, exactly as for football. An absent file is an empty one.
  let scores;
  try {
    scores = await fetchScores(env, season, league);
  } catch (e) {
    scores = { season, games: {} };
  }
  try {
    const e = await sweepEspn(env, season, scores, now, league);
    if (e.added) console.log(`espn ${league}: ${JSON.stringify(e)}`);
  } catch (e) {
    console.log(`espn ${league}: ${e && (e.message || e)}`);
  }
  const report = await scoreAll(env, season, now, { scores, league });
  const changed = report.filter((r) => r.changed).length;
  return { league, season, imported, changed };
}

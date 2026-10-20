// What a tick is for.
//
// The Worker used to be told what to do by which cron woke it: four schedules
// in wrangler.toml, each of them a statement about days and hours written in
// Cloudflare's own calendar (1 = Sunday), and one env var listing the two
// that should also dispatch a deploy. The Tuesday import ran on Monday for a
// while because of that calendar, and nothing could test it, because a cron
// string is not code.
//
// So the decision lives here instead. jobsAt() takes the time a tick was
// scheduled for and says which jobs belong to it, and that is a pure function
// of the clock: testable for every ten minutes of any week, and indifferent
// to how many triggers deliver the ticks. Today the four old schedules still
// do. They can be replaced by one trigger every ten minutes without touching
// this file, and test/schedule.test.js holds the two arrangements to the same
// answers.
//
// TEN MINUTES is the grain, and nothing here needs finer. Jobs sit on :30
// because they always have: the publisher writes at :00 and a sweep half an
// hour behind it reads a publish that finished. A tick with no job returns
// before it reads or writes anything.
//
// All times are UTC. A US Saturday runs from Saturday noon UTC into Sunday
// morning UTC, which is why the weekend covers both days and the small hours
// of Monday, Friday and Saturday catch the Sunday, Thursday and Friday night
// games.

import { DEFAULT } from "./league.js";

const SUN = 0, MON = 1, TUE = 2, FRI = 5, SAT = 6;

/** August to December. Out of it, only the daily heartbeat runs. */
function inSeason(d) {
  const m = d.getUTCMonth() + 1;
  return m >= 8 && m <= 12;
}

// MEN'S BASKETBALL keeps a different calendar: November to the Big 12
// tournament in Kansas City, which ends by mid-March. Its pages are static
// like football's and go stale the same way, but nothing else on this clock
// knew January existed, so from New Year to the tournament the section would
// not have rebuilt at all.
function hoopsSeason(d) {
  const m = d.getUTCMonth() + 1, day = d.getUTCDate();
  return m >= 11 || m <= 2 || (m === 3 && day <= 16);
}

// The tournament's week, when games run from late morning Central into the
// night every day rather than only in the evening.
function hoopsTournament(d) {
  return d.getUTCMonth() === 2 && d.getUTCDate() >= 8;
}

/**
 * Whether a basketball night wants a rebuild at this hour. Weeknight games
 * tip 23:00 to 03:00 UTC and finish two hours later, so the hours 01 to 06
 * catch them; weekend and tournament afternoons add 18 to 23. On :40, ten
 * minutes clear of football's :30, and only in an hour football has not
 * already deployed: pages.yml cancels a run in progress when the next
 * arrives, so two dispatches an hour would mostly cancel each other.
 */
function hoops(d, footballDeploy) {
  if (!hoopsSeason(d) || d.getUTCMinutes() !== 40) return false;
  const h = d.getUTCHours(), dow = d.getUTCDay();
  const night = h >= 1 && h <= 6;
  const day = h >= 18 && h <= 23
    && (dow === SAT || dow === SUN || hoopsTournament(d));
  return (night || day) && !footballDeploy(h);
}

// When, on a Tuesday, the Worker may ask for the week to be opened. Three
// chances and no more, two hours or more apart: each dispatch is a refresh,
// which spends CFBD calls, and a retry exists for a refresh that failed, not
// for one that is still running. The first is the first sweep of the day, so
// the slate it publishes is imported by the next one, an hour later and
// inside the 09:00 UTC the pools pages promise.
export const REFRESH_HOURS = [7, 9, 12];

/**
 * The jobs a tick scheduled for `ms` should run.
 *
 *   sweep    import slates, correct kickoffs, read ESPN, grade, heartbeat
 *   deploy   dispatch pages.yml, so the static pages rebuild on this clock
 *   refresh  dispatch the weekly refresh IF the week has not been opened;
 *            whether it has is weekNeedsOpening's question, asked after the
 *            sweep has imported whatever is there
 *   scores   dispatch scores.yml, which asks CFBD what finished and posts
 *            it back here to be graded
 *   hoops    dispatch pages.yml for the men's basketball pages, on the
 *            nights games finish; the same deploy as `deploy`, on its own
 *            calendar, so the football tests can keep proving `deploy`
 *            against the triggers it replaced
 */
export function jobsAt(ms) {
  const d = new Date(ms);
  const dow = d.getUTCDay(), h = d.getUTCHours(), m = d.getUTCMinutes();
  const season = inSeason(d);
  const half = season && m === 30;

  const weekend = half && (dow === SAT || dow === SUN);
  const smallHours = half && (dow === MON || dow === FRI || dow === SAT)
    && h <= 8 && h % 2 === 0;
  const tuesday = half && dow === TUE && h >= 7;
  // All year, and the only job out of season. It is the run that pings the
  // dead man's switch, and a switch that goes quiet every January gets muted.
  const noon = m === 0 && h === 12;

  // THE SCORE CHANNEL, on the windows scores.yml already declares for
  // itself: every twenty minutes through Saturday and Sunday, every twenty
  // through the small hours of Monday, Friday and Saturday, and once a day
  // at 17:30 to catch whatever those missed. Those are GitHub crons, and on
  // the weekend of 2026-10-10 GitHub delivered 8 of Saturday's 72 and 6 of
  // Sunday's. This asks for the same runs from a clock that keeps time.
  //
  // It only matters once ticks arrive every ten minutes. The four triggers
  // in wrangler.toml fire at :30 and at noon, so until they are replaced
  // this is true on a handful of ticks a week and the rest never come.
  const third = season && m % 20 === 0;
  const scores = (third && (dow === SAT || dow === SUN))
    || (third && (dow === MON || dow === FRI || dow === SAT) && h <= 8)
    || (season && h === 17 && m === 30);

  // Football's deploy for any hour of this day, so basketball can stand
  // aside from it: the same two rules as `weekend` and `smallHours` above.
  const footballDeploy = (hour) => season
    && ((dow === SAT || dow === SUN)
      || ((dow === MON || dow === FRI || dow === SAT) && hour <= 8 && hour % 2 === 0));

  return {
    sweep: weekend || smallHours || tuesday || noon,
    deploy: weekend || smallHours,
    refresh: tuesday && REFRESH_HOURS.includes(h),
    scores,
    hoops: hoops(d, footballDeploy),
  };
}

export function anyJob(jobs) {
  return jobs.sweep || jobs.deploy || jobs.refresh || jobs.scores || jobs.hoops;
}

// How long after a week locks the Worker will still ask for the next one. A
// week that locked more than this long ago with nothing after it is a bye or
// the end of the season, and asking again every Tuesday until December would
// spend a refresh's worth of quota each time to be told the same thing.
const OPEN_WITHIN_S = 8 * 24 * 3600;

/**
 * Whether the next pools week is still waiting to be opened.
 *
 * FROM THE DATABASE, NOT THE CALENDAR. The week is opened by the refresh
 * publishing its slate and this Worker importing it, so "has it been opened"
 * is exactly "is the newest week we hold one that has already locked". On a
 * Tuesday morning that is last Saturday's week, and the answer is yes until
 * the import lands.
 *
 * An empty season answers no. Nothing here knows when a season starts, and
 * a Worker that asked for a refresh every Tuesday of August would be spending
 * quota on a slate the publisher is not ready to write. The first week of a
 * season is opened by pages.yml's own Tuesday cron, as it always has been.
 */
export async function weekNeedsOpening(env, season, nowS, league = DEFAULT) {
  const newest = await env.DB.prepare(
    `SELECT week, lock_at FROM weeks WHERE league = ? AND season = ?
      ORDER BY week DESC LIMIT 1`).bind(league, season).first();
  if (!newest || newest.lock_at == null) return false;
  return newest.lock_at <= nowS && nowS - newest.lock_at <= OPEN_WITHIN_S;
}

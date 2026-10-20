// Which pool a request or a job is about: football, men's basketball or
// women's basketball.
//
// Every pools table carries `league` since migration 0014, and every query on
// one names it, so two pools can share a season and a week number without
// seeing each other's rows. Football is the default everywhere, because
// football is what every client and every job asked for before the column
// existed: a request with no `league` means what it always meant.
//
// THE RULES THE CODE FOLLOWS, written once here so they read the same in
// every file:
//   - an exported function that touches a pools table takes `league` as its
//     LAST parameter, defaulting to DEFAULT, and passes it on;
//   - every SELECT, UPDATE and DELETE on a pools table says `league = ?`
//     (on every pools table in a join);
//   - every INSERT names the column, and an upsert's ON CONFLICT target puts
//     league first, matching the indexes 0014 created.

export const LEAGUES = ["cfb", "mbb", "wbb"];
export const DEFAULT = "cfb";

/** A league from a query string or a request body, or null if it is not
 * one. Missing means football. */
export function parse(raw) {
  if (raw == null || raw === "") return DEFAULT;
  const v = String(raw).toLowerCase();
  return LEAGUES.includes(v) ? v : null;
}

/** The league a request names in its query string. */
export function fromUrl(url) {
  return parse(url.searchParams.get("league"));
}

/** Whether a league's pick'em locks game by game (basketball, migration
 * 0015) rather than all at once at the week's first kickoff (football). */
export function perGame(league) {
  return league !== DEFAULT;
}

/** The season a league is playing at `ms`. Football names a season by the
 * year it starts and is configured (env.SEASON); basketball names it by the
 * year it ends, as CBBD and ESPN do, so from August the coming one is live. */
export function seasonOf(env, league, ms = Date.now()) {
  if (league === DEFAULT) {
    return Number(env.SEASON || new Date(ms).getUTCFullYear());
  }
  const d = new Date(ms);
  return d.getUTCMonth() >= 7 ? d.getUTCFullYear() + 1 : d.getUTCFullYear();
}

// The late-joiner handicap: arrive having already spent the chalk.
//
// A survivor pool gets hard in November for one reason — everybody burned the
// safe teams in September. Someone who joins in week six without that history
// is not playing the same game: they pick the best team on the board every
// week while the August players work around five they have already spent.
//
// So a joiner entering at week N starts with the biggest favourite of every
// week they missed already spent. Not the most popular team (with a dozen
// players that is one person's opinion), not the best team by some rating
// (publish a rating and every argument about the handicap becomes an argument
// about the rating) — the chalk, which is the same benchmark the pick'em
// already scores itself against.
//
// Three properties make this the version that works:
//
//   It is a function of the frozen slate alone, so it needs no other players
//   and cannot drift. slate_games_frozen makes the inputs immutable, which
//   makes the handicap reproducible and un-arguable after the fact.
//
//   It reproduces what the incumbents actually did rather than approximating
//   it. The chalk is what a careful player spends first.
//
//   It is knowable before you sign up. "Join at week six and you start
//   without these five teams" is a real price, quoted in advance.

/** Below this many usable teams left, entering is not a game. */
export const MIN_USABLE = 4;

/**
 * The teams a player entering at `entryWeek` starts with already spent.
 *
 * One per missed week: that week's largest favourite, and where that team is
 * already spent by an earlier week, the next largest. So the burned set is
 * always exactly as many teams as there are weeks missed — a handicap that
 * quietly shrank because two weeks shared a favourite would be a discount
 * for joining after a bye.
 */
export async function chalkRoster(env, season, entryWeek) {
  if (!(entryWeek > 1)) return [];

  const { results } = await env.DB.prepare(
    `SELECT week, game_id, home, away, spread_x2
       FROM slate_games
      WHERE season = ? AND week < ? AND spread_x2 IS NOT NULL
      ORDER BY week, game_id`).bind(season, entryWeek).all();

  const byWeek = new Map();
  for (const g of results || []) {
    if (g.spread_x2 === 0) continue;            // a pick'em is not chalk
    if (!byWeek.has(g.week)) byWeek.set(g.week, []);
    byWeek.get(g.week).push({
      team: g.spread_x2 < 0 ? g.home : g.away,
      margin: Math.abs(g.spread_x2),
      game_id: g.game_id,
    });
  }

  const burned = [];
  const taken = new Set();
  for (const week of [...byWeek.keys()].sort((a, b) => a - b)) {
    // Biggest favourite first; game_id breaks a tie so the answer is the same
    // every time it is computed.
    const games = byWeek.get(week)
      .sort((a, b) => b.margin - a.margin || a.game_id - b.game_id);
    const g = games.find((x) => !taken.has(x.team));
    if (!g) continue;                            // nothing left this week
    taken.add(g.team);
    burned.push({ week, team: g.team, margin_x2: g.margin });
  }
  return burned;
}

/**
 * The week a player entered, which is the week of their first pick.
 *
 * Derived rather than stored, and deliberately: survivor_board.entered_week is
 * materialised by the scoring cron and so lags a brand-new entrant, and the
 * handicap has to be right on the very first pick or it is not a handicap.
 * Before that first pick there is nothing to derive from, so the week being
 * picked now is the entry week — which is what makes joining cost something.
 */
export async function entryWeek(env, season, userId, thisWeek) {
  const row = await env.DB.prepare(
    `SELECT MIN(week) AS w FROM survivor_picks
      WHERE user_id = ? AND season = ?`).bind(userId, season).first();
  return row && row.w != null ? row.w : thisWeek;
}

/** Teams still available to someone entering now: never chalk, never spent. */
export async function usableFrom(env, season, week, burnedTeams) {
  const { results } = await env.DB.prepare(
    `SELECT DISTINCT home, away FROM slate_games
      WHERE season = ? AND week >= ? AND spread_x2 IS NOT NULL`)
    .bind(season, week).all();
  const out = new Set();
  for (const g of results || []) {
    if (!burnedTeams.has(g.home)) out.add(g.home);
    if (!burnedTeams.has(g.away)) out.add(g.away);
  }
  return out;
}

/**
 * Everything a handler or the client needs about one player's roster.
 *
 * `closed` is the natural end of joining. Rather than an arbitrary deadline
 * that shuts out everyone who finds the site in October, entry closes when the
 * handicap would leave too little to pick from — a consequence somebody can
 * see coming rather than a rule imposed on them.
 */
export async function rosterFor(env, season, userId, thisWeek) {
  const entered = await entryWeek(env, season, userId, thisWeek);
  const burned = await chalkRoster(env, season, entered);
  const teams = new Set(burned.map((b) => b.team));
  const usable = await usableFrom(env, season, thisWeek, teams);
  return {
    entered_week: entered,
    joining: entered === thisWeek,
    burned,
    usable: usable.size,
    closed: usable.size < MIN_USABLE,
  };
}

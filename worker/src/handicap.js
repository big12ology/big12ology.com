// The late-joiner handicap: arrive having already spent the chalk.
//
// A survivor pool gets hard in November for one reason — everybody burned the
// safe teams in September. Someone who joins in week six without that history
// is not playing the same game: they pick the best team on the board every
// week while the August players work around five they have already spent.
//
// So a joiner entering at week N starts with the biggest favorite of every
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

/** Whether a survivor pick on this side of this game is allowed at all. */
export function isPickable(game, team) {
  if (!game || !game.b12) return false;
  if (game.b12 === "both") return team === game.home || team === game.away;
  return team === (game.b12 === "home" ? game.home : game.away);
}

/**
 * The favorite of a game, but only if it is one that can be picked.
 *
 * A game where the non-conference side is favored has no chalk for survivor
 * purposes — the favorite is unpickable and the underdog is not the chalk.
 */
function pickableFavourite(g) {
  const fav = g.spread_x2 < 0 ? g.home : g.away;
  return isPickable(g, fav) ? fav : null;
}

/** Below this many usable teams left, entering is not a game. */
export const MIN_USABLE = 4;

/**
 * The teams a player entering at `entryWeek` starts with already spent.
 *
 * One per missed week: that week's largest favorite, and where that team is
 * already spent by an earlier week, the next largest. So the burned set is
 * always exactly as many teams as there are weeks missed — a handicap that
 * quietly shrank because two weeks shared a favorite would be a discount
 * for joining after a bye.
 */
export async function chalkRoster(env, season, entryWeek) {
  if (!(entryWeek > 1)) return [];

  const { results } = await env.DB.prepare(
    `SELECT week, game_id, home, away, spread_x2, b12
       FROM slate_games
      WHERE season = ? AND week < ? AND spread_x2 IS NOT NULL
      ORDER BY week, game_id`).bind(season, entryWeek).all();

  const byWeek = new Map();
  for (const g of results || []) {
    if (g.spread_x2 === 0) continue;            // a pick'em is not chalk
    // Only a team somebody could have picked can have been spent. Burning a
    // visiting non-conference favorite would take away nothing and leave the
    // handicap a team short.
    const team = pickableFavourite(g);
    if (!team) continue;
    if (!byWeek.has(g.week)) byWeek.set(g.week, []);
    byWeek.get(g.week).push({
      team, margin: Math.abs(g.spread_x2), game_id: g.game_id,
    });
  }

  const burned = [];
  const taken = new Set();
  for (const week of [...byWeek.keys()].sort((a, b) => a - b)) {
    // Biggest favorite first; game_id breaks a tie so the answer is the same
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

/**
 * Below this many pickable teams, a week is not a survivor week.
 *
 * Survivor is a choice about which team to spend. One legal team is not a
 * choice — it is a toll, and it is worse than that, because the pool cannot
 * tell somebody who does not already know the rules that skipping is free.
 *
 * 2026 opens with exactly this: a lone week 0 game in Dublin, North Carolina
 * at TCU, of which only TCU is a Big 12 side. A new player arriving for launch
 * sees a survivor pool with one game and one legal pick and reasonably
 * concludes they must take it — spending a team who is on bye the following
 * week and hosts an FCS opponent the week after, in exchange for a seventy
 * percent shot at a game they were not allowed to decline.
 *
 * Two, not one, because two is where a decision starts existing.
 */
export const MIN_SURVIVOR_TEAMS = 2;

/**
 * How many different teams a survivor player may choose from in one week.
 *
 * Not filtered by what any one player has spent — this asks whether the WEEK
 * is a contest at all, which is the same answer for everybody. A player who
 * has personally run out is the `stranded` case and is handled by scoring.
 */
export async function pickableCount(env, season, week) {
  const { results } = await env.DB.prepare(
    `SELECT home, away, b12 FROM slate_games
      WHERE season = ? AND week = ? AND spread_x2 IS NOT NULL`)
    .bind(season, week).all();
  const teams = new Set();
  for (const g of results || []) {
    for (const t of [g.home, g.away]) if (isPickable(g, t)) teams.add(t);
  }
  return teams.size;
}

/** Teams still available to someone entering now: never chalk, never spent. */
export async function usableFrom(env, season, week, burnedTeams) {
  const { results } = await env.DB.prepare(
    `SELECT DISTINCT home, away, b12 FROM slate_games
      WHERE season = ? AND week >= ? AND spread_x2 IS NOT NULL`)
    .bind(season, week).all();
  const out = new Set();
  for (const g of results || []) {
    for (const t of [g.home, g.away]) {
      if (isPickable(g, t) && !burnedTeams.has(t)) out.add(t);
    }
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

/**
 * The weeks a player could not have picked in, whatever they intended.
 *
 * A locked week, after they entered, with no pick on it — and no Big 12 team
 * left that they were allowed to choose. Every unspent side is on a bye, or
 * is in a game with no posted line, so the card had nothing pickable on it.
 * Scoring treats these as neither a loss nor a miss.
 *
 * Spent means the same thing the no-reuse trigger means, and is derived the
 * same way, so the two can never disagree about what a player was holding:
 * the chalk they arrived having burned, plus every earlier pick whose week
 * was not scored void. Earlier picks only — at the moment a week locked, a
 * pick saved for some later week was a pick the player could still have
 * withdrawn, so counting it against them here would excuse a week they could
 * in fact have played.
 *
 * Returns one row per stranded (user, week). The common answer is none, and
 * the query stops early when nobody is missing a locked week at all.
 */
export async function strandedWeeks(env, season) {
  const { results: gaps } = await env.DB.prepare(
    `WITH entrants AS (
       SELECT user_id, MIN(week) AS entered
         FROM survivor_picks WHERE season = ? GROUP BY user_id)
     SELECT e.user_id, e.entered, w.week
       FROM entrants e
       JOIN weeks w ON w.season = ? AND w.lock_at IS NOT NULL
                   AND w.lock_at <= unixepoch() AND w.week >= e.entered
       LEFT JOIN survivor_picks p
         ON p.user_id = e.user_id AND p.season = ? AND p.week = w.week
      WHERE p.user_id IS NULL
      ORDER BY e.user_id, w.week`).bind(season, season, season).all();
  if (!gaps || !gaps.length) return [];

  // Only the weeks somebody actually skipped need a board, and a season has
  // few enough of those to fetch one week at a time rather than the slate.
  const slates = new Map();
  const slateFor = async (week) => {
    if (!slates.has(week)) {
      const { results } = await env.DB.prepare(
        `SELECT home, away, b12 FROM slate_games
          WHERE season = ? AND week = ? AND spread_x2 IS NOT NULL`)
        .bind(season, week).all();
      const teams = new Set();
      for (const g of results || []) {
        for (const t of [g.home, g.away]) if (isPickable(g, t)) teams.add(t);
      }
      slates.set(week, teams);
    }
    return slates.get(week);
  };

  // The chalk burn is a function of the entry week alone, so it is computed
  // once per player however many weeks they skipped.
  const burnedFor = new Map();
  const out = [];
  for (const gap of gaps) {
    if (!burnedFor.has(gap.user_id)) {
      const chalk = await chalkRoster(env, season, gap.entered);
      burnedFor.set(gap.user_id, new Set(chalk.map((b) => b.team)));
    }
    const spent = new Set(burnedFor.get(gap.user_id));
    const { results: prior } = await env.DB.prepare(
      `SELECT p.team FROM survivor_picks p
        WHERE p.user_id = ? AND p.season = ? AND p.week < ?
          AND NOT EXISTS (
            SELECT 1 FROM survivor_scores s
             WHERE s.user_id = p.user_id AND s.season = p.season
               AND s.week = p.week AND s.outcome = 'void')`)
      .bind(gap.user_id, season, gap.week).all();
    for (const r of prior || []) spent.add(r.team);

    const playing = await slateFor(gap.week);
    let usable = 0;
    for (const t of playing) if (!spent.has(t)) usable++;
    if (usable === 0) out.push({ user_id: gap.user_id, week: gap.week });
  }
  return out;
}

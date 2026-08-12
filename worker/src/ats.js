// Against the spread. The whole game reduces to this one comparison, so it is
// kept alone in a file with no imports and no I/O.
//
// The convention is CFBD's, which the rest of this repo already speaks:
// the number is the HOME team's spread, and negative means the home team is
// favored. tiebreaker/fetch.py:fetch_lines writes it that way,
// tiebreaker/scorecard.py scores it that way, tiebreaker/pickem.py freezes it
// that way. A sign error here is the single most dangerous bug in the feature:
// it would not crash, it would not look wrong, it would just quietly grade
// every game backwards for a season. tests/parity.test.js diffs this against
// the Python side over every completed game to make sure that cannot happen.

/** No line, so nothing to be right or wrong about. */
export const UNPICKABLE = null;

/**
 * Which side covered.
 *
 * spreadX2 is the frozen home spread times two — see migrations/0001_init.sql
 * for why the doubling is not an optimization. Because it is an integer and
 * the points are integers, `adj` is exact, and a push is a real equality
 * rather than a float comparison that happens to land.
 *
 * @param {number|null} homePoints
 * @param {number|null} awayPoints
 * @param {number} spreadX2
 * @returns {"home"|"away"|"push"|"void"}
 */
export function ats(homePoints, awayPoints, spreadX2) {
  if (homePoints == null || awayPoints == null) return "void";
  if (!Number.isInteger(spreadX2)) {
    throw new TypeError(`spreadX2 must be an integer, got ${spreadX2}`);
  }
  // Everything doubled, so the half-point line stays exact:
  //   home margin + spread > 0  ->  home covered
  const adj = 2 * (homePoints - awayPoints) + spreadX2;
  return adj > 0 ? "home" : adj < 0 ? "away" : "push";
}

/**
 * What a pick on that game was worth.
 *
 * A push is its own outcome, not a loss. Folding pushes into losses is the
 * most reliable way for a pick'em board to lose people's trust, because the
 * player knows they did not lose and the table says they did.
 *
 * @param {"home"|"away"} side
 * @param {"home"|"away"|"push"|"void"} result
 * @returns {"win"|"loss"|"push"|"void"}
 */
export function outcome(side, result) {
  if (result === "void") return "void";
  if (result === "push") return "push";
  return side === result ? "win" : "loss";
}

/**
 * The line as a person reads it, from the perspective of one side.
 *
 * Returns a number: negative gives points, positive gets them. Formatting —
 * the minus sign, "PK" at zero — belongs to the page, not here.
 *
 * @param {number} spreadX2 frozen home spread × 2
 * @param {"home"|"away"} side
 */
export function displaySpread(spreadX2, side) {
  return (side === "home" ? spreadX2 : -spreadX2) / 2;
}

/**
 * Win percentage over a set of outcomes.
 *
 * Pushes and voids leave both terms alone: they are not wins and they are not
 * losses, and counting them in the denominator would punish a player for a
 * canceled game. Returns null rather than 0 when nothing has been decided —
 * a player with one push is not a 0% player.
 *
 * @param {{w:number,l:number}} rec
 */
export function pct({ w, l }) {
  return w + l === 0 ? null : w / (w + l);
}

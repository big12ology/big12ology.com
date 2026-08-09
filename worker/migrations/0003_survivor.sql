-- The survivor pool.
--
-- A second game on the same slate: one team a week, picked to win the game
-- outright — the spread plays no part — and no team twice in a season. Lose,
-- or let a week lock without picking after you have entered, and you are out.
-- A voided game spares you and returns the team, because nobody should be
-- eliminated by a cancellation.
--
-- Same constitution as 0001: the invariants that are not allowed to be wrong
-- live here as triggers, not in handler code.
--
--   1. A pick never changes after the lock.   survivor_locked_{ins,upd,del}
--   2. A pick names a team actually playing   survivor_in_game_{ins,upd}
--      a pickable game on that week's slate.
--   3. A team is used once per season.        survivor_no_reuse_{ins,upd}
--
-- What is NOT here: elimination. Whether a player is alive is a derived fact
-- about picks and results, recomputed wholesale by scoring into
-- survivor_board, and a stored pick from a player the recompute later finds
-- dead is simply ignored by the walk. Enforcing it at write time would need
-- the trigger to know about missed weeks — an absence, which a row-level
-- trigger cannot see — so the handler refuses the obvious case and the
-- recompute is the truth.

-- One row per player per week. The team is stored by name, exactly as it
-- appears on the slate row it references — the same argument as users.team in
-- 0002: teams.json is the canonical list and a copy in D1 would be a second
-- place for it to be wrong. The pair (game_id, team) is the receipt: which
-- game, and which side of it, with no way to drift apart because the trigger
-- checks one against the other.
CREATE TABLE survivor_picks (
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  season     INTEGER NOT NULL,
  week       INTEGER NOT NULL,
  game_id    INTEGER NOT NULL,
  team       TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, season, week),
  FOREIGN KEY (season, week, game_id)
    REFERENCES slate_games(season, week, game_id) ON DELETE RESTRICT
);
CREATE INDEX survivor_picks_week ON survivor_picks(season, week);
-- The no-reuse trigger walks this by (user, season, team).
CREATE INDEX survivor_picks_team ON survivor_picks(user_id, season, team);

-- THE LOCK, on all three verbs, same clock as the pick'em card. The message
-- is the same string picks_locked_* raises, so the handler maps both games'
-- lock failures with one test.
CREATE TRIGGER survivor_locked_insert
BEFORE INSERT ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM weeks w WHERE w.season = NEW.season AND w.week = NEW.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'week_locked'); END;

CREATE TRIGGER survivor_locked_update
BEFORE UPDATE ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM weeks w WHERE w.season = OLD.season AND w.week = OLD.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'week_locked'); END;

CREATE TRIGGER survivor_locked_delete
BEFORE DELETE ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM weeks w WHERE w.season = OLD.season AND w.week = OLD.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'week_locked'); END;

-- The team must be one of the two playing that game, and the game must carry
-- a line. The line itself is irrelevant to a straight-up pick — the reason is
-- plumbing, stated so nobody "fixes" it: scoring only writes results rows for
-- games with a line (see scoring.js on junk voids), so a lineless game can
-- never be graded, and a pick that can never be graded is a player who can
-- never be eliminated. If the game is on the card, it is survivor-pickable;
-- if it is not, it is not.
CREATE TRIGGER survivor_in_game_insert
BEFORE INSERT ON survivor_picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id
     AND g.spread_x2 IS NOT NULL
     AND NEW.team IN (g.home, g.away))
BEGIN SELECT RAISE(ABORT, 'survivor_not_in_game'); END;

CREATE TRIGGER survivor_in_game_update
BEFORE UPDATE ON survivor_picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id
     AND g.spread_x2 IS NOT NULL
     AND NEW.team IN (g.home, g.away))
BEGIN SELECT RAISE(ABORT, 'survivor_not_in_game'); END;

-- ONCE PER SEASON. A prior use blocks the team unless that week was scored
-- void — the game stopped existing, so the team was never really spent. An
-- unscored prior week counts as a use: the pick is presumed live until the
-- result says otherwise, or a player could ride one team every week of a
-- long scoring outage. Same-week writes are excluded so changing your mind
-- back and forth before the lock is not self-collision.
CREATE TRIGGER survivor_no_reuse_insert
BEFORE INSERT ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM survivor_picks sp
   WHERE sp.user_id = NEW.user_id AND sp.season = NEW.season
     AND sp.week <> NEW.week AND sp.team = NEW.team
     AND NOT EXISTS (
       SELECT 1 FROM survivor_scores ss
        WHERE ss.user_id = sp.user_id AND ss.season = sp.season
          AND ss.week = sp.week AND ss.outcome = 'void'))
BEGIN SELECT RAISE(ABORT, 'survivor_team_reused'); END;

CREATE TRIGGER survivor_no_reuse_update
BEFORE UPDATE ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM survivor_picks sp
   WHERE sp.user_id = NEW.user_id AND sp.season = NEW.season
     AND sp.week <> NEW.week AND sp.team = NEW.team
     AND NOT EXISTS (
       SELECT 1 FROM survivor_scores ss
        WHERE ss.user_id = sp.user_id AND ss.season = sp.season
          AND ss.week = sp.week AND ss.outcome = 'void'))
BEGIN SELECT RAISE(ABORT, 'survivor_team_reused'); END;

-- ------------------------------------------------------------------ derived

-- Straight-up outcomes, one per graded pick. Never written by a handler:
-- deleted and recreated from survivor_picks ⋈ slate_games ⋈ results, exactly
-- as pick_scores is, so it cannot disagree with the picks it summarises.
CREATE TABLE survivor_scores (
  user_id TEXT NOT NULL,
  season  INTEGER NOT NULL,
  week    INTEGER NOT NULL,
  game_id INTEGER NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('win','loss','void')),
  PRIMARY KEY (user_id, season, week),
  FOREIGN KEY (user_id, season, week)
    REFERENCES survivor_picks(user_id, season, week) ON DELETE CASCADE
);
CREATE INDEX survivor_scores_week ON survivor_scores(season, week);

-- The materialised standings. Recomputed wholesale by the scoring cron.
--
-- Unlike leaderboard_week this holds EVERY entrant, active or not, because a
-- row here is doing two jobs: the public board (which filters to active at
-- read time, same rule as PUBLISHABLE) and the player's own standing — which
-- the pick handler consults to refuse picks from the eliminated, and which a
-- provisional player must be able to see about themselves.
CREATE TABLE survivor_board (
  season       INTEGER NOT NULL,
  user_id      TEXT NOT NULL,
  wins         INTEGER NOT NULL,
  alive        INTEGER NOT NULL CHECK (alive IN (0,1)),
  entered_week INTEGER NOT NULL,               -- first pick; earlier weeks
                                               -- never count against anyone
  out_week     INTEGER,
  out_reason   TEXT CHECK (out_reason IN ('loss','missed')),
  rank         INTEGER NOT NULL,
  computed_at  INTEGER NOT NULL,
  PRIMARY KEY (season, user_id)
);
CREATE INDEX survivor_board_rank ON survivor_board(season, rank);

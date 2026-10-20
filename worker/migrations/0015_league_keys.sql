-- League in the keys, the second of the two steps 0014 described.
--
-- 0014 added `league` and a unique index with league in front of every old
-- key, and the Worker has read and written league ever since (c70fb39f). The
-- old keys still say (season, week), so men's basketball week 3 and
-- football's week 3 of the same season cannot both exist. This rebuilds every
-- pools table so league is part of each primary and foreign key, drops the old
-- keys, and rewrites the triggers to look rows up by league as well.
--
-- WHY IT IS DONE THIS WAY. SQLite cannot change a primary key in place, so a
-- table has to be rebuilt, and the textbook rebuild (create new, copy, drop
-- old, rename) goes wrong here twice over: dropping a parent such as `weeks`
-- would cascade-delete every row of `slate_games` under it, and renaming a
-- table rewrites the foreign keys that point at it. So instead:
--
--   1. every table is copied into a plain backup table with no constraints;
--   2. the originals are dropped from the leaves to the root, so no table is
--      ever dropped while a child still points at it, and nothing cascades;
--   3. they are created again from the root down, with league in the keys;
--   4. the rows go back in the same order, parents before children;
--   5. the triggers are created last, after the rows are back, so the lock
--      triggers do not refuse the copied picks of weeks that already locked.
--
-- results_history is left alone: its key is its own id, 0014 already gave it
-- league and an index, and nothing points at it.
--
-- WHAT CHANGES IN BEHAVIOR. One thing, and only for basketball: a pick'em pick
-- locks at its own game's tip ('game_started'), the pools spec's decision,
-- because a basketball week has games on four or five days. Football keeps
-- its week lock ('week_locked') exactly as before. Every other trigger says
-- what it said, scoped to the row's league.

PRAGMA defer_foreign_keys = true;

-- 1. Backups -----------------------------------------------------------------

CREATE TABLE bk_weeks AS SELECT * FROM weeks;
CREATE TABLE bk_slate_games AS SELECT * FROM slate_games;
CREATE TABLE bk_picks AS SELECT * FROM picks;
CREATE TABLE bk_results AS SELECT * FROM results;
CREATE TABLE bk_pick_scores AS SELECT * FROM pick_scores;
CREATE TABLE bk_leaderboard_week AS SELECT * FROM leaderboard_week;
CREATE TABLE bk_leaderboard_season AS SELECT * FROM leaderboard_season;
CREATE TABLE bk_survivor_picks AS SELECT * FROM survivor_picks;
CREATE TABLE bk_survivor_scores AS SELECT * FROM survivor_scores;
CREATE TABLE bk_survivor_board AS SELECT * FROM survivor_board;
CREATE TABLE bk_survivor_stranded AS SELECT * FROM survivor_stranded;

-- 2. Drop, leaves first ---------------------------------------------------------

DROP TABLE pick_scores;
DROP TABLE survivor_scores;
DROP TABLE leaderboard_week;
DROP TABLE leaderboard_season;
DROP TABLE survivor_board;
DROP TABLE survivor_stranded;
DROP TABLE results;
DROP TABLE picks;
DROP TABLE survivor_picks;
DROP TABLE slate_games;
DROP TABLE weeks;

-- 3. Create, root first -----------------------------------------------------------

CREATE TABLE weeks (
  league         TEXT NOT NULL DEFAULT 'cfb'
                   CHECK (league IN ('cfb', 'mbb', 'wbb')),
  season         INTEGER NOT NULL,
  week           INTEGER NOT NULL,
  status         TEXT NOT NULL DEFAULT 'published'
                   CHECK (status IN ('published','no_contest','final')),
  published_at   INTEGER NOT NULL,
  lock_at        INTEGER,
  game_count     INTEGER NOT NULL,
  pickable_count INTEGER NOT NULL,
  source_sha256  TEXT,
  scored_at      INTEGER,
  scored_rev     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (league, season, week)
);

CREATE TABLE slate_games (
  league      TEXT NOT NULL DEFAULT 'cfb'
                CHECK (league IN ('cfb', 'mbb', 'wbb')),
  season      INTEGER NOT NULL,
  week        INTEGER NOT NULL,
  game_id     INTEGER NOT NULL,
  home        TEXT NOT NULL,
  away        TEXT NOT NULL,
  kickoff_at  INTEGER NOT NULL,
  spread_x2   INTEGER,
  spread_raw  REAL,
  books       INTEGER,
  frozen_at   INTEGER NOT NULL,
  b12         TEXT CHECK (b12 IN ('home', 'away', 'both')),
  neutral     INTEGER CHECK (neutral IN (0, 1)),
  PRIMARY KEY (league, season, week, game_id),
  FOREIGN KEY (league, season, week) REFERENCES weeks(league, season, week)
    ON DELETE CASCADE
);
CREATE UNIQUE INDEX slate_game_unique ON slate_games(league, season, game_id);

CREATE TABLE picks (
  league     TEXT NOT NULL DEFAULT 'cfb'
               CHECK (league IN ('cfb', 'mbb', 'wbb')),
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  season     INTEGER NOT NULL,
  week       INTEGER NOT NULL,
  game_id    INTEGER NOT NULL,
  side       TEXT NOT NULL CHECK (side IN ('home','away')),
  spread_x2  INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (league, user_id, season, week, game_id),
  FOREIGN KEY (league, season, week, game_id)
    REFERENCES slate_games(league, season, week, game_id) ON DELETE RESTRICT
);
CREATE INDEX picks_week ON picks(league, season, week);

CREATE TABLE results (
  league      TEXT NOT NULL DEFAULT 'cfb'
                CHECK (league IN ('cfb', 'mbb', 'wbb')),
  season      INTEGER NOT NULL,
  week        INTEGER NOT NULL,
  game_id     INTEGER NOT NULL,
  home_points INTEGER,
  away_points INTEGER,
  status      TEXT NOT NULL CHECK (status IN ('scheduled','final','void')),
  ats         TEXT CHECK (ats IN ('home','away','push','void')),
  source_hash TEXT NOT NULL,
  revision    INTEGER NOT NULL DEFAULT 1,
  scored_at   INTEGER NOT NULL,
  PRIMARY KEY (league, season, week, game_id),
  FOREIGN KEY (league, season, week, game_id)
    REFERENCES slate_games(league, season, week, game_id) ON DELETE CASCADE
);

CREATE TABLE pick_scores (
  league  TEXT NOT NULL DEFAULT 'cfb'
            CHECK (league IN ('cfb', 'mbb', 'wbb')),
  user_id TEXT NOT NULL,
  season  INTEGER NOT NULL,
  week    INTEGER NOT NULL,
  game_id INTEGER NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('win','loss','push','void')),
  PRIMARY KEY (league, user_id, season, week, game_id),
  FOREIGN KEY (league, user_id, season, week, game_id)
    REFERENCES picks(league, user_id, season, week, game_id) ON DELETE CASCADE
);
CREATE INDEX pick_scores_week ON pick_scores(league, season, week);

CREATE TABLE leaderboard_week (
  league TEXT NOT NULL DEFAULT 'cfb' CHECK (league IN ('cfb', 'mbb', 'wbb')),
  season INTEGER NOT NULL, week INTEGER NOT NULL, user_id TEXT NOT NULL,
  w INTEGER NOT NULL, l INTEGER NOT NULL, p INTEGER NOT NULL, v INTEGER NOT NULL,
  pct REAL, rank INTEGER NOT NULL, computed_at INTEGER NOT NULL,
  PRIMARY KEY (league, season, week, user_id)
);
CREATE INDEX lb_week_rank ON leaderboard_week(league, season, week, rank);

CREATE TABLE leaderboard_season (
  league TEXT NOT NULL DEFAULT 'cfb' CHECK (league IN ('cfb', 'mbb', 'wbb')),
  season INTEGER NOT NULL, user_id TEXT NOT NULL,
  w INTEGER NOT NULL, l INTEGER NOT NULL, p INTEGER NOT NULL, v INTEGER NOT NULL,
  pct REAL, weeks_played INTEGER NOT NULL, rank INTEGER NOT NULL,
  computed_at INTEGER NOT NULL,
  PRIMARY KEY (league, season, user_id)
);
CREATE INDEX lb_season_rank ON leaderboard_season(league, season, rank);

CREATE TABLE survivor_picks (
  league     TEXT NOT NULL DEFAULT 'cfb'
               CHECK (league IN ('cfb', 'mbb', 'wbb')),
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  season     INTEGER NOT NULL,
  week       INTEGER NOT NULL,
  game_id    INTEGER NOT NULL,
  team       TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (league, user_id, season, week),
  FOREIGN KEY (league, season, week, game_id)
    REFERENCES slate_games(league, season, week, game_id) ON DELETE RESTRICT
);
CREATE INDEX survivor_picks_week ON survivor_picks(league, season, week);
CREATE INDEX survivor_picks_team ON survivor_picks(league, user_id, season, team);

CREATE TABLE survivor_scores (
  league  TEXT NOT NULL DEFAULT 'cfb'
            CHECK (league IN ('cfb', 'mbb', 'wbb')),
  user_id TEXT NOT NULL,
  season  INTEGER NOT NULL,
  week    INTEGER NOT NULL,
  game_id INTEGER NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('win','loss','void')),
  PRIMARY KEY (league, user_id, season, week),
  FOREIGN KEY (league, user_id, season, week)
    REFERENCES survivor_picks(league, user_id, season, week) ON DELETE CASCADE
);
CREATE INDEX survivor_scores_week ON survivor_scores(league, season, week);

CREATE TABLE survivor_board (
  league       TEXT NOT NULL DEFAULT 'cfb'
                 CHECK (league IN ('cfb', 'mbb', 'wbb')),
  season       INTEGER NOT NULL,
  user_id      TEXT NOT NULL,
  wins         INTEGER NOT NULL,
  alive        INTEGER NOT NULL CHECK (alive IN (0,1)),
  entered_week INTEGER NOT NULL,
  out_week     INTEGER,
  out_reason   TEXT CHECK (out_reason IN ('loss','missed')),
  rank         INTEGER NOT NULL,
  computed_at  INTEGER NOT NULL,
  ranked       INTEGER NOT NULL DEFAULT 1 CHECK (ranked IN (0, 1)),
  PRIMARY KEY (league, season, user_id)
);
CREATE INDEX survivor_board_rank ON survivor_board(league, season, rank);
CREATE INDEX survivor_board_ranked
  ON survivor_board(league, season, ranked, rank);

CREATE TABLE survivor_stranded (
  league      TEXT NOT NULL DEFAULT 'cfb'
                CHECK (league IN ('cfb', 'mbb', 'wbb')),
  season      INTEGER NOT NULL,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week        INTEGER NOT NULL,
  usable      INTEGER NOT NULL,
  computed_at INTEGER NOT NULL,
  PRIMARY KEY (league, season, user_id, week)
);
CREATE INDEX survivor_stranded_week ON survivor_stranded(league, season, week);

-- 4. Rows back, parents first ------------------------------------------------------

INSERT INTO weeks (league, season, week, status, published_at, lock_at,
                   game_count, pickable_count, source_sha256, scored_at,
                   scored_rev)
  SELECT league, season, week, status, published_at, lock_at, game_count,
         pickable_count, source_sha256, scored_at, scored_rev
    FROM bk_weeks;
INSERT INTO slate_games (league, season, week, game_id, home, away, kickoff_at,
                         spread_x2, spread_raw, books, frozen_at, b12, neutral)
  SELECT league, season, week, game_id, home, away, kickoff_at, spread_x2,
         spread_raw, books, frozen_at, b12, neutral
    FROM bk_slate_games;
INSERT INTO picks (league, user_id, season, week, game_id, side, spread_x2,
                   created_at, updated_at)
  SELECT league, user_id, season, week, game_id, side, spread_x2, created_at,
         updated_at
    FROM bk_picks;
INSERT INTO results (league, season, week, game_id, home_points, away_points,
                     status, ats, source_hash, revision, scored_at)
  SELECT league, season, week, game_id, home_points, away_points, status, ats,
         source_hash, revision, scored_at
    FROM bk_results;
INSERT INTO pick_scores (league, user_id, season, week, game_id, outcome)
  SELECT league, user_id, season, week, game_id, outcome FROM bk_pick_scores;
INSERT INTO leaderboard_week (league, season, week, user_id, w, l, p, v, pct,
                              rank, computed_at)
  SELECT league, season, week, user_id, w, l, p, v, pct, rank, computed_at
    FROM bk_leaderboard_week;
INSERT INTO leaderboard_season (league, season, user_id, w, l, p, v, pct,
                                weeks_played, rank, computed_at)
  SELECT league, season, user_id, w, l, p, v, pct, weeks_played, rank,
         computed_at
    FROM bk_leaderboard_season;
INSERT INTO survivor_picks (league, user_id, season, week, game_id, team,
                            created_at, updated_at)
  SELECT league, user_id, season, week, game_id, team, created_at, updated_at
    FROM bk_survivor_picks;
INSERT INTO survivor_scores (league, user_id, season, week, game_id, outcome)
  SELECT league, user_id, season, week, game_id, outcome
    FROM bk_survivor_scores;
INSERT INTO survivor_board (league, season, user_id, wins, alive, entered_week,
                            out_week, out_reason, rank, computed_at, ranked)
  SELECT league, season, user_id, wins, alive, entered_week, out_week,
         out_reason, rank, computed_at, ranked
    FROM bk_survivor_board;
INSERT INTO survivor_stranded (league, season, user_id, week, usable,
                               computed_at)
  SELECT league, season, user_id, week, usable, computed_at
    FROM bk_survivor_stranded;

DROP TABLE bk_weeks;
DROP TABLE bk_slate_games;
DROP TABLE bk_picks;
DROP TABLE bk_results;
DROP TABLE bk_pick_scores;
DROP TABLE bk_leaderboard_week;
DROP TABLE bk_leaderboard_season;
DROP TABLE bk_survivor_picks;
DROP TABLE bk_survivor_scores;
DROP TABLE bk_survivor_board;
DROP TABLE bk_survivor_stranded;

-- 5. Triggers, last, each scoped to its row's league ---------------------------

CREATE TRIGGER weeks_lock_monotonic
BEFORE UPDATE OF lock_at ON weeks
FOR EACH ROW WHEN OLD.lock_at IS NOT NULL
                  AND (NEW.lock_at IS NULL OR NEW.lock_at > OLD.lock_at)
BEGIN
  SELECT RAISE(ABORT, 'lock_at_may_not_move_later');
END;

CREATE TRIGGER slate_games_frozen
BEFORE UPDATE ON slate_games
FOR EACH ROW WHEN
     (OLD.spread_x2 IS NOT NULL AND OLD.spread_x2 IS NOT NEW.spread_x2)
  OR (OLD.kickoff_at <> NEW.kickoff_at
      AND (OLD.kickoff_at <= unixepoch() OR NEW.kickoff_at <= unixepoch()))
  OR OLD.game_id    <> NEW.game_id
  OR OLD.frozen_at  <> NEW.frozen_at
  OR OLD.league     <> NEW.league
BEGIN
  SELECT RAISE(ABORT, 'slate_frozen');
END;

CREATE TRIGGER slate_games_no_delete_after_lock
BEFORE DELETE ON slate_games
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM weeks w
   WHERE w.league = OLD.league AND w.season = OLD.season AND w.week = OLD.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN
  SELECT RAISE(ABORT, 'slate_locked');
END;

-- The pick'em lock. Football: the week's lock_at, as since 0001.
CREATE TRIGGER picks_locked_insert
BEFORE INSERT ON picks
FOR EACH ROW WHEN NEW.league = 'cfb' AND EXISTS (
  SELECT 1 FROM weeks w
   WHERE w.league = NEW.league AND w.season = NEW.season AND w.week = NEW.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'week_locked'); END;

CREATE TRIGGER picks_locked_update
BEFORE UPDATE ON picks
FOR EACH ROW WHEN OLD.league = 'cfb' AND EXISTS (
  SELECT 1 FROM weeks w
   WHERE w.league = OLD.league AND w.season = OLD.season AND w.week = OLD.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'week_locked'); END;

CREATE TRIGGER picks_locked_delete
BEFORE DELETE ON picks
FOR EACH ROW WHEN OLD.league = 'cfb' AND EXISTS (
  SELECT 1 FROM weeks w
   WHERE w.league = OLD.league AND w.season = OLD.season AND w.week = OLD.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'week_locked'); END;

-- Basketball: each game at its own tip.
CREATE TRIGGER picks_game_started_insert
BEFORE INSERT ON picks
FOR EACH ROW WHEN NEW.league <> 'cfb' AND EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = NEW.league AND g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id AND g.kickoff_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'game_started'); END;

CREATE TRIGGER picks_game_started_update
BEFORE UPDATE ON picks
FOR EACH ROW WHEN OLD.league <> 'cfb' AND EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = OLD.league AND g.season = OLD.season AND g.week = OLD.week
     AND g.game_id IN (OLD.game_id, NEW.game_id)
     AND g.kickoff_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'game_started'); END;

CREATE TRIGGER picks_game_started_delete
BEFORE DELETE ON picks
FOR EACH ROW WHEN OLD.league <> 'cfb' AND EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = OLD.league AND g.season = OLD.season AND g.week = OLD.week
     AND g.game_id = OLD.game_id AND g.kickoff_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'game_started'); END;

CREATE TRIGGER picks_require_line_insert
BEFORE INSERT ON picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = NEW.league AND g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id
     AND g.spread_x2 IS NOT NULL AND g.spread_x2 = NEW.spread_x2)
BEGIN SELECT RAISE(ABORT, 'unpickable_or_stale_line'); END;

CREATE TRIGGER picks_require_line_update
BEFORE UPDATE ON picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = NEW.league AND g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id
     AND g.spread_x2 IS NOT NULL AND g.spread_x2 = NEW.spread_x2)
BEGIN SELECT RAISE(ABORT, 'unpickable_or_stale_line'); END;

CREATE TRIGGER survivor_locked_insert
BEFORE INSERT ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = NEW.league AND g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id AND g.kickoff_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'game_started'); END;

CREATE TRIGGER survivor_locked_update
BEFORE UPDATE ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = OLD.league AND g.season = OLD.season AND g.week = OLD.week
     AND g.game_id IN (OLD.game_id, NEW.game_id)
     AND g.kickoff_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'game_started'); END;

CREATE TRIGGER survivor_locked_delete
BEFORE DELETE ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = OLD.league AND g.season = OLD.season AND g.week = OLD.week
     AND g.game_id = OLD.game_id AND g.kickoff_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'game_started'); END;

CREATE TRIGGER survivor_in_game_insert
BEFORE INSERT ON survivor_picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = NEW.league AND g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id
     AND NEW.team IN (g.home, g.away))
BEGIN SELECT RAISE(ABORT, 'survivor_not_in_game'); END;

CREATE TRIGGER survivor_in_game_update
BEFORE UPDATE ON survivor_picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.league = NEW.league AND g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id
     AND NEW.team IN (g.home, g.away))
BEGIN SELECT RAISE(ABORT, 'survivor_not_in_game'); END;

CREATE TRIGGER survivor_no_reuse_insert
BEFORE INSERT ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM survivor_picks sp
   WHERE sp.league = NEW.league AND sp.user_id = NEW.user_id
     AND sp.season = NEW.season
     AND sp.week <> NEW.week AND sp.team = NEW.team
     AND NOT EXISTS (
       SELECT 1 FROM survivor_scores ss
        WHERE ss.league = sp.league AND ss.user_id = sp.user_id
          AND ss.season = sp.season AND ss.week = sp.week
          AND ss.outcome = 'void'))
BEGIN SELECT RAISE(ABORT, 'survivor_team_reused'); END;

CREATE TRIGGER survivor_no_reuse_update
BEFORE UPDATE ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM survivor_picks sp
   WHERE sp.league = NEW.league AND sp.user_id = NEW.user_id
     AND sp.season = NEW.season
     AND sp.week <> NEW.week AND sp.team = NEW.team
     AND NOT EXISTS (
       SELECT 1 FROM survivor_scores ss
        WHERE ss.league = sp.league AND ss.user_id = sp.user_id
          AND ss.season = sp.season AND ss.week = sp.week
          AND ss.outcome = 'void'))
BEGIN SELECT RAISE(ABORT, 'survivor_team_reused'); END;

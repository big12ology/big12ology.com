-- Which pool a row belongs to: football, men's basketball or women's.
--
-- The pools were written for football, so every table is keyed by season and
-- week alone. Basketball reuses all of it (the spec: claude.ai/code/artifact/
-- f7d4175c-16e7-46c4-b533-678d1b2042b6), and its season 2027 week 3 is not
-- football's, so every pools table gains `league`.
--
-- THE FIRST OF TWO STEPS, AND ONLY ADDITIONS. The Worker deploys on every push
-- to main; migrations run when someone dispatches migrate.yml. Whatever this
-- file does, the code already running has to survive it, and that code names
-- the old keys in its ON CONFLICT clauses: rebuild a primary key under it and
-- every upsert fails with "ON CONFLICT clause does not match any PRIMARY KEY
-- or UNIQUE constraint" until the new code lands. So:
--
--   this file   adds the column, defaulting to 'cfb' so every existing row
--               and every insert the old code makes is football's, and adds a
--               unique index with league in front of each old key. Old code
--               runs unchanged; new code's ON CONFLICT (league, ...) targets
--               resolve against these indexes.
--   a later one, before basketball opens, rebuilds the tables so league is
--               in the primary keys, drops the old keys (two leagues can then
--               share a season and week), and rewrites the triggers that look
--               rows up by season and week alone. That one needs the new code
--               already running, so it cannot be this one.
--
-- Nothing here changes what football does: the old keys still hold, every row
-- is 'cfb', and the triggers are untouched.

ALTER TABLE weeks ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE slate_games ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE picks ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE results ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE results_history ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE pick_scores ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE leaderboard_week ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE leaderboard_season ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE survivor_picks ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE survivor_scores ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE survivor_board ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));
ALTER TABLE survivor_stranded ADD COLUMN league TEXT NOT NULL DEFAULT 'cfb'
  CHECK (league IN ('cfb', 'mbb', 'wbb'));

-- Each old key again with league in front. Unique because the old key is,
-- and the conflict target the new code's upserts name.
CREATE UNIQUE INDEX weeks_league ON weeks(league, season, week);
CREATE UNIQUE INDEX slate_games_league
  ON slate_games(league, season, week, game_id);
CREATE UNIQUE INDEX slate_game_unique_league
  ON slate_games(league, season, game_id);
CREATE UNIQUE INDEX picks_league
  ON picks(league, user_id, season, week, game_id);
CREATE UNIQUE INDEX results_league ON results(league, season, week, game_id);
CREATE UNIQUE INDEX pick_scores_league
  ON pick_scores(league, user_id, season, week, game_id);
CREATE UNIQUE INDEX leaderboard_week_league
  ON leaderboard_week(league, season, week, user_id);
CREATE UNIQUE INDEX leaderboard_season_league
  ON leaderboard_season(league, season, user_id);
CREATE UNIQUE INDEX survivor_picks_league
  ON survivor_picks(league, user_id, season, week);
CREATE UNIQUE INDEX survivor_scores_league
  ON survivor_scores(league, user_id, season, week);
CREATE UNIQUE INDEX survivor_board_league
  ON survivor_board(league, season, user_id);
CREATE UNIQUE INDEX survivor_stranded_league
  ON survivor_stranded(league, season, user_id, week);
CREATE INDEX results_history_league
  ON results_history(league, season, week, game_id, id);

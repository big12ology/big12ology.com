-- The Kansas City bracket challenge: one bracket per player, per basketball
-- league and season, picked whole before the first-round tip.
--
-- The bracket itself comes from the repository like a slate does
-- (basketball/pickem/<league>/<season>/bracket.json, written by
-- bb_pickem.write_bracket once the regular season is over): the sixteen seeds,
-- the fifteen games as pairs of seeds or earlier games, the points per game,
-- the lock, and the results as they arrive. The Worker keeps a copy here to
-- check entries against and score them by.
--
-- Same guarantees the pick'em's tables give, in the same place, the database:
-- once the lock passes the seeds, the shape and the points cannot change and
-- no entry can be written, changed or withdrawn; the lock itself may only move
-- earlier. Results keep arriving after the lock, which is the point.

CREATE TABLE brackets (
  league        TEXT NOT NULL CHECK (league IN ('mbb', 'wbb')),
  season        INTEGER NOT NULL,
  published_at  INTEGER NOT NULL,
  lock_at       INTEGER NOT NULL,
  seeds         TEXT NOT NULL,                 -- JSON: sixteen teams by seed
  games         TEXT NOT NULL,                 -- JSON: fifteen [a, b] pairs
  points        TEXT NOT NULL,                 -- JSON: fifteen point values
  results       TEXT NOT NULL DEFAULT '{}',    -- JSON: {game index: winner}
  source_sha256 TEXT,
  PRIMARY KEY (league, season)
);

CREATE TRIGGER brackets_frozen
BEFORE UPDATE ON brackets
FOR EACH ROW WHEN OLD.lock_at <= unixepoch() AND (
     OLD.seeds  <> NEW.seeds
  OR OLD.games  <> NEW.games
  OR OLD.points <> NEW.points)
BEGIN SELECT RAISE(ABORT, 'bracket_frozen'); END;

CREATE TRIGGER brackets_lock_monotonic
BEFORE UPDATE OF lock_at ON brackets
FOR EACH ROW WHEN NEW.lock_at > OLD.lock_at
BEGIN SELECT RAISE(ABORT, 'lock_at_may_not_move_later'); END;

CREATE TABLE bracket_entries (
  league     TEXT NOT NULL CHECK (league IN ('mbb', 'wbb')),
  season     INTEGER NOT NULL,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  picks      TEXT NOT NULL,                    -- JSON: fifteen winners or null
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (league, season, user_id),
  FOREIGN KEY (league, season) REFERENCES brackets(league, season)
    ON DELETE RESTRICT
);

CREATE TRIGGER bracket_entries_locked_insert
BEFORE INSERT ON bracket_entries
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM brackets b
   WHERE b.league = NEW.league AND b.season = NEW.season
     AND b.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'bracket_locked'); END;

CREATE TRIGGER bracket_entries_locked_update
BEFORE UPDATE ON bracket_entries
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM brackets b
   WHERE b.league = OLD.league AND b.season = OLD.season
     AND b.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'bracket_locked'); END;

CREATE TRIGGER bracket_entries_locked_delete
BEFORE DELETE ON bracket_entries
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM brackets b
   WHERE b.league = OLD.league AND b.season = OLD.season
     AND b.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'bracket_locked'); END;

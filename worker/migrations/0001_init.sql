-- The pick'em database.
--
-- Three things in here are not allowed to be wrong, and all three are enforced
-- by triggers rather than by handler code:
--
--   1. A published line never moves.        slate_games_frozen
--   2. A pick never changes after the lock. picks_locked_{insert,update,delete}
--   3. A lock never moves later.            weeks_lock_monotonic
--
-- Handler code is the wrong place for them. It is the part most likely to be
-- refactored, it is duplicated across every endpoint that writes, and a bug in
-- it is invisible: the wrong number is still a number, and a leaderboard
-- computed from a line that moved looks exactly like one that is correct. Put
-- them here and there is no code path — no admin endpoint, no console session,
-- no migration script written in a hurry — that can get around them.
--
-- Two conventions run through the whole file:
--
--   * All times are INTEGER unix seconds. They compare with unixepoch() in the
--     triggers, and they sidestep the ISO-8601 format drift already visible
--     between games_*.json ("...000Z") and forecasts/*.json ("+00:00").
--   * The spread is stored as spread_x2: the home spread times two, as an
--     integer. Scoring compares margin-plus-spread against zero to find a
--     push, and -12.8 as a float makes that comparison a rounding-error
--     lottery. Doubling a half-point line makes every value exact.

-- D1 enforces foreign keys by default; this is here for node:sqlite, where the
-- schema tests run and where the default is off.
PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------- identity

CREATE TABLE users (
  id              TEXT PRIMARY KEY,          -- ULID, generated in the Worker
  display_name    TEXT,                      -- NULL until the player chooses
  display_norm    TEXT,                      -- normalised; see src/names.js
  -- A new account's picks count, but it stays off the public board until it
  -- has completed one scored week. Anonymous OAuth cannot stop someone farming
  -- accounts, so this does not pretend to: it removes the payoff. A throwaway
  -- cannot appear on the leaderboard at all, and a real new player is
  -- inconvenienced for exactly one week.
  status          TEXT NOT NULL DEFAULT 'provisional'
                    CHECK (status IN ('provisional','active','shadowbanned','banned')),
  is_admin        INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0,1)),
  created_at      INTEGER NOT NULL,
  name_changed_at INTEGER,
  -- HMAC, never the address itself. Enough to notice twenty accounts from one
  -- place, useless for finding out where anyone lives. Campus and dorm NAT
  -- make shared addresses the normal case here, so this feeds review, never an
  -- automatic block.
  signup_ip_hash  TEXT,
  signup_asn      INTEGER
);
CREATE UNIQUE INDEX users_display_norm
  ON users(display_norm) WHERE display_norm IS NOT NULL;

-- A rename leaves a trail, so a banned display name cannot be laundered into a
-- clean one and then quietly reclaimed.
CREATE TABLE name_history (
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  display_norm TEXT NOT NULL,
  display_name TEXT NOT NULL,
  set_at       INTEGER NOT NULL,
  PRIMARY KEY (user_id, set_at)
);

-- What the provider told us, and nothing else. subject_hash is
-- base64url(HMAC-SHA256(IDENTITY_PEPPER, provider || '|' || sub)) — a dump of
-- this table without the pepper links to no one. We never request or store an
-- email address, a name or a picture.
CREATE TABLE identities (
  provider     TEXT NOT NULL CHECK (provider IN ('google','github')),
  subject_hash TEXT NOT NULL,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  linked_at    INTEGER NOT NULL,
  PRIMARY KEY (provider, subject_hash)
);
CREATE INDEX identities_user ON identities(user_id);

-- KV is the hot path for session lookup. This exists for audit, for "sign out
-- everywhere", and because KV's eventual consistency makes it a poor system of
-- record for revocation.
CREATE TABLE sessions (
  sid_hash   TEXT PRIMARY KEY,               -- SHA-256 of the cookie value
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  revoked_at INTEGER,
  ua_hash    TEXT,
  ip_hash    TEXT
);
CREATE INDEX sessions_user ON sessions(user_id, expires_at);

-- -------------------------------------------------------------------- slate

-- `week` is the pick'em week from tiebreaker/pickem.py — Tuesday-to-Monday,
-- derived from the kickoff's Central-local date. It is NOT CFBD's week field,
-- whose 2025 "week 1" spans nine days, and it is not the tiebreaker site's
-- week either. Nothing should ever join on it. Join on game_id.
CREATE TABLE weeks (
  season         INTEGER NOT NULL,
  week           INTEGER NOT NULL,
  status         TEXT NOT NULL DEFAULT 'published'
                   CHECK (status IN ('published','no_contest','final')),
  published_at   INTEGER NOT NULL,
  lock_at        INTEGER,                    -- NULL when no game has a line
  game_count     INTEGER NOT NULL,
  pickable_count INTEGER NOT NULL,
  source_sha256  TEXT,                       -- of the committed week-NN.json
  scored_at      INTEGER,
  scored_rev     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (season, week)
);

CREATE TABLE slate_games (
  season      INTEGER NOT NULL,
  week        INTEGER NOT NULL,
  game_id     INTEGER NOT NULL,              -- CFBD/ESPN id. The only join key.
  home        TEXT NOT NULL,
  away        TEXT NOT NULL,
  kickoff_at  INTEGER NOT NULL,
  spread_x2   INTEGER,                       -- frozen home spread × 2; NULL = unpickable
  spread_raw  REAL,                          -- the averaged value, audit only
  books       INTEGER,
  frozen_at   INTEGER NOT NULL,
  PRIMARY KEY (season, week, game_id),
  FOREIGN KEY (season, week) REFERENCES weeks(season, week) ON DELETE CASCADE
);
-- A game belongs to exactly one week. This is the constraint that makes
-- joining on game_id safe, and it catches the failure mode where an upstream
-- re-week would otherwise duplicate a game into two slates.
CREATE UNIQUE INDEX slate_game_unique ON slate_games(season, game_id);

-- THE FROZEN LINE.
--
-- A line that has been published is the number people are being scored
-- against, and moving it rewrites what they played. Not "should not" — cannot.
--
-- The one permitted transition is NULL to a value. A game published without a
-- market never froze anything, so filling it in later changes nothing anyone
-- acted on; that is how a Tuesday with no line becomes playable by Thursday
-- instead of dead for the week. Everything else about the row that a pick
-- depends on is immutable.
CREATE TRIGGER slate_games_frozen
BEFORE UPDATE ON slate_games
FOR EACH ROW WHEN
     (OLD.spread_x2 IS NOT NULL AND OLD.spread_x2 IS NOT NEW.spread_x2)
  OR OLD.kickoff_at <> NEW.kickoff_at
  OR OLD.game_id    <> NEW.game_id
  OR OLD.frozen_at  <> NEW.frozen_at
BEGIN
  SELECT RAISE(ABORT, 'slate_frozen');
END;

-- Removing a game after the lock would silently delete the picks on it.
-- A game that vanishes upstream is voided at scoring time, where it is
-- visible on the card, not deleted here, where it is not.
CREATE TRIGGER slate_games_no_delete_after_lock
BEFORE DELETE ON slate_games
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM weeks w
   WHERE w.season = OLD.season AND w.week = OLD.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN
  SELECT RAISE(ABORT, 'slate_locked');
END;

-- The lock may move earlier — a game is added whose kickoff is sooner — and
-- never later. Later reopens a week that has already started, which would let
-- a pick be entered on a game whose result is known.
CREATE TRIGGER weeks_lock_monotonic
BEFORE UPDATE OF lock_at ON weeks
FOR EACH ROW WHEN OLD.lock_at IS NOT NULL
                  AND (NEW.lock_at IS NULL OR NEW.lock_at > OLD.lock_at)
BEGIN
  SELECT RAISE(ABORT, 'lock_at_may_not_move_later');
END;

-- -------------------------------------------------------------------- picks

CREATE TABLE picks (
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  season     INTEGER NOT NULL,
  week       INTEGER NOT NULL,
  game_id    INTEGER NOT NULL,
  side       TEXT NOT NULL CHECK (side IN ('home','away')),
  -- The receipt. Denormalised on purpose and validated against the slate by
  -- picks_require_line: if a line ever did move, these rows independently
  -- record what was on screen when the pick was made, and the mismatch is
  -- detectable instead of merely suspected.
  spread_x2  INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, season, week, game_id),
  FOREIGN KEY (season, week, game_id)
    REFERENCES slate_games(season, week, game_id) ON DELETE RESTRICT
);
CREATE INDEX picks_week ON picks(season, week);

-- THE LOCK, on all three verbs.
--
-- The handler checks this too, cheaply, so the common case is a clean 409
-- before anything is touched. These exist for the request that crosses the
-- boundary mid-flight, and for every future code path that forgets to ask.
CREATE TRIGGER picks_locked_insert
BEFORE INSERT ON picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM weeks w WHERE w.season = NEW.season AND w.week = NEW.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'week_locked'); END;

CREATE TRIGGER picks_locked_update
BEFORE UPDATE ON picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM weeks w WHERE w.season = OLD.season AND w.week = OLD.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'week_locked'); END;

CREATE TRIGGER picks_locked_delete
BEFORE DELETE ON picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM weeks w WHERE w.season = OLD.season AND w.week = OLD.week
     AND w.lock_at IS NOT NULL AND w.lock_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'week_locked'); END;

-- You may not pick a game with no posted line, and the number you submit must
-- be the number on the slate. Both verbs, because changing a side must not be
-- a way to smuggle in a different spread.
CREATE TRIGGER picks_require_line_insert
BEFORE INSERT ON picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.season = NEW.season AND g.week = NEW.week AND g.game_id = NEW.game_id
     AND g.spread_x2 IS NOT NULL AND g.spread_x2 = NEW.spread_x2)
BEGIN SELECT RAISE(ABORT, 'unpickable_or_stale_line'); END;

CREATE TRIGGER picks_require_line_update
BEFORE UPDATE ON picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.season = NEW.season AND g.week = NEW.week AND g.game_id = NEW.game_id
     AND g.spread_x2 IS NOT NULL AND g.spread_x2 = NEW.spread_x2)
BEGIN SELECT RAISE(ABORT, 'unpickable_or_stale_line'); END;

-- ------------------------------------------------------------------ results

CREATE TABLE results (
  season      INTEGER NOT NULL,
  week        INTEGER NOT NULL,
  game_id     INTEGER NOT NULL,
  home_points INTEGER,
  away_points INTEGER,
  status      TEXT NOT NULL CHECK (status IN ('scheduled','final','void')),
  ats         TEXT CHECK (ats IN ('home','away','push','void')),
  -- Scoring writes only when this changes, so a re-run is a no-op and a
  -- corrected score is an explicit revision rather than a silent movement.
  source_hash TEXT NOT NULL,
  revision    INTEGER NOT NULL DEFAULT 1,
  scored_at   INTEGER NOT NULL,
  PRIMARY KEY (season, week, game_id),
  FOREIGN KEY (season, week, game_id)
    REFERENCES slate_games(season, week, game_id) ON DELETE CASCADE
);

CREATE TABLE pick_scores (
  user_id TEXT NOT NULL,
  season  INTEGER NOT NULL,
  week    INTEGER NOT NULL,
  game_id INTEGER NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('win','loss','push','void')),
  PRIMARY KEY (user_id, season, week, game_id),
  FOREIGN KEY (user_id, season, week, game_id)
    REFERENCES picks(user_id, season, week, game_id) ON DELETE CASCADE
);
CREATE INDEX pick_scores_week ON pick_scores(season, week);

-- -------------------------------------------------------- materialised board

-- Recomputed wholesale by the scoring cron, never incrementally: a board that
-- can drift from the picks it claims to summarise is worse than a slow one.
-- pct counts neither pushes nor voids in either term.
CREATE TABLE leaderboard_week (
  season INTEGER NOT NULL, week INTEGER NOT NULL, user_id TEXT NOT NULL,
  w INTEGER NOT NULL, l INTEGER NOT NULL, p INTEGER NOT NULL, v INTEGER NOT NULL,
  pct REAL, rank INTEGER NOT NULL, computed_at INTEGER NOT NULL,
  PRIMARY KEY (season, week, user_id)
);
CREATE INDEX lb_week_rank ON leaderboard_week(season, week, rank);

CREATE TABLE leaderboard_season (
  season INTEGER NOT NULL, user_id TEXT NOT NULL,
  w INTEGER NOT NULL, l INTEGER NOT NULL, p INTEGER NOT NULL, v INTEGER NOT NULL,
  pct REAL, weeks_played INTEGER NOT NULL, rank INTEGER NOT NULL,
  computed_at INTEGER NOT NULL,
  PRIMARY KEY (season, user_id)
);
CREATE INDEX lb_season_rank ON leaderboard_season(season, rank);

-- ------------------------------------------------------------ ops / control

-- In D1, not KV: the free tier allows 1,000 KV writes a day, so a counter that
-- writes on every request is a self-inflicted outage.
CREATE TABLE rate_limits (
  bucket       TEXT PRIMARY KEY,
  window_start INTEGER NOT NULL,
  count        INTEGER NOT NULL
);

CREATE TABLE audit_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  at      INTEGER NOT NULL,
  actor   TEXT,
  action  TEXT NOT NULL,
  subject TEXT,
  detail  TEXT
);
CREATE INDEX audit_at ON audit_log(at);

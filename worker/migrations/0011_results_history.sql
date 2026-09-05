-- One row per result that actually moved.
--
-- WHAT THIS IS FOR. results.revision already counts corrections, and the
-- comment on it says the point is that a board moving after the fact should be
-- "explicable rather than merely mysterious". It is not, quite. A count with a
-- single scored_at can say a row was rewritten fifteen times; it cannot say
-- when, in which direction, or what it held in between, because each write
-- overwrites the last. So the column proves that something happened and then
-- refuses to say what.
--
-- 2026 week 1 is the case that made that gap expensive. Three Thursday games
-- came out final on Friday morning, went void some time on Saturday, and were
-- final again by Saturday evening, and a player screenshotted the middle of
-- it: three VOID chips and a record that had dropped from 1-4 to 1-1, because
-- a void does not count. By the time anyone looked, the rows read final and
-- correct, and the only surviving evidence was revision 15, 11 and 11 against
-- revision 1 on every other game of the week. Enough to prove a regression.
-- Not enough to find one.
--
-- APPEND ONLY, and nothing reads it on a request path. This is a log for the
-- next investigation, not an input to a board, so it is deliberately dumb:
-- insert on change, never update, never delete, and let a query sort it out
-- later.
--
-- id RATHER THAN (season, week, game_id, revision) AS THE KEY. Two workflows
-- publish scores -- scores.yml on its cron and pages.yml on every deploy --
-- and they share no concurrency group with each other. Two passes that read
-- the same revision and both write revision+1 would collide on a natural key
-- and fail the whole batch, which would take the results write down with it
-- for the sake of a log entry. An autoincrement id cannot collide, and a
-- duplicate revision number in the log is itself worth seeing: it means two
-- graders were in the same row at the same moment.
--
-- COST. A write only happens when source_hash changed, which is the same test
-- that gates the results write itself, so a quiet pass adds nothing. A settled
-- week is about fifteen rows, one per game. The pathological week that
-- prompted this would have been about forty.
CREATE TABLE results_history (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  season      INTEGER NOT NULL,
  week        INTEGER NOT NULL,
  game_id     INTEGER NOT NULL,
  -- The revision this write produced, so a row here lines up with what
  -- results.revision read at the time.
  revision    INTEGER NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('scheduled','final','void')),
  ats         TEXT CHECK (ats IN ('home','away','push','void')),
  home_points INTEGER,
  away_points INTEGER,
  scored_at   INTEGER NOT NULL
);

-- The two questions this table exists to answer. One game's life, in order:
CREATE INDEX results_history_game ON results_history(season, week, game_id, id);
-- and everything that moved in a window, when the report is "the board changed
-- overnight" and nobody knows which game yet.
CREATE INDEX results_history_at ON results_history(scored_at);

-- NO FOREIGN KEY TO results, on purpose. The parent is ON DELETE CASCADE from
-- slate_games, and a slate rewrite that drops a game would then delete the
-- record of what that game did -- which is exactly the history somebody would
-- be looking for at that point. A log that a schema change can quietly erase
-- is not a log.

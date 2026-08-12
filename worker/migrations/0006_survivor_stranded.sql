-- The week a player had nothing left to pick.
--
-- Survivor eliminates you for letting a week lock without a pick, which is
-- right: the roster is the game, and forgetting is a real mistake. But there
-- is a version of "no pick" that is not a mistake at all — every Big 12 team
-- you have not already spent is on a bye, or is in a game nobody posted a
-- line on, so the board had nothing on it you were allowed to choose. The
-- walk could not tell those two apart, and eliminated both.
--
-- It is not reachable on the 2026 schedule: the byes land in weeks four to
-- nine, when nobody has spent more than eight teams, and weeks ten to
-- thirteen are a full sixteen. That is a fact about one season's draw and not
-- a property of the game — a season with byes in November, or a stretch where
-- several games go without a line, moves the arithmetic without anybody
-- touching the code. So this is a guard against a rule the pool would
-- otherwise be enforcing by accident.
--
-- Derived, like survivor_scores and survivor_board: deleted and rebuilt
-- wholesale by the scoring cron from the frozen slate and the picks. Never
-- written by a handler, never adjusted. It cannot be a 'void' row in
-- survivor_scores, which is where this belongs by meaning, because that table
-- is keyed to a pick and the whole point here is that there is no pick.
--
-- No spare-the-team clause is needed. Nothing was spent, because nothing was
-- picked; the roster carries over untouched, which is exactly what a player
-- who could not play deserves.
CREATE TABLE survivor_stranded (
  season      INTEGER NOT NULL,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week        INTEGER NOT NULL,
  usable      INTEGER NOT NULL,   -- always 0 today; stored so a future
                                  -- "fewer than N left" rule has somewhere
                                  -- to put its number, and so a support
                                  -- question has an answer in the row
  computed_at INTEGER NOT NULL,
  PRIMARY KEY (season, user_id, week)
);
CREATE INDEX survivor_stranded_week ON survivor_stranded(season, week);

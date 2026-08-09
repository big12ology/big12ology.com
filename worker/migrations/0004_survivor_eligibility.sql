-- Who is playing for the title, and who is just playing.
--
-- Joining stays open all season — the chalk handicap prices the roster
-- advantage, so a late entrant is not getting anything free. What it cannot
-- price is the length of the run. In a survivor pool almost everybody is dead
-- by December, so somebody who entered in the last week and won once could be
-- the ONLY player still alive and take the season on a single pick. That is
-- not a pool anybody would enter in August.
--
-- So entry stays open and the leaderboard does not. Enter by the cutoff and
-- you are ranked; enter after it and you play the same game, under the same
-- handicap, with the same board position visible — you are simply not in the
-- running for the season. It is knowable before you sign up, which is the
-- whole point.
--
-- `ranked` rather than a rule applied at read time: the board is materialised,
-- and a leaderboard whose membership is recomputed differently by every reader
-- is a leaderboard that disagrees with itself.

ALTER TABLE survivor_board
  ADD COLUMN ranked INTEGER NOT NULL DEFAULT 1 CHECK (ranked IN (0, 1));

-- Rank is issued within each group (see the PARTITION in rebuildSurvivorBoard),
-- so an unranked entrant carries a number that is never displayed rather than
-- a NULL that would force the column nullable for everyone.
CREATE INDEX survivor_board_ranked ON survivor_board(season, ranked, rank);

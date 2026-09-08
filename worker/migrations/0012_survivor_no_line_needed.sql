-- Survivor stops requiring a betting line.
--
-- WHAT WAS WRONG. survivor_in_game_insert and _update have asked for
-- spread_x2 IS NOT NULL since 0003, and every layer above them agreed: the API
-- returns "unpickable", and the client drops the row from the card entirely.
-- So a Big 12 team hosting an FCS opponent simply was not on the survivor
-- board. 2026 week 2 lost six of fifteen games that way: UT Martin at West
-- Virginia, Weber State at Colorado, Western Carolina at Cincinnati, Southern
-- at Houston, Prairie View A&M at Baylor, Grambling at TCU.
--
-- WHY IT IS WRONG RATHER THAN STRICT. The rules page promises "the card shows
-- every game a Big 12 team plays", and one paragraph earlier it says the pick
-- is "to win outright. Not to cover a spread -- to win." A survivor pick never
-- touches the number. The requirement is the pick'em's, where it is correct
-- and stays: you cannot pick against a spread that does not exist.
--
-- It also removed exactly the weeks a survivor player wants. Baylor over
-- Prairie View A&M is the safe week the whole format is built around spending,
-- and those were the only games the pool would not sell.
--
-- The line is still frozen and still refused a change, because the pick'em
-- depends on it; slate_games and its other triggers are untouched. Only the
-- two that decide whether a survivor pick names a real game are rebuilt, and
-- only the spread clause is dropped from them.
--
-- CODE HAS TO LAND WITH THIS. Without the grader writing results for a
-- lineless game, a pick allowed here would join to nothing in
-- rebuildSurvivorScores and sit ungraded for good: not a win, not a loss, just
-- absent. Deploy the Worker after this, and see worker.yml's schema gate,
-- which refuses to ship code whose migrations are not applied.

DROP TRIGGER survivor_in_game_insert;
DROP TRIGGER survivor_in_game_update;

-- Identical to 0003's pair, less "AND g.spread_x2 IS NOT NULL". The rest of
-- the condition is the point of the trigger and is unchanged: the game must be
-- on this season's slate for this week, and the team must be one of the two
-- playing in it.
CREATE TRIGGER survivor_in_game_insert
BEFORE INSERT ON survivor_picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id
     AND NEW.team IN (g.home, g.away))
BEGIN SELECT RAISE(ABORT, 'survivor_not_in_game'); END;

CREATE TRIGGER survivor_in_game_update
BEFORE UPDATE ON survivor_picks
FOR EACH ROW WHEN NOT EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id
     AND NEW.team IN (g.home, g.away))
BEGIN SELECT RAISE(ABORT, 'survivor_not_in_game'); END;

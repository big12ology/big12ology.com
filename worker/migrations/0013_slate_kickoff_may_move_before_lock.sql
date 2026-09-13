-- A kickoff may be corrected, but only while the game has not started.
--
-- WHAT WAS WRONG. slate_games.kickoff_at has been immutable since 0001, on the
-- same footing as the frozen line. That is right for a game people have
-- already played, and wrong for a game that has not kicked, because it also
-- froze in a time that was simply incorrect.
--
-- THE CASE THAT MATTERS IS start_tbd. CFBD returns a placeholder hour for an
-- unannounced window, and pickem.py knows it: such a game is published with
-- the placeholder in kickoff_at and marked unpickable with "kickoff_tbd",
-- because, in its words, picking against it would mean locking at an hour that
-- was never real. That guard holds only while the game stays unpickable. When
-- the window is announced the game gets a line and goes playable, the import
-- fills the NULL spread in because the trigger permits exactly that, and the
-- kickoff stayed the placeholder, because nothing could move it. The game then
-- locked at the hour that was never real, which is the thing the publisher
-- refused to let happen. As of 2026-09-13 that is 7 or 8 games a week from
-- week 4 through week 13.
--
-- IT ALSO HAPPENS WITH start_tbd FALSE. 2026 week 2, Oklahoma State at Oregon
-- (401856782): the published slate said 2026-09-12T16:00Z. ESPN had it
-- scheduled at 17:30Z, and the first snap came at 17:28Z. Ninety minutes.
-- Utah/Arkansas was fifteen minutes early and Baylor/Prairie View A&M eight;
-- the other twelve were exact.
--
-- WHAT A WRONG KICKOFF COSTS. Two things key off this column directly:
--
--   Picks lock at kickoff_at (api.js, and survivor per game in 0010). A time
--   that is early closes the card while the game is still an hour away. A time
--   that is late is the serious direction: it would accept a pick on a game
--   already being played.
--
--   The ESPN fill measures its settle window from kickoff_at (espn.js). Early
--   means it asks the scoreboard about a game still in progress, which costs a
--   call and nothing else. Late means it waits past the point it was built to
--   cover.
--
-- THE ONE PERMITTED TRANSITION, and it is the same shape as the NULL-to-value
-- rule the line already has: a kickoff may move only while the game has not
-- started under EITHER the old time or the new one. Both in the future means
-- nobody's pick was locked by the old value and none is locked by the new, so
-- nothing anyone acted on changes. Once either side is in the past the row is
-- history and stays put.
--
-- A correction that arrives after the OLD time has passed is therefore
-- refused, on purpose. By then the card is locked, and moving the kickoff
-- later would reopen picking on a game, which is the same hazard
-- weeks_lock_monotonic refuses at the week level.
--
-- THIS MAKES A CORRECTION POSSIBLE, IT DOES NOT MAKE ONE. The 16:00 above came
-- from the CFBD cache the Tuesday build reads (tiebreaker/data/games_2026.json,
-- where that game carries start_tbd false and the wrong time), so the
-- published week-02.json carries it too and a re-import would import it again.
-- Nothing here reaches upstream.

DROP TRIGGER slate_games_frozen;

-- Identical to 0001's, with one clause widened. spread_x2, game_id and
-- frozen_at are untouched and still refuse any change at all.
CREATE TRIGGER slate_games_frozen
BEFORE UPDATE ON slate_games
FOR EACH ROW WHEN
     (OLD.spread_x2 IS NOT NULL AND OLD.spread_x2 IS NOT NEW.spread_x2)
  OR (OLD.kickoff_at <> NEW.kickoff_at
      AND (OLD.kickoff_at <= unixepoch() OR NEW.kickoff_at <= unixepoch()))
  OR OLD.game_id    <> NEW.game_id
  OR OLD.frozen_at  <> NEW.frozen_at
BEGIN
  SELECT RAISE(ABORT, 'slate_frozen');
END;

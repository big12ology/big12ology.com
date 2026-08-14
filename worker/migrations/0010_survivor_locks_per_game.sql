-- Survivor locks per game, not per week.
--
-- WHY THE PICK'EM AND SURVIVOR SHOULD NOT SHARE A DEADLINE. On the card,
-- locking every game at the first kickoff is the fairness rule: one slate, one
-- deadline, and nobody picks on more information than anybody else. That
-- argument does not carry over. A survivor entry is one team in one game — the
-- other fourteen games are not on your card and knowing how they are going
-- tells you nothing about the one you took. So the week deadline was not
-- protecting anything here; it was only taking Friday and Saturday away
-- because a Thursday game existed.
--
-- 2026 week 1 is the case that made it obvious: six pickable games, the first
-- two at Thursday lunchtime and the last on Friday afternoon. An hour into the
-- week, four games had not kicked off and none of them could be picked — one
-- of them a full day out.
--
-- WHAT STAYS WEEKLY, and this is the half that matters. Scoring still judges a
-- missed week at the LAST kickoff (scoring.js), and the board still withholds
-- everybody's pick until every game has started (api.js getSurvivorBoard).
-- Locking per game without those two would have been a leak: a player holding
-- an open Friday game could have read the board on Thursday night and seen
-- which teams the field had already spent.
--
-- THREE TRIGGERS, AND THE UPDATE ONE GUARDS BOTH SIDES. Moving a pick off a
-- game that has kicked off is as much a change after the fact as picking one
-- that has, so the update trigger tests OLD and NEW separately: you may move
-- between two games that have both not started, and nothing else.
--
-- The abort strings change from 'week_locked' to 'game_started', because they
-- now mean a different thing and api.js maps them to a different answer.

DROP TRIGGER survivor_locked_insert;
DROP TRIGGER survivor_locked_update;
DROP TRIGGER survivor_locked_delete;

CREATE TRIGGER survivor_locked_insert
BEFORE INSERT ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.season = NEW.season AND g.week = NEW.week
     AND g.game_id = NEW.game_id AND g.kickoff_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'game_started'); END;

-- Both sides. OLD is the game being abandoned, NEW the one being taken; a
-- pick may move only while neither has started.
CREATE TRIGGER survivor_locked_update
BEFORE UPDATE ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.season = OLD.season AND g.week = OLD.week
     AND g.game_id IN (OLD.game_id, NEW.game_id)
     AND g.kickoff_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'game_started'); END;

-- Withdrawing is a change too: once your game is under way the pick is spent,
-- whatever happens to it.
CREATE TRIGGER survivor_locked_delete
BEFORE DELETE ON survivor_picks
FOR EACH ROW WHEN EXISTS (
  SELECT 1 FROM slate_games g
   WHERE g.season = OLD.season AND g.week = OLD.week
     AND g.game_id = OLD.game_id AND g.kickoff_at <= unixepoch())
BEGIN SELECT RAISE(ABORT, 'game_started'); END;

-- Which side of each game is actually in the conference.
--
-- The pick'em never needed this: you pick a SIDE, and both sides of a game
-- exist equally. The survivor pool does, because there you spend a TEAM for
-- the season — and a non-conference visitor plays a Big 12 team once all
-- year. Spending BYU costs their eleven remaining appearances; spending Notre
-- Dame costs nothing at all, so the dominant strategy was to survive on
-- borrowed opponents and never touch your own roster. A survivor pool of
-- sixteen teams is the game; a survivor pool of every team anyone plays is
-- not one.
--
-- Nullable, and read as "unknown, so not pickable in survivor": a slate row
-- imported before this column existed must not silently become a free pick.
ALTER TABLE slate_games ADD COLUMN b12 TEXT
  CHECK (b12 IN ('home', 'away', 'both'));

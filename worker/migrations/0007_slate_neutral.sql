-- Whether the game has a host at all.
--
-- "at" names a host: in "Arizona State at Kansas", Kansas had the crowd. A
-- neutral-site game has no such team — Arizona State and Kansas both flew to
-- Wembley — and the feed still labels one of them home because a schedule
-- needs a column for it. Printing that as "at" tells the reader something
-- untrue about who was at home, so a neutral game joins its two sides with
-- "vs" instead. The rest of the domain has done this since the hub started
-- showing the next kickoff; the slate could not, because it did not know.
--
-- Read-only as far as scoring goes. The spread already carries whatever the
-- market thought of the venue, and grading never looks at this.
--
-- Nullable, and read as "not neutral": a slate row imported before this
-- column existed keeps reading "at", which is what it said yesterday and is
-- right for all but a handful of games a season.
ALTER TABLE slate_games ADD COLUMN neutral INTEGER
  CHECK (neutral IN (0, 1));

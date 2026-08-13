-- Stop enumerating providers in the schema.
--
-- 0001 wrote CHECK (provider IN ('google','github')). SQLite cannot drop a
-- CHECK, so every provider added from here — Apple, Microsoft, Amazon —  would
-- need its own table rebuild, forever, or it would be refused at write time by
-- a constraint nobody thought to look at.
--
-- WHY THE LIST DOES NOT BELONG HERE. It is already in oauth.js, and index.js
-- refuses any provider not on it before a query is built: an unknown name is a
-- 404 at the router, not a constraint violation in D1. So this CHECK was never
-- the thing keeping bad data out — it was a second copy of a list, in the one
-- place that costs a table rebuild to edit. That is the "one chrome, two
-- places" hazard this project has been bitten by before, and the answer is the
-- same as it was there: one source of truth, and it is the code.
--
-- WHAT IS KEPT. A shape check, which is the part that never goes stale: a
-- provider is a short lowercase identifier. It still refuses '', ' ', NULL,
-- 'GOOGLE', 'goo gle' and a hundred-character string — every malformed value a
-- bug could produce — without having an opinion about which companies exist.
--
-- TWO GLOBS, NOT ONE, and the reason is a trap worth writing down. GLOB's `*`
-- is "any sequence of anything", NOT a quantifier on the class before it, so
-- the obvious `provider GLOB '[a-z][a-z0-9]*'` reads like an anchored pattern
-- and is not one: it accepts 'goo gle' and 'goo-gle', which is most of what
-- the check was for. Both were accepted when this was first written and tried.
-- The working form is a positive test for the first character and a NEGATIVE
-- test for anything outside the set anywhere.
--
-- The rebuild is SQLite's documented ALTER TABLE procedure. No PRAGMA
-- foreign_keys dance is needed: identities REFERENCES users, and nothing
-- references identities, so there is no inbound key to be invalidated by the
-- drop. The FK out to users is re-declared below and re-checked on rename.
--
-- Idempotent enough to re-run: the new table is created under a temporary name
-- and the rename is the last statement, so a failure part-way leaves the
-- original intact and the batch rolls back.

CREATE TABLE identities_new (
  provider     TEXT NOT NULL
                 CHECK (length(provider) BETWEEN 2 AND 32
                        AND provider GLOB '[a-z]*'
                        AND provider NOT GLOB '*[^a-z0-9]*'),
  subject_hash TEXT NOT NULL,
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  linked_at    INTEGER NOT NULL,
  PRIMARY KEY (provider, subject_hash)
);

INSERT INTO identities_new (provider, subject_hash, user_id, linked_at)
  SELECT provider, subject_hash, user_id, linked_at FROM identities;

DROP TABLE identities;

ALTER TABLE identities_new RENAME TO identities;

-- The index goes with the table, so it has to be made again by hand.
CREATE INDEX identities_user ON identities(user_id);

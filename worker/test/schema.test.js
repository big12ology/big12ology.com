// The migration, executed, with each of its guarantees attacked directly.
//
// These are the properties the whole feature rests on, and they are written as
// triggers precisely so that no code path can get around them. A test that
// exercised them through handler code would prove the handler asks nicely. So
// this goes at the database and tries to do the forbidden thing.
//
// node:sqlite is stdlib from Node 22, so this needs nothing installed. D1 runs
// a close SQLite (3.4x here, 3.4x there) and the features used are old and
// dull: triggers, RAISE(ABORT), partial indexes, foreign keys.

import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCHEMA = readFileSync(join(HERE, "..", "migrations", "0001_init.sql"),
                            "utf8");

// Anchored on the real clock, not a fixed literal. The lock triggers compare
// against unixepoch(), so a hardcoded timestamp silently becomes the past and
// every fixture arrives already locked — which is how the first version of
// this file "passed" its lock tests for the wrong reason.
const NOW = Math.floor(Date.now() / 1000);
const HOUR = 3600;

/** A database with the real schema and one week already published. */
function db({ lockAt = NOW + 48 * HOUR } = {}) {
  const d = new DatabaseSync(":memory:");
  d.exec(SCHEMA);
  d.exec("PRAGMA foreign_keys = ON");
  d.prepare(`INSERT INTO weeks (season, week, published_at, lock_at,
                                game_count, pickable_count)
             VALUES (2026, 3, ?, ?, 2, 1)`).run(NOW - HOUR, lockAt);
  d.prepare(`INSERT INTO slate_games (season, week, game_id, home, away,
                                      kickoff_at, spread_x2, frozen_at)
             VALUES (2026, 3, 401, 'Iowa State', 'Kansas', ?, -13, ?)`)
   .run(lockAt, NOW - HOUR);
  // A second game with no market: shown on the page, not pickable.
  d.prepare(`INSERT INTO slate_games (season, week, game_id, home, away,
                                      kickoff_at, spread_x2, frozen_at)
             VALUES (2026, 3, 402, 'Baylor', 'Houston', ?, NULL, ?)`)
   .run(lockAt + HOUR, NOW - HOUR);
  d.prepare(`INSERT INTO users (id, display_name, display_norm, created_at)
             VALUES ('u1', 'Chris', 'chris', ?)`).run(NOW - HOUR);
  return d;
}

const raises = (fn, msg) =>
  assert.throws(fn, (e) => e.message.includes(msg),
                `expected the database to refuse with "${msg}"`);

test("the migration applies cleanly", () => {
  const d = db();
  const tables = d.prepare(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    .all().map((r) => r.name).filter((n) => !n.startsWith("sqlite_"));
  assert.deepEqual(tables, [
    "audit_log", "identities", "leaderboard_season", "leaderboard_week",
    "name_history", "pick_scores", "picks", "rate_limits", "results",
    "sessions", "slate_games", "users", "weeks",
  ]);
  const triggers = d.prepare(
    "SELECT count(*) n FROM sqlite_master WHERE type='trigger'").get().n;
  assert.equal(triggers, 8);
});

// --- 1. a published line never moves --------------------------------------

test("a frozen spread cannot be changed", () => {
  const d = db();
  raises(() => d.prepare(
    "UPDATE slate_games SET spread_x2 = -20 WHERE game_id = 401").run(),
    "slate_frozen");
  assert.equal(
    d.prepare("SELECT spread_x2 s FROM slate_games WHERE game_id=401").get().s,
    -13);
});

test("a frozen spread cannot be nulled out either", () => {
  const d = db();
  raises(() => d.prepare(
    "UPDATE slate_games SET spread_x2 = NULL WHERE game_id = 401").run(),
    "slate_frozen");
});

test("a game with no line may gain one, exactly once", () => {
  const d = db();
  // This is the permitted transition: nothing was frozen, so nothing moves.
  d.prepare("UPDATE slate_games SET spread_x2 = 7 WHERE game_id = 402").run();
  assert.equal(
    d.prepare("SELECT spread_x2 s FROM slate_games WHERE game_id=402").get().s,
    7);
  // And now it is frozen like any other.
  raises(() => d.prepare(
    "UPDATE slate_games SET spread_x2 = 9 WHERE game_id = 402").run(),
    "slate_frozen");
});

test("kickoff and freeze time are immutable too", () => {
  const d = db();
  raises(() => d.prepare(
    "UPDATE slate_games SET kickoff_at = kickoff_at + 3600 WHERE game_id=401")
    .run(), "slate_frozen");
  raises(() => d.prepare(
    "UPDATE slate_games SET frozen_at = 1 WHERE game_id = 401").run(),
    "slate_frozen");
  // Something incidental is still editable — the trigger is targeted, not a
  // blanket read-only.
  d.prepare("UPDATE slate_games SET books = 6 WHERE game_id = 401").run();
});

// --- 2. a pick never changes after the lock --------------------------------

const pick = (d, gid = 401, side = "home", spread = -13) =>
  d.prepare(`INSERT INTO picks (user_id, season, week, game_id, side,
                                spread_x2, created_at, updated_at)
             VALUES ('u1', 2026, 3, ?, ?, ?, ?, ?)`)
   .run(gid, side, spread, NOW, NOW);

test("before the lock, picking works", () => {
  const d = db();
  pick(d);
  d.prepare("UPDATE picks SET side='away', updated_at=? WHERE game_id=401")
   .run(NOW + 60);
  assert.equal(
    d.prepare("SELECT side FROM picks WHERE game_id=401").get().side, "away");
  d.prepare("DELETE FROM picks WHERE game_id = 401").run();
});

test("after the lock, no insert, no update, no delete", () => {
  const past = db({ lockAt: NOW - HOUR });          // already locked
  raises(() => pick(past), "week_locked");

  // A pick made before the lock, then the week locks under it.
  const d = db();
  pick(d);
  d.prepare("UPDATE weeks SET lock_at = ? WHERE season=2026 AND week=3")
   .run(Math.floor(Date.now() / 1000) - HOUR);

  raises(() => d.prepare("UPDATE picks SET side='away' WHERE game_id=401")
    .run(), "week_locked");
  raises(() => d.prepare("DELETE FROM picks WHERE game_id=401").run(),
    "week_locked");
  // And it is still exactly what it was.
  assert.equal(
    d.prepare("SELECT side FROM picks WHERE game_id=401").get().side, "home");
});

test("the lock is inclusive: at the instant itself, it is shut", () => {
  const now = Math.floor(Date.now() / 1000);
  raises(() => pick(db({ lockAt: now })), "week_locked");
  // Later is fine. A minute, not a second: the shut half above is the exact
  // boundary and stays exact, but this half only needs a lock that is still
  // in the future when the insert runs, and a one-second margin lost a race
  // against a slow CI runner (2026-08-25, the second between Date.now() and
  // the trigger's unixepoch()).
  const open = db({ lockAt: now + 60 });
  pick(open);
});

// --- 3. a lock never moves later ------------------------------------------

test("the lock may move earlier but never later", () => {
  const d = db();
  d.prepare("UPDATE weeks SET lock_at = ? WHERE season=2026 AND week=3")
   .run(NOW + 24 * HOUR);                                   // earlier: fine
  raises(() => d.prepare(
    "UPDATE weeks SET lock_at = ? WHERE season=2026 AND week=3")
    .run(NOW + 72 * HOUR), "lock_at_may_not_move_later");
  // Nor may it be removed, which would be "later" by another name.
  raises(() => d.prepare(
    "UPDATE weeks SET lock_at = NULL WHERE season=2026 AND week=3").run(),
    "lock_at_may_not_move_later");
});

test("a week that never had a lock can get one", () => {
  const d = db();
  d.prepare(`INSERT INTO weeks (season, week, status, published_at, lock_at,
                                game_count, pickable_count)
             VALUES (2026, 4, 'no_contest', ?, NULL, 3, 0)`).run(NOW);
  d.prepare("UPDATE weeks SET lock_at=? WHERE season=2026 AND week=4")
   .run(NOW + HOUR);
  assert.equal(d.prepare(
    "SELECT lock_at l FROM weeks WHERE week=4").get().l, NOW + HOUR);
});

// --- the line you picked is the line on the slate -------------------------

test("a game with no posted line cannot be picked", () => {
  const d = db();
  raises(() => pick(d, 402, "home", 0), "unpickable_or_stale_line");
});

test("a pick carrying the wrong number is refused", () => {
  const d = db();
  raises(() => pick(d, 401, "home", -14), "unpickable_or_stale_line");
  raises(() => pick(d, 401, "home", 13), "unpickable_or_stale_line");
  pick(d, 401, "home", -13);        // the number actually on the slate
});

test("a side change cannot smuggle in a different spread", () => {
  const d = db();
  pick(d);
  raises(() => d.prepare(
    "UPDATE picks SET side='away', spread_x2=-20 WHERE game_id=401").run(),
    "unpickable_or_stale_line");
});

test("a pick on a game that is not on the slate is refused", () => {
  const d = db();
  raises(() => pick(d, 999, "home", -13), "unpickable_or_stale_line");
});

// --- structural guarantees -------------------------------------------------

test("a game belongs to exactly one week", () => {
  const d = db();
  d.prepare(`INSERT INTO weeks (season, week, published_at, lock_at,
                                game_count, pickable_count)
             VALUES (2026, 4, ?, ?, 1, 1)`).run(NOW, NOW + 96 * HOUR);
  raises(() => d.prepare(
    `INSERT INTO slate_games (season, week, game_id, home, away, kickoff_at,
                              spread_x2, frozen_at)
     VALUES (2026, 4, 401, 'Iowa State', 'Kansas', ?, -13, ?)`)
    .run(NOW + 96 * HOUR, NOW), "UNIQUE");
});

test("two players cannot hold the same folded name", () => {
  const d = db();
  raises(() => d.prepare(
    `INSERT INTO users (id, display_name, display_norm, created_at)
     VALUES ('u2', 'CHRIS', 'chris', ?)`).run(NOW), "UNIQUE");
  // But any number of players may be unnamed.
  d.prepare("INSERT INTO users (id, created_at) VALUES ('u3', ?)").run(NOW);
  d.prepare("INSERT INTO users (id, created_at) VALUES ('u4', ?)").run(NOW);
});

test("a slate game cannot be deleted out from under a pick", () => {
  const d = db();
  pick(d);
  raises(() => d.prepare("DELETE FROM slate_games WHERE game_id=401").run(),
    "FOREIGN KEY");
});

test("deleting a user takes their picks and identities with them", () => {
  const d = db();
  pick(d);
  d.prepare(`INSERT INTO identities (provider, subject_hash, user_id, linked_at)
             VALUES ('google', 'h1', 'u1', ?)`).run(NOW);
  d.prepare("DELETE FROM users WHERE id='u1'").run();
  assert.equal(d.prepare("SELECT count(*) n FROM picks").get().n, 0);
  assert.equal(d.prepare("SELECT count(*) n FROM identities").get().n, 0);
});

test("outcomes and statuses are constrained to the real ones", () => {
  const d = db();
  raises(() => pick(d, 401, "sideways", -13), "CHECK");
  raises(() => d.prepare(
    "INSERT INTO users (id, status, created_at) VALUES ('u9','vip',?)")
    .run(NOW), "CHECK");
  raises(() => d.prepare(
    `INSERT INTO identities (provider, subject_hash, user_id, linked_at)
     VALUES ('facebook','h','u1',?)`).run(NOW), "CHECK");
});

test("window functions are available for ranking the board", () => {
  // Used by the scoring recompute. Cheap to assert, annoying to discover late.
  const d = db();
  const r = d.prepare(
    "SELECT RANK() OVER (ORDER BY x DESC) k FROM (SELECT 1 x UNION SELECT 2)")
    .all();
  assert.deepEqual(r.map((x) => x.k), [1, 2]);
});

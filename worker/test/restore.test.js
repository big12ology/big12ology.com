// A backup you have never restored is a rumour.
//
// The site can rebuild almost everything it serves: slates are committed
// JSON, results come from a file the build writes, the boards are a pure
// recompute. Accounts and picks are not. With no email on file a lost users
// table cannot be re-associated with anybody, so this is the one dataset where
// the recovery path has to be known to work rather than assumed to.
//
// What is checked here is the property a restore actually has to have, and the
// one a row-count check would miss: the schema comes back WITH ITS TEETH. Every
// invariant this project relies on — the frozen line, the lock, no team twice
// — lives in a trigger, not in application code. A dump that returns the rows
// but not the triggers produces a database that accepts what the original
// refused, and it looks completely healthy until somebody moves a spread.

import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { makeEnv, seedWeek, seedUser, seedPick, seedSurvivorPick, forceLock }
  from "./helpers/env.js";

/**
 * What `wrangler d1 export` produces, near enough: the schema and the rows,
 * in the order sqlite would replay them.
 *
 * Not a mock of the wrangler command — a dump taken from the same node:sqlite
 * database the rest of the suite runs against, so what is restored here is
 * what the real schema does, and the assertions below are about SQLite's
 * behavior rather than about a fixture somebody wrote.
 */
function dump(db, { triggers = true } = {}) {
  const out = ["PRAGMA defer_foreign_keys=TRUE;"];
  const objs = db.prepare(
    `SELECT type, name, sql FROM sqlite_master
      WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'`).all();
  for (const o of objs.filter((o) => o.type === "table")) {
    out.push(`${o.sql};`);
    const rows = db.prepare(`SELECT * FROM "${o.name}"`).all();
    for (const r of rows) {
      const cols = Object.keys(r).map((c) => `"${c}"`).join(",");
      const vals = Object.values(r).map((v) =>
        v == null ? "NULL"
          : typeof v === "number" ? String(v)
            : `'${String(v).replace(/'/g, "''")}'`).join(",");
      out.push(`INSERT INTO "${o.name}" (${cols}) VALUES (${vals});`);
    }
  }
  // Indexes and triggers after the rows, exactly as the real export orders
  // them — a trigger present during the insert would fire on the restore.
  for (const o of objs.filter((o) => o.type !== "table")) {
    if (!triggers && o.type === "trigger") continue;
    out.push(`${o.sql};`);
  }
  return out.join("\n");
}

function populate(env) {
  seedWeek(env, { season: 2026, week: 1, games: [
    { game_id: 101, home: "Utah", away: "BYU", spread_x2: -7, b12: "both" },
    { game_id: 102, home: "Kansas", away: "TCU", spread_x2: 5, b12: "both" },
  ] });
  seedUser(env, "u1", { name: "Someone" });
  seedPick(env, "u1", 2026, 1, 101, "home", -7);
  seedSurvivorPick(env, "u1", 2026, 1, 101, "Utah");
}

test("a dump restores the rows", () => {
  const env = makeEnv();
  populate(env);
  const sql = dump(env.raw);

  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "d1-")), "r.db");
  const restored = new DatabaseSync(file);
  restored.exec(sql);

  for (const [t, n] of [["users", 1], ["picks", 1], ["survivor_picks", 1],
                        ["slate_games", 2], ["weeks", 1]]) {
    assert.equal(restored.prepare(`SELECT COUNT(*) n FROM ${t}`).get().n, n,
      `${t} did not come back`);
  }
  assert.equal(restored.prepare(`SELECT display_name FROM users`).get()
    .display_name, "Someone");
});

test("and restores the teeth: the triggers are there and still bite", () => {
  const env = makeEnv();
  populate(env);
  const sql = dump(env.raw);

  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "d1-")), "r.db");
  const restored = new DatabaseSync(file);
  restored.exec(sql);

  const triggers = restored.prepare(
    `SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name`)
    .all().map((r) => r.name);
  const original = env.raw.prepare(
    `SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name`)
    .all().map((r) => r.name);
  assert.deepEqual(triggers, original, "the restored schema lost triggers");
  assert.ok(triggers.length >= 10, `only ${triggers.length} triggers restored`);

  // Present is not the same as working. Each of these is an invariant the
  // board's credibility rests on, so each is provoked rather than counted.
  assert.throws(() => restored.prepare(
    `UPDATE slate_games SET spread_x2 = -99 WHERE game_id = 101`).run(),
    /frozen|constraint/i, "a frozen line moved in the restored database");

  restored.prepare(
    `UPDATE weeks SET lock_at = 1 WHERE season = 2026 AND week = 1`).run();
  assert.throws(() => restored.prepare(
    `INSERT INTO picks (user_id, season, week, game_id, side, spread_x2,
                        created_at, updated_at)
     VALUES ('u1', 2026, 1, 102, 'home', 5, 1, 1)`).run(),
    /week_locked|constraint/i, "a locked week accepted a pick after restore");
});

test("a dump with the rows but no triggers is detectably useless", () => {
  // The failure this file exists for, made explicit: strip the triggers and
  // the restore still looks perfect by every row count, while quietly
  // accepting a rewrite of a frozen line.
  const env = makeEnv();
  populate(env);
  // Omitted whole, not line-by-line: a trigger's body spans several lines and
  // half of one is a syntax error rather than a plausible-looking backup.
  const sql = dump(env.raw, { triggers: false });

  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "d1-")), "r.db");
  const restored = new DatabaseSync(file);
  restored.exec(sql);

  assert.equal(restored.prepare(`SELECT COUNT(*) n FROM picks`).get().n, 1,
    "the rows should still be there — that is what makes it deceptive");
  restored.prepare(
    `UPDATE slate_games SET spread_x2 = -99 WHERE game_id = 101`).run();
  assert.equal(restored.prepare(
    `SELECT spread_x2 FROM slate_games WHERE game_id = 101`).get().spread_x2,
    -99, "expected the trigger-less copy to accept this");
  // So the backup job greps for CREATE TRIGGER before it keeps the artifact.
});

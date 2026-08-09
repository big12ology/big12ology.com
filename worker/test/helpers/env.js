// A fake `env` good enough to run the real handlers against.
//
// D1 is SQLite with a promise wrapper, and node:sqlite is SQLite in the
// standard library, so the gap between them is an API shim rather than a
// simulation. That is worth saying plainly, because it bounds what these
// tests prove: they exercise the actual SQL — the triggers, the window
// functions, the ON CONFLICT clauses — and they do not exercise D1's network
// behaviour or its statement limits. The things most likely to be wrong here
// are the queries, and the queries are real.
//
// miniflare is in node_modules and would be closer. It is also a Worker
// runtime to boot per test file, and it cannot check a trigger any harder
// than this can.

import { DatabaseSync } from "node:sqlite";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const MIGRATIONS = join(HERE, "..", "..", "migrations");

class Stmt {
  constructor(db, sql) { this.db = db; this.sql = sql; this.args = []; }
  bind(...args) {
    const s = new Stmt(this.db, this.sql);
    // node:sqlite rejects undefined and booleans; D1 accepts both and coerces.
    s.args = args.map((a) =>
      a === undefined ? null : typeof a === "boolean" ? (a ? 1 : 0) : a);
    return s;
  }
  async first() {
    const r = this.db.prepare(this.sql).get(...this.args);
    return r === undefined ? null : r;
  }
  async all() { return { results: this.db.prepare(this.sql).all(...this.args) }; }
  async run() { return { success: true, ...this.db.prepare(this.sql).run(...this.args) }; }
}

class D1 {
  constructor(db) { this.db = db; }
  prepare(sql) { return new Stmt(this.db, sql); }
  /**
   * A D1 batch is one implicit transaction, and the handlers rely on that:
   * a trigger abort inside it must roll the whole thing back, which is what
   * stops a half-saved card.
   */
  async batch(stmts) {
    this.db.exec("BEGIN");
    try {
      const out = [];
      for (const s of stmts) out.push(await s.run());
      this.db.exec("COMMIT");
      return out;
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
  }
}

class KV {
  constructor() { this.map = new Map(); }
  async get(k) { return this.map.has(k) ? this.map.get(k) : null; }
  async put(k, v) { this.map.set(k, v); }
  async delete(k) { this.map.delete(k); }
}

export function makeEnv(overrides = {}) {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).sort()) {
    if (f.endsWith(".sql")) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  }
  db.exec("PRAGMA foreign_keys = ON");
  return {
    raw: db,
    DB: new D1(db),
    SESSIONS: new KV(),
    SITE_ORIGIN: "https://big12ology.com",
    PAGES_ORIGIN: "https://big12ology.github.io",
    GOOGLE_CLIENT_ID: "google-client-id",
    GITHUB_CLIENT_ID: "github-client-id",
    GOOGLE_CLIENT_SECRET: "google-secret",
    GITHUB_CLIENT_SECRET: "github-secret",
    IDENTITY_PEPPER: "test-pepper-permanent-in-production",
    STATE_SIGNING_KEY: "test-state-key",
    SEASON: "2026",
    ...overrides,
  };
}

export const NOW = () => Math.floor(Date.now() / 1000);
export const HOUR = 3600;

/** A published week with `n` pickable games plus one with no line. */
export function seedWeek(env, { season = 2026, week = 3, lockAt, games } = {}) {
  const now = NOW();
  lockAt = lockAt ?? now + 48 * HOUR;
  games = games || [
    { game_id: 401, home: "Iowa State", away: "Kansas", spread_x2: -13 },
    { game_id: 402, home: "Baylor", away: "Houston", spread_x2: 7 },
    { game_id: 403, home: "TCU", away: "Utah", spread_x2: null },
  ];
  env.raw.prepare(
    `INSERT INTO weeks (season, week, published_at, lock_at, game_count,
                        pickable_count)
     VALUES (?, ?, ?, ?, ?, ?)`)
    .run(season, week, now - HOUR, lockAt, games.length,
         games.filter((g) => g.spread_x2 != null).length);
  for (const g of games) {
    env.raw.prepare(
      `INSERT INTO slate_games (season, week, game_id, home, away, kickoff_at,
                                spread_x2, b12, frozen_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      // Conference game unless a fixture says otherwise: that is the common
      // case, and it keeps every test written before the survivor pool cared
      // about conference membership saying what it always said.
      .run(season, week, g.game_id, g.home, g.away,
           g.kickoff_at ?? lockAt, g.spread_x2,
           // `in`, not ??: an explicit b12: null is a fixture saying "no
           // conference side", and ?? would quietly turn it into "both".
           "b12" in g ? g.b12 : "both", now - HOUR);
  }
  return { season, week, lockAt, games };
}

export function seedUser(env, id, { name = null, status = "active" } = {}) {
  env.raw.prepare(
    `INSERT INTO users (id, display_name, display_norm, status, created_at)
     VALUES (?, ?, ?, ?, ?)`)
    .run(id, name, name ? name.toLowerCase() : null, status, NOW());
  return id;
}

export function seedPick(env, userId, season, week, gameId, side, spreadX2) {
  const now = NOW();
  env.raw.prepare(
    `INSERT INTO picks (user_id, season, week, game_id, side, spread_x2,
                        created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(userId, season, week, gameId, side, spreadX2, now, now);
}

export function seedSurvivorPick(env, userId, season, week, gameId, team) {
  const now = NOW();
  env.raw.prepare(
    `INSERT INTO survivor_picks (user_id, season, week, game_id, team,
                                 created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`)
    .run(userId, season, week, gameId, team, now, now);
}

/** Move a week's lock into the past without tripping weeks_lock_monotonic. */
export function forceLock(env, season, week, at = NOW() - HOUR) {
  env.raw.exec("PRAGMA writable_schema = ON");
  env.raw.prepare(`UPDATE weeks SET lock_at = ? WHERE season = ? AND week = ?`)
    .run(at, season, week);
  env.raw.exec("PRAGMA writable_schema = OFF");
}

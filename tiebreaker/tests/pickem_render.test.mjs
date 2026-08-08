// Smoke test: the pick'em client must actually render against real data.
//
// The same lesson attendance/tests/render.test.mjs already learned, arrived at
// the same way. Namespacing this section's CSS classes with a string-replace
// rewrote `c.num` — a JavaScript property, not a class — into `c.pk-num`,
// which parses as `c.pk - num` and throws ReferenceError the moment a board is
// drawn. `node --check` passes it, because it is valid syntax. Nothing but
// running it finds it, and it had already reached a commit.
//
// So this runs the real file against a stub DOM and stub API and asserts that
// each of the three views puts something on the page.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "site_pickem", "app.js");
const DATA = path.join(HERE, "..", "data");

const read = (p) => JSON.parse(fs.readFileSync(p, "utf8"));
const games = read(path.join(DATA, "games_2026.json"));
const teams = read(path.join(DATA, "teams.json"));

// A slate shaped exactly like tiebreaker/pickem.py emits, with a mix of
// pickable and not, and some of it graded.
function slate() {
  const week = games.slice(0, 8).map((g, i) => ({
    game_id: g.id, home: g.home, away: g.away, kickoff: g.start,
    kickoff_at: Math.floor(Date.parse(g.start) / 1000),
    spread_x2: i < 5 ? [-13, 7, -14, 3, -20][i] : null,
    ...(i >= 5 ? { unpickable: "no_line" } : {}),
    ...(i < 3 ? { result: { home_points: 31, away_points: 21,
                            ats: ["home", "away", "push"][i] } } : {}),
    ...(i < 5 ? { consensus: { home: 110 + i, away: 70 - i } } : {}),
  }));
  return {
    season: 2026, week: 3, status: "published", locked: false,
    lock_at: Math.floor(Date.now() / 1000) + 86400,
    game_count: week.length,
    pickable_count: week.filter((g) => g.spread_x2 != null).length,
    games: week,
  };
}

const S = slate();
const PICKS = {};
S.games.filter((g) => g.spread_x2 != null).slice(0, 3)
  .forEach((g, i) => { PICKS[g.game_id] = i % 2 ? "away" : "home"; });

const API = {
  "/api/me": { user_id: "u1", display_name: "Tester", needs_name: false,
               status: "active", identities: [{ provider: "google" }] },
  "/api/slate": S,
  "/api/picks": { season: 2026, week: 3, locked: false,
                  lock_at: S.lock_at, picks: PICKS },
  "/api/leaderboard": {
    season: 2026, week: 3,
    chalk: { w: 20, l: 18, p: 1, pct: 0.5263 },
    rows: [
      { rank: 1, user_id: "u1", display_name: "Tester", w: 25, l: 13, p: 1, pct: 0.6579 },
      { rank: 2, user_id: "u2", display_name: "Someone", w: 19, l: 19, p: 0, pct: 0.5 },
      { rank: 3, user_id: "u3", display_name: "Nobody", w: 12, l: 26, p: 2, pct: 0.3158 },
    ],
  },
  "/pickem/teams.json": Object.fromEntries(
    Object.entries(teams).map(([t, v]) => [t, { color: v.color }])),
};

// --- the smallest DOM the client will run against -------------------------

function stubEl(tag = "div") {
  const el = {
    tagName: tag.toUpperCase(), children: [], className: "", style: {
      setProperty() {}, getPropertyValue: () => "",
    },
    hidden: false, checked: false, value: "", type: "", title: "",
    dataset: {}, options: [], disabled: false,
    classList: { add() {}, toggle() {}, contains: () => false },
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); },
    remove() {}, focus() {}, closest: () => null,
    addEventListener() {}, removeAttribute() {}, setAttribute() {},
    hasAttribute: () => false, getAttribute: () => null,
    querySelector: () => null, querySelectorAll: () => [],
    // A real textContent is the concatenation of everything below, which
    // matters here: the client sets it to "" and then appends nodes, so a
    // getter that only returned the last assignment would report every
    // rendered panel as empty.
    get textContent() {
      return (this._t || "") + this.children
        .map((c) => (c && (c.textContent ?? c.nodeValue)) || "").join("");
    },
    set textContent(v) { this._t = v; if (v === "") this.children.length = 0; },
  };
  return el;
}

// Every id the client looks for. Missing ones return null, which the client
// is written to tolerate — that is how one file serves five pages.
const IDS = ["slate", "slateform", "slateload", "lockcard", "lockat", "cd",
             "cdsr", "slatecount", "savestate", "alertstate", "signedout",
             "card", "cardnote", "board", "boardnote", "wksel",
             "signin", "named", "acctinfo", "acctbody", "nameform",
             "dname", "dnameerr"];

function harness(override) {
  const byId = Object.fromEntries(IDS.map((id) => [id, stubEl()]));
  const listeners = {};
  const doc = {
    hidden: false,
    getElementById: (id) => byId[id] || null,
    createElement: (t) => stubEl(t),
    createTextNode: (t) => ({ nodeValue: t, textContent: t }),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener(ev, fn) { (listeners[ev] ||= []).push(fn); },
    body: stubEl(),
  };
  const ctx = {
    document: doc,
    window: { B12PCT: { ats: () => "hsl(120 50% 40%)" } },
    console,
    setTimeout, clearTimeout, setInterval: () => 0,
    Date, Math, JSON, Promise, Error, TypeError, Number, String, Object, Array,
    encodeURIComponent, isNaN, parseInt, parseFloat,
    fetch(url) {
      const p = String(url).split("?")[0];
      const body = (override && p in override) ? override[p] : API[p];
      return Promise.resolve({
        ok: body !== undefined,
        status: body === undefined ? 404 : 200,
        text: () => Promise.resolve(body === undefined ? "" : JSON.stringify(body)),
        json: () => Promise.resolve(body),
      });
    },
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(APP, "utf8"), ctx, { filename: "app.js" });
  return { byId, fire: () => Promise.all(
    (listeners.DOMContentLoaded || []).map((f) => f())) };
}

// Promises resolve on the microtask queue; the client chains several deep.
const settle = () => new Promise((r) => setTimeout(r, 40));

test("the slate renders rows against a real week", async () => {
  const h = harness();
  await h.fire();
  await settle();
  const rows = h.byId.slate.children;
  assert.ok(rows.length >= S.games.length,
    `expected at least ${S.games.length} rows, drew ${rows.length}`);
  assert.match(h.byId.slatecount.textContent, /\d+ games/);
});

test("the board renders every player, and the chalk", async () => {
  const h = harness();
  await h.fire();
  await settle();
  // thead + tbody + tfoot; the tfoot is the chalk benchmark.
  const parts = h.byId.board.children;
  assert.equal(parts.length, 3,
    "expected thead, tbody and the chalk tfoot");
  const bodyRows = parts[1].children;
  assert.equal(bodyRows.length, API["/api/leaderboard"].rows.length);
  assert.ok(!/could not reach/i.test(h.byId.boardnote.textContent),
    `board reported a failure: ${h.byId.boardnote.textContent}`);
});

test("the card renders a row per pickable game", async () => {
  const h = harness();
  await h.fire();
  await settle();
  const list = h.byId.card.children[0];
  assert.ok(list, "no card list rendered");
  assert.equal(list.children.length, S.pickable_count);
  assert.match(h.byId.cardnote.textContent + "", /.+/);
});

// A card read on a Saturday afternoon is mostly games that have not finished.
// The status column carries what the GAME is doing; the chip at the other end
// carries what your pick did. These are the states in between, which are the
// ones you cannot see by loading the page on a Tuesday.
test("the status column says what the game is doing", async () => {
  const now = Math.floor(Date.now() / 1000);
  const cases = [
    { name: "not kicked off",        at: now + 600,        result: null,  want: /\d/        },
    { name: "kicked off, no score",  at: now - 600,        result: null,  want: /IN PLAY/  },
    { name: "kick \u002B 3h, no score",  at: now - 4 * 3600,   result: null,  want: /WAITING/  },
    { name: "graded",                at: now - 4 * 3600,
      result: { home_points: 31, away_points: 21, ats: "home" },          want: /FINAL/    },
  ];
  for (const c of cases) {
    const g = { ...S.games[0], kickoff_at: c.at,
                kickoff: new Date(c.at * 1000).toISOString() };
    delete g.result;
    if (c.result) g.result = c.result;
    const h = harness({
      "/api/slate": { ...S, locked: true, games: [g] },
      "/api/picks": { season: 2026, week: 3, locked: true,
                      picks: { [g.game_id]: "home" } },
    });
    await h.fire();
    await settle();
    const status = h.byId.card.children[0].children[0].children[0];
    assert.match(status.textContent, c.want,
      `${c.name}: status read "${status.textContent}"`);
  }
});

test("an unpicked game says so rather than pretending to be pending", async () => {
  const h = harness({ "/api/picks": { season: 2026, week: 3, picks: {} } });
  await h.fire();
  await settle();
  const rows = h.byId.card.children[0].children;
  assert.ok(rows.length > 0);
  for (const r of rows) {
    assert.match(r.textContent, /NO PICK/);
  }
});

test("the crowd split is never rendered before the lock", async () => {
  // The server withholds it, but the client must not invent it either: this
  // is the one number that would change how people play if it leaked early.
  const open = { ...S, locked: false,
                 games: S.games.map((g) => ({ ...g })) };  // no consensus field
  const h = harness({ "/api/slate": open });
  await h.fire();
  await settle();
  const list = h.byId.card.children[0];
  const withSplit = [...list.children].filter((r) => /% took/.test(r.textContent));
  assert.equal(withSplit.length, 0, "a split was drawn on an unlocked week");
});

test("no view reports an error against good data", async () => {
  const h = harness();
  await h.fire();
  await settle();
  for (const id of ["boardnote", "cardnote", "alertstate"]) {
    assert.ok(!/server said no|could not reach|went wrong/i
      .test(h.byId[id].textContent),
      `${id} says: ${h.byId[id].textContent}`);
  }
});

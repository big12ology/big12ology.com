/* Shared what-if links, against the real app.js.
 *
 * A link packs picks by POSITION in a list of game ids and fingerprints that
 * list, so a list that shifts must refuse rather than restore somebody else's
 * board. For a season the list was the games still OPEN, which shrinks every
 * Saturday: the fingerprint moved whenever a game was played, and the link
 * was refused under a message blaming a schedule change that never happened.
 * Measured 2026-09-14, 31 of 120 games in: 4 of the 6 links that arrived in a
 * week died on arrival.
 *
 * Nothing but running it finds that. `node --check` passes it, the codec
 * round-trips perfectly inside one page load, and both halves are correct in
 * isolation -- the bug only exists in the gap between writing a link and
 * reading it back after a weekend. So these tests write a link with the real
 * file, play the games, and read it back with the real file.
 *
 *   node --test tests/lab_scenario.test.mjs
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const SITE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..",
                       "site");

// Out of the built page rather than rebuilt here, so this runs against what
// actually deploys -- the same reason race_client.test.mjs reads it there.
function livePayload() {
  const html = fs.readFileSync(path.join(SITE, "lab.html"), "utf8");
  const m = html.match(
    /<script id=payload type=application\/json>([\s\S]*?)<\/script>/);
  assert.ok(m, "no payload in site/lab.html");
  return JSON.parse(m[1]);
}

/* Play games that have not been played, newest week first, and say which.
   28-17 is what a pick scores, so a game the reader picked and a game the
   season decided are indistinguishable to everything downstream. */
function play(payload, n) {
  const copy = JSON.parse(JSON.stringify(payload));
  const played = [];
  for (const g of copy.games) {
    if (played.length >= n) break;
    if (g.completed || g.ccg) continue;
    g.completed = true;
    g.home_points = 28;
    g.away_points = 17;
    played.push(String(g.id));
  }
  assert.equal(played.length, n, `only ${played.length} games left to play`);
  return { payload: copy, played };
}

// --- the smallest DOM app.js will run against -----------------------------

function stubEl(tag = "div") {
  return {
    tagName: tag.toUpperCase(), children: [], className: "", id: "",
    hidden: false, checked: false, value: "", type: "", title: "",
    disabled: false, dataset: {}, options: [], rows: [],
    style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    appendChild(c) { this.children.push(c); return c; },
    insertBefore(c) { this.children.push(c); return c; },
    removeChild(c) {
      const i = this.children.indexOf(c);
      if (i >= 0) this.children.splice(i, 1);
    },
    remove() {}, focus() {}, closest: () => null, scrollIntoView() {},
    addEventListener() {}, removeAttribute() {}, setAttribute() {},
    hasAttribute: () => false, getAttribute: () => null,
    querySelector: () => null, querySelectorAll: () => [],
    get textContent() {
      return (this._t || "") + this.children
        .map((c) => (c && (c.textContent ?? c.nodeValue)) || "").join("");
    },
    set textContent(v) { this._t = v; if (v === "") this.children.length = 0; },
    get innerHTML() { return this._h || ""; },
    set innerHTML(v) { this._h = v; this.children.length = 0; },
  };
}

// Every id app.js looks for. It tolerates missing ones -- one file serves the
// live page and every archived season -- but giving it all of them keeps a
// silently skipped control from reading as a passing test.
const IDS = ["payload", "stand", "stories", "matchcard", "raceout", "tablewrap",
             "sort-pct", "sort-raw", "team-sel", "team-out", "wgames",
             "w-chip", "w-chip2", "w-chip3", "w-clear", "w-conf", "w-count",
             "w-fav", "w-favun", "w-link", "w-model", "w-note", "w-weeks"];

/* One page load. `hash` is the scenario in the URL when the page opens, which
   has to be in place before app.js runs: it reads the link on the way up. */
function load(payload, hash) {
  const byId = Object.fromEntries(IDS.map((id) => [id, stubEl()]));
  byId.payload.textContent = JSON.stringify(payload);
  // notice() hangs its paragraph off #wgames's parent, so there has to be one.
  const host = stubEl();
  host.children.push(byId.wgames);
  byId.wgames.parentNode = host;

  const sent = [];
  const store = {};
  let written = null;
  const ctx = {
    console, setTimeout, clearTimeout, Date, Math, JSON, RegExp,
    Error, TypeError, Number, String, Object, Array, Boolean, isNaN,
    parseInt, parseFloat, encodeURIComponent, decodeURIComponent,
    document: {
      getElementById: (id) => byId[id] || null,
      createElement: (t) => stubEl(t),
      createTextNode: (t) => ({ nodeValue: t, textContent: t }),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
      body: stubEl(),
    },
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;
  ctx.matchMedia = () => ({ matches: false, addEventListener() {} });
  ctx.addEventListener = () => {};
  // The storage layer is stubbed and the engine is not. state.js owns a real
  // location and a real localStorage, neither of which says anything about
  // whether a link survives a weekend; B12Engine decides what a pick does to
  // the standings, which is the half that has to be real.
  ctx.B12State = {
    hashRead: () => hash || "",
    hashWrite: (k, v) => { written = v; },
    get: (k, d) => (k in store ? store[k] : d),
    set: (k, v) => { store[k] = v; },
  };
  ctx.B12Metrics = {
    send: (n, d) => sent.push([n, d]),
    atEnd: () => {},
  };
  vm.createContext(ctx);
  for (const f of ["engine.js", "pct.js", "race.js", "app.js"]) {
    vm.runInContext(fs.readFileSync(path.join(SITE, f), "utf8"), ctx, f);
  }
  return {
    byId, sent, store,
    link: () => written,
    scenario: () => sent.filter((r) => r[0] === "scenario").map((r) => r[1]),
    note: () => host.children
      .filter((c) => c !== byId.wgames)
      .map((c) => c.textContent).join(" "),
    fillFavorites: () => byId["w-fav"].onclick(),
  };
}

/* A link written by the page itself, with the model's favorite in every open
   game. Built by the real control rather than assembled here: a link this
   test knows how to write is a link this test cannot prove the page writes. */
function share(payload) {
  const page = load(payload);
  page.fillFavorites();
  const raw = page.link();
  assert.ok(raw, "the fill button wrote no link");
  return raw;
}

test("a shared link says which packing it is in", () => {
  const raw = share(livePayload());
  const bits = raw.split(".");
  assert.equal(bits.length, 4, `four fields, got ${bits.length}: ${raw}`);
  assert.equal(bits[0], String(livePayload().year));
  assert.match(bits[1], /^b[0-9a-f]{1,6}$/,
    "the fingerprint must carry a version letter");
});

test("the link survives the games it was made about being played", () => {
  const live = livePayload();
  const raw = share(live);
  const after = play(live, 6);
  const page = load(after.payload, raw);

  assert.deepEqual(page.scenario(), ["opened"],
    `a weekend of football retired the link: ${page.note()}`);
  assert.doesNotMatch(page.note(), /schedule changed/,
    "six games being played is not a schedule change");
});

test("picks on games played since are dropped, and said out loud", () => {
  const live = livePayload();
  const raw = share(live);
  const after = play(live, 6);
  const page = load(after.payload, raw);

  // Every game the fill touched was open when the link was written, so all
  // six are picks the season has since answered.
  assert.match(page.note(), /6 games in that scenario have been played/,
    `expected the aged note, got: ${page.note()}`);
});

test("a pick never outlives the lever that made it", () => {
  const live = livePayload();
  const raw = share(live);
  const after = play(live, 6);
  const page = load(after.payload, raw);
  assert.deepEqual(page.scenario(), ["opened"]);

  // "N of M games picked", where M is the games still open. A scenario can
  // never hold more picks than the board has levers: a pick above that line
  // is one simGames() would score 28-17 over a result that already happened,
  // on a row the locked board draws no lever for, with nothing on the page
  // saying so and no way for the reader to take it back.
  const count = page.byId["w-count"].textContent;
  const m = count.match(/^(\d+) of (\d+) games picked$/);
  assert.ok(m, `unreadable counter: ${count}`);
  const [picked, levers] = [Number(m[1]), Number(m[2])];
  assert.ok(picked > 0, "the shared scenario applied nothing at all");
  assert.ok(picked <= levers,
    `${picked} picks against ${levers} levers: ${picked - levers} of them ` +
    "are sitting on games that have already been played");
});

test("a schedule that really changes is still refused", () => {
  const live = livePayload();
  const raw = share(live);
  const moved = JSON.parse(JSON.stringify(live));
  const victim = moved.games.findIndex((g) => !g.ccg && !g.completed);
  moved.games.splice(victim, 1);

  const page = load(moved, raw);
  assert.deepEqual(page.scenario(), ["stale"]);
  assert.match(page.note(), /schedule changed/);
});

test("a link in the old packing is told so, and not counted as stale", () => {
  const live = livePayload();
  const bits = share(live).split(".");
  bits[1] = bits[1].slice(1); // what a link written before the fix looks like
  const page = load(live, bits.join("."));

  assert.deepEqual(page.scenario(), ["retired"],
    "old links must not spend a season inflating the staleness number");
  assert.doesNotMatch(page.note(), /schedule changed/,
    "the schedule did not change, the packing did");
});

test("a scenario from another season is still refused", () => {
  const live = livePayload();
  const bits = share(live).split(".");
  bits[0] = String(live.year - 1);
  const page = load(live, bits.join("."));
  assert.match(page.note(), new RegExp(`${live.year - 1} season`));
});

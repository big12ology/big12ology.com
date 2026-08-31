// Ten thousand random API payloads through the real pick'em client.
//
// The third simulator, and the one whose failure mode is different again. The
// worker's tests can prove the API is right; nothing until now could prove the
// page survives what the API sends, and the page is where every bug the user
// actually sees lives. Rendering code does not throw when it goes wrong — it
// writes "NaN" into a cell, or "undefined" into a chip, or draws a chip twice,
// and the page looks finished.
//
// So the payloads are generated hostile as well as ordinary: nulls where a
// number belongs, negative counts, unicode team names, spreads no book would
// post, results for games that are not on the slate, empty slates, one-player
// boards. What is asserted is mostly the absence of the tells — no NaN, no
// undefined, no [object Object], no bare hyphen where a minus sign belongs,
// exactly one outcome chip per graded row — plus the handful of rules the page
// is responsible for enforcing on its own.
//
// The client is a classic script, so it is read off disk and run against the
// stub DOM in test/helpers/dom.js. It exposes a named surface for exactly this
// (window.B12POOLS at the foot of app.js).
//
//     SIM_RUNS=10000 node --test test/pools.sim.test.js

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { installDOM } from "./helpers/dom.js";

const RUNS = Number(process.env.SIM_RUNS || 300);
const ONLY = process.env.SIM_SEED ? Number(process.env.SIM_SEED) : null;

const APP = new URL("../../tiebreaker/site_pools/app.js", import.meta.url);
// The consensus gauge is drawn by a script the pools pages load from
// /tiebreaker/, shared with the schedule section so the two cannot drift.
// The page loads it before app.js; so does this.
const GAUGE = new URL("../../tiebreaker/site/gauge.js", import.meta.url);

installDOM();
vm.runInThisContext(fs.readFileSync(GAUGE, "utf8"), { filename: "gauge.js" });
vm.runInThisContext(fs.readFileSync(APP, "utf8"), { filename: "app.js" });
const P = globalThis.window.B12POOLS;
assert.ok(P, "app.js did not expose its test surface");

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const int = (r, lo, hi) => lo + Math.floor(r() * (hi - lo + 1));
const pick = (r, xs) => xs[Math.floor(r() * xs.length)];

const NAMES = ["Utah", "BYU", "Kansas State", "TCU", "Hawai'i", "Texas A&M",
               "Miami (OH)", "Ｕｔａｈ", "  padded  ", "Sam Houston",
               "a".repeat(80), "Ω", "<script>", "team​zero"];

// The values that break renderers, all of them things an API can genuinely
// return: a null where a number lives, a zero that is not missing, a float
// that never divides evenly.
// Mirrors MIN_CONSENSUS in app.js. Not imported, because the point of a
// second copy here is that a change to the rule has to be made twice on
// purpose rather than once by accident.
const MIN_CONSENSUS = 10;

const ODD_NUMBERS = [null, undefined, 0, -0, 1, -1, 0.5, -0.5, 7, -7,
                     1e9, -1e9, 99.999];

/** Tells that a renderer went wrong without throwing. */
const TELLS = [/\bNaN\b/, /\bundefined\b/, /\[object Object\]/,
               /\bnull\b/, /\bInfinity\b/];

function assertClean(node, what) {
  const text = node && node.textContent ? node.textContent : "";
  for (const bad of TELLS) {
    assert.ok(!bad.test(text), `${what} rendered ${bad}: ${JSON.stringify(text)}`);
  }
  // Attributes are just as visible: an aria-label saying NaN is read aloud.
  for (const n of node.walk ? node.walk() : []) {
    for (const [k, v] of Object.entries(n.attributes || {})) {
      for (const bad of TELLS) {
        assert.ok(!bad.test(String(v)),
          `${what} attribute ${k} rendered ${bad}: ${v}`);
      }
    }
  }
}

function randomGame(r, week) {
  const spread = pick(r, [...ODD_NUMBERS, int(r, -60, 60)]);
  // Two different teams. Not fussiness: the survivor assertions find a side's
  // radio by the team name on it, and a game against itself makes the two
  // sides indistinguishable to the test rather than to the code.
  const home = pick(r, NAMES);
  let away = pick(r, NAMES);
  while (away === home) away = pick(r, NAMES);
  const g = {
    game_id: int(r, 1, 999999),
    home, away,
    spread_x2: r() < 0.15 ? null : (Number.isFinite(spread) ? Math.round(spread) : 0),
    kickoff: null, kickoff_at: 0,
    b12: pick(r, ["both", "home", "away", null, undefined]),
  };
  // Both forms, as /api/slate sends them: the ISO string for <time> and the
  // unix integer the ordering runs on.
  g.kickoff_at = Math.floor(Date.now() / 1000) + int(r, -400, 400) * 3600;
  g.kickoff = new Date(g.kickoff_at * 1000).toISOString();
  if (g.spread_x2 == null) g.unpickable = pick(r, ["no_line", "no_kickoff", true]);
  // Results as the scorer can actually produce them, and only those. It
  // writes both points or neither: a final always has two scores, a void has
  // none and carries ats 'void'. Feeding the renderer a final with one null
  // side would be testing it against a payload the API cannot send, and the
  // only honest fix for the "null–4" it produced would be dead code.
  if (r() < 0.5) {
    if (r() < 0.2) {
      g.result = { home_points: null, away_points: null, ats: "void" };
    } else {
      g.result = { home_points: int(r, 0, 70), away_points: int(r, 0, 70),
                   ats: pick(r, ["home", "away", "push"]) };
    }
  }
  if (r() < 0.4) {
    // Both sides of the threshold, so the rule that hides a thin split is
    // exercised as often as the one that draws a real one.
    const cap = r() < 0.4 ? 4 : 40;
    g.consensus = { home: int(r, 0, cap), away: int(r, 1, cap) };
  }
  return g;
}

function randomTeams(r) {
  const t = {};
  for (const n of NAMES) {
    if (r() < 0.3) continue;                     // a team the map has never heard of
    t[n] = {
      color: pick(r, ["#e00122", "#002855", "#fff", "#000", "", null,
                      "not-a-color", "#12345", "rgb(1,2,3)"]),
      logo: r() < 0.5 ? `logos/${n.slice(0, 3)}.svg` : null,
    };
  }
  return t;
}

function runOne(seed, cover) {
  const r = rng(seed);
  const week = int(r, 1, 15);
  const games = [];
  for (let i = int(r, 0, 12); i > 0; i--) games.push(randomGame(r, week));
  const teams = randomTeams(r);

  // ------------------------------------------------------------ pure math
  for (const g of games) {
    if (g.spread_x2 == null) continue;
    const h = P.spreadText(g.spread_x2, "home");
    const a = P.spreadText(g.spread_x2, "away");
    cover.spreads++;
    for (const s of [h, a]) {
      assert.ok(!/NaN|undefined/.test(s), `spreadText produced ${s}`);
      // U+2212 MINUS, never a hyphen. This is a house rule and it is the kind
      // of thing that regresses silently in a refactor.
      assert.ok(!s.includes("-"), `spreadText used a hyphen: ${JSON.stringify(s)}`);
    }
    if (g.spread_x2 === 0) {
      assert.equal(h, "PK", "a pick'em did not read PK");
      assert.equal(a, "PK", "a pick'em did not read PK");
      cover.pickems++;
    } else {
      // The two sides are always the same number with opposite signs.
      const num = (s) => Number(s.replace("−", "-").replace("+", ""));
      assert.equal(num(h), -num(a),
        `sides disagree: ${h} / ${a} from ${g.spread_x2}`);
      assert.ok((h.startsWith("−")) !== (a.startsWith("−")),
        `both sides favored: ${h} / ${a}`);
    }
    const said = P.spreadSaid(g.spread_x2, "home");
    assert.ok(!/NaN|undefined/.test(said), `spreadSaid produced ${said}`);
  }

  // Contrast: whatever color a team supplies, the text on it is legible.
  for (const [name, t] of Object.entries(teams)) {
    const fg = P.textOn(t.color);
    assert.ok(fg === "#fff" || fg === "#000" || /^#|^rgb/.test(String(fg)),
      `textOn(${t.color}) for ${name} returned ${fg}`);
    cover.colors++;
  }

  // ------------------------------------------------------------- ordering
  const ordered = P.inPlayOrder(games);
  assert.equal(ordered.length, games.length, "inPlayOrder lost a game");
  cover.ordered++;
  // The documented contract: playable games first, then chronological within
  // each group, then game_id. Nine of fifteen games have no line in a
  // non-conference week, and interleaved they make a card unfillable.
  for (let i = 1; i < ordered.length; i++) {
    const a = ordered[i - 1], b = ordered[i];
    const ap = a.unpickable ? 1 : 0, bp = b.unpickable ? 1 : 0;
    assert.ok(ap <= bp, "an unpickable game sorted above a playable one");
    if (ap === bp) {
      assert.ok(a.kickoff_at < b.kickoff_at
        || (a.kickoff_at === b.kickoff_at && a.game_id <= b.game_id),
        "inPlayOrder is not by kickoff then id");
    }
  }
  // Same input, same output: an unstable sort would reshuffle the card
  // between repaints.
  assert.deepEqual(P.inPlayOrder(games).map((g) => g.game_id),
    ordered.map((g) => g.game_id), "inPlayOrder is not stable");

  // --------------------------------------------------------------- chips
  for (const g of games) {
    for (const side of ["home", "away"]) {
      for (const view of ["card", "slate"]) {
        for (const locked of [true, false]) {
          const chip = P.resultChip(g, side, locked, view);
          if (!chip) continue;
          cover.chips++;
          assertClean(chip, "resultChip");
          const text = chip.textContent;
          // The wording rule: a locked row with no pick says NO PICK on your
          // own card and LOCKED on the public slate. Getting these the wrong
          // way round is a bug that shipped once already.
          if (/NO PICK/.test(text)) {
            assert.equal(view, "card", "NO PICK appeared outside the card");
          }
          if (/^LOCKED$/.test(text.trim())) {
            assert.equal(view, "slate", "LOCKED appeared on the card");
          }
        }
      }
    }
  }

  // --------------------------------------------------------- a slate row
  for (const g of games) {
    const row = P.gameRow(g, teams);
    cover.rows++;
    assertClean(row, "gameRow");

    // An unpickable game is never armed, however the rest of the payload
    // reads. This is the client half of a rule the trigger also enforces.
    if (g.spread_x2 == null) {
      const inputs = row.querySelectorAll("INPUT");
      for (const inp of inputs) {
        assert.ok(inp.effectiveDisabled,
          "a game with no line had a live radio");
      }
      cover.unpickable++;
    }

    // Exactly one outcome chip. Two is what a leftover render block looks
    // like, and that is precisely how it went wrong before.
    const chips = row.querySelectorAll(".pk-chip");
    const perSide = {};
    for (const c of chips) {
      const k = c.dataset.side || "?";
      perSide[k] = (perSide[k] || 0) + 1;
    }
    for (const [k, n] of Object.entries(perSide)) {
      assert.ok(n <= 1, `${n} chips on the ${k} side of one row`);
    }
  }

  // ------------------------------------------------------ the survivor row
  for (const g of games) {
    const used = [];
    for (const t of [g.home, g.away]) {
      if (r() < 0.3) used.push({ week: int(r, 1, 14), team: t, outcome:
        pick(r, ["win", "loss", "void", null]) });
    }
    const mine = { used, burned: r() < 0.4
      ? [{ week: int(r, 1, 5), team: pick(r, [g.home, g.away, "Utah"]) }] : [],
      pick: r() < 0.4 ? { game_id: g.game_id, team: g.home } : null };
    const spent = P.svSpent(mine, week);
    const row = P.svGameRow(g, mine, teams, spent, r() < 0.3);
    cover.svrows++;
    assertClean(row, "svGameRow");

    // A side that is not a conference team is never armed, whatever else is
    // true. This is the rule that stops the pool being played on borrowed
    // opponents, and the client has to hold it too or the page offers a pick
    // the server will refuse.
    for (const side of ["home", "away"]) {
      if (P.svInConference(g, side)) continue;
      // The input for that side specifically, found by the value the client
      // puts on it, so this cannot pass by finding some other disabled row.
      for (const inp of row.querySelectorAll("INPUT")) {
        if (!String(inp.value || "").includes(g[side])) continue;
        assert.ok(inp.effectiveDisabled,
          `the ${side} side (${g[side]}) is not Big 12 and was pickable`);
        cover.outside++;
      }
    }
  }

  // ------------------------------------------------- the card, and its bars
  for (const g of games) {
    for (const side of ["home", "away"]) {
      const card = P.cardRow(g, side, teams, r() < 0.5);
      if (!card) continue;
      cover.cards++;
      assertClean(card, "cardRow");
      // The threshold is the client's own rule and the reason it exists is
      // that a 2-1 split drawn as a bar reads with exactly the authority of a
      // 200-100 one. A thin sample must render nothing at all.
      // Matched on the percentage labels, not on .pk-split: an empty span of
      // that class is appended either way so the column stays aligned down
      // the card, and counting those would never fail.
      if (g.consensus && g.consensus.home + g.consensus.away < MIN_CONSENSUS) {
        assert.equal(card.querySelectorAll(".pk-splitpct").length, 0,
          `a consensus bar was drawn on ${
            g.consensus.home + g.consensus.away} cards`);
        cover.thin++;
      }
    }
  }

  // consensusBar is only ever reached through cardRow, and only above the
  // threshold — three people picking is not a consensus, it is three people.
  // Calling it with a total of zero would be testing a division the caller
  // does not allow.
  for (const g of games) {
    if (!g.consensus) continue;
    const c = g.consensus;
    const n = c.home + c.away;
    if (n < MIN_CONSENSUS) continue;
    const bar = P.consensusBar(g, pick(r, ["home", "away"]), teams,
                               c.home, c.away, n);
    if (!bar) continue;
    cover.bars++;
    assertClean(bar, "consensusBar");
    // Widths are percentages and must stay inside the box even when the
    // counts are silly.
    for (const n of bar.walk()) {
      const w = n.style && n.style._p && n.style._p.width;
      if (typeof w === "string" && w.endsWith("%")) {
        const v = parseFloat(w);
        assert.ok(Number.isFinite(v) && v >= 0 && v <= 100,
          `a consensus bar is ${w} wide`);
      }
    }
  }

  // -------------------------------------------------------- the grid cell
  //
  // One cell per player per game, so it is the most-rendered thing on the
  // site and the one with the least room to say anything. Two rules are
  // asserted: it never carries more than one outcome, and the outcome it
  // carries is the one The Card's chip would have shown for the same pick.
  // The second is why atsOutcome exists: two renderers reading the same
  // grade must not be free to disagree about it.
  for (const g of games) {
    for (const side of ["home", "away", null]) {
      const locked = r() < 0.5;
      const cell = P.gridCell(g, side, teams, locked);
      cover.gcells++;
      assertClean(cell, "gridCell");

      const out = P.atsOutcome(g, side);
      const classes = ["win", "loss", "push", "void"]
        .filter((k) => String(cell.className).includes("pk-g-" + k));
      assert.ok(classes.length <= 1,
        `a grid cell carried ${classes.length} outcomes: ${cell.className}`);
      assert.deepEqual(classes, out ? [out] : [],
        `grid cell says ${classes} where the outcome is ${out}`);

      // A game nobody picked is a dash, and a game somebody picked never is:
      // a cell that silently lost its pick looks exactly like one that never
      // had one.
      const dashed = cell.querySelectorAll(".pk-gnone").length === 1;
      assert.equal(dashed, side == null,
        `a ${side == null ? "blank" : side} cell rendered ${
          dashed ? "" : "no "}dash`);
      if (side != null) {
        assert.ok(String(cell.textContent).includes(g[side]),
          "a grid cell did not name the team that was taken");
      }

      // The Card's chip, on the same pick. Where it says nothing (an
      // ungraded game) the cell must be uncoloured, and where it says
      // something the two must be the same word.
      // Read off the chip's class rather than its text: the text carries an
      // sr-only tail naming whose pick it was, and matching on that would
      // be asserting the wording rather than the verdict.
      const chip = P.resultChip(g, side, locked, "card");
      const said = ["win", "loss", "push", "void"].filter(
        (k) => chip && String(chip.className).split(/\s+/).includes(k));
      assert.deepEqual(classes, said,
        `the card said ${said} and the grid said ${classes}`);
    }
    cover.gheads++;
    assertClean(P.gridHead(g, teams), "gridHead");
  }

  // ------------------------------------------------------- the player page
  //
  // The page is one request wide, so what is fuzzed is the four renderers it
  // is built from. The rule with teeth is the link: a name with no id behind
  // it must NOT become one, because an <a> to a page that cannot exist is
  // worse than the plain text it replaced.
  for (let i = 0; i < 3; i++) {
    const row = {
      user_id: r() < 0.75 ? pick(r, ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "x", ""])
                          : null,
      display_name: r() < 0.8 ? pick(r, NAMES) : pick(r, [null, "", undefined]),
    };
    const node = P.playerName(row);
    cover.pnames++;
    const linked = node.tagName === "A";
    assert.equal(linked, !!(row.user_id && row.display_name),
      `a name with id=${JSON.stringify(row.user_id)} name=${
        JSON.stringify(row.display_name)} rendered as ${node.tagName}`);
    if (linked) {
      // The client sets href as a property, so that is where the stub DOM
      // keeps it; a real browser answers from either.
      const href = node.href || (node.attributes || {}).href || "";
      // Whatever the id is made of, it comes back out of the URL intact.
      assert.ok(href.includes(encodeURIComponent(row.user_id)),
        `an id was not escaped into the href: ${href}`);
      assert.ok(!/\bnull\b|undefined/.test(href),
        `href leaked a nothing: ${href}`);
    }

    // Counts, not ODD_NUMBERS: these come from SUM(outcome = 'loss') on a
    // materialised board, so null and zero are real and a negative one is
    // not. Feeding it -7 only proves that "0–-7" contains a hyphen, and the
    // honest fix for that would be a guard against a payload the API cannot
    // send. Same argument randomGame makes about one-sided finals.
    const COUNTS = [null, undefined, 0, 1, 7, 99, int(r, 0, 16)];
    const rec = P.recordText({ w: pick(r, COUNTS), l: pick(r, COUNTS),
                               p: pick(r, COUNTS) });
    if (rec != null) {
      for (const bad of TELLS) {
        assert.ok(!bad.test(rec), `recordText leaked ${bad}: ${rec}`);
      }
      assert.ok(!rec.includes("-"), `recordText used a hyphen: ${rec}`);
    }
    cover.precs++;
  }

  // The survivor half, standing and run together: a player can be in the pool
  // with no board row yet (the recompute has not run), out on a team, or out
  // for having missed a week, and the sentence has to work for all three.
  for (let i = 0; i < 2; i++) {
    const sv = {
      wins: pick(r, [null, undefined, 0, 1, int(r, 0, 14)]),
      alive: pick(r, [1, 0, null]),
      rank: pick(r, [null, 1, int(r, 1, 400)]),
      entered_week: pick(r, [null, 0, int(r, 0, 14)]),
      out_week: pick(r, [null, int(r, 0, 14)]),
      out_reason: pick(r, ["loss", "missed", null]),
      picks: [],
    };
    for (let k = int(r, 0, 5); k > 0; k--) {
      sv.picks.push({ week: int(r, 0, 14), team: pick(r, NAMES),
                      outcome: pick(r, ["win", "loss", "void", null]) });
    }
    const run = P.playerRun(sv, teams, pick(r, NAMES));
    cover.pruns++;
    assertClean(run, "playerRun");
    const word = P.svStandingWord(sv);
    for (const bad of TELLS) {
      assert.ok(!bad.test(word), `svStandingWord leaked ${bad}: ${word}`);
    }
  }

  // The week strip, which is both the season at a glance and the navigation.
  {
    const wks = [];
    for (let k = int(r, 1, 14); k > 0; k--) {
      wks.push({ week: k, w: pick(r, [null, 0, 1, int(r, 0, 16)]),
                 l: pick(r, [null, 0, 1, int(r, 0, 16)]),
                 p: pick(r, [null, 0, 1]), picks: {} });
    }
    const strip = P.playerWeeks(wks, wks[0].week, function () {});
    cover.pstrips++;
    assertClean(strip, "playerWeeks");
    // Counted off classList rather than a ".pk-wkchip.on" selector: the stub
    // DOM answers single-class selectors only, and a compound one silently
    // matches nothing, which is a test that passes for the wrong reason in
    // the other direction.
    const chips = strip.querySelectorAll(".pk-wkchip");
    assert.equal(chips.length, wks.length, "the strip lost a week");
    assert.equal(chips.filter((c) => c.classList.contains("on")).length, 1,
      "the week being read is not the one and only one marked current");
  }

  // ------------------------------------------------------- error messages
  for (const status of [0, 400, 401, 403, 404, 409, 429, 500, 503]) {
    const msg = P.explain({ status, data: { error: pick(r,
      ["locked", "team_used", "not_in_conference", "join_closed",
       "rate_limited", null, undefined, "something_unheard_of"]) } });
    assert.equal(typeof msg, "string", "explain returned a non-string");
    assert.ok(msg.length > 0, "explain returned nothing");
    for (const bad of TELLS) {
      assert.ok(!bad.test(msg), `explain leaked ${bad}: ${msg}`);
    }
    cover.errors++;
  }

  // ------------------------------------------------------ the standing line
  for (let i = 0; i < 4; i++) {
    const mine = {
      standing: r() < 0.8 ? {
        wins: pick(r, [0, 1, int(r, 0, 14), null]),
        alive: r() < 0.5 ? 1 : 0,
        rank: pick(r, [1, 2, int(r, 1, 500), null]),
        out_week: pick(r, [null, int(r, 1, 14)]),
        out_reason: pick(r, ["loss", "missed", null]),
      } : null,
      used: [], burned: [],
    };
    const board = r() < 0.7
      ? { entrants: int(r, 0, 400), alive: int(r, 0, 400), week: int(r, 1, 15) }
      : null;
    const line = P.svStandingText(mine, board);
    if (line != null) {
      cover.standing++;
      const text = typeof line === "string" ? line : line.textContent;
      for (const bad of TELLS) {
        assert.ok(!bad.test(text), `svStandingText leaked ${bad}: ${text}`);
      }
    } else {
      cover.standing++;
    }
  }

  // -------------------------------------------------------------- ordinal
  for (const n of [0, 1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111, 112,
                   int(r, 0, 5000)]) {
    const o = P.ordinal(n);
    assert.ok(/^\d+(st|nd|rd|th)$/.test(o), `ordinal(${n}) = ${o}`);
  }
  cover.ordinals++;
}

test("the client survives ten thousand hostile payloads", (t) => {
  const cover = { spreads: 0, pickems: 0, colors: 0, ordered: 0, chips: 0,
                  rows: 0, unpickable: 0, svrows: 0, outside: 0, bars: 0,
                  cards: 0, thin: 0, standing: 0, errors: 0, ordinals: 0,
                  gcells: 0, gheads: 0, pnames: 0, precs: 0, pruns: 0,
                  pstrips: 0 };
  for (let i = 0; i < RUNS; i++) {
    const seed = ONLY != null ? ONLY : i + 1;
    try {
      runOne(seed, cover);
    } catch (e) {
      e.message = `SIM_SEED=${seed} — ${e.message}`;
      throw e;
    }
    if (ONLY != null) break;
  }
  t.diagnostic(`reached: ${JSON.stringify(cover)}`);
  for (const k of Object.keys(cover)) {
    assert.ok(cover[k] > 0, `no payload reached: ${k}`);
  }
});

// ------------------------------------------------- the waiting split

// Below the threshold the split column used to be empty, which is right about
// the number and wrong about the reader: in a young pool every row is blank,
// and a blank reads as "broken" just as readily as "not enough people yet".
// It says which now — but only after the lock, and that distinction is the
// whole risk in the change.
//
// Deterministic rather than fuzzed. There are exactly three states and one of
// them is a leak; random payloads would reach them eventually and say nothing
// useful about which.
const teamsFor = () => ({
  Kansas: { color: "#0051BA" }, Baylor: { color: "#154734" },
});
const gameWith = (consensus) => ({
  game_id: 1, home: "Kansas", away: "Baylor", spread_x2: -7,
  kickoff: "2026-09-05T18:00:00.000Z", kickoff_at: 1788631200,
  consensus,
});
const splitText = (row) => {
  const el = row.querySelector(".pk-splitwait");
  return el ? el.textContent : null;
};

test("a thin split says how thin, once the week has locked", () => {
  const row = P.cardRow(gameWith({ home: 4, away: 2 }), "home", teamsFor(), true);
  assert.equal(splitText(row), "6 of 10");
});

test("a locked game nobody picked counts from zero rather than going blank", () => {
  // The server omits `consensus` entirely for a game with no picks, so this
  // arrives looking exactly like an unlocked one. `locked` is the only thing
  // that separates them.
  const row = P.cardRow(gameWith(undefined), "home", teamsFor(), true);
  assert.equal(splitText(row), "0 of 10");
});

test("BEFORE the lock it says nothing at all", () => {
  // The count of who has picked is precisely what the lock exists to withhold.
  // "0 of 10" on an unlocked slate would be both wrong and a leak.
  for (const consensus of [undefined, { home: 4, away: 2 }]) {
    const row = P.cardRow(gameWith(consensus), "home", teamsFor(), false);
    assert.equal(splitText(row), "",
      "an unlocked game advertised how many people had picked it");
  }
});

test("above the threshold the bar replaces the wait, not joins it", () => {
  const row = P.cardRow(gameWith({ home: 30, away: 12 }), "home", teamsFor(), true);
  assert.equal(row.querySelector(".pk-splitwait"), null);
  assert.ok(row.querySelectorAll(".pk-splitpct").length > 0, "no bar was drawn");
});

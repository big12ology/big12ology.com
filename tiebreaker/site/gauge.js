/* The pick'em consensus gauge. One implementation, for the three places on
 * the site that draw it:
 *
 *   /pools/pickem/card.html   a row of your card          (site_pools/app.js)
 *   /schedule/                a line on each slate card   (pickcon.js)
 *   /schedule/game/*.html     the "Pickem says" card      (pickcon.js)
 *
 * It reads the same way in all three because it IS the same code: each side's
 * mark, its share of the room, and a bar filled to that share. There were two
 * copies before this, in two sections that cannot import from each other, and
 * they had already drifted into saying different things about the same number.
 *
 * Shared the way pct.js is shared: a classic script under /tiebreaker/ that
 * the pools pages reach back for, so there is one file and one hash rather
 * than a copy per section.
 *
 * WHAT THE CALLER OWNS. Colors, because each section has its own token for a
 * team it has no color for, and where the gauge sits on the page, because
 * that is layout. Everything else is here, the accessible sentence included:
 * the visible labels are marks with an empty alt, so a gauge without that
 * sentence is two numbers and a bar to anyone not reading it by eye.
 */
(function (root) {
  "use strict";

  var CSS =
    ".pk-split{display:flex;align-items:center;gap:6px;min-width:0}" +
    /* TWO BLOCKS, NOT A GRADIENT WITH A MARKER ON IT. A blend between two
       dark team colors is one smear, and against the neutral a team with no
       color gets it is not even that, so the only thing left carrying the
       number was a 3px mark floating on top. A mark on an undifferentiated
       bar does not say "the split is here", it says "a line is drawn here",
       and it got read as the spread and as a fill boundary that made
       whichever team it sat nearest look like the favorite.
       Length carries it instead. Geometry does not depend on hue. */
    ".pk-splitbar{display:flex;align-items:stretch;gap:3px;flex:1;" +
      "min-width:40px;height:7px}" +
    /* flex-grow off a zero basis: the pair divides the column in exactly the
       ratio of the vote. The floor keeps a side nobody took visible as a nub,
       because 3% and 0% should not both render as nothing. */
    ".pk-splitbar i{flex-basis:0;min-width:3px;border-radius:3px}" +
    /* And the numbers say WHOSE. Two bare percentages beside a bar arrive
       unattached: 18 and 82, of what. The mark is the label, and it is the
       same mark the matchup elsewhere in the row already carries. Not an
       abbreviation, because only conference teams have one and truncating the
       rest into existence is the collision build.py's team_abbr refuses. */
    ".pk-splitpct{display:inline-flex;align-items:center;gap:4px;" +
      "font-size:12px;color:var(--dim);font-weight:600;" +
      "font-variant-numeric:tabular-nums;white-space:nowrap;min-width:3.9em}" +
    ".pk-splitpct.pk-home{justify-content:flex-end;text-align:right}" +
    ".pk-splitpct .mark{width:13px;height:13px;margin:0;flex:none;" +
      "vertical-align:baseline}" +
    ".pk-splitpct .nomark{width:13px;height:13px;line-height:13px;" +
      "font-size:10px}" +
    /* One size up, for the game page, where the gauge is the card's whole
       subject rather than one line of a card about something else. */
    ".pk-splitbig{gap:9px;margin:4px 0 0}" +
    ".pk-splitbig .pk-splitbar{height:9px;min-width:80px;gap:4px}" +
    ".pk-splitbig .pk-splitbar i{border-radius:4px}" +
    ".pk-splitbig .pk-splitpct{font-size:14px;color:var(--ink);" +
      "min-width:4.6em}" +
    ".pk-splitbig .pk-splitpct .mark{width:18px;height:18px}" +
    ".pk-splitbig .pk-splitpct .nomark{width:18px;height:18px;" +
      "line-height:18px;font-size:13px}" +
    /* Read aloud, never drawn. */
    ".pk-sr{position:absolute;width:1px;height:1px;overflow:hidden;" +
      "clip-path:inset(50%);white-space:nowrap}" +
    /* The neutral for a side we have no team color for, declared the way the
       rest of the site declares a theme-varying token: once for readers on
       "system", once for readers who chose. A single baked hex put the light
       theme's graphite on a dark card, where it is invisible, and a block
       that vanishes is half the reading.
       The pools section passes its own --tc-none instead, which carries the
       same two values for the same reason. */
    ":root{--pk-none:#252932}" +
    "@media (prefers-color-scheme:dark){" +
      ":root:not([data-theme=\"light\"]){--pk-none:#454c5a}}" +
    ":root[data-theme=\"dark\"]{--pk-none:#454c5a}";

  var styled = false;
  function style(doc) {
    if (styled) return;
    // getElementsByTagName as well as .head, and a bail if there is neither:
    // the pick'em client is exercised against a stub DOM in
    // worker/test/pools.sim.test.js, which builds nodes but has no document
    // to hang a stylesheet on. Nothing there looks at pixels.
    var head = doc.head ||
      (doc.getElementsByTagName && doc.getElementsByTagName("head")[0]);
    if (!head) return;
    styled = true;
    var s = doc.createElement("style");
    s.textContent = CSS;
    head.appendChild(s);
  }

  function seg(doc, pct, color) {
    var s = doc.createElement("i");
    s.style.flexGrow = String(pct);
    s.style.background = color;
    return s;
  }

  // A share and whose it is. The mark leads on the away side and trails on
  // the home side, so each number sits against its own end of the bar.
  //
  // A team with no usable mark gets its number alone. That is honest: this
  // site does not draw a stand-in for a mark it does not have, and the gauge
  // is still unambiguous while the other end is marked.
  function label(doc, side, pct, mark, cls) {
    var s = doc.createElement("span");
    s.className = "pk-splitpct pk-" + side + (cls ? " " + cls : "");
    var num = doc.createTextNode(pct + "%");
    if (side === "away") {
      if (mark) s.appendChild(mark);
      s.appendChild(num);
    } else {
      s.appendChild(num);
      if (mark) s.appendChild(mark);
    }
    return s;
  }

  /**
   * away, home: {pct, color, name, mark}
   *   pct    whole number, and the two must total 100
   *   color  the team's color, or the caller's own neutral for one we have
   *          no color for; falls back to --pk-none when empty
   *   name   spelled out, for the sentence and the tooltip
   *   cls    an extra class for that side's label, or nothing. The pools
   *          card marks the side you took with it; the schedule, which does
   *          not know what you took, passes none.
   *   mark   an element, or null. Callers pass one they already have: the
   *          pools client builds it from teams.json, and the schedule moves
   *          the one build.py rendered into the page, so neither has to
   *          re-decide which teams have a usable mark.
   * opts: {big, cards, title}
   */
  function build(away, home, opts) {
    // The ambient document, falling back off `root` only if there is one
    // there instead. A browser has both; the stub DOM the pick'em client is
    // fuzzed against installs the global but hangs nothing off its `window`.
    var doc = (typeof document !== "undefined" && document) || root.document;
    opts = opts || {};
    style(doc);

    var wrap = doc.createElement("div");
    wrap.className = "pk-split" + (opts.big ? " pk-splitbig" : "");
    wrap.appendChild(label(doc, "away", away.pct, away.mark, away.cls));

    var bar = doc.createElement("span");
    bar.className = "pk-splitbar";
    bar.appendChild(seg(doc, away.pct, away.color || "var(--pk-none)"));
    bar.appendChild(seg(doc, home.pct, home.color || "var(--pk-none)"));
    wrap.appendChild(bar);

    wrap.appendChild(label(doc, "home", home.pct, home.mark, home.cls));

    if (opts.title !== false) {
      wrap.title = away.pct + "% took " + away.name + ", " +
                   home.pct + "% took " + home.name +
                   (opts.cards ? ", " + opts.cards + " cards" : "");
    }
    var sr = doc.createElement("span");
    sr.className = "pk-sr";
    sr.textContent = (opts.cards ? "Of " + opts.cards + " cards, " : "") +
      away.pct + " percent took " + away.name + " and " +
      home.pct + " percent took " + home.name + ".";
    wrap.appendChild(sr);
    return wrap;
  }

  root.B12GAUGE = { build: build };
})(typeof window !== "undefined" ? window : globalThis);

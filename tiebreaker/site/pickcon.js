/* What the pick'em made of a game, on the pages that already describe it.
 *
 * Two shapes, one file, one request:
 *
 *   #pickcon          the full card on a game page — a gauge, both
 *                     percentages, and a sentence
 *   [data-pkcon]      a one-line gauge on each card of a week's slate, with
 *                     the two team abbreviations and no numbers at all
 *
 * The slate page carries sixteen of these, so it asks once for the whole week
 * rather than sixteen times for one game each.
 *
 * The schedule section is otherwise entirely static and this is its only
 * dependency on /api/*. Everything ships hidden and empty, so a Worker that is
 * down, a week that has not locked, a game with no line, or a game too few
 * people picked all leave the page exactly as it was generated. There is no
 * failure message because there is no failure: the row is simply not there.
 */
(function () {
  "use strict";

  // Below this many cards a split is not a consensus, it is a handful of
  // people — and drawn as a bar it would carry exactly the authority of a
  // real one. Matches MIN_CONSENSUS in the pick'em client.
  var MIN = 10;

  var full = document.getElementById("pickcon");
  var rows = [].slice.call(document.querySelectorAll("[data-pkcon]"));
  if (!full && !rows.length) return;

  var ids = rows.map(function (r) { return r.dataset.pkcon; });
  if (full && full.dataset.gid) ids.push(full.dataset.gid);
  if (!ids.length) return;

  var CSS =
    ".pcsplit{display:flex;align-items:center;gap:8px;margin:2px 0 0}" +
    ".pcbar{display:flex;align-items:stretch;flex:1;min-width:60px;" +
      "height:8px;gap:3px}" +
    ".pcbar i{flex-basis:0;min-width:3px;border-radius:3px}" +
    ".pcpct{font-size:13px;font-variant-numeric:tabular-nums;" +
      "white-space:nowrap;font-weight:600}" +
    ".pcpct.r{text-align:right}" +
    ".pcnames{display:flex;justify-content:space-between;gap:10px;" +
      "font-size:12px;color:var(--dim);margin-top:4px}" +
    /* the slate version: shorter, quieter, no numbers */
    "[data-pkcon] .pcsplit{gap:8px;margin:0}" +
    "[data-pkcon] .pcbar{height:6px;min-width:40px;flex:1}" +
    "[data-pkcon] .pcab{font-size:11px;color:var(--dim);font-weight:600;" +
      "letter-spacing:.02em}";

  var styled = false;
  function style() {
    if (styled) return;
    styled = true;
    var s = document.createElement("style");
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  // Two solid blocks, not a gradient with a needle laid over it.
  //
  // The gradient was the whole bug. Between two dark team colors, and against
  // the graphite a non-conference opponent gets for having no color at all,
  // the blend reads as one continuous smear. That left a 3px marker as the
  // only thing carrying the number, and a marker on an undifferentiated bar
  // is not a proportion: it reads as a line drawn ON something. Which is how
  // it was being read, as the spread, and as a fill boundary that made the
  // team nearest it look like the favorite when it was the one nobody took.
  //
  // Filled to length instead. The size of each block IS that side's share, so
  // the reading survives two colors a reader cannot tell apart, and the break
  // between them is a real gap in the page rather than a mark that has to
  // out-contrast sixteen team colors at once.
  function gauge(ap, ac, hc) {
    var bar = document.createElement("span");
    bar.className = "pcbar";
    bar.appendChild(seg(ap, ac));
    bar.appendChild(seg(100 - ap, hc));
    return bar;
  }

  // flex-grow against a zero basis, so the two blocks split whatever the
  // column gives them in exactly the ratio of the vote. The min-width keeps a
  // side nobody took visible as a nub: 3% and 0% should not both be nothing.
  function seg(pct, color) {
    var s = document.createElement("i");
    s.style.flexGrow = String(pct);
    s.style.background = color;
    return s;
  }

  function fillRow(node, d) {
    var h = d.home || 0, a = d.away || 0, n = h + a;
    if (n < MIN) return;
    style();
    var ap = 100 - Math.round(100 * h / n);
    var wrap = document.createElement("div");
    wrap.className = "pcsplit";
    var l = document.createElement("span");
    l.className = "pcab";
    l.textContent = node.dataset.al;
    var r = document.createElement("span");
    r.className = "pcab";
    r.textContent = node.dataset.hl;
    wrap.appendChild(l);
    wrap.appendChild(gauge(ap, node.dataset.ac, node.dataset.hc));
    wrap.appendChild(r);
    node.appendChild(wrap);
    node.title = ap + "% of " + n + " pick'em cards took " + node.dataset.al +
                 ", " + (100 - ap) + "% took " + node.dataset.hl;
    node.hidden = false;
  }

  function fillCard(node, d) {
    var h = d.home || 0, a = d.away || 0, n = h + a;
    if (n < MIN) return;
    style();
    var hp = Math.round(100 * h / n), ap = 100 - hp;
    var away = node.dataset.away, home = node.dataset.home;
    var body = node.querySelector(".pcbody");

    var wrap = document.createElement("div");
    wrap.className = "pcsplit";
    var l = document.createElement("span");
    l.className = "pcpct";
    l.textContent = ap + "%";
    var r = document.createElement("span");
    r.className = "pcpct r";
    r.textContent = hp + "%";
    wrap.appendChild(l);
    wrap.appendChild(gauge(ap, node.dataset.ac || "#252932",
                               node.dataset.hc || "#252932"));
    wrap.appendChild(r);
    body.appendChild(wrap);

    var names = document.createElement("div");
    names.className = "pcnames";
    var ln = document.createElement("span");
    ln.textContent = away;
    var rn = document.createElement("span");
    rn.textContent = home;
    names.appendChild(ln);
    names.appendChild(rn);
    body.appendChild(names);

    var note = document.createElement("p");
    note.className = "note";
    note.textContent = n + " card" + (n === 1 ? "" : "s") +
      ", counted after the week locked. " +
      (Math.max(hp, ap) >= 65
        ? "A clear lean toward " + (hp > ap ? home : away) + "."
        : Math.max(hp, ap) <= 55
          ? "Close to an even split."
          : "A modest lean toward " + (hp > ap ? home : away) + ".");
    body.appendChild(note);
    node.hidden = false;
  }

  fetch("/api/consensus?games=" + encodeURIComponent(ids.join(",")),
        {headers: {Accept: "application/json"}})
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.games) return;
      rows.forEach(function (node) {
        var c = d.games[node.dataset.pkcon];
        if (c) fillRow(node, c);
      });
      if (full && d.games[full.dataset.gid]) fillCard(full, d.games[full.dataset.gid]);
    })
    // Silent. Nothing here is load-bearing, and a page that announced a failed
    // request for a feature the reader never asked for would be worse than one
    // that quietly does not show it.
    .catch(function () {});
})();

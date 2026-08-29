/* What the pick'em made of a game, on the pages that already describe it.
 *
 * TWO OF THE THREE PLACES THE CONSENSUS GAUGE APPEARS:
 *
 *   #pickcon          the full card on a game page, gauge plus a sentence
 *   [data-pkcon]      the same gauge, small, on each card of a week's slate
 *
 * It does not draw either of them. gauge.js does, and the pick'em card row
 * calls the same function, which is what keeps the three from drifting. This
 * file is the part that is actually specific to the schedule section: which
 * elements to look for, one request for the whole page, and what to say
 * underneath on a page with room for a sentence.
 *
 * The marks are not built here either. They arrive in the page as hidden
 * .pcmk spans that build.py filled with logo_img, and this hands them to the
 * gauge, so the one place that knows which teams have a usable mark stays the
 * one place that decides.
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
(function (root) {
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

  // The two marks build.py parked in the node, in away/home order. Handed to
  // the gauge as elements rather than as URLs: they are already correct,
  // including the placeholder a team with no usable mark gets, and appendChild
  // on a node that has a parent moves it, so each ends up in its label.
  function marks(node) {
    var mk = node.querySelectorAll(".pcmk");
    return [mk[0] && mk[0].firstElementChild, mk[1] && mk[1].firstElementChild];
  }

  function fillRow(node, d) {
    var h = d.home || 0, a = d.away || 0, n = h + a;
    if (n < MIN) return;
    var ap = 100 - Math.round(100 * h / n), m = marks(node);
    node.appendChild(root.B12GAUGE.build(
      {pct: ap, color: node.dataset.ac, name: node.dataset.al, mark: m[0]},
      {pct: 100 - ap, color: node.dataset.hc, name: node.dataset.hl, mark: m[1]},
      {cards: n}));
    node.hidden = false;
  }

  function fillCard(node, d) {
    var h = d.home || 0, a = d.away || 0, n = h + a;
    if (n < MIN) return;
    var hp = Math.round(100 * h / n), ap = 100 - hp;
    var away = node.dataset.away, home = node.dataset.home;
    var body = node.querySelector(".pcbody");
    var m = marks(node);

    body.appendChild(root.B12GAUGE.build(
      {pct: ap, color: node.dataset.ac, name: away, mark: m[0]},
      {pct: hp, color: node.dataset.hc, name: home, mark: m[1]},
      {big: true, cards: n}));

    // The one thing this page has room for that the others do not. The gauge
    // says how far; this says what that is worth calling.
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
})(window);

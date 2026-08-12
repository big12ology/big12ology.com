// Every card on the domain, collapsible. Open unless you closed it.
//
// Progressive enhancement, and deliberately so: cards are generated in five
// unrelated places — build.py's three page shells, the attendance app, the
// pools app, and the four hand-written pages — and none of them share a
// template. A script that finds cards after the fact needs no change in any
// of them, and a page this never reaches simply has cards that are always
// open, which is the default state anyway.
//
// IT DOES NOT RESTRUCTURE THE CARD. The only DOM change is inside the <h2>:
// its contents move into a button. Everything below the heading stays exactly
// where it was, in the same parent, in the same order — because the Lab, the
// race board, the pick'em consensus and the attendance charts all reach into
// their cards by id and would not survive having a wrapper introduced above
// what they are looking for. Collapsing is then a class on the card and a
// rule in brand.css, not a move.
//
// If some other script later rewrites a heading's innerHTML, this button
// disappears with it and that card stops being collapsible. That is a
// degradation, not a break, and it is the right way round.
(function () {
  "use strict";

  // Storage, versioning and the fact that localStorage throws in private
  // mode all belong to state.js. If it is missing this degrades to cards
  // that collapse but do not remember, which is the right way round.
  var S = window.B12State;

  // Cards keyed by page and by their own id where they have one. A heading
  // slug is the fallback, and it is a weaker promise on two counts: rewrite
  // the copy and the reader's choice is forgotten, and the pre-paint restore
  // in the page head cannot match it, so a slug-keyed card flashes open
  // before it closes. Give a card an id and it gets both.
  function keyFor(card, h2) {
    if (card.id) return card.id;
    return (h2.textContent || "").trim().toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
  }

  function load() {
    var mine = S ? S.getPage("cards", []) : [];
    return Array.isArray(mine) ? mine : [];
  }

  function save(list) {
    if (S) S.setPage("cards", list);
  }

  var CHEV = "<svg class='cardchev' viewBox='0 0 24 24' aria-hidden='true'" +
    " fill='none' stroke='currentColor' stroke-width='2'" +
    " stroke-linecap='round' stroke-linejoin='round'>" +
    "<path d='M6 9l6 6l6 -6'/></svg>";

  function setup() {
    var closed = load();
    // .chart-card is the attendance tracker's panel: same idea, different
    // class, and the thirteen of them stacked down that page are the single
    // best argument for being able to close one.
    var cards = document.querySelectorAll(".card, .chart-card");
    for (var i = 0; i < cards.length; i++) {
      (function (card) {
        // h3 as well: the chart panels head themselves one level down,
        // which is right for their place in the document and irrelevant to
        // whether the panel can be closed.
        var h2 = card.querySelector(":scope > h2, :scope > h3");
        if (!h2 || h2.querySelector(".cardtoggle")) return;

        var key = keyFor(card, h2);
        if (!key) return;

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "cardtoggle";
        // The heading's parts go into a span, not straight into the button.
        // The button is a flex row so the chevron can sit at the far end,
        // and a heading made of several nodes — "What if… " plus a dim
        // subtitle, "Conference standings" plus a count — became flex ITEMS
        // and laid out side by side in columns.
        var label = document.createElement("span");
        label.className = "cardlabel";
        while (h2.firstChild) label.appendChild(h2.firstChild);
        btn.appendChild(label);
        btn.insertAdjacentHTML("beforeend", CHEV);
        h2.appendChild(btn);

        var on = closed.indexOf(key) === -1;
        var apply = function (open) {
          card.classList.toggle("is-collapsed", !open);
          btn.setAttribute("aria-expanded", open ? "true" : "false");
        };
        apply(on);

        btn.addEventListener("click", function () {
          on = !on;
          apply(on);
          var at = closed.indexOf(key);
          if (on && at !== -1) closed.splice(at, 1);
          else if (!on && at === -1) closed.push(key);
          save(closed);
          // Which way a card is thrown, and nothing about which card. The key
          // above is a heading slug or an element id and would be perfectly
          // good data — it is left out because the question this answers is
          // whether the pages are too long, and that is answered by the count.
          if (window.B12Metrics) {
            window.B12Metrics.send("card", on ? "expand" : "collapse");
          }
        });
      })(cards[i]);
    }
  }

  // The attendance tracker and the pools app build their cards after load, so
  // one pass at DOMContentLoaded would find half a page. Re-running is safe:
  // a card that already carries a toggle is skipped.
  function watch() {
    setup();
    if (!window.MutationObserver) return;
    var due = null;
    new MutationObserver(function () {
      if (due) return;
      due = setTimeout(function () { due = null; setup(); }, 120);
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch);
  } else {
    watch();
  }
})();

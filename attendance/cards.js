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
        // A CARD THAT IS ITSELF A LINK CANNOT BE COLLAPSIBLE, and the reason
        // is the anchor rather than anything about the card.
        //
        // The hub's four cards and the two on /pools/ are written as
        // <a class=card href=...> — the whole panel is the link. Put a button
        // inside one and a press does both things: the card collapses and the
        // browser follows the href. So the reader lands on another page with
        // the state already saved, comes back, and finds a card showing its
        // heading and nothing else — with no way to reopen it, because every
        // attempt navigates away again. It reads as a card that has gone
        // blank, which is exactly what it is.
        //
        // Detected rather than declared. An opt-out class would work until
        // somebody adds the seventh link-card and does not know to write it;
        // this asks the question that actually decides the answer. closest()
        // returns the card itself when the card IS the anchor, so one call
        // covers both shapes.
        if (card.closest("a")) return;

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

  /**
   * Retire the pre-paint stylesheet, now that the classes say the same thing.
   *
   * THE BUG THIS FIXES. Each page head carries a tiny script that reads the
   * saved list before anything paints and writes a stylesheet for it —
   * `#thatcard > *:not(h2):not(h3) { display:none }` — so a card the reader
   * closed last time does not flash open first. That is worth having, and it
   * worked. What it also did was outlive its job: the rule keys on the card's
   * ID, not on the class, so when this file later removed `is-collapsed` the
   * stylesheet went on hiding the contents anyway.
   *
   * The card opened. The chevron turned, the class came off, the padding
   * changed, and the inside stayed display:none — 9,000 characters of race
   * board sitting there at zero height. It only ever bit a card that was
   * ALREADY closed when the page loaded, which is why closing and reopening
   * one in the same visit always looked fine, and why this survived: you had
   * to leave and come back to see it.
   *
   * Removed rather than overridden. The obvious alternative — a louder rule
   * for the open state — would have to out-!important a selector written by
   * another file, and it would also un-hide children that are meant to be
   * hidden for their own reasons, like the fact lines on the hub that carry a
   * `hidden` attribute until something fills them.
   *
   * By id first, then by shape. New pages mark the tag; a page still serving
   * the older head script does not, and matching the rule text catches those
   * until the HTML catches up. Both are cheap and neither can fire twice.
   */
  function dropPrepaint() {
    var s = document.getElementById("b12-precollapse");
    if (s && s.parentNode) s.parentNode.removeChild(s);
    var all = document.head ? document.head.getElementsByTagName("style") : [];
    for (var i = all.length - 1; i >= 0; i--) {
      if (/:not\(h2\):not\(h3\)\s*\{\s*display:\s*none\s*\}/.test(all[i].textContent)) {
        all[i].parentNode.removeChild(all[i]);
      }
    }
  }

  // The attendance tracker and the pools app build their cards after load, so
  // one pass at DOMContentLoaded would find half a page. Re-running is safe:
  // a card that already carries a toggle is skipped.
  function watch() {
    setup();
    // After the first pass, never before it: every card that existed at parse
    // time now carries its own class, so the stylesheet has nothing left to
    // say. A card built later — the Lab's, the pools app's — is handled by
    // the observer below, and the worst it can now do is appear open for one
    // frame before its class lands. A frame of flash beats a card that never
    // opens again.
    dropPrepaint();
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

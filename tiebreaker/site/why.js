/* Why a tied team sits where it does on the fully-broken board.
 *
 * build.py writes a note for every team the ladder had to place: the step
 * that settled it, the team's figure at that step, and the pass in words.
 * This file draws that note twice over, as the chip on the row and as the
 * panel under it, and opens the panel when the chip is pressed.
 *
 * It decides nothing. Every word and every number arrives in the note, and
 * tie_chip and tie_panel in build.py draw the same two strings on the
 * server, so the row a reader sees before the replay repaints the board is
 * the row they see after. tests/test_tie_notes.py holds the two together.
 */
(function () {
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
  }

  function chip(tb) {
    return "<br><button class=tbchip type=button aria-expanded=false>" +
      "<b>" + esc(tb.s) + "</b><span>" + esc(tb.l) +
      (tb.v ? " " + esc(tb.v) : "") + "</span>" +
      "<svg class=tbchev viewBox='0 0 24 24' width=12 height=12 " +
      "aria-hidden=true><path d='M6 9l6 6l6 -6'/></svg></button>";
  }

  function panel(tb, team) {
    var steps = tb.st.map(function (s) {
      var cmp = "";
      if (s[2] && tb.rows.length) {
        cmp = "<div class=tbcmp>" + tb.rows.map(function (r) {
          return "<div class='tbline" + (r[4] ? " win" : "") +
            (r[0] === team ? " me" : "") + "'><span>" + esc(r[0]) +
            "</span><span class=tbnum>" + esc(r[1]) + "</span>" +
            (r[3] === null ? "<span></span>"
              : "<span class=tbbar><i style='width:" + r[3] +
                "%'></i></span>") +
            "<span class=tbnum>" + esc(r[2]) + "</span></div>";
        }).join("") + "</div>";
      }
      return "<li" + (s[2] ? " class=won" : "") + "><b>" + esc(s[0]) +
        "</b><div>" + esc(s[1]) + cmp + "</div></li>";
    }).join("");
    return "<tr class=tbrow hidden><td></td><td colspan=5>" +
      "<div class=tbbox><p>" + esc(tb.lead) + "</p>" +
      "<ol class=tbsteps>" + steps + "</ol></div></td></tr>";
  }

  var api = { chip: chip, panel: panel };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window === "undefined") return;
  window.B12WHY = api;

  // One listener for the board, not one per chip: the replay replaces every
  // row each time the week changes, and a chip drawn a moment ago has to
  // work without anything being wired to it.
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest(".tbchip") : null;
    if (!b) return;
    var tr = b.closest("tr");
    var next = tr && tr.nextElementSibling;
    if (!next || !next.classList.contains("tbrow")) return;
    var open = next.hidden;
    next.hidden = !open;
    tr.classList.toggle("tbopen", open);
    b.setAttribute("aria-expanded", open ? "true" : "false");
  });
})();

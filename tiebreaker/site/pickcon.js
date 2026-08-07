/* What the public did with the number — on a game page.
 *
 * The schedule section is otherwise entirely static, and this is its first
 * dependency on /api/*. It is written to be droppable: the card ships hidden,
 * this fills it in only when there is something to say, and a Worker that is
 * down, a game with no slate, or a week that has not locked all leave the page
 * exactly as it was generated.
 *
 * The consensus is served only after the week locks. That is not this file's
 * decision to make — the endpoint simply has nothing before then — but it is
 * the reason the card can sit on a public page at all: before the lock it
 * would be a way to see how everyone else was picking while you still could.
 *
 * Styles are injected here rather than added to the page's stylesheet so the
 * component is one file. The split bar also exists on /pickem/'s card, and two
 * copies of a rule in two stylesheets is exactly the drift this project has
 * already been bitten by with brand.css.
 */
(function () {
  "use strict";
  var card = document.getElementById("pickcon");
  if (!card || !card.dataset.gid) return;

  var CSS =
    "#pickcon .pcsplit{display:flex;align-items:center;gap:10px;margin:2px 0 0}" +
    "#pickcon .pcbar{position:relative;flex:1;min-width:80px;height:9px;" +
      "border-radius:5px;overflow:hidden}" +
    "#pickcon .pcmark{position:absolute;top:-2px;bottom:-2px;width:3px;" +
      "transform:translateX(-1.5px);background:var(--bg);" +
      "box-shadow:0 0 0 1px var(--ink,var(--fg));border-radius:1px}" +
    "#pickcon .pcpct{font-size:13px;font-variant-numeric:tabular-nums;" +
      "white-space:nowrap;font-weight:600}" +
    "#pickcon .pcpct.r{text-align:right}" +
    "#pickcon .pcnames{display:flex;justify-content:space-between;gap:10px;" +
      "font-size:12px;color:var(--dim);margin-top:4px}";

  function style() {
    var s = document.createElement("style");
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function render(d) {
    var h = d.home || 0, a = d.away || 0, n = h + a;
    if (!n) return;
    var hp = Math.round(100 * h / n), ap = 100 - hp;
    var away = card.dataset.away, home = card.dataset.home;
    var ac = card.dataset.ac || "#252932", hc = card.dataset.hc || "#252932";

    style();
    var wrap = document.createElement("div");
    wrap.className = "pcsplit";
    wrap.innerHTML =
      '<span class="pcpct">' + ap + "%</span>" +
      '<span class="pcbar" style="background:linear-gradient(90deg,' +
        ac + " 0%," + hc + ' 100%)">' +
        '<i class="pcmark" style="left:' + ap + '%"></i></span>' +
      '<span class="pcpct r">' + hp + "%</span>";

    var names = document.createElement("div");
    names.className = "pcnames";
    names.innerHTML = "<span></span><span></span>";
    names.children[0].textContent = away;
    names.children[1].textContent = home;

    var body = card.querySelector(".pcbody");
    body.appendChild(wrap);
    body.appendChild(names);

    var note = document.createElement("p");
    note.className = "note";
    note.textContent = n + " card" + (n === 1 ? "" : "s") +
      " from the pick'em, counted after the week locked. " +
      (Math.max(hp, ap) >= 65
        ? "A clear lean towards " + (hp > ap ? home : away) + "."
        : Math.max(hp, ap) <= 55
          ? "Close to an even split."
          : "A modest lean towards " + (hp > ap ? home : away) + ".");
    body.appendChild(note);

    card.hidden = false;
  }

  fetch("/api/consensus/" + encodeURIComponent(card.dataset.gid),
        {headers: {Accept: "application/json"}})
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d) render(d); })
    // Silent on purpose. Nothing here is load-bearing, and a game page that
    // announced a failed request for a feature the reader never asked about
    // would be worse than one that simply does not show the card.
    .catch(function () {});
})();

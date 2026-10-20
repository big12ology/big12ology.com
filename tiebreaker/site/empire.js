/* The Empire: redraw the imperialism map for any week of the season.
 *
 * build.py computes every week from the same results the rest of the site
 * reads and embeds them; this file only recolors the sixteen territories,
 * swaps the logo in each, and redraws the holdings table. It decides
 * nothing, so the scrubber cannot disagree with the map it scrubs.
 */
(function () {
  var node = document.getElementById("empire-data");
  if (!node) return;
  var data = JSON.parse(node.textContent);
  var frames = data.frames || [];
  if (frames.length < 2) return;
  var blocks = data.blocks, teams = data.teams;
  var at = frames.length - 1, timer = null;

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
  }

  function name(t) {
    var tm = teams[t] || {};
    var mark = tm.logo ? "<img class=mark src='" + tm.logo +
      "' alt='' width=20 height=20>" : "";
    var label = tm.page ? "<a class=teamlink href='" + tm.page + "'>" +
      esc(t) + "</a>" : esc(t);
    return mark + label;
  }

  // Same rows as build.py's _empire_rows, biggest empire first, the teams
  // that hold nothing at the bottom.
  function rows(f) {
    var hold = {};
    Object.keys(f.owner).forEach(function (b) {
      (hold[f.owner[b]] = hold[f.owner[b]] || []).push(b);
    });
    var list = Object.keys(blocks).map(function (t) {
      var mine = (hold[t] || []).sort();
      var area = mine.reduce(function (s, b) { return s + blocks[b].area; }, 0);
      return { team: t, blocks: mine, area: Math.round(area * 10000) / 10000 };
    });
    list.sort(function (a, b) {
      return b.area - a.area || (a.team < b.team ? -1 : a.team > b.team ? 1 : 0);
    });
    return list.map(function (r, i) {
      var held = r.blocks.length > 0;
      var pct = (r.area * 100).toFixed(1);
      return "<tr" + (held ? "" : " class=emout") + "><td class=posc>" +
        (held ? i + 1 : "") + "</td><td class=teamcell>" + name(r.team) +
        "</td><td class=emshare><span class=embar><i style='width:" + pct +
        "%'></i></span>" + pct + "%</td><td class=emlands>" +
        (held ? r.blocks.map(esc).join(", ")
              : "<span class=dim>none, out until it wins</span>") +
        "</td></tr>";
    }).join("");
  }

  function paint() {
    var f = frames[at];
    var took = {};
    f.took.forEach(function (b) { took[b] = true; });
    document.querySelectorAll("#em-blocks path").forEach(function (p) {
      var b = p.getAttribute("data-b"), t = f.owner[b];
      p.setAttribute("fill", teams[t].color);
      p.classList.toggle("emnew", !!took[b]);
      var title = p.querySelector("title");
      if (title) title.textContent = b + "'s home territory, held by " + t;
    });
    document.querySelectorAll("#em-marks image").forEach(function (im) {
      im.setAttribute("href", teams[f.owner[im.getAttribute("data-b")]].logo);
    });
    document.getElementById("em-rows").innerHTML = rows(f);
    document.getElementById("em-label").textContent = f.label;
    document.getElementById("em-range").value = String(at);
    document.getElementById("empirecard")
      .classList.toggle("scrubbed", at !== frames.length - 1);
  }

  function go(k) {
    at = Math.max(0, Math.min(frames.length - 1, k));
    paint();
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
    document.getElementById("em-play").textContent = "▶ Play";
  }

  function play() {
    if (timer) return stop();
    if (at === frames.length - 1) at = 0;
    paint();
    document.getElementById("em-play").textContent = "⏸ Pause";
    timer = setInterval(function () {
      if (at >= frames.length - 1) return stop();
      go(at + 1);
    }, 1200);
  }

  document.getElementById("empirebar").innerHTML =
    "<div class=rpline>" +
      "<button id=em-play class=wbtn>▶ Play</button>" +
      "<button id=em-prev class=wbtn aria-label='Previous week'>◀</button>" +
      "<button id=em-next class=wbtn aria-label='Next week'>▶</button>" +
      "<input id=em-range type=range min=0 max='" + (frames.length - 1) +
        "' step=1 value='" + at + "' aria-label='Week'>" +
    "</div>";
  document.getElementById("em-play").onclick = play;
  document.getElementById("em-prev").onclick = function () { stop(); go(at - 1); };
  document.getElementById("em-next").onclick = function () { stop(); go(at + 1); };
  document.getElementById("em-range").oninput = function () {
    stop();
    go(Number(this.value));
  };
})();

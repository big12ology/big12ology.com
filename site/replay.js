/* Season replay for The Standings — scrub or play through the year and
 * watch both boards rearrange week by week.
 *
 * Every frame was computed in Python by the same rules engine that renders
 * the page, then embedded. This file only draws them; it never decides an
 * order of its own, so the replay cannot disagree with the standings it is
 * replaying.
 */
(function () {
  var node = document.getElementById("replay-data");
  if (!node) return;
  var data = JSON.parse(node.textContent);
  var frames = data.frames || [];
  var teams = data.teams || {};
  if (frames.length < 2) return;

  var PCT = window.B12PCT;
  var bar = document.getElementById("replaybar");
  var leftBody = document.getElementById("board-left");
  var rightBody = document.getElementById("board-right");
  var at = frames.length - 1;
  var timer = null;

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function color(t) {
    return (teams[t] && teams[t].color) || "#888888";
  }

  function mark(t) {
    var logo = teams[t] && teams[t].logo;
    return logo
      ? "<img class=mark src='" + logo + "' alt='' width=20 height=20>"
      : "";
  }

  // Struck through once a team can no longer reach the title game, bold
  // once it has clinched a berth, bold italic for the top seed.
  function statusClass(state, pos) {
    if (state === "eliminated") return " st-out";
    if (state === "clinched") return pos === "1" ? " st-in st-top" : " st-in";
    return "";
  }

  function teamCell(r, pos) {
    var t = r.t;
    return "<td class='teamcell" + statusClass(r.s, pos) +
      "'><span class=cbar style='background:" + color(t) + "'></span>" +
      mark(t) + esc(t) + "</td>";
  }

  function recCells(r) {
    var p = PCT.pct(r.w, r.l);
    return "<td>" + r.w + "–" + r.l + "</td>" +
      (p === null ? "<td>—</td>"
                  : "<td style='color:" + PCT.color(p) + "'>" +
                    PCT.fmt(p) + "</td>");
  }

  // Rank a team held in the frame before this one, for the movement arrows.
  function priorRank(k, team) {
    if (k <= 0) return null;
    var rows = frames[k - 1].right;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].t === team) return i + 1;
    }
    return null;
  }

  function renderLeft(k) {
    return frames[k].left.map(function (r) {
      var pos = r.i === 0
        ? "<td class=posc" + (r.n > 1 ? " rowspan=" + r.n : "") + ">" +
          esc(r.p) + "</td>"
        : "";
      var cls = (r.n > 1 && r.i === r.n - 1) ? " class=grpend" : "";
      return "<tr" + cls + ">" + pos + teamCell(r, r.p) + recCells(r) +
        "</tr>";
    }).join("");
  }

  function renderRight(k) {
    return frames[k].right.map(function (r, i) {
      var was = priorRank(k, r.t);
      var d = was === null ? 0 : was - (i + 1);
      var mv = d === 0 ? "" :
        "<span class='mv " + (d > 0 ? "up" : "down") + "'>" +
        (d > 0 ? "▲" : "▼") + Math.abs(d) + "</span>";
      var cls = d === 0 ? "" : " class='moved " + (d > 0 ? "up" : "down") + "'";
      return "<tr" + cls + "><td class=posc>" + esc(r.p) + mv + "</td>" +
        teamCell(r, r.p) + recCells(r) + "</tr>";
    }).join("");
  }

  function paint() {
    var f = frames[at];
    leftBody.innerHTML = renderLeft(at);
    rightBody.innerHTML = renderRight(at);
    document.getElementById("rp-label").innerHTML =
      "Through <b>" + esc(f.label) + "</b>" +
      (f.date ? " <span class=dim>· " + esc(f.date) + "</span>" : "");
    document.getElementById("rp-range").value = String(at);
    var live = at === frames.length - 1;
    bar.classList.toggle("scrubbed", !live);
    document.getElementById("rp-now").hidden = live;
  }

  function go(k) {
    at = Math.max(0, Math.min(frames.length - 1, k));
    paint();
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
    document.getElementById("rp-play").textContent = "▶ Play";
  }

  function play() {
    if (timer) return stop();
    if (at === frames.length - 1) at = 0;
    paint();
    document.getElementById("rp-play").textContent = "⏸ Pause";
    timer = setInterval(function () {
      if (at >= frames.length - 1) return stop();
      go(at + 1);
    }, 1100);
  }

  bar.innerHTML =
    "<div class=rpline>" +
      "<button id=rp-play class=wbtn>▶ Play</button>" +
      "<button id=rp-prev class=wbtn aria-label='Previous week'>◀</button>" +
      "<button id=rp-next class=wbtn aria-label='Next week'>▶</button>" +
      "<input id=rp-range type=range min=0 max='" + (frames.length - 1) +
        "' step=1 value='" + at + "' aria-label='Week'>" +
      "<span id=rp-label></span>" +
      "<button id=rp-now class=wbtn hidden>Back to final</button>" +
    "</div>";

  document.getElementById("rp-play").onclick = play;
  document.getElementById("rp-prev").onclick = function () { stop(); go(at - 1); };
  document.getElementById("rp-next").onclick = function () { stop(); go(at + 1); };
  document.getElementById("rp-now").onclick = function () {
    stop();
    go(frames.length - 1);
  };
  document.getElementById("rp-range").oninput = function () {
    stop();
    go(Number(this.value));
  };
  paint();
})();

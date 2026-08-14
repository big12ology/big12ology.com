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

  function teamCell(r, pos, trail) {
    var t = r.t;
    return "<td class='teamcell" + statusClass(r.s, pos) +
      "'><span class=cbar style='background:" + color(t) + "'></span>" +
      mark(t) + esc(t) + (trail || "") + "</td>";
  }

  // Four cells, matching the head standings_page writes. The last two are
  // non-conference and overall: they decide nothing, which is why they are
  // dimmed, and they are here because a September board with only conference
  // records on it is all zeroes and reads as broken. A row this draws has to
  // have the same shape as the row the server drew, or the two new columns
  // go blank the instant the replay repaints the table.
  function recCells(r) {
    var p = PCT.pct(r.w, r.l);
    return "<td>" + r.w + "–" + r.l + "</td>" +
      (p === null ? "<td>—</td>"
                  : "<td style='color:" + PCT.color(p) + "'>" +
                    PCT.fmt(p) + "</td>") +
      "<td class=dim>" + r.nw + "–" + r.nl + "</td>" +
      "<td class=dim>" + r.ow + "–" + r.ol + "</td>";
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

  // The official board's position as a number: "T5" and "5" are both 5th,
  // "—" is a team with no conference result yet.
  function posNum(p) {
    var n = parseInt(String(p).replace("T", ""), 10);
    return isNaN(n) ? null : n;
  }

  function priorPos(k, team) {
    if (k <= 0) return null;
    var rows = frames[k - 1].left;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].t === team) return posNum(rows[i].p);
    }
    return null;
  }

  // Places gained since the previous frame; positive is a climb. Both
  // boards render this identically, in the same place — see the
  // presentation rules in README.md.
  function arrow(d) {
    return d === 0 ? "" :
      "<span class='mv " + (d > 0 ? "up" : "down") + "'>" +
      (d > 0 ? "▲" : "▼") + Math.abs(d) + "</span>";
  }

  function movedClass(d) {
    return d === 0 ? "" : " moved " + (d > 0 ? "up" : "down");
  }

  function renderLeft(k) {
    return frames[k].left.map(function (r) {
      var pos = r.i === 0
        ? "<td class=posc" + (r.n > 1 ? " rowspan=" + r.n : "") + ">" +
          esc(r.p) + "</td>"
        : "";
      // Movement against the official board, where a whole tied group can
      // move together. The arrow rides in the team cell because the
      // position cell is shared by everyone at that place.
      var was = priorPos(k, r.t), now = posNum(r.p);
      var d = (was === null || now === null) ? 0 : was - now;
      var cls = ((r.n > 1 && r.i === r.n - 1) ? " grpend" : "") + movedClass(d);
      return "<tr" + (cls ? " class='" + cls.trim() + "'" : "") + ">" + pos +
        teamCell(r, r.p, arrow(d)) + recCells(r) + "</tr>";
    }).join("");
  }

  function renderRight(k) {
    return frames[k].right.map(function (r, i) {
      var was = priorRank(k, r.t);
      var d = was === null ? 0 : was - (i + 1);
      var cls = movedClass(d);
      // Same placement as the official board: movement trails the team
      // name on both, so the eye finds it in one place.
      return "<tr" + (cls ? " class='" + cls.trim() + "'" : "") +
        "><td class=posc>" + esc(r.p) + "</td>" +
        teamCell(r, r.p, arrow(d)) + recCells(r) + "</tr>";
    }).join("");
  }

  function labelHTML(f) {
    return "Through <b>" + esc(f.label) + "</b>" +
      (f.date ? " <span class=dim>· " + esc(f.date) + "</span>" : "");
  }

  // The label is the only part of the bar whose text length varies, and the
  // slider is what absorbs the difference — so pin the label to the widest
  // week it will ever show and the bar stops breathing as you scrub.
  function pinLabelWidth() {
    var el = document.getElementById("rp-label");
    var widest = 0;
    frames.forEach(function (f) {
      el.innerHTML = labelHTML(f);
      widest = Math.max(widest, el.offsetWidth);
    });
    el.style.minWidth = widest + "px";
  }

  function paint() {
    var f = frames[at];
    leftBody.innerHTML = renderLeft(at);
    rightBody.innerHTML = renderRight(at);
    document.getElementById("rp-label").innerHTML = labelHTML(f);
    document.getElementById("rp-range").value = String(at);
    var live = at === frames.length - 1;
    bar.classList.toggle("scrubbed", !live);
    // Hidden but still occupying its space: `hidden` would collapse the
    // button and resize the slider every time you reach the last week.
    var back = document.getElementById("rp-now");
    back.classList.toggle("invis", live);
    back.disabled = live;
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
      "<button id=rp-now class='wbtn invis' disabled>Back to final" +
      "</button>" +
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
  pinLabelWidth();
  paint();
})();

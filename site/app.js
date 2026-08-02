/* Big 12 Tiebreaker Tracker — page behavior.
 *
 * Two jobs: (1) the standings sort toggle, (2) the what-if simulator. Picks
 * mark unplayed conference games as completed wins and re-run the official
 * tiebreaker via B12Engine (see engine.js, parity-tested against the Python
 * engine); the matchup card, standings, and tie narratives re-render to the
 * simulated season. Clearing all picks restores the server-rendered page.
 */
(function () {
  "use strict";

  var payload = JSON.parse(document.getElementById("payload").textContent);
  var teams = payload.teams;
  var stand = document.getElementById("stand");
  var stories = document.getElementById("stories");
  var matchcard = document.getElementById("matchcard");
  var chip = document.getElementById("w-chip");
  var sortRaw = false;
  var picks = {}; // gameId -> winning team
  var models = payload.models || [];
  var model = models.length ? models[0].name : null;

  function favs() {
    return (model && payload.favorites[model]) || {};
  }

  function modelYear(name) {
    for (var i = 0; i < models.length; i++) {
      if (models[i].name === name) return models[i].year;
    }
    return null;
  }

  var orig = {
    match: matchcard ? matchcard.innerHTML : "",
    stand: stand ? stand.innerHTML : "",
    stories: stories ? stories.innerHTML : "",
  };

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/'/g, "&#39;").replace(/"/g, "&quot;");
  }

  function mark(team, size) {
    var m = teams[team];
    if (!m) return "";
    return "<img class=mark src='" + m.logo + "' alt='' width=" + size +
      " height=" + size + " loading=lazy>";
  }

  function textOn(bg) {
    var c = bg.replace("#", "");
    if (c.length === 3) c = c.replace(/./g, "$&$&");
    var r = parseInt(c.substr(0, 2), 16), g = parseInt(c.substr(2, 2), 16),
      b = parseInt(c.substr(4, 2), 16);
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? "#1a1c20" : "#ffffff";
  }

  function color(team) {
    return (teams[team] && teams[team].color) || "#888888";
  }

  // ---------------------------------------------------------------- sorting

  function applySort() {
    if (!stand) return;
    var rows = Array.prototype.slice.call(stand.rows);
    rows.sort(function (a, b) {
      if (sortRaw) {
        var d = (+b.dataset.w) - (+a.dataset.w);
        if (d) return d;
        d = (+a.dataset.l) - (+b.dataset.l);
        if (d) return d;
      }
      return (+a.dataset.rank) - (+b.dataset.rank);
    });
    rows.forEach(function (r) { stand.appendChild(r); });
    var p = document.getElementById("sort-pct");
    var w = document.getElementById("sort-raw");
    if (p) p.classList.toggle("on", !sortRaw);
    if (w) w.classList.toggle("on", sortRaw);
  }

  (function bindSort() {
    var p = document.getElementById("sort-pct");
    var w = document.getElementById("sort-raw");
    if (p) p.onclick = function () { sortRaw = false; applySort(); };
    if (w) w.onclick = function () { sortRaw = true; applySort(); };
  })();

  // ------------------------------------------------------------- simulation

  function pickable() {
    return payload.games.filter(function (g) {
      return g.conference_game && !g.ccg && !g.completed;
    });
  }

  function simGames() {
    return payload.games.map(function (g) {
      var w = picks[String(g.id)];
      if (!w) return g;
      var sim = {};
      for (var k in g) sim[k] = g[k];
      sim.completed = true;
      sim.home_points = w === g.home ? 28 : 17;
      sim.away_points = w === g.home ? 17 : 28;
      return sim;
    });
  }

  function active() {
    return Object.keys(picks).length > 0;
  }

  // -------------------------------------------------------------- rendering

  function fmtPct(p) {
    return p === null ? "—" : p.toFixed(3);
  }

  function renderMatch(ccg, nLeft) {
    if (!ccg) {
      return "<h2>What-if projection</h2><p>Not enough simulated results " +
        "to project a matchup yet.</p>";
    }
    var badge = ccg.resolved
      ? "<span class='badge ok'>resolved</span>"
      : "<span class='badge warn'>needs SportSource rating or coin toss</span>";
    var status = nLeft === 0 ? "What-if championship matchup"
      : "What-if projection (" + nLeft + " conference games still unpicked)";
    var html = "<h2>" + status + " " + badge + "</h2><div class=matchup>";
    [ccg.seed1, ccg.seed2].forEach(function (t, i) {
      html += "<div class=side style='border-bottom-color:" + color(t) + "'>" +
        mark(t, 56) + "<div><span class=seed>" + (i + 1) + "</span> " +
        "<span class=tname>" + esc(t) + "</span></div></div>";
      if (i === 0) html += "<span class=vs>vs</span>";
    });
    html += "</div>";
    if (ccg.note) html += "<p class=note>" + esc(ccg.note) + "</p>";
    return html;
  }

  function renderRows(rows) {
    var tieColors = {};
    var html = rows.map(function (r) {
      var cls = "";
      var mk = "";
      if (r.tie_group) {
        if (!(r.tie_group in tieColors)) {
          tieColors[r.tie_group] = Object.keys(tieColors).length;
        }
        cls = "tie" + (tieColors[r.tie_group] % 4);
        mk = "<sup>" + (tieColors[r.tie_group] + 1) + "</sup>";
      }
      var p = (r.conf_w + r.conf_l) ? r.conf_w / (r.conf_w + r.conf_l) : null;
      return "<tr class='" + cls + "' data-rank=" + r.rank +
        " data-w=" + r.conf_w + " data-l=" + r.conf_l + ">" +
        "<td>" + r.rank + "</td>" +
        "<td class=teamcell><span class=cbar style='background:" +
        color(r.team) + "'></span>" + mark(r.team, 20) + esc(r.team) + mk +
        "</td><td>" + r.conf_w + "–" + r.conf_l + "</td>" +
        "<td>" + fmtPct(p) + "</td>" +
        "<td>" + r.overall_w + "–" + r.overall_l + "</td></tr>";
    }).join("");
    return html;
  }

  function renderStories(rows) {
    var n = 0;
    var html = "";
    rows.forEach(function (r) {
      if (r.log === null) return;
      n += 1;
      var names = r.tie_group.split("+").join(", ");
      html += "<details" + (n === 1 ? " open" : "") + "><summary><sup>" + n +
        "</sup> How the " + esc(names) + " tie breaks</summary>" +
        "<ol class=steps>" +
        r.log.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") +
        "</ol></details>";
    });
    return html || "<p class=dim>No ties in the standings.</p>";
  }

  var tablewrap = document.getElementById("tablewrap");
  var origWrapHidden = tablewrap ? tablewrap.hidden : false;

  function refresh() {
    renderPickList();
    if (!active()) {
      if (matchcard) matchcard.innerHTML = orig.match;
      if (stand) stand.innerHTML = orig.stand;
      if (stories) stories.innerHTML = orig.stories;
      if (chip) chip.hidden = true;
      if (tablewrap) tablewrap.hidden = origWrapHidden;
      applySort();
      return;
    }
    var games = simGames();
    var rows = B12Engine.standings(games, payload.overrides || {});
    var ccg = B12Engine.championship(games, payload.overrides || {});
    var nLeft = pickable().length - Object.keys(picks).length;
    if (matchcard) matchcard.innerHTML = renderMatch(ccg, nLeft);
    if (stand) stand.innerHTML = renderRows(rows);
    if (stories) stories.innerHTML = renderStories(rows);
    if (chip) chip.hidden = false;
    if (tablewrap) tablewrap.hidden = rows.length === 0;
    applySort();
  }

  // ------------------------------------------------------------- pick panel

  function weekLabel(g) {
    return "Week " + g.week;
  }

  function renderPickList() {
    var box = document.getElementById("wgames");
    if (!box) return;
    var games = pickable();
    var byWeek = {};
    var weeks = [];
    games.forEach(function (g) {
      var k = weekLabel(g);
      if (!byWeek[k]) { byWeek[k] = []; weeks.push(k); }
      byWeek[k].push(g);
    });
    var openWeek = weeks.length ? weeks[0] : null;
    var html = weeks.map(function (wk) {
      var inner = byWeek[wk].map(function (g) {
        var id = String(g.id);
        var fav = favs()[id];
        var date = (g.start || "").slice(5, 10).replace("-", "/");
        return "<div class=wgame>" +
          pickBtn(id, g.away, fav) +
          "<span class=at>at</span>" +
          pickBtn(id, g.home, fav) +
          "<span class=wdate>" + date + "</span></div>";
      }).join("");
      var picked = byWeek[wk].filter(function (g) {
        return picks[String(g.id)];
      }).length;
      return "<details" + (wk === openWeek ? " open" : "") + "><summary>" +
        wk + " <span class=dim>(" + picked + "/" + byWeek[wk].length +
        " picked)</span></summary>" + inner + "</details>";
    }).join("");
    box.innerHTML = html;
    var count = document.getElementById("w-count");
    if (count) {
      count.textContent = Object.keys(picks).length + " of " + games.length +
        " games picked";
    }
    box.querySelectorAll(".pick").forEach(function (btn) {
      btn.onclick = function () {
        var id = btn.dataset.id, team = btn.dataset.team;
        if (picks[id] === team) delete picks[id];
        else picks[id] = team;
        refresh();
      };
    });
  }

  function pickBtn(id, team, fav) {
    var sel = picks[id] === team;
    var isFav = fav && fav.team === team;
    var style = sel
      ? " style='background:" + color(team) + ";border-color:" + color(team) +
        ";color:" + textOn(color(team)) + "'"
      : "";
    return "<button class='pick" + (sel ? " sel" : "") + "' data-id='" + id +
      "' data-team='" + esc(team) + "'" + style + ">" + mark(team, 18) +
      esc(team) +
      (isFav ? "<span class=star title='" + esc(model) + " favorite by ~" +
        fav.margin + "'>★</span>" : "") +
      "</button>";
  }

  function updateNote() {
    var note = document.getElementById("w-note");
    if (!note) return;
    if (!model) {
      note.textContent = "No rating models available — pick every game by hand.";
      return;
    }
    note.textContent = "★ marks the " + model + " (" + modelYear(model) +
      " ratings) favorite; hover for the projected margin in points, " +
      "home field included. Picks re-run the full official tiebreaker " +
      "instantly — the matchup, standings, and tie narratives above update " +
      "to the simulated season. Non-conference results stay as they actually " +
      "happened (they only matter for the total-wins step).";
  }

  (function bindControls() {
    var favBtn = document.getElementById("w-fav");
    var clearBtn = document.getElementById("w-clear");
    var sel = document.getElementById("w-model");
    if (favBtn) {
      favBtn.onclick = function () {
        pickable().forEach(function (g) {
          var f = favs()[String(g.id)];
          if (f) picks[String(g.id)] = f.team;
        });
        refresh();
      };
    }
    if (clearBtn) clearBtn.onclick = function () { picks = {}; refresh(); };
    if (sel) {
      sel.onchange = function () {
        model = sel.value;
        updateNote();
        renderPickList();
      };
      if (model) sel.value = model;
    }
    updateNote();
  })();

  renderPickList();
})();

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

  // Current-state cache for the team explainer: actual season at load,
  // replaced by the simulated season while what-if picks are active.
  var actualRows = B12Engine.standings(payload.games, payload.overrides || {});
  var actualCcg = B12Engine.championship(payload.games, payload.overrides || {});
  var lastRows = actualRows;
  var lastCcg = actualCcg;

  function refresh() {
    renderPickList();
    if (!active()) {
      if (matchcard) matchcard.innerHTML = orig.match;
      if (stand) stand.innerHTML = orig.stand;
      if (stories) stories.innerHTML = orig.stories;
      if (chip) chip.hidden = true;
      if (tablewrap) tablewrap.hidden = origWrapHidden;
      lastRows = actualRows;
      lastCcg = actualCcg;
      renderTeamWhy();
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
    lastRows = rows;
    lastCcg = ccg;
    renderTeamWhy();
    applySort();
  }

  // ------------------------------------------------------- team explainer

  var STEP_NAMES = {
    a: "head-to-head",
    b: "record against common opponents",
    c: "the standings walk (next-highest-placed common opponent)",
    d: "strength of conference schedule",
    e: "total wins",
    f: "the SportSource Analytics rating",
    g: "a coin toss",
  };

  function ordinal(n) {
    var s = ["th", "st", "nd", "rd"];
    var v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  function fmtRec(r) {
    return r.conf_w + "–" + r.conf_l;
  }

  function renderTeamWhy() {
    var out = document.getElementById("team-out");
    var sel = document.getElementById("team-sel");
    if (!out || !sel) return;
    var chip2 = document.getElementById("w-chip2");
    if (chip2) chip2.hidden = !active();
    var team = sel.value;
    if (!team) {
      out.innerHTML = "<p class=dim>Pick a team to get a plain-English " +
        "explanation of exactly where they stand and which rule put them " +
        "there.</p>";
      return;
    }
    var rows = lastRows;
    var scenario = active() ? "In your what-if scenario, " : "";
    if (!rows.length) {
      out.innerHTML = "<p class=dim>All sixteen teams are 0–0 — there " +
        "are no standings to explain yet. Pick winners in the what-if " +
        "section above and this tool will explain the simulated pecking " +
        "order.</p>";
      return;
    }
    var row = null;
    rows.forEach(function (r) { if (r.team === team) row = r; });
    if (!row) {
      out.innerHTML = "<p class=dim>" + esc(team) + " has no completed " +
        "conference games in this scenario yet, so they have no standing " +
        "to explain.</p>";
      return;
    }

    var p = row.conf_w / (row.conf_w + row.conf_l);
    var html = "<div class=whyhead>" + mark(team, 22) + esc(team) + "</div>";
    html += "<p>" + esc(scenario) + esc(team) + " is <b>" +
      ordinal(row.rank) + "</b> at <b>" + fmtRec(row) + "</b> (" +
      p.toFixed(3) + " in conference play).";
    if (row.rank === 1) {
      html += " That is the projected <b>#1 seed</b> — in the championship " +
        "game.";
    } else if (row.rank === 2) {
      html += " That is the projected <b>#2 seed</b> — in the championship " +
        "game.";
    } else if (row.rank === 3) {
      html += " First team out: the top two make the championship game.";
    } else {
      html += " The top two make the championship game.";
    }
    html += "</p>";

    if (!row.tie_group) {
      var above = null, below = null;
      rows.forEach(function (r) {
        if (r.rank === row.rank - 1) above = r;
        if (r.rank === row.rank + 1) below = r;
      });
      html += "<p>No tiebreaker involved — no other team has their exact " +
        "winning percentage" +
        (above ? ", trailing " + esc(above.team) + " (" + fmtRec(above) + ")"
               : "") +
        (below ? (above ? " and" : ",") + " leading " + esc(below.team) +
          " (" + fmtRec(below) + ")" : "") +
        " on record alone. The official standard is winning percentage, " +
        "not raw wins, so games-played differences don't matter.</p>";
    } else {
      html += renderLadder(team, row, rows);
    }
    if (row.rank <= 2 && lastCcg && lastCcg.note) {
      html += "<p class=dim>" + esc(lastCcg.note) + "</p>";
    }
    out.innerHTML = html;
  }

  // Full resolution trace: every round the team was part of, every step of
  // the ladder in that round, each with a verdict — won here, lost here (to
  // whom), no separation, or not reached.
  var LETTERS = ["a", "b", "c", "d", "e", "f", "g"];

  function splitRounds(log) {
    var rounds = [{ lines: [], unresolved: false }];
    (log || []).forEach(function (l) {
      if (l.indexOf("Restarting procedure") === 0) {
        rounds.push({ lines: [], unresolved: false });
      } else if (l.indexOf("UNRESOLVED:") === 0) {
        rounds[rounds.length - 1].unresolved = true;
      } else {
        rounds[rounds.length - 1].lines.push(l);
      }
    });
    return rounds;
  }

  function renderLadder(team, row, rows) {
    var groupRows = rows.filter(function (r) {
      return r.tie_group === row.tie_group;
    });
    var first = groupRows[0];
    var events = first.events || [];
    var others = groupRows.filter(function (r) { return r.team !== team; })
      .map(function (r) { return r.team; });
    var pos = groupRows.indexOf(row) + 1;
    var rounds = splitRounds(first.log);
    var myEvIdx = -1;
    events.forEach(function (e, i) { if (e.team === team) myEvIdx = i; });

    var html = "<p>They're in a <b>" + groupRows.length + "-way tie</b> at " +
      fmtRec(row) + " with " + others.map(esc).join(", ") + ", and finished <b>" +
      ordinal(pos) + " of " + groupRows.length + "</b> in it. ";
    if (myEvIdx >= 0) {
      html += "Here is every level of the procedure they went through:</p>";
    } else if (first.resolved) {
      html += "They never won a level — the last spot is theirs by " +
        "elimination. Here is each level and who took it:</p>";
    } else {
      html += "Their tie <b>can't be fully broken from public data</b> — " +
        "it reaches the SportSource rating or coin toss, which only the " +
        "conference holds. Here is how far the public steps got:</p>";
    }

    html += "<div class=ladder>";
    var remaining = groupRows.map(function (r) { return r.team; });
    for (var ri = 0; ri < rounds.length; ri++) {
      var round = rounds[ri];
      var decider = ri < events.length ? events[ri] : null;
      var inRound = remaining.indexOf(team) !== -1;
      if (!inRound) {
        html += "<p class=dim style='font-size:14px'>" + esc(team) +
          " was already placed; the remaining teams (" +
          remaining.map(esc).join(", ") + ") re-ran the procedure — see the " +
          "full narrative below.</p>";
        break;
      }
      if (rounds.length > 1) {
        html += "<div class=roundhead>Round " + (ri + 1) + " · " +
          remaining.map(esc).join(" · ") + "</div>";
      }
      // lines per letter for this round
      var byLetter = {};
      round.lines.forEach(function (l) {
        var letter = l.charAt(0) === "(" ? l.charAt(1) : null;
        if (!letter) return;
        (byLetter[letter] = byLetter[letter] || []).push(l);
      });
      var deciderLetter = decider ? decider.step : null;
      var decided = false;
      LETTERS.forEach(function (L) {
        var lines = byLetter[L] || [];
        var isDecider = decider && L === deciderLetter;
        var status, chip, cls = "";
        if (decided || (!lines.length && !isDecider)) {
          if (decided) {
            status = "not reached — tie already broken";
            chip = "<span class='lchip skip'>not reached</span>";
            cls = " skip";
            lines = [];
          } else {
            return; // step never evaluated and nothing decided yet: shouldn't happen
          }
        } else if (isDecider) {
          decided = true;
          if (decider.team === team) {
            chip = "<span class='lchip win'>" + esc(team) + " wins here</span>";
          } else {
            chip = "<span class='lchip lose'>lost — " + esc(decider.team) +
              " seeded</span>";
          }
        } else {
          chip = "<span class='lchip none'>no separation</span>";
        }
        html += "<div class='lstep" + cls + "'>" +
          "<span class=lletter>" + L + "</span><div class=lbody>" +
          "<span class=lname>" + esc(STEP_NAMES[L]) + "</span>" + chip +
          lines.map(function (l) {
            return "<span class=evline>" + esc(l) + "</span>";
          }).join("") + "</div></div>";
      });
      if (round.unresolved) {
        html += "<p class=dim style='font-size:14px'>The remaining steps " +
          "need the conference's SportSource rating or a coin toss — " +
          "order among the stuck teams is provisional until then.</p>";
      }
      if (decider) {
        remaining = remaining.filter(function (t) {
          return t !== decider.team;
        });
        if (decider.team === team) {
          if (remaining.length > 1) {
            html += "<p class=dim style='font-size:14px'>With " + esc(team) +
              " placed, the remaining teams (" + remaining.map(esc).join(", ") +
              ") restarted the procedure without them.</p>";
          }
          break;
        }
        if (remaining.length === 1) {
          // selected team is the last one standing
          html += "<p class=dim style='font-size:14px'>That left " +
            esc(team) + " as the only team remaining — they take the last " +
            "spot in the group without another comparison.</p>";
          break;
        }
      }
    }
    html += "</div>";
    html += "<details><summary>Raw engine narrative for this tie group" +
      "</summary><ol class=steps>" +
      (first.log || []).map(function (x) {
        return "<li>" + esc(x) + "</li>";
      }).join("") + "</ol></details>";
    return html;
  }

  (function bindTeamWhy() {
    var sel = document.getElementById("team-sel");
    if (!sel) return;
    Object.keys(teams).sort().forEach(function (t) {
      var o = document.createElement("option");
      o.value = t;
      o.textContent = t;
      sel.appendChild(o);
    });
    sel.onchange = renderTeamWhy;
    renderTeamWhy();
  })();

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

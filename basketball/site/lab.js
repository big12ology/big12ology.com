/* The Lab: pick any of the conference games left and see the race move.
 *
 * Everything here is engine.js, the same file the build seeds the standings
 * with, so a pick in the browser and a result on the server go through one
 * procedure. The season is embedded in the page as JSON (#lab-data) rather
 * than fetched, so the page and its data cannot be a deploy apart.
 *
 * A pick is a result; a game left alone is played by the simulator from the
 * ratings, two thousand times. Once few enough games are left undecided the
 * page also tries every one of their outcomes and says which seeds are still
 * possible at all, the same proof The Race shows in the last week.
 *
 * The picks live in the address (#p=...), one character a game in schedule
 * order: h for the home side, a for the away side, a dot for undecided. A
 * link carries the whole scenario and nothing is stored anywhere.
 */
(function () {
  "use strict";
  var E = window.B12BB;
  var el = document.getElementById("lab-data");
  var root = document.getElementById("lab");
  if (!E || !el || !root) return;
  var D = JSON.parse(el.textContent);
  var teams = D.teams;
  var games = D.games;
  var left = games.filter(function (g) { return !g.final; });
  var pick = {};          // game id -> "h" | "a"
  var N = 2000, EXACT = 12;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function logo(t) {
    return D.logos[t] ? '<img src="' + esc(D.logos[t]) + '" alt="" width="16" height="16" loading="lazy"> ' : "";
  }
  // A check mark is a proof, not a simulation that happened to agree with
  // itself two thousand times: `sure` comes from exact() or from a season
  // with nothing left to decide.
  function pct(p, sure) {
    if (sure) return "<span class=bbsure>✓</span>";
    if (p >= 0.995) return "&gt;99%";
    if (p <= 0) return "<span class=dim>—</span>";
    if (p < 0.005) return "&lt;1%";
    return Math.round(p * 100) + "%";
  }
  function heat(p) {
    return "background:color-mix(in srgb, var(--accent) " + Math.round(p * 70) + "%, transparent)";
  }
  function runs(list) {
    var out = [], run = [];
    list.forEach(function (k) {
      if (run.length && k === run[run.length - 1] + 1) { run.push(k); return; }
      if (run.length) out.push(run);
      run = [k];
    });
    if (run.length) out.push(run);
    return out.map(function (r) {
      return r.length > 1 ? r[0] + "–" + r[r.length - 1] : String(r[0]);
    }).join(", ");
  }

  // ---- the address ----------------------------------------------------------

  function save() {
    var s = left.map(function (g) { return pick[g.id] || "."; }).join("");
    var h = /[ha]/.test(s) ? "#p=" + s.replace(/\.+$/, "") : "";
    if (location.hash !== h) {
      history.replaceState(null, "", location.pathname + location.search + h);
    }
  }
  function load() {
    var m = /#p=([ha.]*)/.exec(location.hash);
    pick = {};
    if (!m) return;
    m[1].split("").forEach(function (c, i) {
      if (left[i] && (c === "h" || c === "a")) pick[left[i].id] = c;
    });
  }

  // ---- the games --------------------------------------------------------------

  function gameRows() {
    var days = [], byDay = {};
    left.forEach(function (g) {
      if (!byDay[g.day]) { byDay[g.day] = []; days.push(g.day); }
      byDay[g.day].push(g);
    });
    return days.map(function (d) {
      return "<h3 class=bbday>" + esc(d) + "</h3><ul class=labgames>" +
        byDay[d].map(function (g) {
          var p = pick[g.id];
          function btn(side, team) {
            var on = p === side;
            return '<button type=button class="labpick' + (on ? " on" : "") +
              '" aria-pressed="' + on + '" data-g="' + g.id + '" data-s="' + side +
              '">' + logo(team) + esc(team) + "</button>";
          }
          var fav = E.winProb(D.model, g.home, g.away, g.neutral);
          return "<li>" + btn("a", g.away) +
            '<span class=labat>' + (g.neutral ? "vs" : "at") + "</span>" +
            btn("h", g.home) +
            '<span class=labfav title="the home side\'s chance, from the ratings">' +
            Math.round(fav * 100) + "% home</span></li>";
        }).join("") + "</ul>";
    }).join("");
  }

  // ---- the race, recomputed -------------------------------------------------------

  function scenario() {
    return games.map(function (g) {
      if (g.final || !pick[g.id]) return g;
      var h = pick[g.id] === "h";
      return { id: g.id, home: g.home, away: g.away, neutral: g.neutral,
        final: true, hp: h ? 1 : 0, ap: h ? 0 : 1 };
    });
  }

  function table(gs) {
    var st = E.standings(gs, teams);
    var sim = E.simulate(gs, teams, D.model, N, 20262027);
    var open = gs.filter(function (g) { return !g.final; }).length;
    var ex = open <= EXACT ? E.exact(gs, teams, EXACT) : null;
    var rows = st.rows.slice().sort(function (x, y) {
      var a = sim.teams[x.team], b = sim.teams[y.team];
      function avg(o) {
        return o.seed.reduce(function (s, p, k) { return s + (k + 1) * p; }, 0);
      }
      return avg(a) - avg(b) || (x.team < y.team ? -1 : 1);
    }).map(function (r) {
      var o = sim.teams[r.team];
      var avg = o.seed.reduce(function (s, p, k) { return s + (k + 1) * p; }, 0);
      var e = ex && ex.teams[r.team];
      var possible = e ? "<td><b>" + runs(e.seeds) + "</b></td>" : "";
      var worst = e ? e.seeds[e.seeds.length - 1] : 99;
      return "<tr><td class=teamcell>" + logo(r.team) + esc(r.team) + "</td>" +
        "<td>" + r.w + "–" + r.l + "</td>" +
        "<td>" + avg.toFixed(1) + "</td>" + possible +
        "<td style='" + heat(o.share) + "'>" + pct(o.share, e && e.share === "yes") + "</td>" +
        "<td style='" + heat(o.top1) + "'>" + pct(o.top1, worst <= 1) + "</td>" +
        "<td style='" + heat(o.top4) + "'>" + pct(o.top4, worst <= 4) + "</td>" +
        "<td style='" + heat(o.top8) + "'>" + pct(o.top8, worst <= 8) + "</td></tr>";
    });
    var head = "<tr><th>Team</th><th>Big 12 so far</th><th>Avg. seed</th>" +
      (ex ? "<th>Possible seeds</th>" : "") +
      "<th>Share of title</th><th>No. 1 seed</th><th>Double bye</th><th>Bye</th></tr>";
    var picked = Object.keys(pick).length;
    var note = (picked ? picked + " game" + (picked === 1 ? "" : "s") + " picked, " : "No games picked, ") +
      open + " left to chance. " +
      (open ? N.toLocaleString() + " simulations of the rest, each seeded with the full Big 12 procedure." : "Every game is decided, so these are the final seeds.") +
      (ex && open ? " With " + open + " undecided, all " + Math.pow(2, open).toLocaleString() +
        " ways they can go were also tried, which is what Possible seeds is." : "");
    return "<div class=tablewrap><table class='stbl bbodds'><thead>" + head +
      "</thead><tbody>" + rows.join("") + "</tbody></table></div><p class=note>" + note + "</p>";
  }

  // ---- drawing ------------------------------------------------------------------------

  var pending = null;
  function draw() {
    root.querySelector("#lab-games").innerHTML = gameRows();
    var out = root.querySelector("#lab-out");
    out.setAttribute("aria-busy", "true");
    if (pending) clearTimeout(pending);
    // A tick's grace, so a run of fast clicks costs one simulation.
    pending = setTimeout(function () {
      out.innerHTML = table(scenario());
      out.setAttribute("aria-busy", "false");
      save();
    }, 30);
  }

  root.addEventListener("click", function (ev) {
    var b = ev.target.closest("button");
    if (!b) return;
    if (b.dataset.g) {
      var id = b.dataset.g, s = b.dataset.s;
      // Clicking the side already picked un-picks it.
      if (pick[id] === s) delete pick[id]; else pick[id] = s;
      draw();
    } else if (b.dataset.act === "reset") {
      pick = {};
      draw();
    } else if (b.dataset.act === "favorites") {
      left.forEach(function (g) {
        pick[g.id] = E.winProb(D.model, g.home, g.away, g.neutral) >= 0.5 ? "h" : "a";
      });
      draw();
    }
  });
  window.addEventListener("hashchange", function () { load(); draw(); });

  if (!left.length) {
    root.querySelector("#lab-games").innerHTML =
      "<p class=dim>Every conference game has been played; nothing is left to pick.</p>";
    root.querySelector("#lab-out").innerHTML = table(games);
    return;
  }
  load();
  draw();
})();

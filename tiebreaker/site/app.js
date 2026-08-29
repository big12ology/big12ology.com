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
  var marks = payload.marks || {};
  var stand = document.getElementById("stand");
  var stories = document.getElementById("stories");
  var matchcard = document.getElementById("matchcard");
  var raceout = document.getElementById("raceout");
  var chip = document.getElementById("w-chip");
  var sortRaw = false;
  var picks = {}; // gameId -> winning team
  var byId = {};
  payload.games.forEach(function (g) { byId[String(g.id)] = g; });
  var models = payload.models || [];
  var model = models.length ? models[0].name : null;

  function favs() {
    return (model && payload.favorites[model]) || {};
  }

  // ------------------------------------------------------------- counted use
  //
  // Whether anybody uses this thing. The Lab is the largest piece of client
  // code on the domain and, until there was somewhere to put this, a pageview
  // was the only evidence it existed — a number that looks exactly the same
  // whether readers rewrite the season or bounce off the standings table.
  //
  // Nothing here identifies anybody and nothing is stored; see metrics.js. The
  // scenario itself never leaves the page: it lives in the hash, and the hash
  // is not in the Referer the beacon carries, which is what makes "was a
  // shared link opened" answerable without anybody learning what was in it.
  var M = window.B12Metrics;
  function count(what, value) { if (M) M.send("whatif", what, value); }

  // Individual picks are tallied rather than sent, because a reader running a
  // whole season clicks sixty times and sixty events is sixty times the
  // traffic for a number that only means anything summed.
  var clicked = 0;
  if (M) {
    M.atEnd(function () {
      if (clicked) count("pick", clicked);
    });
  }

  // ------------------------------------------------------- the shareable URL
  //
  // A what-if belongs in the address bar, not in storage. "Here is how BYU
  // gets in" is the most sendable thing on this site, and a copy kept in
  // localStorage can be neither linked nor shown to anybody. The hash carries
  // it: never sent to the server, never a second URL for the same page.
  //
  // Shape:  #lab=<season>.<fingerprint>.<model>.<packed picks>
  //
  // THE FINGERPRINT IS THE POINT. Picks are packed by POSITION in the
  // season's id-sorted game list, which is compact — two bits a game, 120
  // games in 40 characters — and silently catastrophic if that list ever
  // shifts: CFBD adds a game, every position moves by one, and an old link
  // restores somebody else's scenario while looking perfectly fine. So the
  // list is fingerprinted and the link refuses rather than guesses. A link
  // that says it is out of date beats a board that is quietly wrong.
  var URL_KEY = "lab";
  var B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

  function pickOrder() {
    return pickable().map(function (g) { return String(g.id); }).sort();
  }

  /** FNV-1a over the ordered ids. Short, stable, and not a security claim. */
  function fingerprint(ids) {
    var h = 0x811c9dc5;
    var s = ids.join(",");
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return h.toString(16).slice(0, 6);
  }

  function modelSlug(name) {
    return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  }

  function encodePicks(ids) {
    var out = "", acc = 0, n = 0;
    for (var i = 0; i < ids.length; i++) {
      var g = byId[ids[i]];
      var w = picks[ids[i]];
      var v = !w ? 0 : (w === g.home ? 1 : 2);
      acc = (acc << 2) | v;
      n += 1;
      if (n === 3) { out += B64[acc]; acc = 0; n = 0; }
    }
    if (n) out += B64[acc << (2 * (3 - n))];
    return out;
  }

  function decodePicks(ids, packed) {
    var got = {};
    for (var i = 0; i < ids.length; i++) {
      var ch = packed[Math.floor(i / 3)];
      if (ch === undefined) break;
      var byte = B64.indexOf(ch);
      if (byte < 0) return null;
      var v = (byte >> (2 * (2 - (i % 3)))) & 3;
      if (v === 1 || v === 2) {
        var g = byId[ids[i]];
        if (g) got[ids[i]] = v === 1 ? g.home : g.away;
      }
    }
    return got;
  }

  var urlHold = false;

  // AND A COPY IN STORAGE, which is not a second home for the scenario but a
  // way back to this one. The URL stays the scenario: it is the thing you can
  // send, and everything above about fingerprints is about making a sent link
  // safe. What the URL cannot survive is the site's own navigation — the
  // subnav's "The Lab" points at lab.html with no hash, so a reader who
  // looked at the standings and came back found the board wiped. Back
  // restored it, which is to say the state was never lost, only unreachable
  // by the one route a reader is most likely to take.
  //
  // So the last scenario is kept, and a hash-less arrival offers it. A link
  // still wins outright when there is one — somebody opening a scenario
  // somebody else sent must see that scenario and not their own.
  var STORE_KEY = "lab";

  function syncUrl() {
    if (urlHold || !window.B12State) return;
    var ids = pickOrder();
    if (!Object.keys(picks).length) {
      B12State.hashWrite(URL_KEY, "");
      // Clearing is a decision, so it clears the copy too. Otherwise "Clear
      // picks" would empty the board and the next visit would put it back.
      B12State.set(STORE_KEY, null);
      return;
    }
    var raw = [payload.year, fingerprint(ids),
      modelSlug(model), encodePicks(ids)].join(".");
    B12State.hashWrite(URL_KEY, raw);
    B12State.set(STORE_KEY, raw);
  }

  function restoreFromUrl() {
    return window.B12State
      ? applyScenario(B12State.hashRead(URL_KEY)) : null;
  }

  /** Returns a message when a scenario could not be honoured, else null. */
  function applyScenario(raw) {
    if (!raw) return null;
    var bits = raw.split(".");
    if (bits.length < 4) return "That link is not a scenario this page reads.";
    var ids = pickOrder();
    if (String(payload.year) !== bits[0]) {
      return "That scenario is from the " + esc(bits[0]) + " season.";
    }
    if (fingerprint(ids) !== bits[1]) {
      return "That scenario was made before the schedule changed, so it " +
        "cannot be applied to these games.";
    }
    var got = decodePicks(ids, bits[3] || "");
    if (!got) return "That scenario could not be read.";
    models.forEach(function (m) {
      if (modelSlug(m.name) === bits[2]) model = m.name;
    });
    picks = got;
    return null;
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
    race: raceout ? raceout.innerHTML : "",
  };

  // The race card starts as the build's own — the proofs and the 10,000
  // simulations The Race publishes — and is handed to race.js only once a
  // pick makes the season hypothetical. Restoring the snapshot on clear puts
  // the published numbers back rather than leaving a 2,000-run estimate of
  // the same real season standing next to them.
  var race = window.B12Race || null;
  var raceChip = document.getElementById("w-chip3");
  if (race && raceout) race.mount(raceout, payload);

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/'/g, "&#39;").replace(/"/g, "&quot;");
  }

  // The sixteen carry color and abbreviation; everyone else on the schedule
  // carries only a mark. Both draw the same logo — a game row shows two
  // teams and should not present them two ways.
  function mark(team, size) {
    var src = (teams[team] && teams[team].logo) || marks[team];
    if (!src) return "";
    return "<img class=mark src='" + src + "' alt='' width=" + size +
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

  // In a finished season every game is a lever; in a live one only the
  // games that have not been played yet.
  var unlocked = !!payload.unlocked;

  function pickable() {
    return payload.games.filter(function (g) {
      return !g.ccg && (unlocked || !g.completed);
    });
  }

  // What actually happened, for the games the user is allowed to rewrite.
  // B12Engine.hasScore rather than a home_points test, so this page and the
  // engine it renders cannot disagree about which games happened. The Lab
  // shows the real standings beside the rewritten ones; if the two functions
  // counted different games, the difference would read as the user's picks.
  function actualWinner(g) {
    if (!g || !g.completed || !B12Engine.hasScore(g)) return null;
    if (g.home_points === g.away_points) return null;
    return g.home_points > g.away_points ? g.home : g.away;
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

  // Win-percentage color curve and formatting live in pct.js, so this file
  // and the server render the same standings row.
  var fmtPct = window.B12PCT.fmt;
  var winPctColor = window.B12PCT.color;


  function renderMatch(ccg, nLeft) {
    if (!ccg) {
      return "<h2>What-if projection</h2><p>Not enough simulated results " +
        "to project a matchup yet.</p>";
    }
    var badge = ccg.resolved
      ? "<span class='badge ok'>resolved</span>"
      : "<span class='badge warn'>needs SportSource rating or coin toss</span>";
    var n = Object.keys(picks).length;
    var status = unlocked
      ? "Rewritten season (" + n + (n === 1 ? " game" : " games") + " changed)"
      : nLeft === 0 ? "What-if championship matchup"
      : "What-if projection (" + nLeft + " games still unpicked)";
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

  // Mirror of pad_standings in build.py: every team appears from the first
  // visit, with the unplayed ones alphabetical at the bottom.
  function padRows(rows) {
    var listed = {};
    rows.forEach(function (r) { listed[r.team] = true; });
    var missing = Object.keys(teams).filter(function (t) { return !listed[t]; })
      .sort();
    if (!missing.length) return rows;
    var tally = {};
    missing.forEach(function (t) { tally[t] = { nw: 0, nl: 0, ow: 0, ol: 0 }; });
    payload.games.forEach(function (g) {
      if (!g.completed || g.ccg || !B12Engine.hasScore(g)) return;
      var w = g.home_points > g.away_points ? g.home
        : g.away_points > g.home_points ? g.away : null;
      if (!w) return;
      var l = w === g.home ? g.away : g.home;
      [[w, true], [l, false]].forEach(function (pair) {
        var t = pair[0];
        if (!tally[t]) return;
        tally[t][pair[1] ? "ow" : "ol"] += 1;
        if (!g.conference_game) tally[t][pair[1] ? "nw" : "nl"] += 1;
      });
    });
    return rows.concat(missing.map(function (t) {
      return { rank: null, team: t, conf_w: 0, conf_l: 0,
               nonconf_w: tally[t].nw, nonconf_l: tally[t].nl,
               overall_w: tally[t].ow, overall_l: tally[t].ol,
               tie_group: null, log: null, events: null, resolved: true };
    }));
  }

  /* {team: rank text}, honest about what the ladder proved — build.py's
     display_ranks, ported. The unresolved remainder of a tie has no order
     (the engine hands it back alphabetically, a storage order), so those
     rows share one position ("T1"); teams the ladder seeded before running
     out of data keep their real ranks, and events counts how many it made. */
  function displayRanks(rows) {
    var out = {};
    var i = 0;
    while (i < rows.length) {
      var r = rows[i];
      if (r.resolved || !r.tie_group) {
        out[r.team] = r.rank ? String(r.rank) : "—";
        i += 1;
        continue;
      }
      var grp = rows.filter(function (x) {
        return x.tie_group === r.tie_group;
      });
      var seeded = (r.events || []).length;
      grp.forEach(function (x, j) {
        out[x.team] = j < seeded ? String(x.rank) : "T" + grp[seeded].rank;
      });
      i += grp.length;
    }
    return out;
  }

  function renderRows(allRows) {
    var rows = padRows(allRows);
    var ranks = displayRanks(rows);
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
      return "<tr class='" + cls + "' data-rank=" + (r.rank || 99) +
        " data-w=" + r.conf_w + " data-l=" + r.conf_l + ">" +
        "<td>" + ranks[r.team] + "</td>" +
        "<td class=teamcell><span class=cbar style='background:" +
        color(r.team) + "'></span>" + mark(r.team, 20) + esc(r.team) + mk +
        "</td><td>" + r.conf_w + "–" + r.conf_l + "</td>" +
        "<td" + (p === null ? "" : " style='color:" + winPctColor(p) + "'") +
        ">" + fmtPct(p) + "</td>" +
        "<td class=dimcell>" + r.nonconf_w + "–" + r.nonconf_l + "</td>" +
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
      html += "<details" + (n === 1 ? " open" : "") + "><summary><sup>" + n +
        "</sup> " + tieHeadline(r.tie_group) + "</summary>" +
        "<ol class=steps>" +
        r.log.map(function (x) {
          return x.indexOf("seeded.") >= 0
            ? "<li class=seeded>" + esc(x) + "</li>"
            : "<li>" + esc(x) + "</li>";
        }).join("") +
        "</ol></details>";
    });
    return html || "<p class=dim>No ties in the standings.</p>";
  }

  var tablewrap = document.getElementById("tablewrap");
  var origWrapHidden = tablewrap ? tablewrap.hidden : false;

  // Current-state cache for the team explainer: actual season at load,
  // replaced by the simulated season while what-if picks are active.
  var actualRows = B12Engine.pad(
    B12Engine.standings(payload.games, payload.overrides || {}), payload.games);
  var actualCcg = B12Engine.championship(payload.games, payload.overrides || {});
  var lastRows = actualRows;
  var lastCcg = actualCcg;

  function refresh() {
    syncUrl();
    syncPicks();
    if (!active()) {
      if (matchcard) matchcard.innerHTML = orig.match;
      if (stand) stand.innerHTML = orig.stand;
      if (stories) stories.innerHTML = orig.stories;
      if (race && raceout) { race.cancel(); raceout.innerHTML = orig.race; }
      if (raceChip) raceChip.hidden = true;
      if (chip) chip.hidden = true;
      if (tablewrap) tablewrap.hidden = false;
      lastRows = actualRows;
      lastCcg = actualCcg;
      renderTeamWhy();
      applySort();
      return;
    }
    var games = simGames();
    // Padded, like the server. Without it a non-conference pick changed
    // nothing on the board: standings() ranks only teams with a conference
    // result, so before the first conference game it returned nothing at all
    // and the table the note promises would move sat at 0–0.
    var rows = B12Engine.pad(
      B12Engine.standings(games, payload.overrides || {}), games);
    var ccg = B12Engine.championship(games, payload.overrides || {});
    var nLeft = pickable().length - Object.keys(picks).length;
    if (matchcard) matchcard.innerHTML = renderMatch(ccg, nLeft);
    if (race && raceout) race.update(games);
    if (raceChip) raceChip.hidden = false;
    if (stand) stand.innerHTML = renderRows(rows);
    if (stories) stories.innerHTML = renderStories(rows);
    if (chip) chip.hidden = false;
    // the table always lists all sixteen teams now, so it never hides
    if (tablewrap) tablewrap.hidden = false;
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
      html += "They never won a level — at every step that separated the " +
        "group, another team was seeded ahead of them. Here is each level " +
        "and who took it:</p>";
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
          // The selected team is the last one standing — but not by default.
          // The round that just ran compared them against the team seeded
          // above, and they lost it; that chip is on screen directly above
          // this note. Saying they placed "without another comparison" read
          // as though nobody ever measured them. The procedure stops here
          // because a group of one has nothing left to break, which is a
          // different claim.
          html += "<p class=dim style='font-size:14px'>That step is where " +
            esc(team) + "'s place was settled. With " + esc(decider.team) +
            " seeded they were the only team left, and a group of one has " +
            "nothing left to break.</p>";
          break;
        }
      }
    }
    html += "</div>";
    html += "<details><summary>Raw engine narrative for this tie group" +
      "</summary><ol class=steps>" +
      (first.log || []).map(function (x) {
        return x.indexOf("seeded.") >= 0
          ? "<li class=seeded>" + esc(x) + "</li>"
          : "<li>" + esc(x) + "</li>";
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

  // Every control in this row carries a glyph now, so its words live in a
  // span and its glyph in a <use>. Writing to textContent would take out
  // both: the button would relabel itself once and lose its icon for good.
  // The glyph is optional here — most buttons never change theirs — but a
  // toggle passes one, because a control whose word says "collapse" over an
  // arrow pointing down is worse than no arrow at all.
  function setBtn(btn, text, glyph) {
    if (!btn) return;
    var lab = btn.querySelector(".blab");
    if (lab) lab.textContent = text;
    else btn.textContent = text;
    var use = glyph && btn.querySelector("use");
    if (use) use.setAttribute("href", "#i-" + glyph);
  }

  // The weeks switch describes what it will do, so it has to be re-read
  // after anything that rebuilds the list — a pick preserves whichever weeks
  // were open, and the button would otherwise still offer the throw it made
  // three picks ago.
  function syncWeeksBtn() {
    var btn = document.getElementById("w-weeks");
    var box = document.getElementById("wgames");
    if (!btn || !box) return;
    var all = box.querySelectorAll("details");
    var anyShut = [].some.call(all, function (d) { return !d.open; });
    setBtn(btn, anyShut ? "Expand all weeks" : "Collapse all weeks",
           anyShut ? "chevdown" : "chevup");
  }

  // One line: every model that has an opinion on this game, in the order the
  // selector lists them — the blend first, the four ratings, then the market.
  // The selected model, short enough to sit on a button. The buttons that
  // apply favorites now say WHOSE favorites they are — "favorites" alone was
  // silent about the fact that the model selector changes what it does.
  function modelShort() {
    if (!model) return "favorites";
    return model.replace(/\s*\(\d{4}\)\s*$/, "").replace(/^The\s+/, "");
  }

  function syncFavLabels() {
    setBtn(document.getElementById("w-fav"), "Use " + modelShort() + " for all");
    setBtn(document.getElementById("w-favun"),
           "Use " + modelShort() + " for unpicked");
  }

  // Mirrors build.py's tie_headline. A comma list cannot modify a noun, so
  // the names are apposed after it rather than stacked in front: "How the
  // three-way tie breaks — Arizona State, BYU and Texas Tech".
  var TIE_WORDS = { 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
                    7: "seven", 8: "eight" };

  function tieHeadline(group) {
    var names = String(group || "").split("+").filter(Boolean);
    if (names.length < 2) {
      return names.length ? "How " + esc(names[0]) + " is placed" : "";
    }
    var kind = (TIE_WORDS[names.length] || names.length) + "-way";
    var listed = names.map(esc);
    var joined = listed.length === 2
      ? listed.join(" and ")
      : listed.slice(0, -1).join(", ") + " and " + listed[listed.length - 1];
    return "How the " + kind + " tie breaks \u2014 " + joined;
  }

  function modelStrip(id) {
    var out = [];
    (payload.models || []).forEach(function (m) {
      var f = (payload.favorites[m.name] || {})[id];
      if (!f) return;
      // "by", not a sign. Every number in this row is the same quantity —
      // a predicted margin for the named team — including Vegas's, which is
      // the spread with its sign taken off. Printing "TCU -7" here beside
      // "TCU 12.2" would put two different conventions in one line and make
      // identical quantities look like opposites. A margin has no sign by
      // nature: it is who, and by how much. The minus belongs on the market
      // card, where the number really is a price.
      out.push("<span class=wm><i>" +
        esc(m.name.replace(/^The\s+/, "")) + "</i>" +
        esc(f.team) + " by " + f.margin + "</span>");
    });
    return out.length ? out.join("")
      : "<span class=dim>No model rates this game.</span>";
  }

  function peekStrip(id) {
    var box = document.getElementById("wgames");
    return box && box.querySelector('.wmodels[data-for="' + id + '"]');
  }

  function isPeekOpen(id) {
    var strip = peekStrip(id);
    return !!strip && !strip.hidden;
  }

  /**
   * Show or hide one game's model strip, and return it.
   *
   * Filled on first open rather than up front: 120 games times six opinions
   * is 720 spans nobody has asked to see yet. Both ways in — the ⋮ and the
   * pointer — come through here, so there is one definition of what "open"
   * means and the button's aria-expanded cannot drift from what is on screen.
   *
   * Two shapes, same strip. The ⋮ opens it in the list, as a row: the list
   * getting longer is what that press asked for. A hover opens it floating
   * over the list, because a hover must not move the thing being pointed at
   * — see bindPeekHover.
   */
  function setPeek(id, on, floating) {
    var strip = peekStrip(id);
    if (!strip) return null;
    if (on && !strip.dataset.filled) {
      strip.innerHTML = modelStrip(id);
      strip.dataset.filled = "1";
    }
    strip.classList.toggle("wfloat", !!(on && floating));
    strip.hidden = !on;
    var btn = document.querySelector('.wpeek[data-id="' + id + '"]');
    if (btn) btn.setAttribute("aria-expanded", on ? "true" : "false");
    return strip;
  }

  // A view filter, not a reset: picks already made on non-conference games
  // stay made and keep feeding the simulation, they are simply not listed.
  // Both fill buttons follow it, because filling games a reader has hidden
  // is the sort of surprise that makes a control untrustworthy.
  var confOnly = false;

  function visible() {
    return pickable().filter(function (g) {
      return !confOnly || g.conference_game;
    });
  }

  /* What the list SHOWS, which is not the same as what it lets you change.
     pickable() is the levers: in a live season, the games not yet played.
     A week whose games have all been played has no levers, and used to
     vanish from the list entirely -- so week 0 of 2026, one game in Dublin,
     was simply missing from a page headed "the season". A week that has
     happened is still part of the season and still worth reading; it just
     cannot be rewritten. Same conference filter, no completed filter. */
  function listed() {
    return payload.games.filter(function (g) {
      return !g.ccg && (!confOnly || g.conference_game);
    });
  }

  /* Whether a row can be touched. In a finished season every game is a
     lever, so nothing is frozen; in a live one a played game is a record. */
  function frozen(g) {
    return !unlocked && g.completed;
  }

  /* Points for one side of a played game, or null. A played row that names
     both teams and withholds what they scored is the one row on the page
     that already knows the answer and will not say it. */
  function score(g, side) {
    if (!B12Engine.hasScore(g)) return null;
    var v = side === "home" ? g.home_points : g.away_points;
    return v == null ? null : v;
  }

  /**
   * Fill a set of games with the selected model's favorites.
   *
   * CLEARS FIRST, and that is the whole point. These buttons only ever added
   * picks, so applying a second model left the first one's picks standing
   * wherever the new one had no opinion — and Vegas has no opinion on a game
   * with no posted line. "Use Vegas favorites for all" therefore produced a
   * board that was part Vegas and part whatever it happened to be before,
   * with nothing on screen saying so. A fill is now exactly the model's
   * answer for these games, and running it twice changes nothing.
   *
   * A game the model does not rate is left unpicked rather than guessed at:
   * no opinion is a real answer, and inventing one would put the reader back
   * where this started.
   *
   * `onlyBlank` is the other question, and it is deliberately the only way to
   * keep what is on the board: fill the games nobody has answered yet and
   * step over the ones the reader has. Somebody who has worked down three
   * weeks by hand and wants chalk for the rest of the season should not have
   * to choose between their own picks and the model's.
   */
  function applyFavs(list, onlyBlank) {
    var f = favs();
    list.forEach(function (g) {
      var id = String(g.id);
      if (onlyBlank && picks[id]) return;
      delete picks[id];
      if (f[id]) picks[id] = f[id].team;
    });
    refresh();
  }

  function renderPickList() {
    var box = document.getElementById("wgames");
    if (!box) return;
    var games = listed();
    var byWeek = {};
    var weeks = [];
    games.forEach(function (g) {
      var k = weekLabel(g);
      if (!byWeek[k]) { byWeek[k] = []; weeks.push(k); }
      byWeek[k].push(g);
    });
    // Keep whatever the reader had open. Rebuilding the list used to snap
    // them back to Week 1 on every pick.
    var wasOpen = {};
    box.querySelectorAll("details").forEach(function (d) {
      var s = d.querySelector("summary");
      if (s && d.open) wasOpen[s.textContent.split(" (")[0].trim()] = true;
    });
    var anyOpen = Object.keys(wasOpen).length > 0;
    // The week in front of you, not the first one on the page. Now that
    // played weeks are listed, weeks[0] is whatever the season opened with,
    // which in September is a game from August nobody can change. The first
    // week that still has something to pick is the one to open; a finished
    // season falls back to its first week, because there every game is live.
    var openWeek = null;
    for (var wi = 0; wi < weeks.length; wi++) {
      var some = byWeek[weeks[wi]].some(function (g) { return !frozen(g); });
      if (some) { openWeek = weeks[wi]; break; }
    }
    if (!openWeek && weeks.length) openWeek = weeks[0];
    var html = weeks.map(function (wk) {
      // A week that mixes the two kinds of game read as ragged: the
      // conference rows carry no chip, so their buttons ran on into the
      // space it would have taken and the date landed further right than on
      // the rows above and below. The chip's width is held open — same text,
      // just not painted — so one column edge runs down the whole week.
      // Only where a week actually has a chip to line up with: an all-
      // conference week has no phantom column to keep.
      var anyNc = byWeek[wk].some(function (g) {
        return !g.conference_game;
      });
      var inner = byWeek[wk].map(function (g) {
        var id = String(g.id);
        var fav = favs()[id];
        var date = (g.start || "").slice(5, 10).replace("-", "/");
        var was = actualWinner(g);
        // "at" names a host, and a neutral-site game has none: Arizona State
        // did not travel to Kansas, they both traveled to Wembley. The feed
        // still fills in a home column because it needs one, so the flag is
        // the only thing that knows. Same rule as joiner() in build.py — this
        // list is the one place that was writing the word itself.
        return "<div class='wgame" + (frozen(g) ? " wplayed" : "") + "'>" +
          pickBtn(id, g.away, fav, was, frozen(g), score(g, "away")) +
          "<span class=at>" + (g.neutral_site ? "vs" : "at") + "</span>" +
          pickBtn(id, g.home, fav, was, frozen(g), score(g, "home")) +
          // The tag, the date and the ⋮ in one wrapper so a phone can put
          // all three on a line of their own. It is display:contents on
          // anything wider, so the row it makes there is the same flat row
          // of six items it has always been.
          "<span class=wmeta>" +
          (g.conference_game
            ? (anyNc ? "<span class='nctag ghost'>non-conf</span>" : "")
            : "<span class=nctag>non-conf</span>") +
          "<span class=wdate>" + date + "</span>" +
          // What every model makes of THIS game, one tap away. The model
          // selector above picks which opinion fills the stars; this says
          // what the other five thought, which is the question a reader has
          // at the moment they disagree with the star.
          // No model rates a game that has been played, so the peek had
          // nothing to open but the words "No model rates this game." A
          // control whose only answer is that it has no answer is better
          // not offered.
          (frozen(g)
            ? ""
            : "<button type=button class=wpeek data-id=\"" + id + "\"" +
              " aria-expanded=false title=\"What each model makes of this" +
              " game\">&#8942;</button>") +
          "</span>" +
          "</div><div class=wmodels hidden data-for=\"" + id + "\"></div>";
      }).join("");
      var picked = byWeek[wk].filter(function (g) {
        return picks[String(g.id)];
      }).length;
      var open = anyOpen ? wasOpen[wk] : wk === openWeek;
      // Favorites for THIS week only. "Use favorites for all" fills in
      // thirteen weeks at once, which answers a different question — the
      // reader working down the season a week at a time wants the chalk for
      // the week in front of them and their own opinion after that.
      // A week with no levers left has nothing to count. "0/1 picked" under
      // a week that finished a month ago reads as something undone.
      var live = byWeek[wk].filter(function (g) { return !frozen(g); }).length;
      var tally = live
        ? "(" + picked + "/" + live + (unlocked ? " changed" : " picked") + ")"
        : "(played)";
      return "<details" + (open ? " open" : "") + "><summary>" +
        wk + " <span class=dim>" + tally + "</span>" +
        // And no chalk button on a week there is nothing to apply it to.
        (live
          ? "<button type=button class=wkfav data-wk=\"" + wk +
            "\" title=\"Pick the " + modelShort() + " favorite in every " +
            wk + " game\"><span class=wkstar>&#9733;</span>" +
            esc(modelShort()) + "</button>"
          : "") +
        "</summary>" + inner + "</details>";
    }).join("");
    box.innerHTML = html;
    // The season, not the filtered view: the scenario still contains 120
    // games however many are on screen.
    updateCount(pickable().length);
    syncWeeksBtn();
    box.querySelectorAll(".wpeek").forEach(function (btn) {
      btn.onclick = function () {
        var id = btn.dataset.id;
        // A hover already has it up, floating. The press is asking to keep
        // it, not to put it away — so it comes down into the list and stays
        // there. Without this the ⋮ is unreachable on a mouse: getting to it
        // means hovering the row, which means the strip is already showing,
        // which would make every press a close.
        var up = peekStrip(id);
        var floating = !!up && !up.hidden && up.classList.contains("wfloat");
        var strip = setPeek(id, floating || !isPeekOpen(id), false);
        if (!strip) return;
        if (strip.hidden) {
          // Closed by hand, with the pointer still sitting on the row that
          // opens it on hover — so without this the strip comes straight back
          // and the ⋮ looks broken. Muted until the pointer leaves the row.
          delete strip.dataset.pin;
          strip.dataset.mute = "1";
        } else {
          // Opened by hand: it stays until it is closed by hand. The pointer
          // moving on is not an instruction to put away something somebody
          // deliberately asked for.
          strip.dataset.pin = "1";
          delete strip.dataset.mute;
        }
      };
    });
    box.querySelectorAll(".wkfav").forEach(function (btn) {
      btn.onclick = function (ev) {
        // The button lives inside <summary>, whose default action is to
        // open or close the disclosure. Filling a week in should not also
        // shut it.
        ev.preventDefault();
        ev.stopPropagation();
        applyFavs(byWeek[btn.dataset.wk] || []);
      };
    });
    box.querySelectorAll(".pick").forEach(function (btn) {
      btn.onclick = function () {
        var id = btn.dataset.id, team = btn.dataset.team;
        if (picks[id] === team) delete picks[id];
        else picks[id] = team;
        // A pick that agrees with the real result is not a what-if.
        var g = byId[id];
        if (g && picks[id] === actualWinner(g)) delete picks[id];
        clicked += 1;
        refresh();
      };
    });
  }

  function updateCount(total) {
    var count = document.getElementById("w-count");
    if (!count) return;
    var n = Object.keys(picks).length;
    count.textContent = unlocked
      ? (n === 0 ? "Nothing changed — this is the season as it happened"
                 : n + (n === 1 ? " game" : " games") + " changed")
      : n + " of " + total + " games picked";
  }

  // A pick changes two buttons and two counters, nothing else. Repainting
  // those in place leaves the open week open and the page where it was.
  function syncPicks() {
    var box = document.getElementById("wgames");
    if (!box) return;
    box.querySelectorAll(".pick").forEach(function (btn) {
      // A played row carries no data-id, because nothing about it can move.
      // Reading one off it gave byId[undefined] and actualWinner threw on
      // every click, which took the whole handler down with it: the Lab
      // stopped accepting picks at all.
      if (btn.disabled) return;
      var id = btn.dataset.id, team = btn.dataset.team;
      var sel = picks[id] === team;
      var stands = !picks[id] && actualWinner(byId[id]) === team;
      btn.classList.toggle("sel", sel);
      btn.classList.toggle("stands", stands);
      btn.style.background = sel ? color(team) : "";
      btn.style.borderColor = sel ? color(team) : "";
      btn.style.color = sel ? textOn(color(team)) : "";
    });
    box.querySelectorAll("details").forEach(function (d) {
      var tag = d.querySelector("summary .dim");
      if (!tag) return;
      // A week with nothing to pick keeps the word for that. Recomputing it
      // here would have relabelled "(played)" as "(0/1 picked)" the moment
      // anything else on the page was touched.
      var live = d.querySelectorAll(".wgame:not(.wplayed)").length;
      if (!live) { tag.textContent = "(played)"; return; }
      tag.textContent = "(" + d.querySelectorAll(".pick.sel").length + "/" +
        live + (unlocked ? " changed" : " picked") + ")";
    });
    updateCount(pickable().length);
  }

  function pickBtn(id, team, fav, was, isFrozen, pts) {
    var sel = picks[id] === team;
    // No pick yet on a played game: show the result that stands.
    var stands = !picks[id] && was === team;
    var isFav = fav && fav.team === team;
    // A played game in a live season is a record, so the button says what
    // happened and refuses the press. disabled rather than a click handler
    // that ignores it: the cursor, the keyboard and assistive tech all read
    // the same answer off the attribute, and none of them has to guess.
    if (isFrozen) {
      return "<button class='pick" + (was === team ? " stands" : "") +
        "' disabled title='" + esc(team) +
        (was === team ? " won this game" : " lost this game") +
        ". Played games cannot be changed.'>" + mark(team, 18) +
        "<span class=nm>" + esc(team) + "</span>" +
        (pts == null ? "" : "<span class=wpts>" + pts + "</span>") +
        "</button>";
    }
    var style = sel
      ? " style='background:" + color(team) + ";border-color:" + color(team) +
        ";color:" + textOn(color(team)) + "'"
      : "";
    return "<button class='pick" + (sel ? " sel" : "") +
      (stands ? " stands" : "") + "' data-id='" + id +
      "' data-team='" + esc(team) + "'" + style + ">" + mark(team, 18) +
      "<span class=nm>" + esc(team) + "</span>" +
      (isFav ? "<span class=star title='" + esc(model) + " favorite by ~" +
        fav.margin + "'>★</span>" : "") +
      "</button>";
  }

  function updateNote() {
    var note = document.getElementById("w-note");
    if (!note) return;
    if (unlocked) {
      note.textContent = "This season is finished, so every game is editable. " +
        "The result that actually happened is outlined; click the other team " +
        "to overrule it and the full official procedure re-runs against the " +
        "season you just wrote. Reset to what happened puts it all back.";
      return;
    }
    if (!model) {
      note.textContent = "No rating models available — pick every game by hand.";
      return;
    }
    // How much of the season this model actually has an opinion on. Vegas
    // rates only the games a book has posted a line on, which in August is a
    // handful — so "Use Vegas favorites for all" correctly fills seven games
    // and looks broken unless the page says why first.
    var all = pickable();
    var rated = 0;
    var f = favs();
    all.forEach(function (g) { if (f[String(g.id)]) rated += 1; });
    if (all.length && rated < all.length * 0.75) {
      note.textContent = modelShort() + " has an opinion on " + rated +
        " of the " + all.length + " games left — " +
        (rated === 0
          ? "nothing to fill in yet."
          : "filling in with it picks those and leaves the rest alone.") +
        " Lines are posted a week or two out, so this grows as the season " +
        "goes on. The rating systems cover every game.";
      return;
    }
    // The blend and the market have no single season behind them, so the
    // "(2025 ratings)" parenthesis is only true of the four. Written blind it
    // produced "the The Nerds (null ratings) favorite" — a definite article
    // doubled by a name that already carries one, and a year that does not
    // exist.
    var yr = modelYear(model);
    note.textContent = "★ marks the " + modelShort() +
      (yr ? " (" + yr + " ratings)" : "") +
      " favorite. Hover a game — or tap its ⋮ — for what every model makes " +
      "of it: the projected margin in points, home field included. " +
      "Picks re-run the full official tiebreaker " +
      "instantly — the matchup, standings, and tie narratives update to " +
      "the simulated season. Non-conference games don't move the " +
      "conference standings, but they feed the Non-conf and Overall " +
      "columns and the total-wins tiebreaker step.";
  }

  (function bindControls() {
    var favBtn = document.getElementById("w-fav");
    var gapBtn = document.getElementById("w-favun");
    var clearBtn = document.getElementById("w-clear");
    var sel = document.getElementById("w-model");
    if (favBtn) {
      favBtn.onclick = function () { count("fill"); applyFavs(visible()); };
    }
    // Both count as one event. The vocabulary in worker/src/events.js is a
    // closed list on purpose, and the question these buttons answer together
    // — is the model worth maintaining — does not need them told apart.
    if (gapBtn) {
      gapBtn.onclick = function () { count("fill"); applyFavs(visible(), true); };
    }
    if (clearBtn) {
      clearBtn.onclick = function () { count("clear"); picks = {}; refresh(); };
    }
    // One switch for all thirteen weeks. Only the next week opens on load,
    // which is right for picking one game and wrong for the reader who wants
    // to run the whole season in one pass — and opening twelve summaries by
    // hand to do it is the kind of small tax nobody pays twice. The label
    // says which way the switch will throw, not which state it is in.
    var linkBtn = document.getElementById("w-link");
    if (linkBtn) {
      linkBtn.onclick = function () {
        syncUrl();
        // Counted on the press, not on the copy succeeding. Somebody who
        // reached for the button wanted to send this to a person, and whether
        // the clipboard permission was granted is a different question from
        // whether the feature is wanted.
        count("share");
        var url = location.href;
        var done = function (ok) {
          setBtn(linkBtn, ok ? "Link copied" : "Press \u2318C to copy",
                 ok ? "check" : "share");
          setTimeout(function () { setBtn(linkBtn, "Share link", "share"); },
                     2200);
        };
        // The modern path needs a secure context and a permission that a
        // reader may have refused. Falling back to selecting the URL is the
        // difference between "copy failed" and a button that does nothing.
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(url).then(function () { done(true); },
            function () { done(false); });
        } else {
          done(false);
        }
      };
    }

    var confBtn = document.getElementById("w-conf");
    if (confBtn) {
      confBtn.onclick = function () {
        confOnly = !confOnly;
        setBtn(confBtn, confOnly ? "All games" : "Conference only",
               confOnly ? "filteroff" : "filter");
        confBtn.setAttribute("aria-pressed", confOnly ? "true" : "false");
        confBtn.title = confOnly
          ? "Showing conference games only; non-conference picks you have "
            + "already made still count"
          : "Show only the games that decide the conference race";
        renderPickList();
      };
      confBtn.title = "Show only the games that decide the conference race";
    }

    var weeksBtn = document.getElementById("w-weeks");
    if (weeksBtn) {
      weeksBtn.onclick = function () {
        var box = document.getElementById("wgames");
        if (!box) return;
        var all = box.querySelectorAll("details");
        // Expand unless every week is already open. Any half-open state
        // resolves toward open, which is what somebody reaching for a
        // control called "expand all" is asking for.
        var expand = [].some.call(all, function (d) { return !d.open; });
        [].forEach.call(all, function (d) { d.open = expand; });
        setBtn(weeksBtn, expand ? "Collapse all weeks" : "Expand all weeks",
               expand ? "chevup" : "chevdown");
      };
      // Opening one week by hand counts too. `toggle` does not bubble, so
      // this listens in the capture phase; the container element survives
      // every rebuild of its own innerHTML, so it is bound once.
      var wbox = document.getElementById("wgames");
      if (wbox) wbox.addEventListener("toggle", syncWeeksBtn, true);
    }
    if (sel) {
      sel.onchange = function () {
        model = sel.value;
        // Which model, deliberately not recorded. The question is whether the
        // selector is used at all — it is the argument for maintaining five
        // rating systems — and naming the choice would put a preference on a
        // record that is otherwise about actions only.
        count("model");
        updateNote();
        syncFavLabels();
        renderPickList();
      };
      if (model) sel.value = model;
    }
    updateNote();
    syncFavLabels();
  })();

  // The card's note promises "hover for the projected margin", and what
  // hovering actually got you was the ★'s native tooltip — one game, one
  // model, and only if you found the star. The strip underneath a row says
  // what all six make of it, and asking for it took a click on a ⋮ the width
  // of three pixels. So on a pointer that can hover, the row opens its own
  // strip and takes it back when the pointer leaves.
  //
  // Gated on (hover: hover): on a touch screen a hover is a tap that has not
  // decided what it is yet, and unfolding a panel under the reader's thumb
  // mid-pick is not a feature. The ⋮ stays for them, and for the keyboard,
  // which cannot hover either.
  //
  // Delayed on the way in, and that delay is the whole difference between
  // this and a flicker: a pointer travelling from week three to the button
  // row crosses eight games and is asking about none of them. The delay on
  // the way out only covers leaving the list; moving to another game takes
  // the old strip down at once, because it is sitting over that game.
  //
  // Floating, not folded into the list. As a row it pushed every game below
  // it down by its own height, so a pointer moving down to the next game
  // arrived, waited out the open delay, and then had the list yanked up from
  // under it as the first strip closed — landing the pointer on a third game
  // it never aimed at, which opened, which moved the list again. Strips are
  // not all the same height, so where the pointer ended up was not even
  // predictable. A layer changes nothing about where anything is.
  (function bindPeekHover() {
    var box = document.getElementById("wgames");
    if (!box || !window.matchMedia) return;
    if (!matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    var OPEN_MS = 160, SHUT_MS = 120;
    var openTimer = null, shutTimer = null, shown = null, over = null;

    // The mute a click leaves behind lasts exactly as long as the pointer
    // stays on the row it was set for.
    function unmute(id) {
      var strip = id && peekStrip(id);
      if (strip) delete strip.dataset.mute;
    }

    function hideShown() {
      shutTimer = null;
      if (!shown) return;
      var strip = peekStrip(shown);
      // Pinned by a click, or gone in a rebuild: either way, not ours to shut.
      if (strip && !strip.dataset.pin) setPeek(shown, false);
      shown = null;
    }

    function scheduleShut() {
      if (openTimer) { clearTimeout(openTimer); openTimer = null; }
      if (!shown || shutTimer) return;
      shutTimer = setTimeout(hideShown, SHUT_MS);
    }

    box.addEventListener("mouseover", function (ev) {
      // A strip counts as part of the row it belongs to. A floating one is
      // pointer-events:none and can never be the target; a pinned one is a
      // row in the list, and reading it must not close it.
      var el = ev.target.closest && ev.target.closest(".wgame, .wmodels");
      var id = null;
      if (el && el.classList.contains("wmodels")) {
        id = el.getAttribute("data-for");
      } else if (el) {
        var pk = el.querySelector(".wpeek");
        id = pk && pk.getAttribute("data-id");
      }
      if (over !== id) { unmute(over); over = id; }
      if (!id) { scheduleShut(); return; }
      if (shutTimer) { clearTimeout(shutTimer); shutTimer = null; }
      if (id === shown) return;
      if (openTimer) clearTimeout(openTimer);
      // One at a time, and down before the next one is even considered: the
      // layer covers the row below the game it belongs to, which is the game
      // the pointer has just arrived on. Leaving it up for the open delay
      // would hide the row being asked about.
      hideShown();
      openTimer = setTimeout(function () {
        openTimer = null;
        var strip = peekStrip(id);
        if (!strip || strip.dataset.mute) return;
        if (isPeekOpen(id)) return;
        setPeek(id, true, true);
        shown = id;
      }, OPEN_MS);
    });
    box.addEventListener("mouseleave", function () {
      unmute(over);
      over = null;
      scheduleShut();
    });
  })();

  urlHold = true;
  var arrivedWithScenario = !!(window.B12State && B12State.hashRead(URL_KEY));
  var urlProblem = restoreFromUrl();
  // Only with no link to honour. A stored scenario that no longer fits the
  // schedule is dropped rather than explained: the paragraph below exists
  // for a link somebody was sent and is owed an answer about, whereas this
  // is the reader's own board going quietly out of date, and telling them
  // their invisible saved copy expired is a notice about nothing they did.
  var resumed = false;
  if (!arrivedWithScenario && window.B12State) {
    var kept = B12State.get(STORE_KEY, null);
    if (kept) {
      if (applyScenario(kept)) B12State.set(STORE_KEY, null);
      else resumed = true;
    }
  }
  urlHold = false;
  // Put it back on the URL, so Share link works on a resumed board without
  // touching anything first, and so the address bar and the page agree.
  if (resumed) syncUrl();
  // The one measurement that justifies the sharing feature, and the one that
  // says when it is broken. `stale` means a link arrived and the page refused
  // it — a schedule that moved under a scenario somebody sent last week. That
  // failure is invisible from here otherwise: the sender sees a working link
  // and the recipient sees a polite paragraph, and nobody reports it.
  if (arrivedWithScenario && M) {
    M.send("scenario", urlProblem ? "stale" : "opened");
  } else if (resumed && M) {
    // Its own value, not folded into "opened". Those two count different
    // things — one says the sharing feature is used, the other says people
    // leave this page and come back to it — and a single number that could
    // mean either answers neither.
    M.send("scenario", "resumed");
  }
  notice(urlProblem
    ? urlProblem + " Showing the season as it stands."
    : (resumed ? "Picked up where you left off. Clear picks starts over."
       : ""));

  /** The one line above the game list. Replaces rather than appends, because
      the hash can change more than once without the page reloading. */
  function notice(text) {
    var old = document.querySelector(".wurlwarn");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    if (!text) return;
    var p = document.createElement("p");
    p.className = "note wurlwarn";
    p.textContent = text;
    var host = document.getElementById("wgames");
    if (host && host.parentNode) host.parentNode.insertBefore(p, host);
  }

  // A SCENARIO PASTED INTO A LAB THAT IS ALREADY OPEN. Changing the fragment
  // does not reload the document, so nothing read it and the board went on
  // showing whatever it already had — the reader's own picks under somebody
  // else's URL, which is worse than ignoring the paste, because the address
  // bar now describes a board that is not on the screen.
  //
  // Our own writes cannot land here: syncUrl goes through replaceState, and
  // replaceState does not fire this event. Anything arriving is the reader —
  // a paste, or Back onto a hash they typed themselves.
  window.addEventListener("hashchange", function () {
    if (!window.B12State) return;
    var raw = B12State.hashRead(URL_KEY);
    // A scenario that cannot be read must not take the stored board down
    // with it. On arrival a bad link leaves storage alone; pasting one is
    // the same event later, and someone else's stale URL is no reason to
    // lose the season you built.
    var keep = B12State.get(STORE_KEY, null);
    urlHold = true;
    picks = {};
    var problem = raw ? applyScenario(raw) : null;
    if (problem) picks = {};
    urlHold = false;
    var msel = document.getElementById("w-model");
    if (msel) msel.value = model;
    renderPickList();
    syncFavLabels();
    updateNote();
    refresh();
    if (problem && keep) B12State.set(STORE_KEY, keep);
    notice(problem ? problem + " Showing the season as it stands." : "");
    // Its own pair of values. "stale" already counts links that failed ON
    // ARRIVAL, which is the measurement that says the sharing feature is
    // broken; a paste failing later is a different event and folding them
    // together would make the first number impossible to read.
    if (M) M.send("scenario", problem ? "pasted-stale" : "pasted");
  });

  renderPickList();
  if (Object.keys(picks).length) {
    // A link that arrived with picks on it has to run them through the
    // engine, not just draw the stars.
    var msel = document.getElementById("w-model");
    if (msel) msel.value = model;
    syncFavLabels();
    updateNote();
    refresh();
  }
})();

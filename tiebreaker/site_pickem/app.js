/* The pick'em client.
 *
 * One classic script, no imports, no bundler. That is deliberate: the HTML
 * carries ?v=<sha1 of this file>, so with no sub-imports that hash is the
 * complete truth about which code is running. The attendance app threads a
 * hand-bumped ?v= through its module specifiers, which assemble.sh's
 * cache-bust gate cannot see and a person has to remember.
 *
 * Everything dynamic arrives from /api/*. Nothing here is generated at build
 * time, because a slate, a countdown and a leaderboard are all wrong the
 * moment they are written down.
 */
"use strict";
(function () {

  // ---------------------------------------------------------------- fetch

  var LOCKED = false;      // set once the server says so; never unset

  function api(path, opts) {
    opts = opts || {};
    var init = {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers: {"Accept": "application/json"}
    };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        try { data = t ? JSON.parse(t) : null; } catch (e) { data = null; }
        if (!r.ok) {
          var err = new Error((data && data.error) || ("http_" + r.status));
          err.status = r.status;
          err.data = data;
          throw err;
        }
        return data;
      });
    });
  }

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function show(node, on) { if (node) node.hidden = !on; }

  // ------------------------------------------------------------ messages

  function status(msg) { var n = $("savestate"); if (n) n.textContent = msg || ""; }
  function alertMsg(msg) { var n = $("alertstate"); if (n) n.textContent = msg || ""; }

  // The one place an API failure becomes words. Everything that can fail
  // routes here so the page never just sits on "Loading…".
  function explain(err) {
    if (!err) return "Something went wrong.";
    if (err.status === 401) return "You are signed out.";
    if (err.status === 403 && err.data && err.data.error === "no_display_name")
      return "Choose a display name before picking.";
    if (err.status === 409) return "This week has locked.";
    if (err.status === 429) return "Too many changes too quickly. Wait a moment.";
    if (err.status === 503) return "The pick'em is temporarily unavailable.";
    if (err.status) return "The server said no (" + err.status + ").";
    return "Could not reach the pick'em. It may not be running yet.";
  }

  // ------------------------------------------------------------- numbers

  // The stored value is the home spread doubled — see worker/src/ats.js. The
  // page is the only place it becomes a human number, and U+2212 MINUS is the
  // typographic one, not a hyphen.
  function spreadText(spreadX2, side) {
    var v = (side === "home" ? spreadX2 : -spreadX2) / 2;
    if (v === 0) return "PK";
    return (v < 0 ? "−" : "+") + Math.abs(v);
  }

  // What a screen reader hears instead of "minus six point five", which is
  // not what the number means to anybody.
  function spreadSaid(spreadX2, side) {
    var v = (side === "home" ? spreadX2 : -spreadX2) / 2;
    if (v === 0) return "pick em, no points";
    var n = Math.abs(v), half = (n % 1) ? " and a half" : "";
    return (v < 0 ? "giving " : "getting ") + Math.floor(n) + half + " points";
  }

  // Readable text on a team's own colour. Selected picks fill with the team
  // colour rather than the brand teal (teal is chrome only), which means the
  // foreground has to be computed rather than chosen.
  function textOn(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return "#fff";
    var n = parseInt(m[1], 16);
    var c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    var L = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    // 0.179 is where white and black draw level against a background: below
    // it white wins, above it black does. The first version used 0.45, which
    // is intuitive and wrong — it put white on UCF's gold (#ba9b37) at about
    // 2.5:1, and would have done the same to every light team colour in the
    // conference. Against #16181A rather than pure black, since that is the
    // ink this site actually uses.
    return L > 0.179 ? "#16181A" : "#ffffff";
  }

  function fmtWhen(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    // Viewer-local, and the zone is always named: a deadline without one reads
    // as local and is not. No year — the season is on screen (README rule 4).
    return d.toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short"
    });
  }

  // ------------------------------------------------------------ countdown

  // Milestones, not seconds. A live region firing once a second is unusable;
  // these are the seven moments a person actually wants told.
  var MILESTONES = [86400, 21600, 3600, 900, 300, 60, 0];
  var said = {};

  function startCountdown(lockAt) {
    var cd = $("cd"), sr = $("cdsr");
    if (!cd || !lockAt) return;
    function tick() {
      if (document.hidden) return;
      var left = Math.floor(lockAt - Date.now() / 1000);
      if (left <= 0) {
        cd.textContent = "locked";
        if (!said[0]) { said[0] = 1; if (sr) sr.textContent = "The slate is locked."; }
        return;
      }
      var d = Math.floor(left / 86400), h = Math.floor(left % 86400 / 3600),
          m = Math.floor(left % 3600 / 60), s = left % 60;
      cd.textContent = (d ? d + "d " : "") +
        String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") +
        ":" + String(s).padStart(2, "0");
      for (var i = 0; i < MILESTONES.length; i++) {
        var t = MILESTONES[i];
        if (left <= t && !said[t]) {
          said[t] = 1;
          if (sr && t > 0) {
            sr.textContent = t >= 86400 ? "One day until the slate locks."
              : t >= 3600 ? (t / 3600) + " hours until the slate locks."
              : (t / 60) + " minutes until the slate locks.";
          }
          break;
        }
      }
    }
    tick();
    setInterval(tick, 1000);
    document.addEventListener("visibilitychange", tick);
  }

  // -------------------------------------------------------------- the slate

  var slate = null, picks = {}, saveTimer = null, inflight = false;

  function gameRow(g, teams) {
    var fs = el("fieldset", "slate-game");
    fs.dataset.gid = g.game_id;
    var why = g.unpickable;
    if (why) fs.disabled = true;

    var lg = el("legend");
    lg.appendChild(document.createTextNode(g.away + " at " + g.home + " · "));
    var t = el("time", null, fmtWhen(g.kickoff));
    t.setAttribute("datetime", g.kickoff);
    lg.appendChild(t);
    var note = el("span", "sr-only");
    note.textContent = why === "no_line"
      ? " — no spread available, this game cannot be picked"
      : why === "kickoff_tbd"
        ? " — kickoff time not announced, this game cannot be picked"
        : " — " + (g.spread_x2 < 0 ? g.home : g.away) + " favoured by " +
          Math.abs(g.spread_x2 / 2);
    lg.appendChild(note);
    fs.appendChild(lg);

    var sides = el("div", "sides");
    ["away", "home"].forEach(function (side, i) {
      if (i === 1) sides.appendChild(el("span", "at", "at"));
      var team = g[side];
      var id = "g" + g.game_id + "-" + side;
      var input = document.createElement("input");
      input.type = "radio";
      input.className = "sr-only";
      input.id = id;
      input.name = "g" + g.game_id;
      input.value = side;
      if (picks[g.game_id] === side) input.checked = true;

      var lab = el("label", "side");
      lab.setAttribute("for", id);
      var colour = (teams[team] && teams[team].color) || "";
      if (colour) {
        lab.style.setProperty("--tc", colour);
        lab.style.setProperty("--tfg", textOn(colour));
      }
      lab.appendChild(el("span", "tname", team));
      if (!why) {
        lab.appendChild(el("span", "num", spreadText(g.spread_x2, side)));
        lab.appendChild(el("span", "sr-only", " " + spreadSaid(g.spread_x2, side)));
      }
      if (g.result) {
        var out = g.result.ats === "void" ? "void"
          : g.result.ats === "push" ? "push"
          : g.result.ats === side ? "win" : "loss";
        if (picks[g.game_id] === side) {
          var chip = el("span", "res " + out, out.toUpperCase());
          chip.appendChild(el("span", "sr-only", " your pick " + out));
          lab.appendChild(chip);
        }
      }
      sides.appendChild(input);
      sides.appendChild(lab);
    });
    fs.appendChild(sides);

    if (why) {
      var tag = el("p", "tag out nopick",
        why === "no_line" ? "No Spread Available" : "Kickoff Not Announced");
      fs.appendChild(tag);
    }
    return fs;
  }

  function renderSlate(teams) {
    var wrap = $("slate");
    wrap.textContent = "";
    if (!slate.games.length) {
      wrap.appendChild(el("p", "note", "No games this week."));
      return;
    }
    slate.games.forEach(function (g) { wrap.appendChild(gameRow(g, teams)); });

    var pickable = slate.games.filter(function (g) { return !g.unpickable; }).length;
    var n = slate.games.length;
    // Count honestly: "14 games, 12 with a line" is the true shape of the
    // week, and hiding the two without one would make the slate look wrong.
    $("slatecount").textContent = n + (n === 1 ? " game" : " games") +
      " · " + pickable + " with a line" +
      (pickable < n ? " · " + (n - pickable) + " without" : "");

    if (LOCKED) lockDown();
  }

  function lockDown() {
    LOCKED = true;
    var form = $("slateform");
    if (form) {
      [].forEach.call(form.querySelectorAll("input[type=radio]"), function (i) {
        i.disabled = true;
      });
    }
    var cd = $("cd");
    if (cd) cd.textContent = "locked";
  }

  function countPicks() {
    var n = 0;
    for (var k in picks) if (picks[k]) n++;
    return n;
  }

  function scheduleSave() {
    if (LOCKED) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 700);
    // Only say "Saving" if it is actually slow. Otherwise the region chatters
    // on every click for no information.
    setTimeout(function () { if (inflight) status("Saving…"); }, 400);
  }

  function save() {
    if (LOCKED || !slate) return;
    inflight = true;
    api("/api/picks", {
      method: "PUT",
      body: {season: slate.season, week: slate.week, picks: picks}
    }).then(function () {
      inflight = false;
      var n = countPicks(), total = slate.games.filter(function (g) {
        return !g.unpickable;
      }).length;
      // The count, not the game. Someone tabbing down the slate wants
      // progress, not a recital of every row they touched.
      status("Saved — " + n + " of " + total + " picks in.");
    }).catch(function (err) {
      inflight = false;
      status("");
      if (err.status === 409) {
        // The server is authoritative and the optimistic state is now a lie.
        // Repaint from what it actually recorded at the lock.
        if (err.data && err.data.picks) picks = err.data.picks;
        lockDown();
        alertMsg("The slate locked before that pick saved. Your card as it " +
                 "stood at kickoff is shown below.");
        loadSlate();
      } else if (err.status === 401) {
        alertMsg("You have been signed out. Your picks were not saved.");
      } else {
        alertMsg(explain(err));
      }
    });
  }

  function loadSlate() {
    return Promise.all([
      api("/api/slate"),
      api("/api/picks").catch(function (e) {
        if (e.status === 401) return {picks: {}};   // signed out: read-only
        throw e;
      }),
      fetch("/pickem/teams.json").then(function (r) {
        return r.ok ? r.json() : {};
      }).catch(function () { return {}; })
    ]).then(function (r) {
      slate = r[0];
      picks = (r[1] && r[1].picks) || {};
      LOCKED = !!slate.locked;
      show($("lockcard"), true);
      if (slate.lock_at) {
        var lt = $("lockat");
        lt.setAttribute("datetime", new Date(slate.lock_at * 1000).toISOString());
        lt.textContent = fmtWhen(slate.lock_at * 1000);
        startCountdown(slate.lock_at);
      }
      renderSlate(r[2] || {});
    }).catch(function (err) {
      var n = $("slateload");
      if (n) n.textContent = explain(err);
    });
  }

  function initSlate() {
    var form = $("slateform");
    if (!form) return;
    form.addEventListener("change", function (e) {
      var t = e.target;
      if (!t || t.type !== "radio") return;
      picks[t.name.slice(1)] = t.value;
      alertMsg("");
      scheduleSave();
    });
    // No submit button by design: the deadline is a wall clock, not a user
    // action, so a slate that needed submitting would silently discard the
    // picks of anyone who filled it in and walked away.
    form.addEventListener("submit", function (e) { e.preventDefault(); });
    loadSlate();
  }

  // --------------------------------------------------------------- account

  function acctChip(me) {
    var chip = document.querySelector(".b12-acct");
    if (!chip) return;
    chip.textContent = "";
    if (me && me.display_name) {
      var a = el("a", null, me.display_name);
      a.href = "/pickem/account.html";
      chip.appendChild(document.createTextNode(""));
      chip.appendChild(a);
    } else {
      var s = el("a", null, me ? "Choose a name" : "Sign in");
      s.href = "/pickem/account.html";
      chip.appendChild(s);
    }
    chip.hidden = false;
  }

  function initAccount(me) {
    var form = $("nameform");
    if (!form) return;
    show($("signin"), !me);
    show($("named"), !!me);
    show($("acctinfo"), !!me);

    if (me) {
      if (me.display_name) $("dname").value = me.display_name;
      var body = $("acctbody");
      body.textContent = "";
      var p = el("p", "note");
      p.textContent = "Signed in with " +
        (me.identities || []).map(function (i) { return i.provider; }).join(" and ") +
        ".";
      body.appendChild(p);
      var out = document.createElement("form");
      out.addEventListener("submit", function (e) {
        e.preventDefault();
        api("/api/auth/logout", {method: "POST"}).then(function () {
          location.href = "/pickem/";
        });
      });
      var btn = el("button", "wbtn", "Sign out");
      btn.type = "submit";
      out.appendChild(btn);
      body.appendChild(out);
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var input = $("dname"), err = $("dnameerr");
      var name = input.value.trim();
      function fail(msg) {
        input.setAttribute("aria-invalid", "true");
        err.textContent = msg;
        err.hidden = false;
        input.focus();          // put them back where the fix is
      }
      err.hidden = true;
      input.removeAttribute("aria-invalid");
      if (name.length < 2) return fail("At least 2 characters.");
      api("/api/me", {method: "PATCH", body: {display_name: name}})
        .then(function (r) {
          status("Name saved.");
          acctChip({display_name: r.display_name});
        })
        .catch(function (e2) {
          // Server and client errors look identical to the reader on purpose.
          var m = e2.data && e2.data.error;
          fail(m === "name_taken" ? "That name is taken."
             : m === "name_reserved" ? "That name is reserved."
             : m === "name_too_short" ? "At least 2 characters."
             : m === "name_too_long" ? "At most 20 characters."
             : m === "name_charset" ? "Letters, numbers, spaces and . _ - only."
             : m === "rename_cooldown" ? "You can change your name once a month."
             : explain(e2));
        });
    });
  }

  // ----------------------------------------------------------------- board

  var boardRows = [], sortKey = "rank", sortDir = 1, meId = null;

  var COLS = [
    {key: "rank", label: "#", num: true},
    {key: "display_name", label: "Player", num: false},
    {key: "w", label: "W", num: true},
    {key: "l", label: "L", num: true},
    {key: "p", label: "P", num: true},
    {key: "pct", label: "ATS%", num: true}
  ];

  function drawBoard() {
    var tbl = $("board");
    if (!tbl) return;
    tbl.textContent = "";
    var thead = el("thead"), tr = el("tr");
    COLS.forEach(function (c) {
      var th = el("th", c.num ? "n" : null);
      if (sortKey === c.key) {
        th.setAttribute("aria-sort", sortDir > 0 ? "ascending" : "descending");
      }
      var b = el("button", null, c.label);
      b.type = "button";
      b.addEventListener("click", function () {
        if (sortKey === c.key) sortDir = -sortDir;
        else { sortKey = c.key; sortDir = c.key === "rank" ? 1 : -1; }
        drawBoard();
      });
      th.appendChild(b);
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    tbl.appendChild(thead);

    var rows = boardRows.slice().sort(function (a, b) {
      var x = a[sortKey], y = b[sortKey];
      if (x == null) x = -Infinity;
      if (y == null) y = -Infinity;
      return x < y ? -sortDir : x > y ? sortDir : 0;
    });

    var tb = el("tbody");
    rows.forEach(function (r) {
      var tr2 = el("tr");
      if (meId && r.user_id === meId) tr2.className = "you";
      COLS.forEach(function (c) {
        var td = el("td", c.num ? "n" : null);
        if (c.key === "pct") {
          td.textContent = r.pct == null ? "—" :
            (r.pct * 100).toFixed(1) + "%";
          // Continuous, so it gets the gradient the flat W/L chips do not.
          // B12PCT.ats diverges around .500 because real ATS records live
          // between .400 and .620, which the win-pct curve crushes flat.
          if (r.pct != null && window.B12PCT && window.B12PCT.ats) {
            td.style.color = window.B12PCT.ats(r.pct);
          }
        } else {
          td.textContent = r[c.key] == null ? "—" : r[c.key];
        }
        tr2.appendChild(td);
      });
      tb.appendChild(tr2);
    });
    tbl.appendChild(tb);
  }

  function fillWeeks(cur) {
    var sel = $("wksel");
    if (!sel) return;
    // Weeks played so far, plus a season-to-date option. Hidden entirely
    // until there is more than one thing to choose: an empty <select> is a
    // control that looks broken, and one option is a control that lies about
    // being a choice.
    var opts = [{v: "", t: "Season"}];
    for (var w = 0; w <= (cur == null ? -1 : cur); w++) {
      opts.push({v: String(w), t: "Week " + w});
    }
    if (opts.length < 2) {
      var lab = sel.closest("label");
      if (lab) lab.hidden = true;
      return;
    }
    sel.textContent = "";
    opts.forEach(function (o) {
      var n = document.createElement("option");
      n.value = o.v; n.textContent = o.t;
      if (o.v === String(cur)) n.selected = true;
      sel.appendChild(n);
    });
    sel.addEventListener("change", function () { loadBoard(sel.value); });
  }

  function loadBoard(week) {
    var note = $("boardnote");
    var q = week ? "?week=" + encodeURIComponent(week) : "";
    return api("/api/leaderboard" + q).then(function (r) {
      boardRows = r.rows || [];
      note.textContent = boardRows.length
        ? (r.week == null ? "Season to date." : "Week " + r.week + ".")
        : "Nobody has a scored week yet.";
      drawBoard();
      return r;
    }).catch(function (err) { note.textContent = explain(err); });
  }

  function initBoard(me) {
    var note = $("boardnote");
    if (!note) return;
    meId = me && me.user_id;
    loadBoard("").then(function (r) { if (r) fillWeeks(r.week); });
  }

  // ------------------------------------------------------------------ card

  function initCard(me) {
    var note = $("cardnote");
    if (!note) return;
    if (!me) { note.textContent = "Sign in to see your card."; return; }
    api("/api/picks").then(function (r) {
      var n = Object.keys(r.picks || {}).length;
      note.textContent = n
        ? n + " picks in for week " + r.week + "."
        : "No picks yet this week.";
    }).catch(function (err) { note.textContent = explain(err); });
  }

  // ------------------------------------------------------------------ boot

  document.addEventListener("DOMContentLoaded", function () {
    // One /api/me for the whole page: the chip needs it, and every section
    // below branches on it.
    api("/api/me").catch(function () { return null; }).then(function (me) {
      acctChip(me);
      show($("signedout"), !me && !!$("slateform"));
      initSlate();
      initAccount(me);
      initBoard(me);
      initCard(me);
    });
  });

})();

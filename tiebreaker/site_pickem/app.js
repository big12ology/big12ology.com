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
  // Whether anybody is signed in. The slate is readable either way — that is
  // deliberate, the lines are the interesting part — but picking is not, and
  // a radio that fills in and then reports a server error is worse than one
  // that was never armed.
  var SIGNED_IN = false;

  // Below this many cards on a game, the split is not shown at all. Three
  // people picking is not a consensus, it is three people — and rendered as a
  // 67/33 bar it would read with exactly the same authority as a real one.
  // The threshold is the client's, so a small pool simply sees nothing rather
  // than something misleading; the server is free to send whatever it has.
  var MIN_CONSENSUS = 10;

  // Two answers that are not a team. Stored as-is so the difference between
  // "no answer yet" and "deliberately no team" survives — one is a question
  // we have not asked, the other is an answer.
  var TEAM_B12 = "__big12", TEAM_CFB = "__cfb";

  var teamsP = null;
  function loadTeams() {
    if (!teamsP) {
      teamsP = fetch("teams.json")
        .then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; });
    }
    return teamsP;
  }

  // The colour to tint a viewer's own rows with. A real team gets its own; the
  // two generic answers get the brand accent, which is chrome and allowed on a
  // row that is chrome rather than data.
  function myColour(me, teams) {
    if (!me || !me.team) return null;
    if (me.team === TEAM_B12 || me.team === TEAM_CFB) return "var(--accent)";
    return (teams[me.team] && teams[me.team].color) || null;
  }

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
    if (err.status === 401) {
      return "You are signed out — sign in and your picks will save.";
    }
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
  // The same shape the rest of the site uses — tiebreaker/site/app.js:mark().
  // A team with no freely-licensed mark gets nothing rather than a stand-in;
  // logos/SOURCES.json is the registry and it says which those are.
  function mark(teams, team, size) {
    var src = teams[team] && teams[team].logo;
    if (!src) return null;
    var img = document.createElement("img");
    img.className = "mark";
    img.src = src;
    img.alt = "";
    img.width = size; img.height = size;
    img.loading = "lazy";
    return img;
  }

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

  // The row version: day and clock only, no month, no zone. The whole slate is
  // one weekend, so the month repeats fifteen times and says nothing, and the
  // zone belongs on the lock — which is the only time on this page anyone has
  // to act on. The full string is still in the legend and in <time datetime>.
  function shortWhen(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var s = d.toLocaleString(undefined, {
      weekday: "short", hour: "numeric", minute: "2-digit"
    });
    // "Sat 12:30 PM" -> "Sat 12:30p". Forty pixels a row, and every one of
    // them comes off a team name. Locales that already use a 24-hour clock
    // match neither pattern and are left alone, being short already.
    return s.replace(/\s*AM$/i, "a").replace(/\s*PM$/i, "p");
  }

  // A game is only "at 12:30" until 12:30. After that the kickoff time is the
  // least useful thing the row could say, so the first column carries the
  // game's own state instead: playing, waiting for a result, or done. What
  // YOUR pick did stays in the chip at the other end — one column for the
  // game, one for you.
  //
  // Three hours is the window. A college game runs about three and a half
  // with stoppages, so past kick+3h "in play" stops being a claim we can
  // stand behind and the honest answer is that we are waiting for the score.
  var LIVE_WINDOW = 3 * 3600;

  function gameStatus(g) {
    if (g.result && g.result.home_points != null) {
      return {kind: "final", text: "FINAL"};
    }
    var k = g.kickoff_at, now = Date.now() / 1000;
    if (!k || now < k) return {kind: "time", text: shortWhen(g.kickoff)};
    if (now < k + LIVE_WINDOW) return {kind: "live", text: "IN PLAY"};
    return {kind: "wait", text: "WAITING"};
  }

  // ------------------------------------------------------------ countdown

  // Milestones, not seconds. A live region firing once a second is unusable;
  // these are the seven moments a person actually wants told.
  var MILESTONES = [86400, 21600, 3600, 900, 300, 60, 0];
  var said = {};

  function startCountdown(lockAt) {
    var cd = $("cd"), sr = $("cdsr");
    if (!cd || !lockAt) return;
    function tick(force) {
      // Skipping work in a background tab is the point of the guard, but the
      // FIRST paint has to happen either way: a page opened in a background
      // tab — cmd-click, "open in new tab", a restored session — is hidden
      // when this runs, and the deadline stayed blank until it was focused.
      if (document.hidden && force !== true) return;
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
    tick(true);
    setInterval(tick, 1000);
    document.addEventListener("visibilitychange", function () { tick(true); });
  }

  // -------------------------------------------------------------- the slate

  var slate = null, picks = {}, saveTimer = null, inflight = false;

  function gameRow(g, teams) {
    var fs = el("fieldset", "pk-slate-game");
    fs.dataset.gid = g.game_id;
    var why = g.unpickable;
    if (why) fs.disabled = true;

    // The legend is the group's accessible name and carries the whole story:
    // matchup, kickoff, the line, and why the game is closed if it is. It is
    // visually hidden because repeating the team names above a row that
    // already shows them cost a line per game — fifteen lines of a slate that
    // has to fit on one screen. The time is re-emitted below as a visible,
    // aria-hidden column so it is said once and shown once.
    var lg = el("legend", "sr-only");
    lg.appendChild(document.createTextNode(
      g.away + " at " + g.home + ", " + fmtWhen(g.kickoff)));
    lg.appendChild(document.createTextNode(
      why === "no_line"
        ? " — no spread available, this game cannot be picked"
        : why === "kickoff_tbd"
          ? " — kickoff time not announced, this game cannot be picked"
          : " — " + (g.spread_x2 < 0 ? g.home : g.away) + " favoured by " +
            Math.abs(g.spread_x2 / 2)));
    fs.appendChild(lg);

    var t = el("time", "pk-when", shortWhen(g.kickoff));
    t.setAttribute("datetime", g.kickoff);
    t.setAttribute("aria-hidden", "true");   // the legend already said it
    fs.appendChild(t);

    var sides = el("div", "pk-sides");
    ["away", "home"].forEach(function (side, i) {
      if (i === 1) sides.appendChild(el("span", "pk-at", "at"));
      var team = g[side];
      var id = "g" + g.game_id + "-" + side;
      var input = document.createElement("input");
      input.type = "radio";
      input.className = "sr-only";
      input.id = id;
      input.name = "g" + g.game_id;
      input.value = side;
      if (picks[g.game_id] === side) input.checked = true;

      var lab = el("label", "pk-side");
      lab.setAttribute("for", id);
      var colour = (teams[team] && teams[team].color) || "";
      if (colour) {
        lab.style.setProperty("--tc", colour);
        lab.style.setProperty("--tfg", textOn(colour));
      }
      var mk = mark(teams, team, 18);
      if (mk) lab.appendChild(mk);
      // The name is ellipsised when it has to be, so a title recovers it on
      // hover. The <legend> already carries it in full for screen readers,
      // which is why this is a convenience rather than the fix.
      var nm = el("span", "pk-tname", team);
      nm.title = team;
      lab.appendChild(nm);
      if (!why) {
        lab.appendChild(el("span", "pk-num", spreadText(g.spread_x2, side)));
        lab.appendChild(el("span", "sr-only", " " + spreadSaid(g.spread_x2, side)));
      }
      sides.appendChild(input);
      sides.appendChild(lab);
    });
    fs.appendChild(sides);

    // The outcome goes in its own column, NOT inside the selected label. In
    // the label it sits on the team's own fill, and the three chip colours
    // were chosen against the panel: LOSS is #c0392b, which on Houston's
    // #c92a39 is invisible, and the greens and greys fared no better on a
    // dark fill. Out here they are always on the background they were
    // designed for, and the result reads as a property of the pick rather
    // than of the team.
    if (why) {
      var tag = el("p", "tag out pk-nopick",
        why === "no_line" ? "No Spread Available" : "Kickoff Not Announced");
      fs.appendChild(tag);
    } else if (LOCKED) {
      // The third column is reserved on every row and stood empty here, which
      // read as a row still waiting for input on a slate that had stopped
      // accepting it. Once locked it says what the game is actually doing.
      //
      // ONE chip, from one function. There used to be a second block above
      // this one appending a graded chip of its own, so every graded row got
      // two: the first in the chip column and the second wrapped onto an
      // implicit grid row beneath it, a WIN pill sitting under a WIN pill.
      var chip = resultChip(g, picks[g.game_id] || null, true, "slate");
      if (chip) fs.appendChild(chip);
    }
    return fs;
  }

  // Playable first, then chronological within each group. The published slate
  // stays in pure kickoff order — it is the durable record of the week and its
  // order should not encode how a page happens to lay it out — so the grouping
  // happens here, at render, where it is a presentation decision.
  //
  // Nine of fifteen games have no line in a non-conference week. Left in
  // kickoff order they interleave with the pickable ones, so filling in a card
  // means hunting past rows you cannot act on.
  function inPlayOrder(games) {
    return games.slice().sort(function (a, b) {
      var ap = a.unpickable ? 1 : 0, bp = b.unpickable ? 1 : 0;
      if (ap !== bp) return ap - bp;
      if (a.kickoff_at !== b.kickoff_at) return a.kickoff_at - b.kickoff_at;
      return a.game_id - b.game_id;
    });
  }

  function renderSlate(teams) {
    var wrap = $("slate");
    wrap.textContent = "";
    if (!slate.games.length) {
      wrap.appendChild(el("p", "note", "No games this week."));
      return;
    }
    var ordered = inPlayOrder(slate.games);
    var firstDead = true;
    ordered.forEach(function (g) {
      // One heading where the pickable games stop, so the break is announced
      // rather than only implied by the styling.
      if (g.unpickable && firstDead) {
        firstDead = false;
        var h = el("h2", "pk-deadhead", "Not playable this week");
        wrap.appendChild(h);
      }
      wrap.appendChild(gameRow(g, teams));
    });

    var pickable = slate.games.filter(function (g) { return !g.unpickable; }).length;
    var n = slate.games.length;
    // Count honestly: "14 games, 12 with a line" is the true shape of the
    // week, and hiding the two without one would make the slate look wrong.
    $("slatecount").textContent = n + (n === 1 ? " game" : " games") +
      " · " + pickable + " with a line" +
      (pickable < n ? " · " + (n - pickable) + " without" : "");

    if (LOCKED) lockDown();
    else if (!SIGNED_IN) readOnly();
  }

  // Signed out: the same treatment as a locked week, for a different reason.
  // Reused rather than reinvented — the reader cannot pick either way, and two
  // visual languages for "not available to you" would be one too many. The
  // difference is what the page SAYS: the notice above the slate offers a way
  // in, where a lock offers none.
  function readOnly() {
    var form = $("slateform");
    if (!form) return;
    [].forEach.call(form.querySelectorAll("input[type=radio]"), function (i) {
      i.disabled = true;
    });
    form.className = "pk-locked pk-readonly";
  }

  function lockDown() {
    LOCKED = true;
    var form = $("slateform");
    if (form) {
      [].forEach.call(form.querySelectorAll("input[type=radio]"), function (i) {
        i.disabled = true;
      });
      // Disabling the inputs stops the picking; it does not stop the page
      // LOOKING like it is still asking. The borders still lit on hover and
      // the cursor still turned, which is an interface promising something it
      // cannot do. The class turns all of that off in one place.
      form.className = "pk-locked";
    }
    show($("signedin"), false);
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
      if (!n) return;
      // 404 here is not an error to apologise for: it is the ordinary state
      // of a week that has not been published. The generic handler said "the
      // server said no (404)", which tells a player nothing they can act on
      // and reads like a fault. Slates go up on the Tuesday refresh.
      n.textContent = err.status === 404
        ? "No slate published yet. The week goes up on Tuesday, once the "
          + "lines are in."
        : explain(err);
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
    // First run: arrived here from the OAuth callback with nothing chosen
    // yet. The query flag is only a hint — needs_name is the fact — so a
    // reader who bookmarks the URL does not get welcomed forever.
    var first = !!me && me.needs_name;
    show($("signin"), !me);
    show($("welcome"), first);
    show($("named"), !!me);
    show($("acctinfo"), !!me);
    if (first) {
      var dn = $("dname");
      if (dn && dn.focus) dn.focus();
    }

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

    initTeam(me);

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
          // The name was the only thing standing between them and picking.
          // Say so, and offer the door, rather than leaving them on a form
          // that has stopped being the point.
          if (first) {
            show($("welcome"), false);
            show($("onward"), true);
            first = false;
          }
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

  // Chips with marks, not a <select>. A native option list can render neither
  // a logo nor a colour, and this is the one question on the site whose
  // answers a reader recognises by sight before they have read them. It is
  // also the same control the slate already uses — hidden radio, styled
  // label — so it keeps native checked state, native keyboard handling and a
  // single tab stop for the whole group, with no ARIA to get wrong.
  function initTeam(me) {
    var card = $("teamcard"), box = $("teampick"), form = $("teamform");
    if (!card || !box || !form || !me) return;
    loadTeams().then(function (teams) {
      // The sixteen first: that is what nearly everyone is answering, and it
      // is the part that can be found by its mark rather than read. The three
      // that are not a team go below a rule — Big 12 with no particular one,
      // college football at large, and no answer at all.
      var opts = Object.keys(teams).filter(function (t) { return teams[t].b12; })
        .sort().map(function (t) { return {v: t, label: t, team: true}; });
      var teamCount = opts.length;
      opts.push({v: TEAM_B12, label: "Big 12, no particular team"},
                {v: TEAM_CFB, label: "College football generally"},
                {v: "", label: "Not saying"});

      while (box.children.length > 1) box.removeChild(box.lastChild);
      opts.forEach(function (o, i) {
        if (i === teamCount) box.appendChild(el("span", "pk-teamsep"));
        var id = "tm" + i;
        var input = document.createElement("input");
        input.type = "radio";
        input.className = "sr-only";
        input.id = id;
        input.name = "team";
        input.value = o.v;
        // Only a real answer preselects. Matching null against the "Not
        // saying" sentinel filled that chip in for everybody who had never
        // been asked, so a question nobody had answered arrived on screen
        // looking answered — on the one page whose job is to ask it.
        if (me.team && me.team === o.v) input.checked = true;

        var lab = el("label", "pk-teamopt" + (o.team ? "" : " pk-teamopt-any"));
        lab.setAttribute("for", id);
        if (o.team) {
          var colour = (teams[o.v] && teams[o.v].color) || "";
          if (colour) {
            lab.style.setProperty("--tc", colour);
            lab.style.setProperty("--tfg", textOn(colour));
          }
          // No stand-in when a team has no freely-licensed mark; the slot
          // still holds its width so the names stay in one column.
          var mk = mark(teams, o.v, 20);
          if (mk) lab.appendChild(mk);
          else lab.appendChild(el("span", "pk-teamgap"));
        }
        lab.appendChild(el("span", "pk-tname", o.label));
        box.appendChild(input);
        box.appendChild(lab);
      });
      card.hidden = false;
    });

    // Saved on choosing it, like the slate. There is nothing to validate,
    // nothing to collide with and no cooldown to spend, so a Save button here
    // was a second step guarding nothing — and the one failure it made
    // possible was choosing a team, leaving, and finding it had not stuck.
    // The name keeps its button: it can be rejected, and it can only be
    // changed once a month, so it must be deliberate.
    function saveTeam(v) {
      api("/api/me", {method: "PATCH", body: {team: v}})
        .then(function () {
          status(v ? "Team saved." : "Saved.");
          me.team = v;
          if (me.display_name || ($("dname") && $("dname").value.trim())) {
            show($("onward"), true);
          }
        })
        .catch(function (err) { alertMsg(explain(err)); });
    }

    box.addEventListener("change", function (e) {
      if (e.target && e.target.name === "team") saveTeam(e.target.value);
    });

    // Kept for the no-JS and keyboard-Enter paths, and because a form that
    // cannot be submitted is a form that reloads the page when you press
    // Enter in it.
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var on = box.querySelector("input:checked");
      saveTeam(on ? on.value : "");
    });
  }

  // ----------------------------------------------------------------- board

  var boardRows = [], sortKey = "rank", sortDir = 1, meId = null,
      chalk = null, room = null, myTint = null, boardTeams = {};

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
      if (meId && r.user_id === meId) {
        tr2.className = "you";
        // Your row in your own team's colour rather than the house accent.
        // A wash, not a fill: it has to sit beside the ATS% ramp without
        // competing with it, and at this strength it reads as "this one is
        // yours" without claiming to mean anything about the numbers.
        if (myTint) tr2.style.setProperty("--you", myTint);
      }
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
        } else if (c.key === "display_name") {
          // Everyone's mark, not just yours. Who a player follows is half the
          // reading of a leaderboard — a Cyclone at the top of the board is a
          // different fact from a name at the top of the board — and it is
          // volunteered rather than guessed, so there is nothing to infer.
          var mk = mark(boardTeams, r.team, 15);
          if (mk) td.appendChild(mk);
          else if (r.team) {
            // Told us, but there is no freely-licensed mark for them, or the
            // answer was "the conference" or "college football". A slot, so
            // the names still line up down the column.
            td.appendChild(el("span", "pk-markgap"));
          }
          td.appendChild(document.createTextNode(r[c.key] == null ? "—" : r[c.key]));
        } else {
          td.textContent = r[c.key] == null ? "—" : r[c.key];
        }
        tr2.appendChild(td);
      });
      tb.appendChild(tr2);
    });
    tbl.appendChild(tb);

    // The chalk: always take the favourite, every game, no thinking. It is the
    // benchmark the whole exercise is measured against — a board where most
    // players sit below it is telling you something true — and it is the same
    // comparison scorecard.py already makes for the models on the race card,
    // presented the same way. In a <tfoot> because it is not a competitor.
    if (chalk) {
      var tf = el("tfoot"), row = el("tr");
      COLS.forEach(function (c) {
        var td = el("td", c.num ? "n" : null);
        if (c.key === "display_name") td.textContent = "The chalk";
        else if (c.key === "rank") td.textContent = "";
        else if (c.key === "pct") {
          td.textContent = chalk.pct == null ? "—"
            : (chalk.pct * 100).toFixed(1) + "%";
        } else td.textContent = chalk[c.key] == null ? "—" : chalk[c.key];
        row.appendChild(td);
      });
      tf.appendChild(row);
      tbl.appendChild(tf);
    }
  }

  function fillWeeks(cur) {
    var sel = $("wksel");
    if (!sel) return;
    // Weeks played so far, plus a season-to-date option. Hidden entirely
    // until there is more than one thing to choose: an empty <select> is a
    // control that looks broken, and one option is a control that lies about
    // being a choice.
    var opts = [{v: "", t: "Season"}];
    // From 1. Weeks are 1-based — pickem.py publishes week-01 — and starting
    // the loop at 0 put a "Week 0" at the top of every board that has never
    // existed and returns nothing.
    for (var w = 1; w <= (cur == null ? 0 : cur); w++) {
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
      chalk = r.chalk || null;
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
    loadTeams().then(function (teams) {
      myTint = myColour(me, teams);
      boardTeams = teams;
      return loadBoard("");
    }).then(function (r) {
      // How many weeks there are to choose from, which is not the same as
      // which one this response is for: the season-to-date response is for no
      // week at all, and reading r.week there left nothing to enumerate.
      if (r) fillWeeks(r.weeks != null ? r.weeks : r.week);
    });
  }

  // ------------------------------------------------------------------ card

  // A pick is only a story once the game has a result, so the card joins the
  // slate to your picks rather than listing either alone: what you took, the
  // number you took it at, and how it came out — including the games you left
  // blank, which is the thing a card is for noticing.
  function cardRow(g, side, teams, locked) {
    var li = el("li", "pk-cardrow");
    var st = gameStatus(g);
    var when = el("span", "pk-when pk-st-" + st.kind, st.text);
    if (st.kind !== "time") {
      when.title = "Kickoff " + fmtWhen(g.kickoff);
    }
    li.appendChild(when);

    var m = el("span", "pk-cardmatch");
    ["away", "home"].forEach(function (s, i) {
      if (i) m.appendChild(el("span", "pk-at", " at "));
      var mk2 = mark(teams, g[s], 15);
      if (mk2) m.appendChild(mk2);
      var t = el("span",
        s === side ? "pk-took" : (side ? "pk-nottook" : null), g[s]);
      if (s === side) {
        var c = (teams[g[s]] && teams[g[s]].color) || "";
        if (c) { t.style.setProperty("--tc", c); t.style.setProperty("--tfg", textOn(c)); }
      }
      m.appendChild(t);
    });
    li.appendChild(m);

    // The score, in reading order: the row says "away at home", so the
    // figures do too. A card that says WIN without saying 31-21 is withholding
    // the thing anyone actually wants to look at.
    var sc = el("span", "pk-score");
    if (g.result && g.result.home_points != null) {
      sc.textContent = g.result.away_points + "\u2013" + g.result.home_points;
      sc.title = g.away + " " + g.result.away_points + ", " +
                 g.home + " " + g.result.home_points;
    }
    li.appendChild(sc);

    li.appendChild(el("span", "pk-num",
      side ? spreadText(g.spread_x2, side) : "—"));

    // The split goes before the result, because the grid places children in
    // DOM order and the result belongs in the last, narrow column. Appending
    // it after put the chip in the gauge's slot and squeezed the gauge into
    // the chip's.
    //
    // A bar running from one team's colour to the other with a marker where
    // the split falls and each side's number at its own end: the question is
    // which way the room leaned and how hard, and that is a position rather
    // than a digit.
    //
    // Absent below MIN_CONSENSUS, and absent entirely before the lock — the
    // server does not send the field, so a late picker cannot follow the room.
    var shown = false;
    if (g.consensus) {
      var h = g.consensus.home || 0, a = g.consensus.away || 0, n = h + a;
      if (n >= MIN_CONSENSUS) {
        li.appendChild(consensusBar(g, side, teams, h, a, n));
        shown = true;
      }
    }
    // The column exists either way, so rows stay aligned down the card
    // whether or not a given game reached the threshold.
    if (!shown) li.appendChild(el("span", "pk-split"));

    var res = resultChip(g, side, locked, "card");
    if (res) li.appendChild(res);
    else li.appendChild(el("span", "pk-res pk-res-none", ""));
    return li;
  }

  function consensusBar(g, side, teams, h, a, n) {
    var hp = Math.round(100 * h / n), ap = 100 - hp;
    var ac = (teams[g.away] && teams[g.away].color) || "";
    var hc = (teams[g.home] && teams[g.home].color) || "";
    var wrap = el("div", "pk-split");

    var lp = el("span", "pk-splitpct pk-away", ap + "%");
    if (side === "away") lp.className += " pk-mine";
    wrap.appendChild(lp);

    var bar = el("span", "pk-splitbar");
    // The gradient carries which team is which; the marker carries where the
    // split actually is. A gradient alone blurs the one number that matters.
    bar.style.background = "linear-gradient(90deg," +
      (ac || "var(--tc-none)") + " 0%," + (hc || "var(--tc-none)") + " 100%)";
    var mark = el("i", "pk-splitmark");
    mark.style.left = ap + "%";
    bar.appendChild(mark);
    wrap.appendChild(bar);

    var rp = el("span", "pk-splitpct pk-home", hp + "%");
    if (side === "home") rp.className += " pk-mine";
    wrap.appendChild(rp);

    wrap.title = ap + "% took " + g.away + ", " + hp + "% took " + g.home +
                 " — " + n + " cards";
    wrap.appendChild(el("span", "sr-only",
      "Of " + n + " cards, " + ap + " percent took " + g.away + " and " +
      hp + " percent took " + g.home + "."));
    return wrap;
  }

  // Five states, not two. A card read on Saturday afternoon is mostly games
  // that have not finished, and "nothing here yet" is the least useful thing
  // a row can say about a game that is currently being played.
  // What YOUR pick did — nothing about the game itself, which the status
  // column already says. Before a result there is no outcome to report, so it
  // stays empty rather than inventing a state for it.
  function resultChip(g, side, locked, view) {
    if (side && g.result) {
      var a = g.result.ats;
      var out = a === "void" ? "void" : a === "push" ? "push"
              : a === side ? "win" : "loss";
      var chip = el("span", "pk-res " + out, out.toUpperCase());
      chip.appendChild(el("span", "sr-only", " \u2014 your pick " + out));
      return chip;
    }

    // Nothing to report yet, and the two pages want different words for that.
    //
    // The Slate is the picker with the picking switched off. The only thing
    // worth saying about a row there is that the week has closed, and it is
    // the same sentence for every row: which side you took is already visible
    // as the filled one, and a row you left blank is not a different kind of
    // closed.
    if (view === "slate") return el("span", "pk-res locked", "LOCKED");

    // The Card is the record of what YOUR picks did, and "locked" is not
    // something a pick did \u2014 it describes the week. A game you left blank
    // is a permanent outcome and says so. A pick still waiting on a score has
    // no outcome yet, and the status column at the other end of the row
    // already says whether the game is in play or waiting, so this stays
    // empty rather than saying it again in different words.
    if (!side) return el("span", "pk-res nopick", "NO PICK");
    if (!locked) return el("span", "pk-res pending", "OPEN");
    return null;
  }

  function tallyCard(games, picks) {
    var t = {w: 0, l: 0, p: 0, v: 0, made: 0, open: 0};
    games.forEach(function (g) {
      if (g.unpickable) return;
      var side = picks[g.game_id];
      if (!side) { t.open++; return; }
      t.made++;
      if (!g.result) return;
      var a = g.result.ats;
      if (a === "void") t.v++;
      else if (a === "push") t.p++;
      else if (a === side) t.w++;
      else t.l++;
    });
    return t;
  }

  function initCard(me) {
    var note = $("cardnote"), wrap = $("card");
    if (!note || !wrap) return;
    if (!me) {
      note.textContent = "";
      var so = el("p", "pk-signedout");
      var a = el("a", null, "Sign in");
      a.href = "account.html";
      so.appendChild(a);
      so.appendChild(document.createTextNode(" to see your card."));
      wrap.appendChild(so);
      return;
    }
    var teamsP = fetch("/pickem/teams.json")
      .then(function (r) { return r.ok ? r.json() : {}; })
      .catch(function () { return {}; });

    function draw(week) {
      var q = week == null || week === "" ? "" : "?week=" + encodeURIComponent(week);
      return Promise.all([api("/api/slate" + q), api("/api/picks" + q), teamsP])
        .then(function (r) {
          var s = r[0], picks = (r[1] && r[1].picks) || {}, teams = r[2] || {};
          var t = tallyCard(s.games, picks);
          var decided = t.w + t.l;

          note.textContent = "";
          var line = el("p", "pk-cardrec");
          line.appendChild(el("b", null,
            t.w + "–" + t.l + (t.p ? "–" + t.p : "")));
          line.appendChild(document.createTextNode(decided
            ? "  ·  " + (100 * t.w / decided).toFixed(1) + "% against the spread"
            : "  ·  nothing graded yet"));
          // Before a lock the useful number is what you have NOT done; after
          // it there is nothing to be done about it, so it stops nagging.
          if (t.open && !s.locked) {
            line.appendChild(el("span", "pk-open",
              "  ·  " + t.open + " still open"));
          }
          note.appendChild(line);

          var ul = el("ul", "pk-card");
          inPlayOrder(s.games).forEach(function (g) {
            if (g.unpickable) return;   // nothing to say about a game nobody could pick
            ul.appendChild(cardRow(g, picks[g.game_id] || null, teams, s.locked));
          });
          wrap.textContent = "";
          wrap.appendChild(ul);
          return s;
        });
    }

    // Season first, because the week is the detail and the season is the
    // record. Failing to get it is not worth blocking the card over.
    api("/api/leaderboard").then(function (b) {
      var mine = (b.rows || []).filter(function (r) {
        return r.user_id === me.user_id; })[0];
      if (!mine) return;
      var el2 = $("cardseason");
      if (!el2) return;
      var d = mine.w + mine.l;
      el2.textContent = "Season  " + mine.w + "–" + mine.l +
        (mine.p ? "–" + mine.p : "") +
        (d ? "  ·  " + (100 * mine.w / d).toFixed(1) + "%" : "") +
        (mine.rank ? "  ·  " + ordinal(mine.rank) + " on the board" : "");
      el2.hidden = false;
    }).catch(function () { /* the week still stands on its own */ });

    draw("").then(function (s) {
      var sel = $("cardwk");
      if (!sel || s == null) return;
      for (var w = 1; w <= s.week; w++) {   // 1-based, as published
        var o = document.createElement("option");
        o.value = String(w);
        o.textContent = "Week " + w;
        if (w === s.week) o.selected = true;
        sel.appendChild(o);
      }
      var lab = sel.closest ? sel.closest("label") : null;
      // One option is not a choice, and a control that offers one is a
      // control that lies about being one.
      if (s.week > 1) { if (lab) lab.hidden = false; }
      sel.addEventListener("change", function () { draw(sel.value); });
    }).catch(function (err) {
      note.textContent = err.status === 404
        ? "No slate published yet. The week goes up on Tuesday, once the "
          + "lines are in."
        : explain(err);
    });
  }

  function ordinal(n) {
    var s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  // ------------------------------------------------------------------ boot

  document.addEventListener("DOMContentLoaded", function () {
    // One /api/me for the whole page: the chip needs it, and every section
    // below branches on it.
    api("/api/me").catch(function () { return null; }).then(function (me) {
      acctChip(me);
      // Signed in is not the same as ready to play. An account with no
      // display name has nothing to put on the board, and the server refuses
      // its picks — so the slate must not offer them either.
      var ready = !!me && !me.needs_name;
      SIGNED_IN = ready;
      show($("signedout"), !me && !!$("slateform"));
      show($("needsname"), !!me && !ready && !!$("slateform"));
      show($("signedin"), ready && !!$("slateform"));
      initSlate();
      initAccount(me);
      initBoard(me);
      initCard(me);
    });
  });

})();

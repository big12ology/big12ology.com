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

  /* The server's clock, approximately, for DISPLAY decisions. Enforcement
     never trusts a browser: the Worker checks its own clock and the D1
     triggers check unixepoch(), so a wound-back clock only buys a rejected
     write. But what the reader SEES has to match what the server will do,
     and a skewed browser clock made the page grey out early or keep
     offering picks the server would refuse. /api/health carries the
     server's `at`; the difference corrects every UI clock on the page.
     Zero until measured, which is the old behavior. */
  var SKEW = 0;
  function serverNow() { return Date.now() + SKEW; }

  // Below this many cards on a game, the split is not shown at all. Three
  // people picking is not a consensus, it is three people — and rendered as a
  // 67/33 bar it would read with exactly the same authority as a real one.
  // The threshold is the client's, so a small pool simply sees nothing rather
  // than something misleading; the server is free to send whatever it has.
  var MIN_CONSENSUS = 10;

  // Below this many players the outer decile band is one person at each edge,
  // which is not a distribution and reads as a second leader line.
  var MIN_BAND = 20;

  // Two answers that are not a team. Stored as-is so the difference between
  // "no answer yet" and "deliberately no team" survives — one is a question
  // we have not asked, the other is an answer.
  var TEAM_B12 = "__big12", TEAM_CFB = "__cfb";

  var teamsP = null;
  function loadTeams() {
    if (!teamsP) {
      // Absolute, because this file now serves pages at two depths —
      // /pools/account.html and /pools/pickem/board.html — and a relative
      // "teams.json" resolves to a different (and mostly wrong) place from
      // each.
      teamsP = fetch("/pools/teams.json")
        .then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; });
    }
    return teamsP;
  }

  // The color to tint a viewer's own rows with. A real team gets its own; the
  // two generic answers get the brand accent, which is chrome and allowed on a
  // row that is chrome rather than data.
  function myColor(me, teams) {
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
    // Every write says application/json, body or no body: csrfOk refuses a
    // write without the pair (Origin plus this content type), and the one
    // body-less write on the site — the logout POST — went out bare, was
    // refused as bad_origin, and the sign-out button looked dead. Launch
    // day's first signed-in user found it.
    if (init.method !== "GET") init.headers["Content-Type"] = "application/json";
    if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
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

  // A missing slate is not a mystery, it is a schedule: the weekly refresh
  // publishes Tuesday 07:00 UTC and the Worker imports hourly from 07:30.
  // The hour promised here is 09:00 UTC — 5am Eastern in season — which the
  // pipeline beats even on GitHub's worst observed scheduler drift.
  // "Tuesday" has to mean Tuesday morning to somebody opening the page at
  // work on the East Coast, not whenever the crons got around to it.
  function nextSlateTime() {
    var now = new Date();
    var t = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(),
                              now.getUTCDate(), 9, 0, 0));
    while (t.getUTCDay() !== 2 || t <= now) t.setUTCDate(t.getUTCDate() + 1);
    return t;
  }

  // Week 1's publish Tuesday. Weeks run Tuesday to Monday with Week 1 ending
  // on Labor Day (pickem.py's display_week, restated for the same reason it
  // restates the attendance script's), so this is the Tuesday six days
  // before September's first Monday.
  function week1Tuesday(year) {
    var d = new Date(Date.UTC(year, 8, 1, 13, 0, 0));
    while (d.getUTCDay() !== 1) d.setUTCDate(d.getUTCDate() + 1);
    d.setUTCDate(d.getUTCDate() - 6);
    return d;
  }

  // 6:00 AM Eastern on the given Tuesday, whatever DST says that week:
  // build both UTC hours that can be 6am in New York and keep the one that
  // is. This is the time the pages STATE — the pipeline's own live-by is
  // 09:00 UTC, a further hour ahead, so the stated time is beaten every
  // week rather than merely met.
  function sixEastern(t) {
    var pick = null;
    [10, 11].forEach(function (h) {
      var c = new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(),
                                t.getUTCDate(), h, 0, 0));
      if (c.toLocaleTimeString("en-US",
            { timeZone: "America/New_York", hour: "numeric" })
          .indexOf("6") === 0) pick = pick || c;
    });
    return pick || t;
  }

  // The one empty-state message, shared by the slate, the card and the
  // survivor page. `pool` is the difference between them: the pick'em waits
  // on the market, so its copy says why Tuesday; survivor picks winners
  // outright, so citing the lines there would imply they matter to the
  // pick. Survivor also sits out Week 0 (one Big 12 game is not a choice,
  // see MIN_SURVIVOR_TEAMS), so before the season its note points at
  // Week 1's Tuesday, not the pick'em's. No countdown: the hours-from-now
  // figure a ticker prints would be beaten most weeks, and a number that
  // cannot be spot on is not worth printing. The stated fact is the date
  // and the 6am ET opening, said the way the schedule pages say kickoffs:
  // the anchor time zone, then the reader's own.
  //
  // With `pv` (the preview slate the message sits above), the same note
  // narrates a different scene: the week is on the page, just not open, so
  // the message says what the reader is looking at and when it goes live.
  function slateOpensNote(node, pool, pv) {
    // A far-out week has no lock yet — every kickoff TBD means nothing is
    // pickable and lock_at is null — but its games still carry real DATES
    // (only the hours are CFBD placeholders), and the publish Tuesday only
    // needs the date. Falling back to nextSlateTime here once claimed Week 5
    // would open in four days.
    var lockish = pv && (pv.lock_at || Math.min.apply(null,
      pv.games.map(function (g) { return g.kickoff_at || Infinity; })));
    var t = pv ? publishTuesday(lockish === Infinity ? null : lockish)
               : nextSlateTime();
    var skipsWeek0 = false;
    if (!pv && pool === "survivor") {
      var w1 = week1Tuesday(t.getUTCFullYear());
      if (t < w1) { t = w1; skipsWeek0 = true; }
    }
    // The stated opening: 6am ET on the slate's Tuesday, then the reader's
    // own clock in parentheses when it differs — the same shape the
    // schedule pages give a kickoff. The "due" flip below keys off the
    // stated time, not the internal one, so the page never claims lateness
    // it has not incurred.
    var open = sixEastern(t);
    var et = open.toLocaleTimeString("en-US",
      { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
    var local = open.toLocaleTimeString(undefined,
      { hour: "numeric", minute: "2-digit" });
    var when = t.toLocaleDateString("en-US",
      { weekday: "long", month: "long", day: "numeric" }) +
      " at " + et + " ET" +
      (local === et ? "" : " (" + local + " your time)");
    function draw() {
      var ms = open - Date.now();
      if (ms <= 0) {
        node.textContent = pv
          ? "Picks should open any time now. Reload for the live week."
          : "No slate published yet, but it is due: " +
            "the week should be up any time now.";
        return;
      }
      node.textContent = pv
        ? (pool === "survivor"
            ? "A look ahead at Week " + pv.week + ". Nothing can be picked " +
              "yet: this week's pick opens " + when +
              ". A line marked est is today's market, not fixed."
            : "A look ahead at Week " + pv.week + ". Nothing can be picked " +
              "yet: picks open " + when + ", once the lines freeze. A line " +
              "marked est is today's market, not fixed; TBD has no market " +
              "yet.")
        : skipsWeek0
          ? "No slate published yet. Week 0 is not a survivor week, so the " +
            "first pick goes up " + when + "."
          : "No slate published yet. The week goes up " + when +
            (pool === "survivor" ? "" : ", once the lines are in") + ".";
    }
    // One ticker per node, however many times the selector lands here: the
    // survivor page reuses its note element across weeks, and two intervals
    // writing one node is a message that flickers between weeks.
    if (node._cdTick) clearInterval(node._cdTick);
    draw();
    node._cdTick = setInterval(function () {
      // The node outlives its usefulness when a slate renders over it —
      // renderSlate clears the container — so let the ticker die with it.
      if (!node.isConnected) { clearInterval(node._cdTick); return; }
      draw();
    }, 60000);
  }

  // The countdown's off switch, for the node that never disconnects: going
  // back to the live week hands the survivor note back to svRepaint.
  function stopOpensNote(node) {
    if (node && node._cdTick) { clearInterval(node._cdTick); node._cdTick = null; }
  }

  /* The look-ahead season, written by build_pools_preview: an index of the
     weeks still to play, and a file per week in the API's own shape. All of
     it renders disabled, and only for weeks the API is not serving. Every
     loader returns null on any failure — the countdown alone is the
     fallback, and it already works. */
  var PVX = null;
  function previewIndex() {
    if (!PVX) {
      PVX = fetch("/pools/preview.json")
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; });
    }
    return PVX;
  }

  function previewWeek(wk) {
    var nn = String(wk).length < 2 ? "0" + wk : String(wk);
    return fetch("/pools/preview/week-" + nn + ".json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s || !s.games || !s.games.length) return null;
        // no_line is a fact about grading, and a look-ahead grades nothing:
        // most of a future week has no market yet, and sorting all of it
        // under "not playable" would read as a verdict on the games rather
        // than on the calendar. The slot shows TBD instead. kickoff_tbd
        // stays — the placeholder times CFBD ships for unset windows are
        // wrong on purpose, and a wrong time shown is worse than a tag.
        s.games.forEach(function (g) {
          if (g.unpickable === "no_line") delete g.unpickable;
        });
        return s;
      })
      .catch(function () { return null; });
  }

  /* The live rows carry the same schedule links the look-ahead rows do: the
     week's preview file already knows each game's page (build_pools_preview
     writes the slug the schedule build produced), so the API slate borrows
     them by game_id rather than teaching the client to slugify. Failing the
     fetch loses only the links. */
  function mergePeeks(s) {
    return previewWeek(s.week).then(function (pv) {
      if (pv) {
        var by = {};
        pv.games.forEach(function (g) { by[g.game_id] = g.preview; });
        s.games.forEach(function (g) {
          g.preview = g.preview || by[g.game_id];
        });
      }
      return s;
    });
  }

  /* When a week's picks open: the Tuesday 09:00 UTC before its lock — the
     same live-by promise nextSlateTime makes, pointed at a particular week
     instead of at the calendar. The pipeline behind the hour is described
     on nextSlateTime. */
  function publishTuesday(lockAt) {
    if (!lockAt) return nextSlateTime();
    var d = new Date(lockAt * 1000);
    var t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(),
                              d.getUTCDate(), 9, 0, 0));
    while (t.getUTCDay() !== 2 || t.getTime() > lockAt * 1000) {
      t.setUTCDate(t.getUTCDate() - 1);
    }
    return t;
  }

  // The shop-window footer under a preview's message: where to go while the
  // week is shut. The rows link to their own game pages; this names the
  // section-level tools.
  function previewLinksNote() {
    var p = el("p", "note pk-pvlinks");
    p.appendChild(document.createTextNode("While you wait, every matchup " +
      "links to its page on the "));
    var a = el("a", null, "Schedule");
    a.href = "/schedule/";
    p.appendChild(a);
    p.appendChild(document.createTextNode(", and the "));
    var b = el("a", null, "Tiebreaker");
    b.href = "/tiebreaker/";
    p.appendChild(b);
    p.appendChild(document.createTextNode(" has the season forecast those " +
      "pages draw from."));
    return p;
  }

  /* A game card's look-ahead dressing — the flipped title, the links note,
     the game count — and its removal. One pair shared by both games, so
     the two cards cannot drift apart and a selector cannot leave half of
     it behind. `title` and `note` are the card's own; the links and count
     are created on first use, keyed off the note's id. */
  function lookaheadMeta(title, note, pv) {
    if (title) title.textContent = "Week " + pv.week + ", before it opens";
    if (!note) return;
    var links = $(note.id + "-pvlinks");
    if (!links) {
      links = previewLinksNote();
      links.id = note.id + "-pvlinks";
      note.insertAdjacentElement("afterend", links);
    }
    show(links, true);
    var count = $(note.id + "-pvcount");
    if (!count) {
      count = el("p", "pk-slatecount");
      count.id = note.id + "-pvcount";
      links.insertAdjacentElement("afterend", count);
    }
    var n = pv.games.length;
    count.textContent = n + (n === 1 ? " game" : " games");
    show(count, true);
  }

  function lookaheadMetaOff(title, note, liveTitle) {
    if (title) title.textContent = liveTitle;
    if (!note) return;
    show($(note.id + "-pvlinks"), false);
    show($(note.id + "-pvcount"), false);
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

  // The word between the two teams. "at" names a host — in "Arizona State at
  // Kansas", Kansas had the crowd — and a neutral-site game has none: they
  // both flew to Wembley. The slate labels a home side anyway because a
  // schedule needs a column for it, so g.neutral is the only thing that
  // knows better. Same rule as joiner() in build.py, and the reason it lives
  // in one function here is that four rows across two games were writing the
  // word themselves.
  //
  // A slate frozen before the field existed reads undefined, which is "at" —
  // what those rows already said.
  function joiner(g) { return g.neutral ? "vs" : "at"; }

  // A result is a result only when both numbers are in it. The port of
  // tiebreaker.py's has_score, kept local because this page does not load
  // engine.js, and phrased against g.result because that is the shape the
  // pools API hands back.
  //
  // Testing home_points alone is what the two call sites below used to do,
  // and the feed does deliver rows with one score posted and the other still
  // null. That combination gets a card labeled FINAL whose score reads
  // "null-28", because a missing number does not stop string concatenation
  // the way it stops arithmetic. The scoreboard is the one place on the site
  // where being confidently wrong is worse than being blank.
  function hasResult(g) {
    return !!g.result && g.result.home_points != null &&
      g.result.away_points != null;
  }

  // What a screen reader hears instead of "minus six point five", which is
  // not what the number means to anybody.
  function spreadSaid(spreadX2, side) {
    var v = (side === "home" ? spreadX2 : -spreadX2) / 2;
    if (v === 0) return "pick em, no points";
    var n = Math.abs(v), half = (n % 1) ? " and a half" : "";
    return (v < 0 ? "giving " : "getting ") + Math.floor(n) + half + " points";
  }

  // Readable text on a team's own color. Selected picks fill with the team
  // color rather than the brand teal (teal is chrome only), which means the
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
    // 2.5:1, and would have done the same to every light team color in the
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

  // For the game whose hour nobody has set: CFBD ships a placeholder time
  // with an unannounced window, and printing it states a fact that is not
  // one. The date half is real, so the row says only that.
  function shortDate(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
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
    if (hasResult(g)) {
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
      var left = Math.floor(lockAt - serverNow() / 1000);
      if (left <= 0) {
        cd.textContent = "locked";
        if (!said[0]) { said[0] = 1; if (sr) sr.textContent = "The slate is locked."; }
        // The controls close the second the clock does. The server was
        // already refusing; without this the page kept LOOKING open until a
        // save bounced, and what the reader sees has to match what the
        // server will do. Guarded on the form: the survivor page runs this
        // countdown too, and its rows close per game, not per week.
        if (!LOCKED && $("slateform")) lockDown();
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
  var LIVE_WEEK = null;  // the API's week, once it has one; null pre-season

  /* The season, browsable: the open week plus every later one from the
     look-ahead index. Fewer than two options is not a choice, so the
     control stays hidden until there are. */
  function slateWeekSelector(current) {
    var sel = $("slatewk");
    if (!sel) return;
    previewIndex().then(function (ix) {
      var wks = ((ix && ix.weeks) || []).filter(function (w) {
        return LIVE_WEEK == null || w > LIVE_WEEK;
      });
      var opts = (LIVE_WEEK != null ? [LIVE_WEEK] : []).concat(wks);
      if (opts.length < 2) return;
      sel.textContent = "";
      opts.forEach(function (w) {
        var o = document.createElement("option");
        o.value = String(w);
        o.textContent = String(w) + (w === LIVE_WEEK ? " (open)" : "");
        sel.appendChild(o);
      });
      sel.value = String(current);
      var lab = sel.closest ? sel.closest("label") : null;
      if (lab) lab.hidden = false;
    });
  }

  function gameRow(g, teams) {
    var fs = el("fieldset", "pk-slate-game");
    fs.dataset.gid = g.game_id;
    var pv = !!(slate && slate.preview);
    var why = g.unpickable;
    var tbd = why === "kickoff_tbd";
    // On the look-ahead the unpickable treatment comes off: "cannot be
    // picked" is a verdict about a live week, and here NOTHING can be
    // picked, so the rows wear the live week's shape and only the facts
    // differ — a date where the hour is unset, a ~ or TBD where the line
    // would be.
    if (pv) why = null;
    if (why) fs.disabled = true;

    // The legend is the group's accessible name and carries the whole story:
    // matchup, kickoff, the line, and why the game is closed if it is. It is
    // visually hidden because repeating the team names above a row that
    // already shows them cost a line per game — fifteen lines of a slate that
    // has to fit on one screen. The time is re-emitted below as a visible,
    // aria-hidden column so it is said once and shown once.
    var lg = el("legend", "sr-only");
    lg.appendChild(document.createTextNode(
      g.away + " " + joiner(g) + " " + g.home + ", " +
      (tbd ? shortDate(g.kickoff) : fmtWhen(g.kickoff))));
    lg.appendChild(document.createTextNode(
      why === "no_line"
        ? " — no spread available, this game cannot be picked"
        : why === "kickoff_tbd"
          ? " — kickoff time not announced, this game cannot be picked"
          : pv
            ? (g.spread_x2 != null
                ? ", today's market has " +
                  (g.spread_x2 < 0 ? g.home : g.away) + " by " +
                  Math.abs(g.spread_x2 / 2) + ", not fixed"
                : ", line to come when the week publishes") +
              (tbd ? "; kickoff not announced yet" : "")
            : " — " + (g.spread_x2 < 0 ? g.home : g.away) + " favored by " +
              Math.abs(g.spread_x2 / 2)));
    fs.appendChild(lg);

    var t = el("time", "pk-when", tbd
      ? shortDate(g.kickoff) : shortWhen(g.kickoff));
    t.setAttribute("datetime", g.kickoff);
    t.setAttribute("aria-hidden", "true");   // the legend already said it
    fs.appendChild(t);

    var sides = el("div", "pk-sides");
    ["away", "home"].forEach(function (side, i) {
      if (i === 1) sides.appendChild(el("span", "pk-at", joiner(g)));
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
      var color = (teams[team] && teams[team].color) || "";
      if (color) {
        lab.style.setProperty("--tc", color);
        lab.style.setProperty("--tfg", textOn(color));
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
        // On the look-ahead the market shows, marked: the pool's promise is
        // one frozen line for everyone, and the schedule pages already quote
        // today's market, so hiding it here made the pools look less
        // informed than their own site. The ~ is the whole caveat (the
        // banner spells it out), and TBD is for a game the market has not
        // priced at all.
        if (slate && slate.preview) {
          var has = g.spread_x2 != null;
          var num = el("span", "pk-num");
          if (has) {
            // "est", a word, not a glyph: the ~ that used to sit here read
            // as a third sign in front of +7. Dim and small, so the number
            // keeps its weight and the qualifier stays a qualifier.
            num.appendChild(el("span", "pk-est", "est"));
            num.appendChild(document.createTextNode(
              " " + spreadText(g.spread_x2, side)));
          } else num.textContent = "TBD";
          lab.appendChild(num);
          lab.appendChild(el("span", "sr-only", has
            ? ", today's market, not fixed: " + spreadSaid(g.spread_x2, side)
            : ", line to come when the week publishes"));
        } else {
          lab.appendChild(el("span", "pk-num", spreadText(g.spread_x2, side)));
          lab.appendChild(el("span", "sr-only", " " + spreadSaid(g.spread_x2, side)));
        }
      }
      sides.appendChild(input);
      sides.appendChild(lab);
    });
    fs.appendChild(sides);

    // The outcome goes in its own column, NOT inside the selected label. In
    // the label it sits on the team's own fill, and the three chip colors
    // were chosen against the panel: LOSS is #c0392b, which on Houston's
    // #c92a39 is invisible, and the greens and grays fared no better on a
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
    // The row's door to its schedule page. The look-ahead files carry the
    // URL and mergePeeks lends it to the live slate, so every row links out
    // in every season state; the grid grows a fourth column for it only on
    // rows that have one (see .pk-peek in styles.css).
    if (g.preview) {
      var peek = el("a", "pk-peek", "preview →");
      peek.href = g.preview;
      fs.appendChild(peek);
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
    // A fresh verdict every render: the preview render before this one left
    // the form classed shut, and coming back to the live week has to undo
    // that before lockDown/readOnly below re-apply whatever is true now.
    var form = $("slateform");
    if (form) form.className = "";
    var wrap = $("slate");
    wrap.textContent = "";
    if (!slate.games.length) {
      // Plainly, inside the card the page already stands in — the same way
      // the survivor picker says it.
      wrap.appendChild(el("p", "note", "No games this week."));
      return;
    }
    // The look-ahead keeps the file's own kickoff order and draws no break:
    // playable-versus-not is a live week's distinction, and here nothing is
    // playable, so a "not playable" group would divide the week by a verdict
    // that does not apply. The published file is already (kickoff, id)
    // sorted, which is the order a reader browses a future week in.
    var ordered = slate.preview ? slate.games : inPlayOrder(slate.games);
    var firstDead = true;
    ordered.forEach(function (g) {
      // One heading where the pickable games stop, so the break is announced
      // rather than only implied by the styling.
      if (!slate.preview && g.unpickable && firstDead) {
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
      fetch("/pools/teams.json").then(function (r) {
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
        lockSpanNote(slate);
      }
      // The lines' own deadline is already behind us, and the page says
      // when: the freeze happened at publish, so the number beside a team
      // has been the number since this moment and will not move.
      var lf = $("linefroze");
      if (lf) {
        if (slate.published_at) {
          lf.textContent = "Lines frozen since " +
            fmtWhen(slate.published_at * 1000) + "; they do not move.";
          show(lf, true);
        } else show(lf, false);
      }
      LIVE_WEEK = slate.week;
      // The card is the live week's again: title back, look-ahead meta off,
      // and the status note is silent — the hints above the card carry the
      // live week's words.
      lookaheadMetaOff($("slatecardtitle"), $("slateload"),
                       "This week's slate");
      var n0 = $("slateload");
      if (n0) { stopOpensNote(n0); n0.textContent = ""; }
      return mergePeeks(slate).then(function () {
        renderSlate(r[2] || {});
        slateWeekSelector(slate.week);
      });
    }).catch(function (err) {
      var n = $("slateload");
      if (!n) return;
      // 404 here is not an error to apologize for: it is the ordinary state
      // of a week that has not been published. The generic handler said "the
      // server said no (404)", which tells a player nothing they can act on
      // and reads like a fault. Slates go up on the Tuesday refresh — and
      // until then the look-ahead slate stands in, disabled, so the page
      // shows the game instead of describing it.
      if (err.status === 404) {
        previewIndex().then(function (ix) {
          var wks = (ix && ix.weeks) || [];
          if (!wks.length) { slateOpensNote(n, "pickem"); return; }
          previewWeek(wks[0]).then(function (pv) {
            if (!pv) { slateOpensNote(n, "pickem"); return; }
            renderPickemPreview(pv);
            slateWeekSelector(pv.week);
          });
        });
      } else n.textContent = explain(err);
    });
  }

  /* The upcoming week, in the slate card the live week uses — the title
     flips to say what this is, the countdown takes the status note, the
     links and count slot in beneath it, and the rows draw where rows always
     draw. Then it is shut: every input disabled and the read-only
     treatment. The rows carry their preview links, so a visitor can walk
     the week before there is anything to do in it. Mirror of the survivor
     card's look-ahead, deliberately: one anatomy, two games. */
  function renderPickemPreview(pv) {
    fetch("/pools/teams.json")
      .then(function (r) { return r.ok ? r.json() : {}; })
      .catch(function () { return {}; })
      .then(function (teams) {
        slate = pv;
        // The live week's lock furniture makes no claim about a week that
        // is not open; the countdown in the card carries this week's dates.
        show($("lockcard"), false);
        renderSlate(teams);
        var form = $("slateform");
        [].forEach.call(form.querySelectorAll("input"), function (i) {
          i.disabled = true;
        });
        form.className = "pk-locked pk-readonly pk-preview";
        lookaheadMeta($("slatecardtitle"), $("slateload"), pv);
        slateOpensNote($("slateload"), "pickem", pv);
      });
  }

  /**
   * Say so when the lock lands days before most of the week is played.
   *
   * "Locks at first kickoff" is the entire rule and it is stated above this
   * line already. The trouble is that it is only surprising on the weeks where
   * it costs something: a week played on one Saturday reads exactly as
   * expected, and a week that opens with a Thursday game reads as though
   * Thursday's kickoff locks Thursday's games. It does not — it locks all of
   * them, and 2026 opens that way, with three Thursday games and eleven on
   * Saturday.
   *
   * DAYS IN THE READER'S OWN ZONE, not Central and not the venue's. The time
   * beside it is already shown in their clock, so a note that said "Thursday"
   * about a Wednesday evening on their calendar would be answering a question
   * nobody asked.
   *
   * Silent when the week is one day, which is most of them — a note that
   * appears every week is a note nobody reads by October.
   */
  function lockSpanNote(slate) {
    var el = $("lockspan");
    if (!el || !slate.lock_at || !slate.games || !slate.games.length) return;
    var DAY = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
               "Friday", "Saturday"];
    var lockDay = new Date(slate.lock_at * 1000).toDateString();
    var later = slate.games.filter(function (g) {
      return g.kickoff_at &&
        new Date(g.kickoff_at * 1000).toDateString() !== lockDay;
    });
    if (!later.length) return;

    // The day the most of them are on, which is the one somebody is picturing
    // when they assume they have until the weekend.
    var tally = {};
    later.forEach(function (g) {
      var d = new Date(g.kickoff_at * 1000).getDay();
      tally[d] = (tally[d] || 0) + 1;
    });
    var big = Object.keys(tally).sort(function (a, b) {
      return tally[b] - tally[a];
    })[0];
    var n = tally[big];

    // "Locks early", never "opens early": on this site "opens" means the
    // Tuesday the week goes up, and this sentence is about the other end of
    // the window. Using one word for both read as a contradiction of the
    // Tuesday promise two lines above it.
    el.textContent = "This week locks early, at its first kickoff: that "
      + "includes the " + n + " game" + (n === 1 ? "" : "s")
      + " played " + DAY[big] + ". Get your picks in before then.";
    el.hidden = false;
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
    var wsel = $("slatewk");
    if (wsel) {
      wsel.addEventListener("change", function () {
        var w = Number(wsel.value);
        if (LIVE_WEEK != null && w === LIVE_WEEK) { loadSlate(); return; }
        previewWeek(w).then(function (pv) {
          if (pv) renderPickemPreview(pv);
        });
      });
    }
    loadSlate();
  }

  // --------------------------------------------------------------- account

  function acctChip(me) {
    var chip = document.querySelector(".b12-acct");
    if (!chip) return;
    chip.textContent = "";
    if (me && me.display_name) {
      var a = el("a", null, me.display_name);
      a.href = "/pools/account.html";
      chip.appendChild(document.createTextNode(""));
      chip.appendChild(a);
    } else {
      var s = el("a", null, me ? "Choose a name" : "Sign in");
      s.href = "/pools/account.html";
      chip.appendChild(s);
    }
    chip.hidden = false;
  }

  // How a provider is spelled to a person. The database stores the lowercase
  // key, which is right for a key and wrong on a page — "Signed in with
  // github" reads like a typo. Anything not listed falls back to the stored
  // name capitalised, so a provider added to the Worker before this map is
  // updated still reads sensibly instead of disappearing.
  var PROVIDER_LABEL = {
    google: "Google", github: "GitHub", microsoft: "Microsoft",
    amazon: "Amazon"
  };
  function providerLabel(p) {
    return PROVIDER_LABEL[p] || (String(p).charAt(0).toUpperCase() + String(p).slice(1));
  }

  // "Google", "Google and GitHub", "Google, Microsoft and GitHub".
  //
  // Was `.join(" and ")`, which was correct for the two providers that existed
  // and produces "Google and Microsoft and GitHub" for the four that do now.
  function joinNames(list) {
    if (!list.length) return "no provider";
    if (list.length === 1) return list[0];
    return list.slice(0, -1).join(", ") + " and " + list[list.length - 1];
  }

  /**
   * Take down the sign-in buttons this deploy cannot honor.
   *
   * account.html ships a button for every provider the Worker knows how to
   * talk to; the Worker knows which ones actually have credentials. Asking it
   * is what lets a provider be switched on with `wrangler secret put` and
   * nothing else — no HTML edit, no deploy, and no list of provider names kept
   * in two places waiting to disagree.
   *
   * FAILS OPEN, deliberately. If the call does not answer, every button stays.
   * A reader who then picks a dark provider gets an error page they can back
   * out of; a reader shown no way in at all because one fetch failed has been
   * told the site is broken. The first is recoverable and the second is not.
   */
  function pruneProviders() {
    var box = document.querySelector(".pk-signins");
    if (!box) return;
    fetch("/api/auth/providers", {credentials: "same-origin"})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !Array.isArray(d.providers)) return;
        box.querySelectorAll("a[data-provider]").forEach(function (a) {
          if (d.providers.indexOf(a.getAttribute("data-provider")) < 0) {
            a.remove();
          }
        });
      })
      .catch(function () {});
  }

  /**
   * Share the pool.
   *
   * THE LINK IS THE SITE, and that is the whole design rather than a first
   * version of something. Everybody plays in one pool, so there is no league
   * to join, no code to redeem and nothing to attribute — which means no
   * invite token to mint, no column to record who brought whom, and nothing
   * stored on anybody's device. What is left is the only part that was ever
   * actually hard on a phone: selecting a URL out of the address bar.
   *
   * NO COUNTER, EITHER. An anonymous "share pressed" tally would have been one
   * line and is deliberately not here. The privacy page earns its missing
   * consent banner on the claim that the only cookies are strictly necessary
   * and that nothing is measured — and a share button that reports itself is
   * measurement, however coarse. It is not worth the sentence it would cost.
   *
   * navigator.share where it exists, which on a phone is the native sheet and
   * lands in the group chat where pools actually get organised. The clipboard
   * otherwise. Both are ordinary browser APIs — no CSP change, no request, no
   * third party.
   */
  function initShare() {
    var btn = $("sharebtn");
    if (!btn) return;
    var msg = $("sharemsg");
    // Absolute and canonical, not location.href: this button is reachable
    // from a preview origin and from a URL carrying whatever query somebody
    // arrived with, and neither belongs in a link somebody else opens.
    var url = "https://big12ology.com/pools/";
    var text = "Big 12 pick'em and survivor — one line for everyone, " +
               "free, no email.";

    function say(s) {
      if (!msg) return;
      msg.textContent = s;
      // Cleared, so the confirmation does not sit there implying the last
      // press is still happening.
      setTimeout(function () { msg.textContent = ""; }, 4000);
    }

    btn.addEventListener("click", function () {
      if (navigator.share) {
        navigator.share({title: "Big 12 Pools", text: text, url: url})
          // A dismissed share sheet rejects, and that is not an error worth
          // reporting — the reader changed their mind.
          .catch(function () {});
        return;
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url)
          .then(function () { say("Link copied."); })
          .catch(function () { say(url); });
        return;
      }
      // Neither API. Show the link so it can be selected by hand rather than
      // leaving a button that does nothing.
      say(url);
    });

    show($("sharecard"), true);
  }

  function initAccount(me) {
    var form = $("nameform");
    if (!form) return;
    // First run: arrived here from the OAuth callback with nothing chosen
    // yet. The query flag is only a hint — needs_name is the fact — so a
    // reader who bookmarks the URL does not get welcomed forever.
    var first = !!me && me.needs_name;
    show($("signin"), !me);
    if (!me) pruneProviders();
    // The top of the funnel, and the only two steps of it the database cannot
    // see. Everything after a provider hands us a subject is a row in D1 and
    // is counted there instead — see tools/pool-report.sh. What is missing
    // without this is the denominator: how many people reached the page that
    // offers a sign-in and did not press anything.
    if (!me && window.B12Metrics) {
      window.B12Metrics.send("pool", "signin_shown");
      document.querySelectorAll("#signin a[href^='/api/auth/login/']")
        .forEach(function (a) {
          a.addEventListener("click", function () {
            // A navigation is about to tear the page down, so this cannot
            // wait for the ordinary end-of-visit batch. sendBeacon survives
            // the unload; that is the whole reason flush() is public.
            window.B12Metrics.send("pool", "signin_click");
            window.B12Metrics.flush();
          });
        });
    }
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
        joinNames((me.identities || []).map(function (i) {
          return providerLabel(i.provider);
        })) + ".";
      body.appendChild(p);
      var out = document.createElement("form");
      out.addEventListener("submit", function (e) {
        e.preventDefault();
        api("/api/auth/logout", {method: "POST"}).then(function () {
          location.href = "/pools/pickem/";
        }).catch(function (err) {
          // A refusal with no message is a dead button; that is how this
          // call's real failure hid for a week.
          alertMsg(explain(err));
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
  // a logo nor a color, and this is the one question on the site whose
  // answers a reader recognizes by sight before they have read them. It is
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
          var color = (teams[o.v] && teams[o.v].color) || "";
          if (color) {
            lab.style.setProperty("--tc", color);
            lab.style.setProperty("--tfg", textOn(color));
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
        // Your row in your own team's color rather than the house accent.
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

    // Two benchmarks, both in the <tfoot> because neither is a competitor,
    // and they answer different questions. The chalk asks whether you beat
    // the market. The room asks whether you beat everybody else put together.
    // A player who clears both did something; a player who clears neither has
    // an explanation available. This is the same comparison scorecard.py
    // already makes for the models on the race card, presented the same way.
    var BENCH = [
      {data: chalk, label: "The chalk",
       why: "Not a player: what taking the favorite in every game would "
          + "have scored. Nothing to do with anyone's picks."},
      {data: room, label: "The room",
       why: "Not a player: the side most people took, on every game, scored "
          + "as one card. Games split exactly down the middle are left out."},
    ].filter(function (b) { return b.data; });

    if (BENCH.length) {
      var tf = el("tfoot");
      BENCH.forEach(function (b) {
        var row = el("tr");
        row.title = b.why +
          (b.data.split ? "  " + b.data.split + " dead heat" +
                          (b.data.split === 1 ? "" : "s") + " excluded." : "");
        COLS.forEach(function (c) {
          var td = el("td", c.num ? "n" : null);
          if (c.key === "display_name") td.textContent = b.label;
          else if (c.key === "rank") td.textContent = "";
          else if (c.key === "pct") {
            td.textContent = b.data.pct == null ? "—"
              : (b.data.pct * 100).toFixed(1) + "%";
          } else td.textContent = b.data[c.key] == null ? "—" : b.data[c.key];
          row.appendChild(td);
        });
        tf.appendChild(row);
      });
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
    // The weeks the server says have a board, not a range built from a count.
    //
    // This counted from 1 to a maximum, on the reasoning that weeks are
    // 1-based. They are not: college football has a week 0, and 2026 opens
    // with one game on August 29 that the publisher numbers week-00. Counting
    // also assumed no gaps. Listing what exists is both correct and shorter.
    var list = Array.isArray(cur) ? cur.slice()
      : (typeof cur === "number" ? function () {
          var a = []; for (var i = 1; i <= cur; i++) a.push(i); return a;
        }() : []);
    list.sort(function (a, b) { return a - b; }).forEach(function (w) {
      opts.push({v: String(w), t: "Week " + w});
    });
    if (opts.length < 2) {
      var lab = sel.closest("label");
      if (lab) lab.hidden = true;
      return;
    }
    sel.textContent = "";
    opts.forEach(function (o) {
      var n = document.createElement("option");
      n.value = o.v; n.textContent = o.t;
      // "Season" is selected, because the season is what the board just
      // loaded — initBoard calls loadBoard("") before it fills this in. The
      // old line preselected the latest week instead, so the control read
      // "Week 1" above a table captioned "Season to date."
      if (o.v === "") n.selected = true;
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
      room = r.room || null;
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
      myTint = myColor(me, teams);
      boardTeams = teams;
      // The season's shape, alongside the table rather than instead of it.
      // Its own request because it is its own question, and because a board
      // that waits on a chart to draw is a board that is slower for the
      // people who only wanted the table.
      //
      // Both are needed before the room card can be drawn — its figures come
      // from the leaderboard and its weekly comparison from the history — so
      // they are joined rather than chained. Fired off first and awaited
      // second: kicking it off inside the .then() and reading `room` from it
      // was a race the table usually won and sometimes did not, which is the
      // worst kind.
      var histP = api("/api/history").catch(function () { return null; });
      return loadBoard("").then(function (r) {
        return histP.then(function (h) {
          if (h) {
            drawHistory(h, teams, me);
            drawRoomCard(h, chalk, room);
          }
          return r;
        });
      });
    }).then(function (r) {
      // How many weeks there are to choose from, which is not the same as
      // which one this response is for: the season-to-date response is for no
      // week at all, and reading r.week there left nothing to enumerate.
      if (r) fillWeeks(r.weeks != null ? r.weeks : r.week);
    });
  }

  // --------------------------------------------------------------- history

  var NS = "http://www.w3.org/2000/svg";
  function sv(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    return n;
  }

  /** #rrggbb -> "r, g, b", so a color can be reused at several alphas. */
  function rgbOf(hex) {
    var h = String(hex || "").replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    if (h.length !== 6) return null;
    return parseInt(h.slice(0, 2), 16) + ", " + parseInt(h.slice(2, 4), 16) +
           ", " + parseInt(h.slice(4, 6), 16);
  }

  /**
   * A team color dark enough to be a 2.5px line on the light theme.
   *
   * Colorado's gold and UCF's are legible as a filled chip with dark text on
   * them — which is what textOn() is for — and nearly invisible as a hairline
   * on cream. Mixed toward black only as far as it has to be, so the line is
   * still recognisably the team's.
   */
  function lineColor(hex, dark) {
    var rgb = rgbOf(hex);
    if (!rgb) return null;
    var p = rgb.split(",").map(Number);
    var lum = (0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]) / 255;
    if (!dark && lum > 0.55) {
      var k = 0.55 / lum;
      p = p.map(function (c) { return Math.round(c * k); });
    }
    return "rgb(" + p.join(",") + ")";
  }

  /**
   * The season, week by week: you against the shape of the field.
   *
   * A band rather than a line per player. At a hundred players the lines are
   * a gray mass — you can see you are above it and nothing else — where the
   * band answers the questions actually being asked: how far above the
   * middle, and is the field tightening. The leader and the room are drawn
   * because they are the two lines worth chasing.
   *
   * Hand-drawn SVG, because this section has no chart library and should not
   * acquire one for a single figure. Six paths and some text.
   */
  function drawHistory(h, teams, me) {
    var box = $("hist"), note = $("histnote");
    if (!box) return;
    var weeks = h.weeks || [];
    if (weeks.length < 2) return;      // one point is not a trend

    var dark = document.documentElement.getAttribute("data-theme") === "dark" ||
      (!document.documentElement.getAttribute("data-theme") &&
       window.matchMedia &&
       window.matchMedia("(prefers-color-scheme: dark)").matches);

    // The reader's own team colors the line, and the band is the same hue
    // behind it — so the figure is about them without a legend saying so.
    var tc = (me && me.team && teams[me.team] && teams[me.team].color) || null;
    var you = lineColor(tc, dark) || (dark ? "#3FC7CE" : "#0B6E77");
    var rgb = rgbOf(tc) || (dark ? "63, 199, 206" : "11, 110, 119");

    // Sized for the column it lives in, not the page. An SVG scales its
    // text with everything else, so a 720-wide viewBox rendered into a
    // half-width card put the axis labels at about seven pixels. At 520 the
    // box is drawn near 1:1 and 11px stays 11px.
    var W = 520, H = 300, L = 40, R = 10, T = 12, B = 26;

    // A decile needs a field to be a decile of. With twelve players the
    // "90th percentile" is the second-best player and the "10th" is the
    // second-worst — one person each, drawn as though they were a
    // distribution, and sitting exactly under the leader's line because the
    // leader IS the player next to them. Below the floor only the quartiles
    // are drawn, which are still three players in from each end.
    //
    // Decided BEFORE the axis, not after, because the axis has to be scaled
    // to what is drawn. Including p10 and p90 unconditionally reserved room
    // for a band that was never painted: a small pool whose worst player had
    // a bad opening week got a plot squashed into its top third to make space
    // for white.
    var f = h.field || [];
    var wide = f.length > 1 && f.every(function (r) { return r.n >= MIN_BAND; });

    var lo = 100, hi = 0;
    var all = [];
    f.forEach(function (r) {
      if (wide) { all.push(r.p10, r.p90); } else { all.push(r.p25, r.p75); }
    });
    [h.you, h.room, h.chalk,
     h.leader && h.leader.rows].forEach(function (rows) {
      (rows || []).forEach(function (r) { all.push(r.pct); });
    });
    all.forEach(function (v) {
      if (v == null) return;
      var p = v * 100;
      if (p < lo) lo = p;
      if (p > hi) hi = p;
    });
    if (hi <= lo) { lo = 40; hi = 60; }
    lo = Math.max(0, Math.floor((lo - 3) / 5) * 5);
    hi = Math.min(100, Math.ceil((hi + 3) / 5) * 5);

    var x = function (i) {
      return L + (weeks.length === 1 ? 0
        : i * (W - L - R) / (weeks.length - 1));
    };
    var y = function (pct) {
      return T + (H - T - B) * (1 - (pct * 100 - lo) / (hi - lo));
    };
    var idx = {};
    weeks.forEach(function (w, i) { idx[w] = i; });

    function area(topRows, botRows, key1, key2) {
      var d = [];
      topRows.forEach(function (f, i) {
        d.push((i ? "L" : "M") + x(i) + " " + y(f[key1]));
      });
      for (var j = botRows.length - 1; j >= 0; j--) {
        d.push("L" + x(j) + " " + y(botRows[j][key2]));
      }
      return d.join(" ") + " Z";
    }
    function line(rows) {
      var d = [], started = false;
      rows.forEach(function (r) {
        if (r.pct == null || idx[r.week] == null) return;
        d.push((started ? "L" : "M") + x(idx[r.week]) + " " + y(r.pct));
        started = true;
      });
      return d.join(" ");
    }

    // aria-label rather than a <title> child. A <title> is the accessible
    // name AND the browser's native tooltip, so hovering anywhere on the plot
    // popped a gray box over the middle of the chart — on top of the readout
    // it was competing with.
    var svg = sv("svg", {viewBox: "0 0 " + W + " " + H, class: "pk-hist",
                         role: "img", "aria-label":
      "Season-to-date percentage after each week, against the field."});

    // Gridlines and the axis, first, so everything else sits over them.
    var step = (hi - lo) > 30 ? 10 : 5;
    for (var g = lo; g <= hi; g += step) {
      svg.appendChild(sv("line", {x1: L, x2: W - R, y1: y(g / 100),
                                  y2: y(g / 100), class: "pk-hgrid"}));
      var lab = sv("text", {x: L - 8, y: y(g / 100) + 4, class: "pk-haxis",
                            "text-anchor": "end"});
      lab.textContent = g + "%";
      svg.appendChild(lab);
    }
    weeks.forEach(function (w, i) {
      var t = sv("text", {x: x(i), y: H - 8, class: "pk-haxis",
                          "text-anchor": "middle"});
      t.textContent = w;
      svg.appendChild(t);
    });

    if (f.length > 1) {
      if (wide) {
        svg.appendChild(sv("path", {d: area(f, f, "p90", "p10"),
          fill: "rgba(" + rgb + ", .10)"}));
      }
      svg.appendChild(sv("path", {d: area(f, f, "p75", "p25"),
        fill: "rgba(" + rgb + ", .20)"}));
      svg.appendChild(sv("path", {
        d: line(f.map(function (r) { return {week: r.week, pct: r.p50}; })),
        fill: "none", class: "pk-hmed"}));
    }

    // Dashes as well as color, so the three are told apart without it.
    if (h.room && h.room.length) {
      svg.appendChild(sv("path", {d: line(h.room), fill: "none",
                                  class: "pk-hroom"}));
    }
    if (h.chalk && h.chalk.length) {
      svg.appendChild(sv("path", {d: line(h.chalk), fill: "none",
                                  class: "pk-hchalk"}));
    }
    if (h.leader && h.leader.rows) {
      svg.appendChild(sv("path", {d: line(h.leader.rows), fill: "none",
                                  class: "pk-hlead"}));
    }
    if (h.you && h.you.length) {
      svg.appendChild(sv("path", {d: line(h.you), fill: "none", stroke: you,
                                  "stroke-width": 2.5, "stroke-linejoin":
                                  "round", "stroke-linecap": "round"}));
      h.you.forEach(function (r) {
        if (r.pct == null) return;
        svg.appendChild(sv("circle", {cx: x(idx[r.week]), cy: y(r.pct), r: 3.5,
                                      fill: you, class: "pk-hdot"}));
      });
    }

    // A rule down the hovered week, and a ring on your point in it. Without
    // one of these the readout is a box floating in the corner of a chart:
    // pinned to the top of the plot it was nowhere near the point it
    // described, and following the point it would have covered the line.
    // The rule joins the two and costs one element.
    var cross = sv("line", {y1: T, y2: H - B, class: "pk-hcross"});
    cross.setAttribute("visibility", "hidden");
    svg.appendChild(cross);
    var ring = sv("circle", {r: 6, class: "pk-hring"});
    ring.setAttribute("visibility", "hidden");
    svg.appendChild(ring);

    // One hit target per week, full height, so the tooltip is easy to reach.
    var tip = el("div", "pk-htip");
    tip.hidden = true;
    weeks.forEach(function (w, i) {
      var half = (W - L - R) / Math.max(1, weeks.length - 1) / 2;
      var hit = sv("rect", {x: x(i) - half, y: T, width: half * 2,
                            height: H - T - B, fill: "transparent"});
      var say = function () {
        // Two columns, not five sentences. Every row is a name and a
        // percentage, so as prose it was five ragged lines carrying one
        // number each and a box wider than the plot it sat on.
        var at = function (rows) {
          return (rows || []).filter(function (r) { return r.week === w; })[0];
        };
        var mine = at(h.you), fw = at(f), rm = at(h.room), ck = at(h.chalk);
        var ld = h.leader && at(h.leader.rows);

        tip.textContent = "";
        tip.appendChild(el("div", "pk-htipwk", "Week " + w));
        var grid = el("div", "pk-htipgrid");
        var row = function (cls, label, pct, extra) {
          if (pct == null) return;
          grid.appendChild(el("span", "pk-htipk " + cls, label));
          grid.appendChild(el("span", "pk-htipv",
            (100 * pct).toFixed(1) + "%"));
          grid.appendChild(el("span", "pk-htipx", extra || ""));
        };
        if (mine) row("you", "You", mine.pct,
                      mine.rank ? ordinal(mine.rank) : "");
        if (ld) row("lead", "Leader", ld.pct, h.leader.display_name || "");
        if (rm) row("room", "Room", rm.pct, "");
        if (ck) row("chalk", "Chalk", ck.pct, "");
        if (fw) row("med", "Median", fw.p50, "of " + fw.n);
        tip.appendChild(grid);
        tip.hidden = false;

        cross.setAttribute("x1", x(i));
        cross.setAttribute("x2", x(i));
        cross.setAttribute("visibility", "visible");

        // Measured against the rendered box rather than the viewBox, because
        // the SVG scales and the two only agree by accident. The card also
        // holds the legend, so vertical offsets come off the svg, not it.
        var bb = box.getBoundingClientRect();
        var sb = svg.getBoundingClientRect();
        var k = sb.width / W;
        var top0 = sb.top - bb.top;

        var anchor = mine && mine.pct != null ? y(mine.pct) : (T + (H - T - B) / 2);
        if (mine && mine.pct != null) {
          ring.setAttribute("cx", x(i));
          ring.setAttribute("cy", anchor);
          ring.setAttribute("visibility", "visible");
        } else {
          ring.setAttribute("visibility", "hidden");
        }

        var tw = tip.offsetWidth, th = tip.offsetHeight;
        tip.style.left = Math.max(2,
          Math.min(bb.width - tw - 2, x(i) * k - tw / 2)) + "px";
        // Above the point, or below it when there is no room above. Either
        // way it is beside the thing it is about, and the rule reaches it.
        var above = anchor * k - th - 14;
        tip.style.top = top0 + (above > 2 ? above : anchor * k + 16) + "px";
      };
      hit.addEventListener("mouseenter", say);
      hit.addEventListener("focus", say);
      var hide = function () {
        tip.hidden = true;
        cross.setAttribute("visibility", "hidden");
        ring.setAttribute("visibility", "hidden");
      };
      hit.addEventListener("mouseleave", hide);
      hit.addEventListener("blur", hide);
      svg.appendChild(hit);
    });

    box.textContent = "";
    var key = el("p", "pk-hkey");
    // The swatch is a real line carrying the SAME class as the one on the
    // chart, so the dash pattern cannot drift from what it stands for. It
    // did: the swatches were border-top-style, which offers solid, dashed
    // and dotted and nothing else, so the chalk's dash-dot was drawn as
    // plain dashes and the leader's long dashes as short ones. A legend that
    // is only approximately the chart is worse than none.
    function chip(cls, label, color) {
      var sp = el("span", "pk-hchip " + cls);
      if (color) sp.style.setProperty("--k", color);
      if (cls.indexOf("band") === 0) {
        sp.appendChild(el("span", "pk-hblock"));
      } else {
        var sw = sv("svg", {class: "pk-hswatch", viewBox: "0 0 20 6",
                            "aria-hidden": "true"});
        var ln = sv("line", {x1: 0, y1: 3, x2: 20, y2: 3, class: cls});
        if (color) ln.setAttribute("stroke", color);
        sw.appendChild(ln);
        sp.appendChild(sw);
      }
      sp.appendChild(document.createTextNode(label));
      key.appendChild(sp);
    }
    if (h.you && h.you.length) chip("pk-hyou", "You", you);
    // "The leader", not their display name. The table below calls its two
    // rows "The chalk" and "The room", and a legend that named a player
    // instead read as a third row of the same kind — especially here, where
    // the leader happens to be called Chalk Eater and the two were one
    // character apart. Who it is belongs in the tooltip, where it changes.
    if (h.leader) chip("pk-hlead", "The leader");
    if (h.room && h.room.length) chip("pk-hroom", "The room");
    if (h.chalk && h.chalk.length) chip("pk-hchalk", "The chalk");
    // The swatch takes the band's own color. It was the house accent while
    // the band itself is the reader's team, so the two disagreed on every
    // page where somebody had chosen one.
    chip("band", "Middle half of the field", "rgba(" + rgb + ", .20)");
    if (wide) chip("band wide", "10th to 90th", "rgba(" + rgb + ", .10)");
    box.appendChild(key);
    box.appendChild(svg);
    box.appendChild(tip);
    if (note) {
      // The largest the field ever was, not the last week's. Somebody who
      // misses the final week drops out of that week's percentiles, and
      // reading the count off the end made a twelve-player pool describe
      // itself as eleven — on a sentence whose whole job is to say how many
      // people the band is drawn from.
      var n = 0;
      f.forEach(function (r) { if (r.n > n) n = r.n; });
      note.textContent = "Season to date after each week. The band is the "
        + "field: the middle 50% of " + n + " player" + (n === 1 ? "" : "s")
        + (wide ? ", and lighter behind it the 10th to 90th." : ".");
    }
    show($("histcard"), true);
  }

  /** What the room did, as a card rather than a row. */
  function drawRoomCard(h, chalkNow, roomNow) {
    var body = $("roombody");
    if (!body || !roomNow) return;
    body.textContent = "";

    var stats = el("div", "pk-roomstats");
    function stat(label, value, cls) {
      var d = el("div", "pk-stat");
      d.appendChild(el("div", "pk-statv" + (cls ? " " + cls : ""), value));
      d.appendChild(el("div", "pk-statl", label));
      stats.appendChild(d);
    }
    stat("Record", roomNow.w + "–" + roomNow.l +
                   (roomNow.p ? "–" + roomNow.p : ""));
    stat("Against the spread", roomNow.pct == null ? "—"
      : (100 * roomNow.pct).toFixed(1) + "%");
    if (chalkNow && chalkNow.pct != null && roomNow.pct != null) {
      var d = (roomNow.pct - chalkNow.pct) * 100;
      stat("Versus the chalk", (d >= 0 ? "+" : "−") +
           Math.abs(d).toFixed(1), d >= 0 ? "up" : "down");
    }
    if (roomNow.split) {
      stat("Dead heats", String(roomNow.split));
    }
    body.appendChild(stats);

    var beat = 0, of = 0;
    (h && h.field ? h.field : []).forEach(function (fw) {
      var rm = (h.room || []).filter(function (r) { return r.week === fw.week; })[0];
      if (!rm || rm.pct == null || fw.p50 == null) return;
      of++;
      if (rm.pct > fw.p50) beat++;
    });
    var p = el("p", "note");
    p.textContent = "The side most people took, on every game, scored as one "
      + "card. Games where the picks landed exactly even are left out."
      + (of ? "  Ahead of the median player after " + beat + " of " + of
              + " weeks." : "");
    body.appendChild(p);
    show($("roomcard"), true);
  }

  // ------------------------------------------------------------------ card

  // A pick is only a story once the game has a result, so the card joins the
  // slate to your picks rather than listing either alone: what you took, the
  // number you took it at, and how it came out — including the games you left
  // blank, which is the thing a card is for noticing.
  function cardRow(g, side, teams, locked) {
    var li = el("li", "pk-cardrow");
    var st = gameStatus(g);
    var when = el("span", "pk-when pk-st-" + st.kind);
    when.appendChild(document.createTextNode(st.text));
    if (st.kind !== "time") {
      // FINAL, IN PLAY and WAITING have taken the kickoff's place, and until
      // now it survived only in a title. Title is hover, and hover is not a
      // reading. Only in this branch: while the chip still shows the time it
      // is saying it already, and twice is worse than tersely.
      var kick = fmtWhen(g.kickoff);
      when.title = "Kickoff " + kick;
      when.appendChild(el("span", "sr-only", ", kickoff " + kick));
    }
    li.appendChild(when);

    var m = el("span", "pk-cardmatch");
    ["away", "home"].forEach(function (s, i) {
      if (i) m.appendChild(el("span", "pk-at", " " + joiner(g) + " "));
      var mk2 = mark(teams, g[s], 15);
      if (mk2) m.appendChild(mk2);
      var took = s === side;
      var t = el("span", took ? "pk-took" : (side ? "pk-nottook" : null));
      t.appendChild(document.createTextNode(g[s]));
      if (took) {
        var c = (teams[g[s]] && teams[g[s]].color) || "";
        if (c) { t.style.setProperty("--tc", c); t.style.setProperty("--tfg", textOn(c)); }
        // The fill and the tick are the visible answer to "which one did I
        // take". The fill is a color and the tick is CSS generated content,
        // so between them they say nothing a screen reader can be relied on
        // to report, on the one page whose entire subject is what you
        // picked, and whose result chip then says "your pick, loss" with
        // nothing anywhere to say what the pick was.
        t.appendChild(el("span", "sr-only", ", your pick"));
      }
      m.appendChild(t);
    });
    li.appendChild(m);

    // The score, in reading order: the row names the away team first, so the
    // figures do too. A card that says WIN without saying 31-21 is withholding
    // the thing anyone actually wants to look at.
    var sc = el("span", "pk-score");
    if (hasResult(g)) {
      var said = g.away + " " + g.result.away_points + ", " +
                 g.home + " " + g.result.home_points;
      // The figures are for the eye, which reads them against a matchup one
      // column to the left. Spoken, "21 to 31" is two numbers with nothing
      // attached to them, so the ear gets the sentence and not the dash.
      var fig = el("span", null,
        g.result.away_points + "\u2013" + g.result.home_points);
      fig.setAttribute("aria-hidden", "true");
      sc.appendChild(fig);
      sc.appendChild(el("span", "sr-only", said));
      sc.title = said;
    }
    li.appendChild(sc);

    // The number for the eye, the sentence for the ear: "-8" read aloud is
    // "minus eight", of what and for whom unstated. spreadSaid already says
    // it properly for the slate and for survivor; this row is the one that
    // never got it.
    var num = el("span", "pk-num");
    num.appendChild(document.createTextNode(
      side ? spreadText(g.spread_x2, side) : "—"));
    if (side) {
      num.appendChild(el("span", "sr-only", " " + spreadSaid(g.spread_x2, side)));
    }
    li.appendChild(num);

    // The split goes before the result, because the grid places children in
    // DOM order and the result belongs in the last, narrow column. Appending
    // it after put the chip in the gauge's slot and squeezed the gauge into
    // the chip's.
    //
    // Each side's mark, its share of the room, and a bar filled to that
    // share: the question is which way the room leaned and how hard, and that
    // is a length rather than a digit. gauge.js draws it, the same way it
    // draws the one on the schedule.
    //
    // Absent below MIN_CONSENSUS, and absent entirely before the lock — the
    // server does not send the field, so a late picker cannot follow the room.
    var shown = false;
    var picked = 0;
    if (g.consensus) {
      var h = g.consensus.home || 0, a = g.consensus.away || 0, n = h + a;
      picked = n;
      if (n >= MIN_CONSENSUS) {
        li.appendChild(consensusBar(g, side, teams, h, a, n));
        shown = true;
      }
    }
    // The column exists either way, so rows stay aligned down the card
    // whether or not a given game reached the threshold.
    //
    // BUT AN EMPTY COLUMN IS NOT AN ANSWER. Below the threshold this drew
    // nothing at all, which is right about the number and wrong about the
    // reader: in a young pool every row is blank, and a blank says "broken"
    // just as readily as it says "not enough people yet". So it says which,
    // and turns the wait into a count that visibly moves.
    //
    // ONLY ONCE LOCKED, and that distinction is not decorative. Before the
    // lock the server does not send `consensus` at all — deliberately, so a
    // late picker cannot follow the room — and a game nobody has picked after
    // the lock ALSO arrives with the field absent. The two are identical from
    // here, so `locked` is the only thing that can tell them apart. Without
    // that check an unlocked slate would advertise "0 of 10 have picked",
    // which is both wrong and the exact number the lock exists to withhold.
    if (!shown) {
      var gap = el("span", "pk-split pk-splitwait");
      if (locked) {
        gap.textContent = picked + " of " + MIN_CONSENSUS;
        gap.title = "The room's split appears once " + MIN_CONSENSUS +
          " people have picked this game. " + picked + " so far.";
      }
      li.appendChild(gap);
    }

    var res = resultChip(g, side, locked, "card");
    if (res) li.appendChild(res);
    else li.appendChild(el("span", "pk-res pk-res-none", ""));
    return li;
  }

  function consensusBar(g, side, teams, h, a, n) {
    var hp = Math.round(100 * h / n), ap = 100 - hp;
    // gauge.js draws it, and the schedule section calls the same function, so
    // the row on your card and the line on a slate card cannot say the same
    // number two different ways. What stays here is what is true only of this
    // page: the marks come from teams.json rather than from the markup, the
    // neutral is this section's own token, and one of the two sides is yours.
    //
    // Your side in weight rather than hue, because the crowd is not a verdict
    // and coloring it would make it look like one.
    return window.B12GAUGE.build(
      {pct: ap, name: g.away, mark: mark(teams, g.away, 13),
       color: (teams[g.away] && teams[g.away].color) || "var(--tc-none)",
       cls: side === "away" ? "pk-mine" : ""},
      {pct: hp, name: g.home, mark: mark(teams, g.home, 13),
       color: (teams[g.home] && teams[g.home].color) || "var(--tc-none)",
       cls: side === "home" ? "pk-mine" : ""},
      {cards: n});
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
      a.href = "/pools/account.html";
      so.appendChild(a);
      so.appendChild(document.createTextNode(" to see your card."));
      wrap.appendChild(so);
      return;
    }
    var teamsP = fetch("/pools/teams.json")
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
      // WHICH WEEKS EXIST, asked rather than assumed.
      //
      // This counted `for (w = 1; w <= s.week; w++)` and called it "1-based,
      // as published". College football has a week 0 — this season opens with
      // one — so the count was wrong at both ends: in week 0 the loop never
      // ran and the control stayed empty, and in week 1 it offered a single
      // option and hid itself. A player could not look back at a card they
      // had played, which is most of what the page is for once a week is
      // graded.
      //
      // The leaderboard knows which weeks have been scored; the slate knows
      // which one is current. Their union is exactly the set worth offering,
      // and it needs no opinion about where the numbering starts.
      return api("/api/leaderboard").catch(function () { return {}; })
        .then(function (b) {
          var weeks = (b && b.weeks ? b.weeks.slice() : []);
          if (weeks.indexOf(s.week) < 0) weeks.push(s.week);
          weeks.sort(function (a, c) { return a - c; });

          weeks.forEach(function (w) {
            var o = document.createElement("option");
            o.value = String(w);
            o.textContent = "Week " + w;
            if (w === s.week) o.selected = true;
            sel.appendChild(o);
          });
          var lab = sel.closest ? sel.closest("label") : null;
          // One option is not a choice, and a control that offers one is a
          // control that lies about being one.
          if (weeks.length > 1 && lab) lab.hidden = false;
          sel.addEventListener("change", function () { draw(sel.value); });
        });
    }).catch(function (err) {
      if (err.status === 404) slateOpensNote(note, "pickem");
      else note.textContent = explain(err);
    });
  }

  function ordinal(n) {
    var s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  // -------------------------------------------------------------- survivor

  /* The survivor page. Same bones as the slate — hidden radio, styled label,
   * saved the moment it changes — but one pick instead of fifteen, so there
   * is no debounce and no card-replace: every change is its own PUT and the
   * server's answer is repainted at once. The pickem's state (picks, LOCKED)
   * is deliberately untouched; the two games share a slate and nothing else.
   */
  var svState = null;   // {slate, mine, teams} once loaded, for repaints
  var SV_PREVIEW = false;  // the look-ahead week: rendered, never pickable
  var svTick = null;    // the repaint scheduled for the next kickoff
  var SV_LIVE = null;   // the API's state, kept so the selector can return

  /* The survivor season, browsable, same shape as the slate's selector but
     over the weeks the pool actually runs (the index's survivor_weeks). */
  function svWeekSelector(current) {
    var sel = $("svwk");
    if (!sel) return;
    previewIndex().then(function (ix) {
      var liveW = SV_LIVE && SV_LIVE.slate ? SV_LIVE.slate.week : null;
      var wks = ((ix && ix.survivor_weeks) || []).filter(function (w) {
        return liveW == null || w > liveW;
      });
      var opts = (liveW != null ? [liveW] : []).concat(wks);
      if (opts.length < 2) return;
      sel.textContent = "";
      opts.forEach(function (w) {
        var o = document.createElement("option");
        o.value = String(w);
        o.textContent = String(w) + (w === liveW ? " (open)" : "");
        sel.appendChild(o);
      });
      sel.value = String(current);
      var lab = sel.closest ? sel.closest("label") : null;
      if (lab) lab.hidden = false;
    });
  }

  function svSpent(mine, week) {
    // Teams already burned: any pick from another week whose outcome is not
    // void. An ungraded pick counts — it is presumed live — which is also
    // exactly what the server's trigger will say if the client gets it wrong.
    var spent = {};
    ((mine && mine.used) || []).forEach(function (u) {
      if (u.week !== week && u.outcome !== "void") {
        spent[u.team] = { week: u.week, chalk: false };
      }
    });
    // The late-joiner handicap: the chalk of every week before you entered is
    // spent for you. Marked apart from your own picks because "already used
    // in week 3" would be a lie to somebody who was not here in week 3.
    ((mine && mine.burned) || []).forEach(function (b) {
      if (!spent[b.team]) spent[b.team] = { week: b.week, chalk: true };
    });
    return spent;
  }

  /**
   * What joining late cost, said once and above the slate.
   *
   * Only for players it applies to, and only while it is news: somebody who
   * entered in week one has nothing to read here, and a permanent banner is
   * a banner nobody reads.
   */
  function svHandicapNote(mine, week) {
    var box = $("svhandicap");
    if (!box) return;
    var burned = (mine && mine.burned) || [];
    if (!SIGNED_IN || !burned.length) { show(box, false); return; }

    box.textContent = "";
    var p = el("p");
    p.appendChild(document.createTextNode(
      "You joined in week " + mine.entered_week + ", so the chalk of the "
      + (burned.length === 1 ? "week" : burned.length + " weeks")
      + " before that is already spent: "));
    burned.forEach(function (b, i) {
      if (i) p.appendChild(document.createTextNode(i === burned.length - 1
        ? " and " : ", "));
      p.appendChild(el("b", null, b.team));
    });
    p.appendChild(document.createTextNode("."));
    box.appendChild(p);

    if (mine.ranked === false) {
      box.appendChild(el("p", "note",
        "Entry after week " + mine.ranked_entry_by + " plays outside the "
        + "leaderboard — your run is shown, but not in the running for the "
        + "season."));
    }
    show(box, true);
  }

  /** Whether this side of this game is one the survivor pool will take. */
  function svInConference(g, side) {
    // Absent rather than "both" when the slate predates the field: unknown
    // has to read as unpickable, or a stale row becomes a free pick.
    if (!g.b12) return false;
    return g.b12 === "both" || g.b12 === side;
  }

  function svGameRow(g, mine, teams, spent, disabled) {
    var fs = el("fieldset", "pk-slate-game");
    fs.dataset.gid = g.game_id;

    // PER GAME, not per week. Survivor picks one team in one game, so a game
    // that has not kicked off is still open however long ago the week's
    // opener started — the pick'em's single deadline was only ever about
    // keeping one card fair against itself. A row closes when its own game
    // does.
    if (!disabled && g.kickoff_at &&
        g.kickoff_at * 1000 <= serverNow()) {
      disabled = true;
      fs.className += " pk-started";
    }

    var lg = el("legend", "sr-only");
    lg.textContent = g.away + " " + joiner(g) + " " + g.home + ", " +
      fmtWhen(g.kickoff) +
      " — pick a team to win the game outright";
    fs.appendChild(lg);

    var t = el("time", "pk-when", g.unpickable === "kickoff_tbd"
      ? shortDate(g.kickoff) : shortWhen(g.kickoff));
    t.setAttribute("datetime", g.kickoff);
    t.setAttribute("aria-hidden", "true");
    fs.appendChild(t);

    var sides = el("div", "pk-sides");
    ["away", "home"].forEach(function (side, i) {
      if (i === 1) sides.appendChild(el("span", "pk-at", joiner(g)));
      var team = g[side];
      var id = "sv" + g.game_id + "-" + side;
      var input = document.createElement("input");
      input.type = "radio";
      input.className = "sr-only";
      input.id = id;
      input.name = "sv";
      input.value = g.game_id + "|" + team;
      if (mine && mine.pick && mine.pick.team === team &&
          mine.pick.game_id === g.game_id) input.checked = true;
      var sp = spent[team];
      // Conference teams only. The slate carries every game a Big 12 team
      // plays, so the visitors are on the board — but a team you can spend
      // once and who appears once is not a cost, and survivor is a game about
      // what a pick costs you later.
      var outside = !svInConference(g, side);
      if (disabled || sp || outside) input.disabled = true;

      var lab = el("label", "pk-side" +
        (sp || outside ? " pk-svspent" : ""));
      lab.setAttribute("for", id);
      var color = (teams[team] && teams[team].color) || "";
      if (color) {
        lab.style.setProperty("--tc", color);
        lab.style.setProperty("--tfg", textOn(color));
      }
      var mk = mark(teams, team, 18);
      if (mk) lab.appendChild(mk);
      var nm = el("span", "pk-tname", team);
      nm.title = team;
      lab.appendChild(nm);
      if (outside) {
        lab.appendChild(el("span", "pk-num", "not Big 12"));
        lab.appendChild(el("span", "sr-only",
          " — not a Big 12 team, so not pickable here"));
      } else if (sp) {
        // Why this one is closed, in the slot the spread would use. The week
        // number is the useful half: it says where to look on your run.
        lab.appendChild(el("span", "pk-num",
          sp.chalk ? "chalk" : "wk " + sp.week));
        lab.appendChild(el("span", "sr-only",
          sp.chalk
            ? " — the chalk of week " + sp.week + ", spent before you joined"
            : " — already used in week " + sp.week));
      } else if (g.spread_x2 != null) {
        // The line, as advice rather than as the bet: survivor is straight
        // up, so the number is information, not the game — at chip weight it
        // read as the thing to optimize. It recedes (.pk-vegas) and names
        // whose opinion it is. The pick'em's rows keep their weight; there
        // the number IS the game. On the look-ahead the same aside carries
        // the ~, today's market rather than the frozen advice.
        var vg = el("span", "pk-num pk-vegas");
        vg.appendChild(document.createTextNode("Vegas says " +
          (SV_PREVIEW ? "est " : "") + spreadText(g.spread_x2, side)));
        lab.appendChild(vg);
        lab.appendChild(el("span", "sr-only", " " + spreadSaid(g.spread_x2, side)));
      }
      sides.appendChild(input);
      sides.appendChild(lab);
    });
    fs.appendChild(sides);
    // The row's door to its schedule page, live and look-ahead alike; see
    // the pick'em gameRow's note.
    if (g.preview) {
      var peek = el("a", "pk-peek", "preview →");
      peek.href = g.preview;
      fs.appendChild(peek);
    }
    return fs;
  }

  function svOutcomeChip(outcome) {
    // Wins and losses take the pickem's chips; the two quiet states get the
    // same muted treatment the card gives games still in flight.
    if (outcome === "win")  return el("span", "pk-res win", "WIN");
    if (outcome === "loss") return el("span", "pk-res loss", "OUT");
    if (outcome === "void") return el("span", "pk-res void", "VOID");
    // Not an outcome the server sends — svDrawRun synthesises it for the week
    // that ended a run by silence, which has no pick and so has no row of its
    // own in `used`.
    if (outcome === "missed") return el("span", "pk-res loss", "MISSED");
    return el("span", "pk-res pending", "OPEN");
  }

  /** How a run ended, in one sentence, used everywhere that has to say it. */
  function svEndLine(s) {
    var when = s.out_week == null ? "a week" : "week " + s.out_week;
    return "Your run ended in " + when + (s.out_reason === "missed"
      ? " — no pick before the lock." : " — your team lost.");
  }

  function svStandingText(mine, board) {
    var s = mine && mine.standing;
    // survivor_board.wins is NOT NULL and out_week is set whenever a run is
    // over, so neither of these should ever be missing. They are coerced
    // anyway because the cost is a token and the failure is a sentence
    // reading "null wins on the run" to somebody whose season just ended.
    var wins = s && typeof s.wins === "number" ? s.wins : 0;
    var plural = function (n) { return n === 1 ? " win" : " wins"; };
    if (s && !s.alive) {
      var when = s.out_week == null ? "a week" : "week " + s.out_week;
      // NOT "week 2 locked without your pick", which is what this said and
      // which reads as a lock — the one word the rest of this page uses for a
      // week that has closed on everybody. A reader who is out and a reader
      // whose week has closed were being told the same thing in the same
      // vocabulary. Elimination gets its own words.
      return "Out — " + (s.out_reason === "missed"
        ? "no pick in " + when
        : "your " + when + " team lost") +
        ". " + wins + plural(wins) + " on the run.";
    }
    var bits = [];
    if (s) {
      bits.push("Alive — " + wins + plural(wins));
      if (s.rank) bits.push(ordinal(s.rank) + " in the pool");
    } else if (mine && mine.used && mine.used.length) {
      bits.push("In — first week still to be graded");
    }
    if (board && board.entrants) {
      bits.push(board.alive + " of " + board.entrants + " still alive");
    }
    return bits.join("  ·  ");
  }

  function svSave(gameId, team, week) {
    api("/api/survivor/pick", { method: "PUT",
      body: { week: week, game_id: gameId, team: team } })
      .then(function (r) {
        status(team
          ? "Saved — " + r.pick.team + " to win."
          : "Pick withdrawn.");
        return api("/api/survivor").then(function (mine) {
          svState.mine = mine;
          svRepaint();
        });
      })
      .catch(function (err) {
        status("");
        var m = err.data && err.data.error;
        if (m === "not_in_conference") {
          alertMsg("Survivor is the sixteen Big 12 teams. Their opponents "
                   + "are on the card, but you cannot spend one.");
        } else if (m === "team_used") {
          alertMsg("You already used that team. Voids give a team back; " +
                   "wins and losses do not.");
        } else if (m === "team_spent_before_entry") {
          alertMsg("That team is the chalk of a week you were not here for, "
                   + "so it was spent when you joined in week "
                   + (err.data.entered_week) + ".");
        } else if (m === "join_closed") {
          alertMsg("Too little of the season is left to start a run — the "
                   + "handicap would leave you almost nothing to pick.");
        } else if (m === "eliminated") {
          alertMsg("Your run is over — the pool is watch-only from here.");
        } else if (err.status === 409) {
          alertMsg("The week locked before that saved. Your pick as it " +
                   "stood at kickoff is shown.");
          if (err.data && "pick" in err.data && svState.mine) {
            svState.mine.pick = err.data.pick;
            svState.mine.locked = true;
          }
        } else {
          alertMsg(explain(err));
        }
        svRepaint();
      });
  }

  function svRepaint() {
    var st = svState;
    if (!st) return;
    var wrap = $("svslate"), note = $("svnote");
    var mine = st.mine, slate = st.slate;
    var week = slate.week;
    // mine.locked is survivor's deadline — every game started — and
    // slate.locked is the card's, the first kickoff. ORing them shut the
    // survivor picker the moment the week's opener began, which is the whole
    // thing per-game locking exists to stop. The slate's answer is only used
    // when there is no survivor state to read, i.e. signed out.
    var locked = mine ? !!mine.locked : !!slate.locked;
    var dead = !!(mine && mine.standing && !mine.standing.alive);
    var spent = svSpent(mine, week);

    svHandicapNote(mine, week);
    // Redrawn on every repaint, not once at load. This week's pick is part of
    // `used`, so changing it changes the roster — and the roster was built in
    // the init callback, which runs once. Picking a different team left the
    // old one struck through and the new one looking available.
    svDrawRoster(mine, st.teams);
    svDrawRun(mine, st.teams);
    if (st.board) svDrawField(st.board, st.teams, mine && mine.user_id);

    // A week the pool does not run. The server refuses a pick on it, so the
    // page has to say why rather than offer a picker that fails: with one
    // legal team there is no choice to make, and a player who does not know
    // that sitting it out is free will spend a team to avoid a miss that was
    // never coming. Entry is your first pick, so a skipped week simply is not
    // part of your run.
    var noContest = !!(mine && mine.no_contest);

    note.textContent = noContest
      ? "Week " + week + " is not a survivor week — only " +
        (mine.pickable_teams === 1 ? "one Big 12 team is"
                                   : mine.pickable_teams + " Big 12 teams are") +
        " playing, so there is no choice to make. Sitting it out costs you " +
        "nothing: you enter the pool with your first pick, and the weeks " +
        "before it never happened for you."
      : locked
        ? "Week " + week + " is locked."
        : dead
          ? svEndLine(mine.standing) +
            " The board keeps score without you now."
          : "One team, to win the game — not to cover. Pick by the lock.";

    wrap.textContent = "";
    var games = inPlayOrder(slate.games).filter(function (g) {
      // A row nobody can pick has no place on a live picker. On the
      // look-ahead nobody can pick anything, so a game whose kickoff is
      // merely unannounced is still the content — a far-out week is all
      // such games, and dropping them showed "no games" about a full week.
      return !g.unpickable ||
             (SV_PREVIEW && g.unpickable === "kickoff_tbd");
    });
    if (!games.length) {
      wrap.appendChild(el("p", "note", "No games this week."));
      return;
    }
    var disabled = SV_PREVIEW || locked || dead || noContest || !SIGNED_IN;
    games.forEach(function (g) {
      wrap.appendChild(svGameRow(g, mine, st.teams, spent, disabled));
    });
    var form = $("svform");
    // pk-svdead on top of pk-locked, not instead of it. The rows still have to
    // close the way any closed row closes; the watermark says WHY this one is
    // closed for you and not for the pool.
    form.className = (disabled ? "pk-locked" + (SIGNED_IN ? "" : " pk-readonly")
                               : "") + (dead ? " pk-svdead" : "");

    // The one control the slate does not have: a pick can be taken back
    // outright, because unlike a card a single withdrawn pick is a state a
    // player may genuinely want — sitting a week out is an elimination, so
    // this is really "I will choose again before the lock".
    var old = $("svwithdraw");
    if (old) old.remove();
    if (!disabled && mine && mine.pick) {
      var btn = el("button", "wbtn", "Withdraw this week’s pick");
      btn.type = "button";
      btn.id = "svwithdraw";
      btn.addEventListener("click", function () {
        api("/api/survivor/pick", { method: "PUT",
          body: { week: week, team: null } })
          .then(function () {
            status("Pick withdrawn. The week still needs one before the lock.");
            return api("/api/survivor").then(function (m2) {
              svState.mine = m2;
              svRepaint();
            });
          })
          .catch(function (err) { alertMsg(explain(err)); });
      });
      form.appendChild(btn);
    }

    // The page keeps itself honest between saves: survivor closes per game,
    // so each upcoming kickoff gets a repaint moment and a row greys the
    // second its game starts — by the server's clock — instead of when a
    // save bounces off the trigger. One timer, always for the nearest
    // boundary; the repaint it fires schedules the next. Not on the
    // look-ahead, where everything is already shut.
    if (svTick) { clearTimeout(svTick); svTick = null; }
    if (!SV_PREVIEW) {
      var nowMs = serverNow();
      var next = Infinity;
      (st.slate.games || []).forEach(function (g) {
        var k = (g.kickoff_at || 0) * 1000;
        if (k > nowMs && k < next) next = k;
      });
      if (next < nowMs + 8 * 86400000) {
        svTick = setTimeout(svRepaint, next - nowMs + 1000);
      }
    }
  }

  /**
   * Your run, week by week — including the week that ended it.
   *
   * `used` is picks and nothing else, so a run killed by MISSING a week had
   * no row for the week that killed it: the list stepped from week 1 to week
   * 3 and read like a run still going. Three things fix that — the missed
   * week gets a row of its own, the ending is stated above the list, and any
   * pick made after the run was over is marked as not counting. The server
   * refuses those now (api.js returns 409 `eliminated`), but rows written
   * before that check existed still have to render honestly.
   *
   * Drawn from svRepaint rather than once at load, for the same reason the
   * roster is: this week's pick is part of the run, so changing it changes
   * what belongs here.
   */
  function svDrawRun(mine, teams) {
    var ul = $("svused"), card = $("svusedcard"), end = $("svrunend");
    if (!ul) return;
    var used = (mine && mine.used) || [];
    var s = (mine && mine.standing) || null;
    var out = s && !s.alive ? s.out_week : null;

    if (!used.length && out == null) { show(card, false); return; }

    var rows = used.slice();
    // Only a miss is missing a row. A run that ended on a loss already has
    // one — the pick that lost is a pick, and it is already in `used`.
    if (out != null && s.out_reason === "missed" &&
        !used.some(function (u) { return u.week === out; })) {
      rows.push({ week: out, team: null, outcome: "missed" });
    }
    rows.sort(function (a, b) { return a.week - b.week; });

    ul.textContent = "";
    rows.forEach(function (u) {
      var moot = out != null && u.week > out;
      var li = el("li", "pk-svrunrow" + (moot ? " pk-svmoot" : ""));
      li.appendChild(el("span", "pk-when", "Wk " + u.week));
      if (u.team == null) {
        li.appendChild(el("span", "pk-svnopick", "no pick"));
      } else {
        var mk = mark(teams, u.team, 15);
        if (mk) li.appendChild(mk);
        li.appendChild(el("span", null, u.team));
      }
      li.appendChild(moot ? el("span", "pk-res void", "N/A")
                          : svOutcomeChip(u.outcome));
      if (moot) {
        li.appendChild(el("span", "sr-only",
          " — picked after your run had ended, so it does not count"));
      }
      ul.appendChild(li);
    });

    if (end) {
      end.textContent = out == null ? "" : svEndLine(s);
      show(end, out != null);
    }
    show(card, true);
  }

  /**
   * All sixteen, and which are gone.
   *
   * A survivor player's real question between weeks is "who have I got left",
   * and until now the only way to answer it was to read back through Your Run
   * and remember which sixteen teams the conference has. The picker greys a
   * spent team out, but only for the games on this week's card — a team you
   * spent in September and who is on a bye today appeared nowhere at all.
   *
   * Three states, and the two ways of losing a team are shown apart because
   * they are not the same fact: `spent` is a pick you made, `chalk` is the
   * handicap charged for joining late, which nobody chose. The rest are yours.
   */
  /**
   * A row of marks: what has been spent, and on your own roster what is left.
   *
   * SPENT FIRST AND IN ORDER, because a survivor roster is read as a history
   * and then as an inventory — "I have had these, so I still have those". A
   * plain alphabetical list of sixteen answers neither question quickly.
   *
   * TWO SHAPES, and which one you want is a question about whose row it is.
   *
   *   opts.named       all sixteen, faded where spent, each one captioned
   *                    with the week it went. Your own roster, where the
   *                    teams you still hold are a decision you are about to
   *                    make, so removing them would remove the point.
   *
   *   opts.spentOnly   what this run has used up, and nothing else. Somebody
   *                    else's row, where the leftovers are not your decision
   *                    and eleven extra logos are eleven things to look past.
   *
   * The fade only ever distinguishes something in the first shape. In the
   * second every mark is spent, so fading them would separate nothing and
   * spend all of the contrast. That is why that column is not faded, and
   * why it must not silently become the first shape again.
   *
   * A fade is also not a label. `named` earns the right to fade because it
   * writes the week beside each mark; `spentOnly` earns it by not needing to.
   * Marks carry alt="", so a column distinguished by opacity alone says
   * nothing at all to a screen reader.
   */
  function teamMarks(usedTeams, teams, size, opts) {
    opts = typeof opts === "object" && opts ? opts : {named: !!opts};
    var ul = document.createDocumentFragment();
    var spent = {};
    usedTeams.forEach(function (u) { spent[u.team || u] = u; });

    var all = Object.keys(teams).filter(function (t) { return teams[t].b12; })
      .sort();
    var rest = all.filter(function (t) { return !spent[t]; });
    // Spent-only keeps every team it was handed, including one the build has
    // never heard of: this column is somebody's record, and a name we cannot
    // draw a mark for still belongs in it as an abbreviation.
    var order = opts.spentOnly
      ? usedTeams.map(function (u) { return u.team || u; })
      : usedTeams.map(function (u) { return u.team || u; })
          .filter(function (t) { return teams[t]; })
          .concat(rest);

    order.forEach(function (t) {
      var li = el("li", "pk-mk" +
        (spent[t] && !opts.spentOnly ? " pk-mkspent" : ""));
      var mk = mark(teams, t, size || 22);
      if (mk) li.appendChild(mk);
      else li.appendChild(el("span", "pk-mkabbr", (teams[t] || {}).abbr || t));
      // The caption, drawn in one shape and spoken in the other. It is the
      // same fact either way, so it is written either way: a mark carries
      // alt="" everywhere on this site, and a column of them with the wording
      // only in a title is a column that says nothing at all to a screen
      // reader. Title is hover, and hover is not a reading.
      var u = spent[t], wk = u && u.week != null ? u.week : null;
      if (opts.named) {
        li.appendChild(el("span", "pk-mkname", t));
        var when = wk != null ? "wk " + wk : (u ? "chalk" : "");
        if (when) li.appendChild(el("span", "pk-mkwhen", when));
      } else if (opts.spentOnly) {
        // Spelled out rather than abbreviated. "wk 5" is a label to glance
        // at; read aloud it is two fragments, and the visible shape is not
        // there to explain it.
        li.appendChild(el("span", "sr-only",
          t + (wk != null ? ", week " + wk : ", chalk")));
      }
      // Kept for the sighted reader hovering a 16px logo they cannot place.
      // It is no longer the only wording on the element.
      li.title = wk != null ? t + " — spent in week " + wk
        : (u ? t + " — spent" : t + " — available");
      ul.appendChild(li);
    });
    return { frag: ul, left: rest.length, total: all.length };
  }

  /** Your own sixteen. */
  function svDrawRoster(mine, teams) {
    var card = $("svrostercard"), ul = $("svroster"), note = $("svrosternote");
    if (!ul || !mine || !teams) return;
    var used = (mine.used || []).slice()
      .sort(function (a, b) { return a.week - b.week; });
    (mine.burned || []).forEach(function (b) {
      // Chalk charged for joining late. Spent, but not by you.
      used.push({ team: b.team, week: null });
    });
    ul.textContent = "";
    ul.className = "pk-roster pk-rostercol";
    var r = teamMarks(used, teams, 26, {named: true});
    ul.appendChild(r.frag);
    if (note) {
      note.textContent = r.left + " of " + r.total + " still yours" +
        ((mine.burned || []).length
          ? " — the chalk of the weeks before you joined is already spent."
          : ".");
    }
    show(card, true);
  }

  /**
   * The same row, once per opponent.
   *
   * Every pool that runs this game publishes it, and it is what late-season
   * decisions are made of: a rival who has burned the good teams is a rival
   * whose remaining weeks are hard. The API sends only closed weeks, so this
   * cannot show what anybody is doing right now.
   */
  function svDrawField(board, teams, meId) {
    var card = $("svfieldcard"), ul = $("svfield"), note = $("svfieldnote");
    if (!ul || !board || !board.rows || !teams) return;
    var rows = board.rows.filter(function (r) { return r.user_id !== meId; });
    if (!rows.length) return;

    ul.textContent = "";
    rows.forEach(function (r) {
      var li = el("li", "pk-fieldrow" + (r.alive ? "" : " pk-fieldout"));
      var who = el("span", "pk-fieldname", r.display_name || "—");
      li.appendChild(who);
      // What this run has spent, and only that. It used to draw all sixteen
      // with the spent ones faded, which is the shape your OWN roster wants:
      // there the teams still standing are the decision in front of you. On
      // somebody else's row they are not, so eleven full-strength logos were
      // eleven things to look past to find the five that mattered, and the
      // five that mattered were the faded ones, which is backwards.
      // The Board's spent column already answered this the same way.
      var marks = el("ul", "pk-roster pk-rostersm");
      marks.appendChild(teamMarks(r.used || [], teams, 18,
                                  {spentOnly: true}).frag);
      li.appendChild(marks);
      if (!r.alive) {
        li.appendChild(el("span", "pk-when",
          "out wk " + r.out_week + (r.out_reason === "missed" ? " — no pick" : "")));
      }
      ul.appendChild(li);
    });
    if (note) {
      note.textContent = rows.length + " other run" +
        (rows.length === 1 ? "" : "s") +
        ". Only weeks that have finished are shown.";
    }
    show(card, true);
  }

  function svDrawBoard(board, teams, me, opts) {
    opts = opts || {};
    var tbl = $(opts.into || "svboard"), note = $(opts.note || "svboardnote");
    if (!tbl) return;
    tbl.textContent = "";
    if (!board || !board.rows || !board.rows.length) {
      note.textContent = "Nobody has a graded week yet. Runs appear here "
        + "once their first week is scored.";
      return;
    }
    if (note && !opts.note) {
      // NAMES ITS OWN POPULATION. This renderer is handed a SUBSET on the pool
      // page — the ranked entrants — while the summary card above it counts
      // everybody, and both used to say "N of M runs still alive". With one
      // late entrant alive, the page read "1 still alive" at the top and "0 of
      // 10 runs still alive" directly beneath it, which looks like the site
      // contradicting itself rather than two different questions.
      note.textContent = board.alive + " of " + board.entrants +
        (board.entrants === 1 ? " run" : " runs") +
        " on the leaderboard still alive.";
    }

    var showPicks = board.rows.some(function (r) { return r.pick; });
    var thead = el("thead"), tr = el("tr");
    var cols = opts.norank ? ["Player", "W", "Run"] : ["#", "Player", "W", "Run"];
    // Which teams each run has burned, beside the run itself. It lived in its
    // own card on the pick page, which put the standings and the thing the
    // standings are made of on two different screens.
    var showSpent = board.rows.some(function (r) { return (r.used || []).length; });
    if (showSpent) cols.push("Spent");
    if (showPicks) cols.push("This week");
    cols.forEach(function (c, i) {
      tr.appendChild(el("th", i === 0 || c === "W" ? "n" : null, c));
    });
    thead.appendChild(tr);
    tbl.appendChild(thead);

    var tb = el("tbody");
    board.rows.forEach(function (r) {
      var tr2 = el("tr");
      if (me && r.user_id === me.user_id) {
        tr2.className = "you";
        if (myTint) tr2.style.setProperty("--you", myTint);
      }
      if (!r.alive) tr2.className += " pk-svout";
      if (!opts.norank) tr2.appendChild(el("td", "n", r.rank));

      var td = el("td");
      var mk = mark(teams, r.team, 15);
      if (mk) td.appendChild(mk);
      else if (r.team) td.appendChild(el("span", "pk-markgap"));
      td.appendChild(document.createTextNode(r.display_name || "—"));
      tr2.appendChild(td);

      tr2.appendChild(el("td", "n", r.wins));

      // The week a run ended is half the fact. The team that ended it is the
      // half people actually talk about, so it goes in the cell with its own
      // mark rather than being left to the graveyard below.
      // The flex box goes INSIDE the cell, not on it. A td that is itself a
      // flex container drops out of the table layout algorithm and takes the
      // column's width with it — the row rules stop at the last real cell and
      // the contents hang off the side of the card.
      var rd = el("td");
      // NOT pk-svrun, which is the <ul> on the pick page. Both were styled
      // under that one name and the badge's rule came second, so the pick
      // page's run — a list, one week per line — was being laid out as an
      // inline-flex nowrap strip: every week of the season on a single line,
      // running off the card. One class, two elements, and the loser was the
      // one that did not share a stylesheet neighbourhood with its rule.
      var run = el("span", "pk-svstate");
      if (r.alive) {
        run.appendChild(el("span", "pk-svalive", "Alive"));
      } else {
        run.appendChild(el("span", "pk-svout", "Out wk " + r.out_week));
        if (r.out_reason === "missed") {
          run.appendChild(el("span", "pk-svwhy", "no pick"));
        } else if (r.out_team) {
          var omk = mark(teams, r.out_team, 14);
          if (omk) run.appendChild(omk);
          run.appendChild(el("span", "pk-svwhy", r.out_team));
        }
      }
      rd.appendChild(run);
      tr2.appendChild(rd);

      if (showSpent) {
        // Spent first and in order, then nothing — the leftovers belong on
        // your own roster, where they are a decision. Here the question is
        // only what this run has already used up.
        var sd = el("td");
        var sul = el("ul", "pk-roster pk-rostersm");
        // This was the same list built by hand, down to the reason it is not
        // faded. teamMarks does it now, under the name for what it is.
        sul.appendChild(teamMarks(r.used || [], teams, 16,
                                  {spentOnly: true}).frag);
        sd.appendChild(sul);
        tr2.appendChild(sd);
      }

      if (showPicks) {
        var pd = el("td");
        if (r.pick) {
          var pmk = mark(teams, r.pick.team, 15);
          if (pmk) pd.appendChild(pmk);
          pd.appendChild(document.createTextNode(r.pick.team));
          if (r.pick.outcome) {
            pd.appendChild(document.createTextNode(" "));
            pd.appendChild(svOutcomeChip(r.pick.outcome));
          }
        } else {
          pd.textContent = "—";
        }
        tr2.appendChild(pd);
      }
      tb.appendChild(tr2);
    });
    tbl.appendChild(tb);
  }

  /**
   * The pool as a page of its own: where it stands, the full board, and what
   * ended the runs that ended.
   *
   * Shares svDrawBoard with the picker page — same ids, same table — so the
   * standings cannot say two different things in two places. What is extra
   * here is the summary above it and the graveyard below.
   */
  function svDrawSummary(board) {
    var box = $("svsummary");
    if (!box || !board) return;
    box.textContent = "";
    var stats = el("div", "pk-roomstats");
    function stat(label, value, cls) {
      var d = el("div", "pk-stat");
      d.appendChild(el("div", "pk-statv" + (cls ? " " + cls : ""), value));
      d.appendChild(el("div", "pk-statl", label));
      stats.appendChild(d);
    }
    stat("Still alive", String(board.alive), board.alive ? "up" : "down");
    stat("Entrants", String(board.entrants));
    var outs = board.entrants - board.alive;
    if (outs) stat("Runs ended", String(outs));
    if (board.missed) stat("By missing a week", String(board.missed));
    if (board.week != null) stat("Week", String(board.week));
    box.appendChild(stats);

    // The number worth saying out loud, and the one a survivor pool is
    // actually about: how thin it has got.
    if (board.entrants) {
      var pct = Math.round(100 * board.alive / board.entrants);
      var line = pct + "% of the field is still in it" +
        (board.alive === 1 ? " — one run left." : ".");
      if (board.unranked) {
        line += " " + board.unranked +
          (board.unranked === 1 ? " run" : " runs") +
          " started after week " + board.ranked_entry_by +
          " and are playing outside the leaderboard.";
      }
      box.appendChild(el("p", "note", line));
    }
    show($("svsumcard"), true);
  }

  /** The same board object with a subset of its rows. */
  function svSplit(board, keep) {
    var rows = (board && board.rows ? board.rows : []).filter(keep);
    var alive = 0;
    rows.forEach(function (r) { if (r.alive) alive++; });
    var out = {};
    for (var k in board) if (board.hasOwnProperty(k)) out[k] = board[k];
    out.rows = rows;
    out.entrants = rows.length;
    out.alive = alive;
    return out;
  }

  function svDrawGraveyard(board, teams) {
    var box = $("svgrave");
    if (!box || !board || !board.graveyard || !board.graveyard.length) return;
    box.textContent = "";
    var ul = el("ul", "pk-svgrave");
    board.graveyard.forEach(function (g) {
      var li = el("li");
      var mk = mark(teams, g.team, 16);
      if (mk) li.appendChild(mk);
      li.appendChild(el("b", null, g.team));
      li.appendChild(document.createTextNode(
        " lost in week " + g.week + " and took "));
      li.appendChild(el("b", null,
        g.ended + (g.ended === 1 ? " run" : " runs")));
      li.appendChild(document.createTextNode(" with it."));
      ul.appendChild(li);
    });
    box.appendChild(ul);
    show($("svgravecard"), true);
  }

  function initSurvivorPool() {
    // Only on the pool page. The picker has its own init and draws the same
    // table from the same call.
    if (!$("svsummary")) return;
    Promise.all([api("/api/survivor/board"), loadTeams()])
      .then(function (r) {
        var board = r[0], teams = r[1] || {};
        // Two groups, one renderer. The leaderboard is the players who
        // entered in time; the rest are playing the same game and their runs
        // are worth showing, but a rank next to them would be a claim on a
        // season they are not in the running for.
        var ranked = svSplit(board, function (x) { return x.ranked !== 0; });
        var late = svSplit(board, function (x) { return x.ranked === 0; });

        svDrawSummary(board);
        svDrawBoard(ranked, teams, null);
        svDrawGraveyard(board, teams);

        if (late.rows.length) {
          $("svlatenote").textContent = late.rows.length +
            (late.rows.length === 1 ? " run" : " runs") +
            " that started after week " + board.ranked_entry_by +
            ". Same rules, same handicap, not in the running for the season.";
          svDrawBoard(late, teams, null,
                      { into: "svlate", note: "svlatenote", norank: true });
          show($("svlatecard"), true);
        }
      })
      .catch(function () {
        var n = $("svboardnote");
        if (n) n.textContent = "The pool is unavailable.";
      });
  }

  function initSurvivor(me) {
    var form = $("svform");
    if (!form) return;

    show($("svsignedout"), !me);
    show($("svneedsname"), !!me && !!me.needs_name);

    var mineP = me
      ? api("/api/survivor").catch(function () { return null; })
      : Promise.resolve(null);
    var boardP = api("/api/survivor/board").catch(function () { return null; });

    Promise.all([api("/api/slate"), mineP, loadTeams(), boardP])
      .then(function (r) {
        var slate = r[0], mine = r[1], teams = r[2] || {}, board = r[3];
        svState = { slate: slate, mine: mine, teams: teams };
        SV_LIVE = svState;
        svWeekSelector(slate.week);

        return mergePeeks(slate).then(function () {
        show($("svlock"), true);
        $("svweek").textContent = slate.week;
        if (slate.lock_at) {
          var lt = $("lockat");
          lt.setAttribute("datetime",
            new Date(slate.lock_at * 1000).toISOString());
          lt.textContent = fmtWhen(slate.lock_at * 1000);
          startCountdown(slate.lock_at);
        }
        $("svstanding").textContent = svStandingText(mine, board);

        svRepaint();

        loadTeams().then(function (tm) { myTint = myColor(me, tm); });
        svState.board = board;
        svDrawRoster(mine, teams);
        svDrawField(board, teams, me && me.user_id);
        // The board itself lives on the Pool page. What belongs beside a
        // picker is the one number that changes how you pick.
        var al = $("svalive");
        if (al && board && board.entrants) {
          al.textContent = board.alive + " of " + board.entrants +
            (board.entrants === 1 ? " run" : " runs") + " still alive.";
        }
        });
      })
      .catch(function (err) {
        if (err.status === 404) {
          // The look-ahead opener, if the build published one: drawn through
          // the ordinary repaint with SV_PREVIEW holding every control shut,
          // and the countdown narrating what it is. Without one, the
          // countdown alone.
          previewIndex().then(function (ix) {
            var wks = (ix && ix.survivor_weeks) || [];
            if (!wks.length) { slateOpensNote($("svnote"), "survivor"); return; }
            previewWeek(wks[0]).then(function (pv) {
              if (!pv) { slateOpensNote($("svnote"), "survivor"); return; }
              SV_PREVIEW = true;
              loadTeams().then(function (tm) {
                svState = { slate: pv, mine: null, teams: tm };
                svRepaint();
                // On top of what svRepaint set: the preview grid makes the
                // chip column fit the row links.
                $("svform").className += " pk-preview";
                lookaheadMeta($("svcardtitle"), $("svnote"), pv);
                slateOpensNote($("svnote"), "survivor", pv);
                svWeekSelector(pv.week);
              });
            });
          });
        } else $("svnote").textContent = explain(err);
        // The picker failed, but the roster is built from your own picks and
        // is still worth drawing — it is the half of this page that does not
        // depend on a slate existing.
        api("/api/survivor").then(function (m2) {
          return loadTeams().then(function (tm) { svDrawRoster(m2, tm); });
        }).catch(function () { /* nothing to show, and nothing to apologise for */ });
      });

    form.addEventListener("change", function (e) {
      var t = e.target;
      if (!t || t.name !== "sv") return;
      alertMsg("");
      var parts = t.value.split("|");
      svSave(Number(parts[0]), parts.slice(1).join("|"),
             svState ? svState.slate.week : null);
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); });

    var svsel = $("svwk");
    if (svsel) {
      svsel.addEventListener("change", function () {
        var w = Number(svsel.value);
        var note = $("svnote");
        var liveW = SV_LIVE && SV_LIVE.slate ? SV_LIVE.slate.week : null;
        if (liveW != null && w === liveW) {
          // Back to the open week: hand the note back to svRepaint and let
          // it recompute everything from the state the API gave us.
          SV_PREVIEW = false;
          stopOpensNote(note);
          lookaheadMetaOff($("svcardtitle"), $("svnote"), "This week's pick");
          show($("svlock"), true);
          svState = SV_LIVE;
          svRepaint();
          return;
        }
        previewWeek(w).then(function (pv) {
          if (!pv) return;
          loadTeams().then(function (tm) {
            SV_PREVIEW = true;
            // The live week's lock strip steps aside the way the pick'em's
            // lock card does: it states a deadline for a week that is not
            // the one on the page.
            show($("svlock"), false);
            // The real `mine` rides along on purpose: which of your teams
            // are already spent is exactly what a look at Week 9 is for.
            svState = { slate: pv, mine: SV_LIVE ? SV_LIVE.mine : null,
                        teams: tm };
            svRepaint();
            $("svform").className += " pk-preview";
            lookaheadMeta($("svcardtitle"), $("svnote"), pv);
            slateOpensNote(note, "survivor", pv);
          });
        });
      });
    }
  }

  // ---------------------------------------------------------------- counts

  /* The population figure, on the hub's rule: a count is an argument once
     enough people are in it and an admission before that, so nothing shows
     below MIN. ONE number and one word everywhere — registrations, said as
     "players" — because three counts with three definitions read as three
     sites disagreeing (the per-game participation counts still exist in
     /api/health for anyone curious; the pages just stopped quoting them).
     Counts and only counts, from /api/health, which already says why that
     is fine to serve. */
  function initCounts() {
    var slots = [
      ["poolusers", "registered", " players have signed up."],
      ["pkplayers", "registered", " players have signed up."],
      ["svplayers", "registered", " players have signed up."],
    ].filter(function (s) { return $(s[0]); });
    if (!slots.length) return;
    var MIN = 10;
    api("/api/health").then(function (h) {
      // The counts' fetch doubles as the page's clock sync; see SKEW.
      if (h && h.at) SKEW = h.at * 1000 - Date.now();
      slots.forEach(function (s) {
        var n = h && h[s[1]];
        if (!n || n < MIN) return;
        var el = $(s[0]);
        el.textContent = s[2];
        el.insertBefore(document.createElement("b"), el.firstChild)
          .textContent = n.toLocaleString();
        el.hidden = false;
      });
    }).catch(function () { /* every page reads fine without them */ });
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
      initCounts();
      initShare();
      initAccount(me);
      initBoard(me);
      initCard(me);
      initSurvivorPool();
      initSurvivor(me);
    });
  });

  // ------------------------------------------------------------- for tests
  //
  // A named surface for test/pools.sim.test.js, which fuzzes these against a
  // stub DOM. The single-file rule this script is built on is about IMPORTS —
  // no sub-imports means the ?v= hash is the whole truth about the code
  // running — and an export breaks none of that. The sibling attendance app
  // is an ES module its tests import directly for exactly this reason.
  //
  // Nothing here is called by the page, and nothing secret passes through it:
  // every one of these functions works on data the API already serves
  // publicly.
  window.B12POOLS = {
    spreadText: spreadText, spreadSaid: spreadSaid, textOn: textOn,
    rgbOf: rgbOf, lineColor: lineColor, ordinal: ordinal,
    inPlayOrder: inPlayOrder, gameStatus: gameStatus, shortWhen: shortWhen,
    fmtWhen: fmtWhen, svSpent: svSpent, svInConference: svInConference,
    svSplit: svSplit, resultChip: resultChip, svOutcomeChip: svOutcomeChip,
    gameRow: gameRow, cardRow: cardRow, svGameRow: svGameRow,
    consensusBar: consensusBar, svStandingText: svStandingText,
    explain: explain,
  };

})();

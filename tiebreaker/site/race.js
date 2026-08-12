/* The Championship race card, computed in the browser from what-if picks.
 *
 * On The Race the same card is rendered by build.py from real results only:
 * clinch.py proves the statuses, odds.py runs 10,000 Monte Carlo seasons,
 * chaos.py scores the tangle. None of that can follow a pick, because all of
 * it runs once in CI. This is the port that can — clinch.bounds, clinch.exact,
 * odds.simulate and chaos.index, against the season the picks describe.
 *
 * Three things make an interactive budget possible where the build's does not
 * need to be:
 *
 *   - Picked games are inputs, not unknowns. Every pick removes a game from
 *     the simulation, so the work shrinks as the user works. Pick the whole
 *     season — which "Use favorites for all" does in one click — and there is
 *     nothing left to simulate: the answer is read straight off the standings.
 *   - 2,000 seasons, not 10,000. Standard error on a 50% estimate is about
 *     1.1 points, which a bar rounded to one decimal cannot show. The build
 *     is deciding what to publish and can afford to be sure; this is
 *     answering "what if" while someone waits.
 *   - The work is sliced across frames and canceled the moment a new pick
 *     lands, so a run in progress never blocks the click that supersedes it.
 *
 * The seed is fixed, so the same picks always produce the same odds. Two
 * readers comparing the same scenario see the same number.
 *
 * Kept behaviorally aligned with clinch.py / odds.py / chaos.py. If you
 * change the model on either side, change it on both.
 */
(function (global) {
  "use strict";

  var E = global.B12Engine;
  var P = global.B12PCT;

  var TEAM_COUNT = 16;      // two of sixteen reach the title game
  var SPOTS = 2;
  var N_SIMS = 2000;
  var SEED = 1996;          // the year of the first Big 12 season
  var MARGIN_SIGMA = 13.5;  // sd of scoring margin vs the spread
  var RATING_SIGMA = 7.0;   // preseason sd of true strength around a rating
  var SIGMA_SHRINK = 4.0;   // games played at which that is ~halved
  var EXACT_BUDGET = 1 << 11;  // completions we will enumerate for proofs
  var SLICE_MS = 12;        // work per frame; one frame at 60Hz is 16.7

  var CHAOS_WEIGHTS = { entropy: 0.60, tangle: 0.25, breadth: 0.15 };
  var CHAOS_LABELS = [[15, "Settled"], [35, "Orderly"], [55, "Simmering"],
                      [75, "Chaotic"], [101, "Pandemonium"]];

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/'/g, "&#39;").replace(/"/g, "&quot;");
  }

  // ------------------------------------------------------------- inventory

  function confTeams(games) {
    var seen = {};
    games.forEach(function (g) {
      if (g.conference_game && !g.ccg) { seen[g.home] = 1; seen[g.away] = 1; }
    });
    return Object.keys(seen).sort();
  }

  function remainingConf(games) {
    return games.filter(function (g) {
      return g.conference_game && !g.ccg && !g.completed;
    });
  }

  // Teams that still have an unplayed non-conference game. Total wins is a
  // tiebreak step, so an unfinished non-conference schedule is what makes a
  // step-e cut unsafe to call.
  function unplayedNonconf(games) {
    var out = {};
    games.forEach(function (g) {
      if (g.completed || g.ccg || g.conference_game) return;
      out[g.home] = 1; out[g.away] = 1;
    });
    return out;
  }

  // ------------------------------------------------------------ membership

  /* (sure, maybe) for the top-2 cut of a hypothetically finished season.
     Only the group straddling the cut needs breaking — groups wholly above
     it are in, wholly below are out. Uncertainty counts against certainty:
     a cut that turns on an unresolved step, or on total wins while a tied
     team still has non-conference games left, is "maybe" and blocks a proof
     in both directions. Mirrors clinch.cut_membership. */
  function cutMembership(games, overrides, ncf) {
    var groups = E.placementGroups(games);
    var sure = {}, maybe = {};
    var seats = SPOTS;
    for (var i = 0; i < groups.length && seats > 0; i++) {
      var grp = groups[i];
      if (grp.length <= seats) {
        grp.forEach(function (t) { sure[t] = 1; });
        seats -= grp.length;
        continue;
      }
      var res = E.breakTie(grp, games, overrides);
      var hazard = grp.some(function (t) { return ncf[t]; });
      var risky = !res.resolved;
      if (!risky) {
        var evs = (res.events || []).slice(0, seats);
        for (var j = 0; j < evs.length; j++) {
          var st = evs[j].step;
          if (hazard && (st === "e" || st === "f" || st === "g")) {
            risky = true;
            break;
          }
        }
      }
      if (risky) grp.forEach(function (t) { maybe[t] = 1; });
      else res.order.slice(0, seats).forEach(function (t) { sure[t] = 1; });
      seats = 0;
    }
    return { sure: sure, maybe: maybe };
  }

  // ---------------------------------------------------------------- bounds

  /* Win-count arithmetic with strict inequalities, so no tiebreaker can
     invalidate a claim. O(n) and always available. Mirrors clinch.bounds. */
  function bounds(games) {
    var teams = confTeams(games);
    var rec = E.confRecords(games);
    var rem = {};
    teams.forEach(function (t) { rem[t] = 0; });
    remainingConf(games).forEach(function (g) {
      rem[g.home] += 1; rem[g.away] += 1;
    });
    var out = {};
    teams.forEach(function (t) {
      var w = rec[t] ? rec[t][0] : 0;
      var l = rec[t] ? rec[t][1] : 0;
      out[t] = { w: w, l: l, r: rem[t], max_w: w + rem[t] };
    });
    teams.forEach(function (t) {
      var me = out[t], canReachNow = 0, aheadForGood = 0, canReachCeiling = 0;
      teams.forEach(function (x) {
        if (x === t) return;
        if (out[x].max_w >= me.w) canReachNow += 1;
        if (out[x].w > me.max_w) aheadForGood += 1;
        if (out[x].max_w >= me.max_w) canReachCeiling += 1;
      });
      me.clinched = canReachNow <= 1;
      me.eliminated = aheadForGood >= 2;
      me.destiny = !me.clinched && me.r > 0 && canReachCeiling <= 1;
    });
    return out;
  }

  // ------------------------------------------------------------ randomness

  /* Seeded so a scenario is reproducible. mulberry32 for the uniforms,
     Marsaglia polar for the normals. */
  function makeRng(seed) {
    var s = seed >>> 0, spare = null;
    function next() {
      s = (s + 0x6D2B79F5) | 0;
      var t = Math.imul(s ^ (s >>> 15), 1 | s);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }
    function gauss(sigma) {
      if (spare !== null) { var v = spare; spare = null; return sigma * v; }
      var u, w, q;
      do {
        u = next() * 2 - 1; w = next() * 2 - 1; q = u * u + w * w;
      } while (q >= 1 || q === 0);
      var mul = Math.sqrt(-2 * Math.log(q) / q);
      spare = w * mul;
      return sigma * u * mul;
    }
    return { next: next, gauss: gauss };
  }

  // Abramowitz & Stegun 7.1.26; |error| < 1.5e-7, far inside the noise of a
  // 2,000-season sample.
  function erf(x) {
    var sign = x < 0 ? -1 : 1;
    x = Math.abs(x);
    var t = 1 / (1 + 0.3275911 * x);
    var y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t -
      0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return sign * y;
  }

  function pFromMargin(m) {
    return 0.5 * (1 + erf(m / (MARGIN_SIGMA * Math.SQRT2)));
  }

  // ------------------------------------------------------------- the model

  /* Expected home margin per game, averaged over the rating systems.
     build.py already published each system's pick and margin for every game
     to drive the ★ favorites, so the ensemble is rebuilt from that rather
     than shipping the raw ratings a second time. Margins include home field,
     which is what the favorites tooltip documents. */
  function ensembleMargins(payload) {
    var byId = {};
    payload.games.forEach(function (g) { byId[String(g.id)] = g; });
    var sum = {}, n = {};
    var fav = payload.favorites || {};
    // THE RATING SYSTEMS ONLY. payload.favorites also carries their blend
    // and the market, which are not opinions to average: the blend IS this
    // average, so including it weights the four twice over, and a betting
    // line is not a rating system. payload.models says which is which; a
    // payload without kinds is older than that distinction and had only
    // ratings in it.
    var kinds = payload.models || [];
    var rating = {};
    var typed = false;
    kinds.forEach(function (m) {
      if (m && m.kind) { typed = true; if (m.kind === "rating") rating[m.name] = 1; }
    });
    Object.keys(fav).filter(function (name) {
      return !typed || rating[name];
    }).forEach(function (model) {
      var picks = fav[model];
      Object.keys(picks).forEach(function (gid) {
        var g = byId[gid];
        if (!g) return;
        var e = picks[gid];
        if (typeof e.margin !== "number") return;
        var home = e.team === g.home ? e.margin : -e.margin;
        sum[gid] = (sum[gid] || 0) + home;
        n[gid] = (n[gid] || 0) + 1;
      });
    });
    var out = {};
    Object.keys(sum).forEach(function (gid) { out[gid] = sum[gid] / n[gid]; });
    return out;
  }

  /* How unsure we are of a team's true strength, in points. This is
     deliberately measured against *real* completed games, not against the
     picks: a picked result is a question the user asked, not evidence about
     how good anyone is. Picking a whole season should not make the model
     confident it understood the teams. Mirrors odds.rating_sigma. */
  function ratingSigma(realGames) {
    var conf = {};
    confTeams(realGames).forEach(function (t) { conf[t] = 1; });
    var teamGames = 0;
    realGames.forEach(function (g) {
      if (!g.completed || g.ccg) return;
      if (conf[g.home]) teamGames += 1;
      if (conf[g.away]) teamGames += 1;
    });
    var nTeams = Object.keys(conf).length || 1;
    var gp = teamGames / nTeams;
    return RATING_SIGMA * Math.sqrt(SIGMA_SHRINK / (SIGMA_SHRINK + gp));
  }

  // ------------------------------------------------------------ simulation

  function makeSimContext(games, realGames, margins, overrides) {
    var base = games.map(function (g) {
      return {
        id: g.id, week: g.week, ccg: g.ccg, home: g.home, away: g.away,
        conference_game: g.conference_game, completed: g.completed,
        home_points: g.home_points, away_points: g.away_points,
      };
    });
    var rem = base.filter(function (g) {
      return !g.completed && !g.ccg && margins[String(g.id)] !== undefined;
    });
    var sides = {};
    rem.forEach(function (g) { sides[g.home] = 1; sides[g.away] = 1; });
    var teams = confTeams(games);
    var inCount = {}, winSum = {};
    teams.forEach(function (t) { inCount[t] = 0; winSum[t] = 0; });
    return {
      base: base, rem: rem, teams: teams,
      sides: Object.keys(sides).sort(),
      margins: margins, overrides: overrides,
      ncf: unplayedNonconf(games),
      rng: makeRng(SEED), sigma: ratingSigma(realGames),
      inCount: inCount, winSum: winSum, done: 0,
    };
  }

  /* One simulated season. The strength offset is drawn once per team and
     held across all of that team's games — a team that is really worse than
     its rating loses more of them together, and that correlation is what
     moves a season-long distribution. Independent per-game noise washes out. */
  function oneSim(ctx) {
    var off = {};
    if (ctx.sigma) {
      for (var s = 0; s < ctx.sides.length; s++) {
        off[ctx.sides[s]] = ctx.rng.gauss(ctx.sigma);
      }
    }
    var i, g;
    for (i = 0; i < ctx.rem.length; i++) {
      g = ctx.rem[i];
      g.completed = true;
      var m = ctx.margins[String(g.id)] +
        (off[g.home] || 0) - (off[g.away] || 0);
      if (ctx.rng.next() < pFromMargin(m)) {
        g.home_points = 28; g.away_points = 17;
      } else {
        g.home_points = 17; g.away_points = 28;
      }
    }
    var cm = cutMembership(ctx.base, ctx.overrides, ctx.ncf);
    var rec = E.confRecords(ctx.base);
    for (i = 0; i < ctx.teams.length; i++) {
      var t = ctx.teams[i];
      ctx.inCount[t] += cm.sure[t] ? 1 : (cm.maybe[t] ? 0.5 : 0);
      ctx.winSum[t] += rec[t] ? rec[t][0] : 0;
    }
    for (i = 0; i < ctx.rem.length; i++) {
      g = ctx.rem[i];
      g.completed = false; g.home_points = null; g.away_points = null;
    }
    ctx.done += 1;
  }

  /* Exhaustive enumeration of every completion, for proofs rather than
     estimates: clinched iff in under all of them, eliminated iff in none.
     Mirrors clinch.exact, minus the this-week scenario grouping — on The Lab
     the user is choosing this week's games by hand, so a prompt telling them
     which combination to choose has nothing to add. */
  function makeExactContext(games, overrides) {
    var rem0 = remainingConf(games);
    if (rem0.length > 24 || (1 << rem0.length) > EXACT_BUDGET) return null;
    var base = games.map(function (g) {
      return {
        id: g.id, week: g.week, ccg: g.ccg, home: g.home, away: g.away,
        conference_game: g.conference_game, completed: g.completed,
        home_points: g.home_points, away_points: g.away_points,
      };
    });
    var rem = base.filter(function (g) {
      return g.conference_game && !g.ccg && !g.completed;
    });
    var teams = confTeams(games);
    var always = {}, ever = {};
    teams.forEach(function (t) { always[t] = true; ever[t] = false; });
    return {
      base: base, rem: rem, teams: teams, overrides: overrides,
      ncf: unplayedNonconf(games), always: always, ever: ever,
      combo: 0, total: 1 << rem.length, done: 0,
    };
  }

  function oneCombo(ctx) {
    var c = ctx.combo, i, g;
    for (i = 0; i < ctx.rem.length; i++) {
      g = ctx.rem[i];
      g.completed = true;
      if ((c >> i) & 1) { g.home_points = 28; g.away_points = 17; }
      else { g.home_points = 17; g.away_points = 28; }
    }
    var cm = cutMembership(ctx.base, ctx.overrides, ctx.ncf);
    for (i = 0; i < ctx.teams.length; i++) {
      var t = ctx.teams[i];
      var sure = !!cm.sure[t];
      ctx.always[t] = ctx.always[t] && sure;
      ctx.ever[t] = ctx.ever[t] || sure || !!cm.maybe[t];
    }
    for (i = 0; i < ctx.rem.length; i++) {
      g = ctx.rem[i];
      g.completed = false; g.home_points = null; g.away_points = null;
    }
    ctx.combo += 1;
    ctx.done += 1;
  }

  // ----------------------------------------------------------------- chaos

  function entropyComponent(p) {
    var keys = Object.keys(p);
    var total = 0;
    keys.forEach(function (t) { total += p[t]; });
    if (total <= 0) return 1;
    var h = 0;
    keys.forEach(function (t) {
      var q = p[t] / total;
      if (q > 0) h -= q * Math.log(q);
    });
    var lo = Math.log(2), hi = Math.log(Math.max(keys.length, 3));
    return Math.max(0, Math.min(1, (h - lo) / (hi - lo)));
  }

  /* A team with no conference result is tied with every other such team, and
     counting them is the whole of this function's early-season correctness.
     The standings rank only teams there is evidence for, so in September
     `rows` is two or four teams while n stays sixteen, and the dozen still on
     0-0 — the largest tie on the board — were falling out of the numerator.
     Mirrors chaos.tangle_component; the long version of why is there. */
  function tangleComponent(rows, statuses, n) {
    if (!n) return 1;
    var listed = {};
    rows.forEach(function (r) { listed[r.team] = 1; });
    var unplayed = Object.keys(statuses).filter(function (t) {
      return !listed[t] && statuses[t] === "alive";
    });
    var tangled = 0;
    rows.forEach(function (r) {
      if (r.tie_group && statuses[r.team] === "alive") tangled += 1;
    });
    if (unplayed.length > 1) tangled += unplayed.length;
    return Math.min(1, tangled / n);
  }

  function breadthComponent(statuses) {
    var keys = Object.keys(statuses);
    if (keys.length <= 2) return 0;
    var alive = 0;
    keys.forEach(function (t) { if (statuses[t] !== "eliminated") alive += 1; });
    return Math.max(0, (alive - 2) / (keys.length - 2));
  }

  function chaosIndex(rows, statuses, probs) {
    var p = {};
    Object.keys(statuses).forEach(function (t) {
      p[t] = statuses[t] === "clinched" ? 1
        : statuses[t] === "eliminated" ? 0
          : (probs[t] || 0);
    });
    var comps = {
      entropy: entropyComponent(p),
      tangle: tangleComponent(rows, statuses, Object.keys(statuses).length),
      breadth: breadthComponent(statuses),
    };
    var score = Math.round(100 * (CHAOS_WEIGHTS.entropy * comps.entropy +
      CHAOS_WEIGHTS.tangle * comps.tangle +
      CHAOS_WEIGHTS.breadth * comps.breadth));
    var label = "Pandemonium";
    for (var i = 0; i < CHAOS_LABELS.length; i++) {
      if (score < CHAOS_LABELS[i][0]) { label = CHAOS_LABELS[i][1]; break; }
    }
    return { score: score, label: label, components: comps };
  }

  // --------------------------------------------------------------- render

  function markImg(state, team) {
    var t = state.payload.teams || {};
    var src = (t[team] && t[team].logo) || (state.payload.marks || {})[team];
    if (!src) return "";
    return "<img class=mark src='" + esc(src) +
      "' alt='' width=18 height=18 loading=lazy>";
  }

  function render(state, model) {
    var b = model.bounds, statuses = model.statuses;
    var probs = model.probs, expw = model.expw;
    var teams = Object.keys(b);
    var html = "";

    if (model.chaos) {
      var cx = model.chaos;
      var ccolor = cx.score >= 55 ? "var(--accent)"
        : cx.score >= 35 ? "var(--warn)" : "var(--dim)";
      var c = cx.components;
      html += "<div class=chaosband>" +
        "<span class=cnum style='color:" + ccolor + "'>" + cx.score +
        "</span><div><b>Chaos Index: " + esc(cx.label) + "</b>" +
        "<div class=chaosscale>0 is a decided race, 100 is a sixteen-way " +
        "pileup</div><div class=chaosparts>Built from three parts, each 0 " +
        "to 1: race entropy " + c.entropy.toFixed(2) +
        ", tie tangle " + c.tangle.toFixed(2) +
        ", still alive " + c.breadth.toFixed(2) + "</div></div></div>";
    }

    var order = teams.slice().sort(function (x, y) {
      var px = statuses[x] === "clinched" ? 1
        : statuses[x] === "eliminated" ? 0 : (probs[x] || 0);
      var py = statuses[y] === "clinched" ? 1
        : statuses[y] === "eliminated" ? 0 : (probs[y] || 0);
      if (py !== px) return py - px;
      if (b[y].w !== b[x].w) return b[y].w - b[x].w;
      return x < y ? -1 : x > y ? 1 : 0;
    });

    var eliminated = [];
    order.forEach(function (t) {
      if (statuses[t] === "eliminated") { eliminated.push(t); return; }
      var i = b[t];
      var p = statuses[t] === "clinched" ? 1 : probs[t];
      var bar = "", pctcell = "";
      if (p !== undefined && p !== null) {
        var col = P.ccg(p, TEAM_COUNT, SPOTS);
        bar = "<span class=obar><i style='width:" + (p * 100).toFixed(1) +
          "%;background:" + col + "'></i></span>";
        pctcell = "<b class=opct style='color:" + col + "'>" +
          esc(P.prob(p)) + "</b>";
      }
      var bits = [];
      if (statuses[t] === "clinched") {
        bits.push("<span class='tag live'>clinched</span>");
      }
      if (i.destiny && statuses[t] === "alive") {
        bits.push("<span class='tag destiny'>controls own destiny</span>");
      }
      var exptxt = "";
      if (expw[t] !== undefined && i.r > 0) {
        exptxt = " <span class=dim>· " + expw[t].toFixed(1) +
          " exp conf wins</span>";
      }
      html += "<div class=clrow><div class=clmain>" +
        markImg(state, t) +
        "<b class=clteam>" + esc(t) + "</b>" +
        "<span class=clbar>" + bar + "</span>" +
        "<span class=clpct>" + pctcell + "</span>" +
        "<span class=cltags>" + bits.join(" ") + "</span>" +
        "<span class='dim clrec'>" + i.w + "–" + i.l + ", " + i.r +
        " left" + exptxt + "</span>" +
        "</div></div>";
    });

    if (eliminated.length) {
      html += "<p class='dim elim'>Eliminated: " +
        eliminated.map(esc).join(", ") + "</p>";
    }

    var notes = [];
    if (model.proof === "exact") {
      notes.push("Clinch/elimination statuses are proven across all " +
        model.nOutcomes.toLocaleString() + " remaining outcomes with the " +
        "full official tiebreaker procedure.");
    } else if (model.proof === "settled") {
      notes.push("Every game is picked, so there is nothing left to " +
        "prove — this is the finished table for the season you built.");
    } else {
      notes.push("Clinch/elimination statuses are proven by strict " +
        "win-count arithmetic — no tiebreaker can undo them. Exhaustive " +
        "proof unlocks once few enough games are left unpicked.");
    }
    if (model.nSims) {
      notes.push("Percentages are championship-game odds from " +
        model.nSims.toLocaleString() + " simulations of the games you " +
        "have not picked (win probabilities from an ensemble of " +
        esc(model.systems.join(", ")) + "); proofs override odds.");
    }
    notes.push("<b>Driven by your picks:</b> a game you have picked counts " +
      "as played, and only the rest is simulated.");

    html += "<p class=note>The percentage is the chance of <b>reaching the " +
      "championship game</b>, not of winning it or of finishing first. Two " +
      "teams get there, so these add up to about 200%. " +
      notes.join(" ") + "</p>";

    if (model.busy) {
      html += "<p class=note id=racebusy>Simulating… " +
        Math.round(model.pending * 100) + "%</p>";
    }
    return html;
  }

  // ---------------------------------------------------------------- driver

  var state = null;
  var job = null;   // cancellation token for the run in flight

  function cancel() {
    if (job) { job.canceled = true; job = null; }
  }

  /* Slice a unit of work across frames until it is done, then hand back.
     Time-boxed rather than counted, so a slow phone takes smaller bites
     instead of dropping frames. */
  function pump(token, step, isDone, onTick, onDone) {
    function slice() {
      if (token.canceled) return;
      var t0 = (global.performance || Date).now();
      while (!isDone() && (global.performance || Date).now() - t0 < SLICE_MS) {
        step();
      }
      if (isDone()) { onDone(); return; }
      onTick();
      global.setTimeout(slice, 0);
    }
    global.setTimeout(slice, 0);
  }

  function paint(model) {
    if (!state || !state.el) return;
    state.el.innerHTML = render(state, model);
  }

  /* A tick only moves the progress number. Repainting the card for it would
     rebuild sixteen rows, their bars and the chaos band tens of times over a
     run — more work than the simulation it is reporting on, and enough
     layout churn to undo the point of slicing in the first place. */
  function paintProgress(model) {
    if (!state || !state.el) return;
    var el = state.el.querySelector("#racebusy");
    if (!el) { paint(model); return; }
    el.textContent = "Simulating… " + Math.round(model.pending * 100) + "%";
  }

  /* The pieces every path shares, so the sliced and the synchronous runs
     cannot drift apart. `prepare` is the part that is always cheap: bounds
     proofs and the standings, which is what the card can show in the same
     frame as the click. */
  function prepare(games, overrides) {
    var b = bounds(games);
    var teams = Object.keys(b);
    var statuses = {};
    teams.forEach(function (t) {
      statuses[t] = b[t].clinched ? "clinched"
        : b[t].eliminated ? "eliminated" : "alive";
    });
    return {
      bounds: b, teams: teams, statuses: statuses, probs: {}, expw: {},
      rows: E.standings(games, overrides),
      remaining: remainingConf(games),
    };
  }

  function applySettled(p) {
    var top = {};
    p.rows.slice(0, SPOTS).forEach(function (r) { top[r.team] = 1; });
    p.teams.forEach(function (t) {
      p.statuses[t] = top[t] ? "clinched" : "eliminated";
      p.probs[t] = top[t] ? 1 : 0;
    });
  }

  function applyExact(p, ex) {
    p.teams.forEach(function (t) {
      p.statuses[t] = ex.always[t] ? "clinched"
        : !ex.ever[t] ? "eliminated" : "alive";
    });
  }

  function applySims(p, sim, n) {
    p.teams.forEach(function (t) {
      p.probs[t] = sim.inCount[t] / n;
      p.expw[t] = sim.winSum[t] / n;
    });
  }

  function modelOf(p, extra) {
    var m = {
      bounds: p.bounds, statuses: p.statuses, probs: p.probs, expw: p.expw,
      chaos: null, proof: "bounds", nSims: 0, pending: 0,
      systems: state ? state.systems : [],
    };
    Object.keys(extra || {}).forEach(function (k) { m[k] = extra[k]; });
    return m;
  }

  /* Recompute the card for `games` — the season as the picks describe it.
     Bounds and the settled case are synchronous, so the card repaints in the
     same frame as the click; the simulation and the enumeration stream in
     behind it. */
  function update(games) {
    if (!state) return;
    cancel();
    var overrides = state.payload.overrides || {};
    var p = prepare(games, overrides);

    // Nothing left to decide: the standings are the answer. No simulation,
    // no enumeration — this is the state "Use favorites for all" produces
    // and it has to feel instant.
    if (!p.remaining.length) {
      applySettled(p);
      paint(modelOf(p, {
        proof: "settled", chaos: chaosIndex(p.rows, p.statuses, p.probs),
      }));
      return;
    }

    var model = modelOf(p, { busy: true });
    paint(model);

    var token = { canceled: false };
    job = token;

    var sim = makeSimContext(games, state.payload.games, state.margins,
                             overrides);
    var ex = makeExactContext(games, overrides);

    function runSim() {
      if (token.canceled) return;
      pump(token,
        function () { oneSim(sim); },
        function () { return sim.done >= N_SIMS; },
        function () {
          model.pending = sim.done / N_SIMS;
          paintProgress(model);
        },
        function () {
          applySims(p, sim, N_SIMS);
          model.nSims = N_SIMS;
          model.pending = 0;
          model.busy = false;
          model.chaos = chaosIndex(p.rows, p.statuses, p.probs);
          paint(model);
          job = null;
        });
    }

    if (ex) {
      pump(token,
        function () { oneCombo(ex); },
        function () { return ex.combo >= ex.total; },
        function () {
          model.pending = 0.5 * (ex.combo / ex.total);
          paintProgress(model);
        },
        function () {
          if (token.canceled) return;
          applyExact(p, ex);
          model.proof = "exact";
          model.nOutcomes = ex.total;
          paint(model);
          runSim();
        });
    } else {
      runSim();
    }
  }

  /* The same computation with the slicing taken out. update() is shaped by
     the need to keep a browser responsive; this is for callers that only
     want the answer — the invariant tests, and anything running headless.
     It walks the identical helpers, so a test of this is a test of that. */
  function computeSync(games, opts) {
    opts = opts || {};
    var payload = opts.payload || (state && state.payload);
    var overrides = payload.overrides || {};
    var margins = opts.margins ||
      (state ? state.margins : ensembleMargins(payload));
    var n = opts.nSims === undefined ? N_SIMS : opts.nSims;
    var p = prepare(games, overrides);

    if (!p.remaining.length) {
      applySettled(p);
      return modelOf(p, {
        proof: "settled", chaos: chaosIndex(p.rows, p.statuses, p.probs),
      });
    }

    var ex = makeExactContext(games, overrides);
    var proof = "bounds", nOutcomes = null;
    if (ex) {
      while (ex.combo < ex.total) oneCombo(ex);
      applyExact(p, ex);
      proof = "exact";
      nOutcomes = ex.total;
    }

    var sim = makeSimContext(games, payload.games, margins, overrides);
    while (sim.done < n) oneSim(sim);
    applySims(p, sim, n);

    return modelOf(p, {
      proof: proof, nOutcomes: nOutcomes, nSims: n,
      chaos: chaosIndex(p.rows, p.statuses, p.probs),
      systems: Object.keys(payload.favorites || {}).sort(),
    });
  }

  function mount(el, payload) {
    state = {
      el: el, payload: payload,
      margins: ensembleMargins(payload),
      systems: Object.keys(payload.favorites || {}).sort(),
    };
  }

  global.B12Race = {
    mount: mount, update: update, cancel: cancel, computeSync: computeSync,
    bounds: bounds, cutMembership: cutMembership, chaosIndex: chaosIndex,
    ensembleMargins: ensembleMargins, pFromMargin: pFromMargin,
    N_SIMS: N_SIMS,
  };
})(typeof window !== "undefined" ? window : globalThis);

/* Big 12 men's basketball: standings, seeding and the tiebreakers.
 *
 * THE RULES are the conference's "Phillips 66 Big 12 Tournament Seeding and
 * Tiebreaker Procedures (As of 2023-2024 Season)",
 * https://big12sports.com/sports/2011/6/22/1520896.aspx. They seed all
 * sixteen teams for the tournament in Kansas City, so every tie is broken,
 * not only the one for first. The regular-season title is not tiebroken at
 * all: the Big 12 names co-champions.
 *
 * Two teams tied:
 *   1. head-to-head;
 *   2. winning percentage against the team in the highest position in the
 *      standings, then down through the standings until one gains an edge;
 *   3. conference road winning percentage;
 *   4. conference road winning percentage against the highest position, down
 *      through the standings;
 *   5. a draw.
 * More than two:
 *   1. a mini round-robin among the tied teams, ranked by winning percentage,
 *      repeated among any that remain level (two left go to the two-team
 *      steps);
 *   2-4. as two-team steps 2-4, eliminating the teams that fall behind;
 *      reduced to two at any point, the two-team steps decide;
 *   5. a draw.
 *
 * Three readings the document leaves to the reader, written down here so the
 * site has one answer and says what it is:
 *
 *   "2-0 is not better than 1-0 or 0-0" means a comparison counts only when
 *   every team being compared has played the opponent it is compared
 *   against; one that has not makes that step a skip. Over a full season
 *   every pair has met, so this only matters while the season is under way.
 *
 *   A tied group further down the standings is compared against as one
 *   collective opponent, "prior to that group's own tiebreaking", except a
 *   two-team group that head-to-head has already split, which is compared
 *   against seed by seed. The tied teams' own group is skipped.
 *
 *   When a multi-team tie produces one winner, that team takes the top seed
 *   in the group and the rest start the procedure again, from the mini
 *   round-robin, among themselves.
 *
 * DECIDES NOTHING ABOUT WHICH GAMES COUNT. The caller passes regular-season
 * conference games only, as {home, away, hp, ap, neutral, final}. A game
 * without a final is ignored here; the simulator fills those in.
 */
(function (root) {
  "use strict";

  function pct(w, l) {
    return w + l ? w / (w + l) : null;
  }

  function fmt(r) {
    return r[0] + "–" + r[1];
  }

  // ---- records ------------------------------------------------------------

  function records(games, teams) {
    var rec = {};
    teams.forEach(function (t) {
      rec[t] = { w: 0, l: 0, rw: 0, rl: 0, vs: {}, road: {} };
    });
    games.forEach(function (g) {
      if (!g.final || !(g.home in rec) || !(g.away in rec)) return;
      if (g.hp === g.ap) return;
      var win = g.hp > g.ap ? g.home : g.away;
      var lose = win === g.home ? g.away : g.home;
      rec[win].w++;
      rec[lose].l++;
      (rec[win].vs[lose] = rec[win].vs[lose] || [0, 0])[0]++;
      (rec[lose].vs[win] = rec[lose].vs[win] || [0, 0])[1]++;
      if (!g.neutral) {
        // A road game is the away side's, on the home side's floor.
        var away = g.away, home = g.home;
        var aWon = win === away;
        rec[away][aWon ? "rw" : "rl"]++;
        (rec[away].road[home] = rec[away].road[home] || [0, 0])[aWon ? 0 : 1]++;
      }
    });
    return rec;
  }

  function vsSet(rec, team, opps, road) {
    var w = 0, l = 0;
    opps.forEach(function (o) {
      var r = (road ? rec[team].road : rec[team].vs)[o];
      if (r) { w += r[0]; l += r[1]; }
    });
    return [w, l];
  }

  // ---- the standings, before any tie is broken -----------------------------

  /* Teams grouped by conference winning percentage, best first. A team with
   * no conference result sits above every team that has only lost and below
   * every team that has won: 0-0 is ahead of 0-3 on any board anyone reads. */
  function groups(rec, teams) {
    function key(t) {
      var r = rec[t];
      if (!r.w && !r.l) return 0.0001;   // between "has won" and "only lost"
      return pct(r.w, r.l) || 0;
    }
    var by = {};
    teams.forEach(function (t) {
      var k = key(t).toFixed(6);
      (by[k] = by[k] || []).push(t);
    });
    return Object.keys(by).sort(function (a, b) { return b - a; })
      .map(function (k) { return by[k].sort(); });
  }

  /* The places to compare against, top down: a team on its own, a two-team
   * tie that head-to-head split (each at its own seed), or a tied group as
   * one collective opponent. */
  function placements(rec, gs) {
    var out = [];
    gs.forEach(function (g) {
      if (g.length === 1) {
        out.push({ teams: g, group: g });
        return;
      }
      if (g.length === 2) {
        var h = rec[g[0]].vs[g[1]];
        if (h && h[0] !== h[1]) {
          var first = h[0] > h[1] ? g[0] : g[1];
          var second = first === g[0] ? g[1] : g[0];
          out.push({ teams: [first], group: g });
          out.push({ teams: [second], group: g });
          return;
        }
      }
      out.push({ teams: g, group: g });
    });
    return out;
  }

  // ---- the procedure ---------------------------------------------------------

  function nameOf(p) {
    return p.teams.length === 1 ? p.teams[0] : p.teams.join(", ");
  }

  /* Walk down the standings comparing `teams` against each place in turn.
   * Returns {survivors, at, vals}: the teams with the best record against the
   * first place where they differ (every one of them having played it), or
   * all of them if nothing separates them. */
  function walk(rec, ctx, teams, road) {
    var own = {};
    teams.forEach(function (t) { own[t] = true; });
    for (var i = 0; i < ctx.places.length; i++) {
      var p = ctx.places[i];
      if (p.group.some(function (t) { return own[t]; })) continue;
      var vals = {}, skip = false;
      teams.forEach(function (t) {
        var r = vsSet(rec, t, p.teams, road);
        if (r[0] + r[1] === 0) skip = true;
        vals[t] = r;
      });
      if (skip) continue;
      var best = Math.max.apply(null, teams.map(function (t) {
        return pct(vals[t][0], vals[t][1]);
      }));
      var top = teams.filter(function (t) {
        return pct(vals[t][0], vals[t][1]) === best;
      });
      if (top.length < teams.length) {
        return { survivors: top, at: p, vals: vals };
      }
    }
    return { survivors: teams.slice(), at: null, vals: null };
  }

  function roadPct(rec, teams) {
    var vals = {}, skip = false;
    teams.forEach(function (t) {
      vals[t] = [rec[t].rw, rec[t].rl];
      if (rec[t].rw + rec[t].rl === 0) skip = true;
    });
    if (skip) return { survivors: teams.slice(), vals: null };
    var best = Math.max.apply(null, teams.map(function (t) {
      return pct(vals[t][0], vals[t][1]);
    }));
    return {
      survivors: teams.filter(function (t) {
        return pct(vals[t][0], vals[t][1]) === best;
      }),
      vals: vals,
    };
  }

  function detail(vals, teams) {
    return teams.map(function (t) { return t + " " + fmt(vals[t]); }).join(", ");
  }

  /* Two teams. Returns [higher, lower] and logs the step that decided it. */
  function two(rec, ctx, a, b) {
    var pair = [a, b].sort();
    a = pair[0]; b = pair[1];
    var h = rec[a].vs[b] || [0, 0];
    if (h[0] !== h[1]) {
      var w = h[0] > h[1] ? a : b;
      ctx.log.push({ teams: pair, step: "head-to-head", winner: w,
        text: w + " won the head-to-head, " + fmt(h[0] > h[1] ? h : [h[1], h[0]]) });
      return [w, w === a ? b : a];
    }
    var r = walk(rec, ctx, pair, false);
    if (r.survivors.length === 1) {
      var w2 = r.survivors[0];
      ctx.log.push({ teams: pair, step: "next-highest", winner: w2,
        text: "split the head-to-head " + fmt(h) + "; against " +
          nameOf(r.at) + ": " + detail(r.vals, pair) });
      return [w2, w2 === a ? b : a];
    }
    var rp = roadPct(rec, pair);
    if (rp.survivors.length === 1) {
      var w3 = rp.survivors[0];
      ctx.log.push({ teams: pair, step: "road", winner: w3,
        text: "conference road record: " + detail(rp.vals, pair) });
      return [w3, w3 === a ? b : a];
    }
    var rr = walk(rec, ctx, pair, true);
    if (rr.survivors.length === 1) {
      var w4 = rr.survivors[0];
      ctx.log.push({ teams: pair, step: "road-next-highest", winner: w4,
        text: "conference road record against " + nameOf(rr.at) + ": " +
          detail(rr.vals, pair) });
      return [w4, w4 === a ? b : a];
    }
    ctx.log.push({ teams: pair, step: "draw", winner: null,
      text: "nothing separates them; the conference would hold a draw" });
    ctx.unresolved.push(pair);
    return pair;
  }

  /* Any number of tied teams, best first. */
  function rank(rec, ctx, teams) {
    teams = teams.slice().sort();
    if (teams.length < 2) return teams;
    if (teams.length === 2) return two(rec, ctx, teams[0], teams[1]);

    // 1. The mini round-robin, only when every tied team has met every other.
    var met = teams.every(function (t) {
      return teams.every(function (o) { return o === t || rec[t].vs[o]; });
    });
    if (met) {
      var mini = {};
      teams.forEach(function (t) {
        mini[t] = vsSet(rec, t, teams.filter(function (o) { return o !== t; }), false);
      });
      var by = {};
      teams.forEach(function (t) {
        var k = pct(mini[t][0], mini[t][1]).toFixed(6);
        (by[k] = by[k] || []).push(t);
      });
      var levels = Object.keys(by).sort(function (x, y) { return y - x; });
      if (levels.length > 1) {
        ctx.log.push({ teams: teams, step: "mini round-robin", winner: null,
          text: "among themselves: " + detail(mini, teams) });
        return [].concat.apply([], levels.map(function (k) {
          return rank(rec, ctx, by[k]);
        }));
      }
    }

    // 2-4. Eliminate down to one, or to two for the two-team steps.
    function settle(survivors, step, text) {
      if (survivors.length === 1) {
        var w = survivors[0];
        ctx.log.push({ teams: teams, step: step, winner: w, text: text });
        return [w].concat(rank(rec, ctx, teams.filter(function (t) {
          return t !== w;
        })));
      }
      if (survivors.length === 2) {
        ctx.log.push({ teams: teams, step: step, winner: null,
          text: text + "; " + survivors.join(" and ") + " go to the two-team steps" });
        var top = two(rec, ctx, survivors[0], survivors[1])[0];
        return [top].concat(rank(rec, ctx, teams.filter(function (t) {
          return t !== top;
        })));
      }
      return null;
    }
    var r = walk(rec, ctx, teams, false);
    var done = r.at && settle(r.survivors, "next-highest",
      "against " + nameOf(r.at) + ": " + detail(r.vals, teams));
    if (done) return done;
    var left = r.survivors;
    var rp = roadPct(rec, left);
    done = rp.vals && rp.survivors.length < left.length &&
      settle(rp.survivors, "road", "conference road record: " + detail(rp.vals, left));
    if (done) return done;
    left = rp.survivors;
    var rr = walk(rec, ctx, left, true);
    done = rr.at && settle(rr.survivors, "road-next-highest",
      "conference road record against " + nameOf(rr.at) + ": " +
      detail(rr.vals, left));
    if (done) return done;
    ctx.log.push({ teams: teams, step: "draw", winner: null,
      text: "nothing separates " + rr.survivors.join(", ") +
        "; the conference would hold a draw" });
    ctx.unresolved.push(rr.survivors.slice());
    var rest = teams.filter(function (t) { return rr.survivors.indexOf(t) < 0; });
    return rr.survivors.concat(rest.length ? rank(rec, ctx, rest) : []);
  }

  /* The standings, seeded. Every row:
   *   {seed, team, w, l, pct, rw, rl, group: [tied teams], log: [steps that
   *    involved this team], tied: bool, unresolved: bool}
   * plus `champions`, the teams sharing the best record. */
  function standings(games, teams) {
    var rec = records(games, teams);
    var gs = groups(rec, teams);
    var ctx = { places: placements(rec, gs), log: [], unresolved: [] };
    var order = [];
    gs.forEach(function (g) { order = order.concat(rank(rec, ctx, g)); });
    var stuck = {};
    ctx.unresolved.forEach(function (u) {
      u.forEach(function (t) { stuck[t] = true; });
    });
    var groupOf = {};
    gs.forEach(function (g) { g.forEach(function (t) { groupOf[t] = g; }); });
    var rows = order.map(function (t, i) {
      var r = rec[t];
      return {
        seed: i + 1, team: t, w: r.w, l: r.l, pct: pct(r.w, r.l),
        rw: r.rw, rl: r.rl, group: groupOf[t], tied: groupOf[t].length > 1,
        unresolved: !!stuck[t],
        log: ctx.log.filter(function (e) { return e.teams.indexOf(t) >= 0; }),
      };
    });
    var top = gs[0] || [];
    var played = top.length && (rec[top[0]].w + rec[top[0]].l) > 0;
    return { rows: rows, champions: played ? top.slice() : [] };
  }

  // ---- probabilities ----------------------------------------------------------

  // The standard normal CDF (Abramowitz and Stegun 7.1.26), which is all a
  // margin model needs to turn a rating gap into a chance of winning.
  function phi(x) {
    var t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2);
    var y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
      - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x / 2);
    return x >= 0 ? (1 + y) / 2 : (1 - y) / 2;
  }

  /* The chance `home` beats `away`: ratings in points, home court added
   * unless the floor is neutral, the spread of a margin around its
   * prediction as sigma. */
  function winProb(model, home, away, neutral) {
    var r = model.ratings || {};
    var gap = (r[home] || 0) - (r[away] || 0) + (neutral ? 0 : (model.hfa || 0));
    return phi(gap / (model.sigma || 11));
  }

  // A seeded generator, so the same results and ratings always give the same
  // odds: the build has to be byte-for-byte repeatable.
  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ---- the tournament -----------------------------------------------------------

  /* The Kansas City bracket by seed, as the 2026 tournament ran it: four
   * first-round games among 9-16, the winners meeting 5-8, those winners
   * meeting 1-4, then the semifinals and the final. Each slot is a seed, or
   * the winner of an earlier game. */
  var BRACKET = [
    // first round
    [9, 16], [12, 13], [10, 15], [11, 14],
    // second round: 8, 5, 7, 6 against the first-round winners
    [8, "g0"], [5, "g1"], [7, "g2"], [6, "g3"],
    // quarterfinals
    [1, "g4"], [4, "g5"], [2, "g6"], [3, "g7"],
    // semifinals, final
    ["g8", "g9"], ["g10", "g11"], ["g12", "g13"],
  ];
  var ROUND_OF = [1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 5];
  var ROUNDS = ["first round", "second round", "quarterfinals", "semifinals",
    "final", "champion"];

  /* Each team's chance of reaching each round, given the seeds:
   * {team: [play first round or later, second, quarters, semis, final, win]}.
   * Exact, by carrying a distribution over who fills each slot. */
  function bracketOdds(order, model) {
    var slot = {};
    function dist(x) {
      if (typeof x === "number") {
        var d = {}; d[order[x - 1]] = 1; return d;
      }
      return slot[x];
    }
    var reach = {};
    order.forEach(function (t, i) {
      var seed = i + 1;
      // Where a team enters: 9-16 the first round, 5-8 the second, 1-4 the
      // quarterfinals. Reaching a round it skips is certain.
      var start = seed <= 4 ? 2 : seed <= 8 ? 1 : 0;
      reach[t] = [0, 0, 0, 0, 0, 0];
      for (var k = 0; k <= start; k++) reach[t][k] = 1;
    });
    BRACKET.forEach(function (game, gi) {
      var a = dist(game[0]), b = dist(game[1]), out = {};
      Object.keys(a).forEach(function (x) {
        Object.keys(b).forEach(function (y) {
          var pa = a[x] * b[y];
          var p = winProb(model, x, y, true);
          out[x] = (out[x] || 0) + pa * p;
          out[y] = (out[y] || 0) + pa * (1 - p);
        });
      });
      slot["g" + gi] = out;
      var next = ROUND_OF[gi];
      Object.keys(out).forEach(function (t) { reach[t][next] += out[t]; });
    });
    return reach;
  }

  function playBracket(order, model, rand) {
    var slot = {};
    function who(x) { return typeof x === "number" ? order[x - 1] : slot[x]; }
    BRACKET.forEach(function (game, gi) {
      var a = who(game[0]), b = who(game[1]);
      slot["g" + gi] = rand() < winProb(model, a, b, true) ? a : b;
    });
    return slot.g14;
  }

  // ---- the season, simulated -------------------------------------------------------

  /* Play the rest of the conference season `n` times and seed each result
   * with the full procedure, then play the bracket from those seeds.
   *
   *   {n, teams: {team: {seed: [16 counts], top1, top4, top8, share,
   *                       title, expW}}}
   *
   * A draw in a simulated season is settled by the generator, the way the
   * conference would settle it with slips of paper. */
  function simulate(games, teams, model, n, seed) {
    var rand = rng(seed || 20262027);
    var done = games.filter(function (g) { return g.final; });
    var left = games.filter(function (g) { return !g.final; });
    var probs = left.map(function (g) {
      return winProb(model, g.home, g.away, g.neutral);
    });
    var out = {};
    teams.forEach(function (t) {
      out[t] = { seed: teams.map(function () { return 0; }), top1: 0, top4: 0,
        top8: 0, share: 0, title: 0, expW: 0 };
    });
    for (var i = 0; i < n; i++) {
      var played = done.slice();
      left.forEach(function (g, j) {
        var homeWins = rand() < probs[j];
        played.push({ home: g.home, away: g.away, neutral: g.neutral,
          final: true, hp: homeWins ? 1 : 0, ap: homeWins ? 0 : 1 });
      });
      var st = standings(played, teams);
      var order = st.rows.map(function (r) { return r.team; });
      // A draw is a coin the conference flips; shuffle the stuck teams.
      st.rows.forEach(function (r, k) {
        out[r.team].seed[k]++;
        if (k === 0) out[r.team].top1++;
        if (k < 4) out[r.team].top4++;
        if (k < 8) out[r.team].top8++;
        out[r.team].expW += r.w;
      });
      st.champions.forEach(function (t) { out[t].share++; });
      out[playBracket(order, model, rand)].title++;
    }
    Object.keys(out).forEach(function (t) {
      var o = out[t];
      ["top1", "top4", "top8", "share", "title", "expW"].forEach(function (k) {
        o[k] = o[k] / n;
      });
      o.seed = o.seed.map(function (c) { return c / n; });
    });
    return { n: n, teams: out };
  }

  // ---- what is already certain ------------------------------------------------------

  /* Clinched and eliminated, for a share of the title and for the top 1, 4
   * and 8 seeds: only what holds however every remaining game and every tie
   * goes. A team is safe in the top k if fewer than k others could even
   * reach its present win total; it is out of the top k if k others already
   * have more wins than it can reach. Sound, and deliberately so: a tie a
   * team might win counts against it here. */
  function certain(games, teams) {
    var rec = records(games, teams);
    var left = {};
    teams.forEach(function (t) { left[t] = 0; });
    games.forEach(function (g) {
      if (!g.final && g.home in left && g.away in left) {
        left[g.home]++; left[g.away]++;
      }
    });
    var out = {};
    teams.forEach(function (t) {
      var lo = rec[t].w, hi = rec[t].w + left[t];
      var threats = teams.filter(function (o) {
        return o !== t && rec[o].w + left[o] >= lo;
      }).length;
      var ahead = teams.filter(function (o) {
        return o !== t && rec[o].w > hi;
      }).length;
      var r = { minW: lo, maxW: hi, left: left[t] };
      [1, 4, 8].forEach(function (k) {
        r["in" + k] = threats < k;
        r["out" + k] = ahead >= k;
      });
      r.shareIn = teams.every(function (o) {
        return o === t || rec[o].w + left[o] <= lo;
      });
      r.shareOut = teams.some(function (o) { return o !== t && rec[o].w > hi; });
      out[t] = r;
    });
    return out;
  }

  var api = { standings: standings, records: records, pct: pct,
    winProb: winProb, bracketOdds: bracketOdds, simulate: simulate,
    certain: certain, ROUNDS: ROUNDS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.B12BB = api;
})(this);

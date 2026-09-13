/* Big 12 tiebreaker engine — JavaScript port of tiebreaker.py.
 *
 * Kept behaviorally identical to the Python engine; tests/test_parity.py
 * runs random seasons through both and diffs the output. If you change one,
 * change the other.
 */
(function (global) {
  "use strict";

  /* Both scores present, which is not the same as the game being over. The
   * port of tiebreaker.py's has_score, and see that docstring for why the
   * feed hands out rows with one score filled and the other still null.
   *
   * The half-filled row fails differently on this side, and worse. Python
   * compares the two numbers and raises TypeError, which stops a build and
   * gets noticed. JavaScript coerces null to 0 in a relational compare, so
   * `28 > null` is true and `null > 17` is false: winner() quietly hands the
   * game to whichever team has a score posted, and the standings render as
   * though that were a real result. Nothing throws and nobody finds out.
   */
  function hasScore(g) {
    return g.home_points !== null && g.home_points !== undefined &&
      g.away_points !== null && g.away_points !== undefined;
  }

  function confGames(games) {
    return games.filter(function (g) {
      return g.conference_game && g.completed && hasScore(g) && !g.ccg;
    });
  }

  /* The team that scored more, or null — including when there is nothing to
   * compare.
   *
   * THE hasScore GUARD IS THE POINT, and it was missing here while the
   * comment at the top of this file described the exact bug it allows. A row
   * with an integer home score and a null away score reaches the comparison,
   * `28 > null` coerces the null to 0 and is true, and the home team is handed
   * a game with no away score. Callers that reach winner() through confGames
   * were safe, because that filters on hasScore; two did not, and they are the
   * two that matter — the overall and non-conference tallies in standings(),
   * which put the wrong record on the page, and stepTotalWins, which is step
   * (e) of the ladder and decides who advances.
   *
   * Guarding here rather than at those two call sites, because the next caller
   * would have to remember too. rules_lite.winner has always read this way;
   * this is the copy that was wrong. Found by tests/test_malformed.py.
   */
  function winner(g) {
    if (!hasScore(g)) return null;
    if (g.home_points === g.away_points) return null;
    return g.home_points > g.away_points ? g.home : g.away;
  }

  function confRecords(games) {
    var rec = {};
    confGames(games).forEach(function (g) {
      var w = winner(g);
      [g.home, g.away].forEach(function (t) {
        if (!rec[t]) rec[t] = [0, 0];
      });
      if (w) {
        rec[w][0] += 1;
        var loser = w === g.home ? g.away : g.home;
        rec[loser][1] += 1;
      }
    });
    return rec;
  }

  function pct(w, l) {
    return (w + l) ? w / (w + l) : null;
  }

  function opponents(team, games) {
    var out = {};
    confGames(games).forEach(function (g) {
      if (g.home === team) out[g.away] = true;
      else if (g.away === team) out[g.home] = true;
    });
    return out;
  }

  function recordVs(team, others, games) {
    var w = 0, l = 0;
    confGames(games).forEach(function (g) {
      var other;
      if (g.home === team && others[g.away]) other = g.away;
      else if (g.away === team && others[g.home]) other = g.home;
      else return;
      var win = winner(g);
      if (win === team) w += 1;
      else if (win === other) l += 1;
    });
    return [w, l];
  }

  function toSet(arr) {
    var s = {};
    arr.forEach(function (t) { s[t] = true; });
    return s;
  }

  function placementGroups(games) {
    var rec = confRecords(games);
    var byPct = {};
    Object.keys(rec).forEach(function (t) {
      var w = rec[t][0], l = rec[t][1];
      var p = (w + l) ? pct(w, l) : -1;
      var k = String(p);
      (byPct[k] = byPct[k] || []).push(t);
    });
    return Object.keys(byPct)
      .sort(function (a, b) { return parseFloat(b) - parseFloat(a); })
      .map(function (k) { return byPct[k].sort(); });
  }

  function fmtPct(p) {
    return p === null ? "—" : p.toFixed(3);
  }

  function bestUnique(vals, higherBetter) {
    if (higherBetter === undefined) higherBetter = true;
    var teams = Object.keys(vals);
    for (var i = 0; i < teams.length; i++) {
      if (vals[teams[i]] === null || vals[teams[i]] === undefined) return null;
    }
    var best = null;
    teams.forEach(function (t) {
      if (best === null || (higherBetter ? vals[t] > best : vals[t] < best)) {
        best = vals[t];
      }
    });
    var leaders = teams.filter(function (t) { return vals[t] === best; });
    return leaders.length === 1 ? leaders[0] : null;
  }

  function stepH2h(tied, games, log) {
    var a = tied[0], b = tied[1];
    var r = recordVs(a, toSet([b]), games);
    if (r[0] > r[1]) {
      log.push("(a) Head-to-head: " + a + " defeated " + b + ".");
      return a;
    }
    if (r[1] > r[0]) {
      log.push("(a) Head-to-head: " + b + " defeated " + a + ".");
      return b;
    }
    log.push("(a) Head-to-head: " + a + " and " + b + " did not play.");
    return null;
  }

  function stepAmongTied(tied, games, log) {
    var pairs = {};
    tied.forEach(function (t) { pairs[t] = {}; });
    var tiedSet = toSet(tied);
    confGames(games).forEach(function (g) {
      if (tiedSet[g.home] && tiedSet[g.away]) {
        pairs[g.home][g.away] = true;
        pairs[g.away][g.home] = true;
      }
    });
    var fullRoundRobin = tied.every(function (t) {
      return tied.every(function (o) { return o === t || pairs[t][o]; });
    });

    if (!fullRoundRobin) {
      for (var i = 0; i < tied.length; i++) {
        var t = tied[i];
        var others = {};
        tied.forEach(function (o) { if (o !== t) others[o] = true; });
        var r = recordVs(t, others, games);
        if (r[0] === tied.length - 1 && r[1] === 0) {
          log.push("(a) Not all tied teams played each other, but " + t +
            " defeated every other tied team — seeded.");
          return t;
        }
      }
      log.push("(a) Not all tied teams played each other and no team " +
        "defeated all others — proceed to next step.");
      return null;
    }

    var vals = {};
    tied.forEach(function (t) {
      var others = {};
      tied.forEach(function (o) { if (o !== t) others[o] = true; });
      var r = recordVs(t, others, games);
      vals[t] = pct(r[0], r[1]);
    });
    var detail = tied.slice().sort().map(function (t) {
      return t + " " + fmtPct(vals[t]);
    }).join(", ");
    var win = bestUnique(vals);
    if (win) {
      log.push("(a) Record among tied teams: " + detail + " — " + win + " seeded.");
    } else {
      log.push("(a) Record among tied teams: " + detail + " — no single leader.");
    }
    return win;
  }

  function stepCommonOpponents(tied, games, log) {
    var tiedSet = toSet(tied);
    var common = null;
    tied.forEach(function (t) {
      var opp = opponents(t, games);
      Object.keys(opp).forEach(function (o) {
        if (tiedSet[o]) delete opp[o];
      });
      if (common === null) common = opp;
      else {
        Object.keys(common).forEach(function (o) {
          if (!opp[o]) delete common[o];
        });
      }
    });
    if (!common || Object.keys(common).length === 0) {
      log.push("(b) Common conference opponents: none — proceed.");
      return null;
    }
    var vals = {};
    tied.forEach(function (t) {
      var r = recordVs(t, common, games);
      vals[t] = pct(r[0], r[1]);
    });
    var detail = tied.slice().sort().map(function (t) {
      return t + " " + fmtPct(vals[t]);
    }).join(", ");
    var win = bestUnique(vals);
    var names = Object.keys(common).sort().join(", ");
    if (win) {
      log.push("(b) vs common opponents (" + names + "): " + detail +
        " — " + win + " seeded.");
    } else {
      log.push("(b) vs common opponents (" + names + "): " + detail +
        " — still tied.");
    }
    return win;
  }

  function stepNextHighestCommon(tied, games, log) {
    var groups = placementGroups(games);
    var tiedSet = toSet(tied);
    for (var gi = 0; gi < groups.length; gi++) {
      var grpSet = {};
      groups[gi].forEach(function (t) { if (!tiedSet[t]) grpSet[t] = true; });
      if (Object.keys(grpSet).length === 0) continue;
      var recs = {};
      var missing = false;
      tied.forEach(function (t) {
        recs[t] = recordVs(t, grpSet, games);
        if (recs[t][0] + recs[t][1] === 0) missing = true;
      });
      if (missing) continue;
      var vals = {};
      tied.forEach(function (t) { vals[t] = pct(recs[t][0], recs[t][1]); });
      var distinct = {};
      tied.forEach(function (t) { distinct[String(vals[t])] = true; });
      if (Object.keys(distinct).length === 1) continue;
      var detail = tied.slice().sort().map(function (t) {
        return t + " " + recs[t][0] + "-" + recs[t][1];
      }).join(", ");
      var win = bestUnique(vals);
      var names = Object.keys(grpSet).sort().join("/");
      if (win) {
        log.push("(c) vs next-highest-placed common opponent group [" +
          names + "]: " + detail + " — " + win + " seeded.");
        return win;
      }
      log.push("(c) vs [" + names + "]: " + detail + " — separates some but " +
        "no single leader; continuing down the standings.");
    }
    log.push("(c) Walked full standings without a single leader — proceed.");
    return null;
  }

  function stepSos(tied, games, log) {
    var rec = confRecords(games);
    var vals = {};
    var parts = [];
    tied.slice().sort().forEach(function (t) {
      var w = 0, l = 0;
      Object.keys(opponents(t, games)).forEach(function (opp) {
        var r = rec[opp] || [0, 0];
        w += r[0];
        l += r[1];
      });
      vals[t] = pct(w, l);
      parts.push(t + " " + w + "-" + l + " (" + fmtPct(vals[t]) + ")");
    });
    var detail = parts.join(", ");
    var win = bestUnique(vals);
    if (win) {
      log.push("(d) Opponents' combined conference record: " + detail +
        " — " + win + " seeded.");
    } else {
      log.push("(d) Opponents' combined conference record: " + detail +
        " — still tied.");
    }
    return win;
  }

  function stepTotalWins(tied, games, log) {
    var vals = {};
    tied.forEach(function (t) {
      var wins = 0, fcsWins = 0;
      games.forEach(function (g) {
        if (!g.completed || g.ccg || winner(g) !== t) return;
        var otherClass = g.home === t ? g.away_class : g.home_class;
        if (g.home !== t && g.away !== t) return;
        if (otherClass && otherClass !== "fbs") fcsWins += 1;
        else wins += 1;
      });
      vals[t] = wins + Math.min(fcsWins, 1);
    });
    var detail = tied.slice().sort().map(function (t) {
      return t + " " + vals[t];
    }).join(", ");
    var win = bestUnique(vals);
    if (win) {
      log.push("(e) Total wins (max one FCS win): " + detail + " — " +
        win + " seeded.");
    } else {
      log.push("(e) Total wins (max one FCS win): " + detail + " — still tied.");
    }
    return win;
  }

  function stepSportsource(tied, overrides, log) {
    var ranks = (overrides && overrides.sportsource) || {};
    var vals = {};
    var missing = false;
    tied.forEach(function (t) {
      vals[t] = ranks[t] !== undefined ? ranks[t] : null;
      if (vals[t] === null) missing = true;
    });
    if (missing) {
      log.push("(f) SportSource Analytics rating: not available — " +
        "cannot be resolved automatically.");
      return null;
    }
    var win = bestUnique(vals, false);
    var detail = tied.slice().sort().map(function (t) {
      return t + " #" + vals[t];
    }).join(", ");
    if (win) {
      log.push("(f) SportSource Analytics ranking: " + detail + " — " +
        win + " seeded.");
    } else {
      log.push("(f) SportSource Analytics ranking: " + detail + " — still tied.");
    }
    return win;
  }

  function stepCoinToss(tied, overrides, log) {
    var order = (overrides && overrides.coin_toss) || [];
    var tiedSet = toSet(tied);
    for (var i = 0; i < order.length; i++) {
      if (tiedSet[order[i]]) {
        log.push("(g) Coin toss: " + order[i] + " won the toss.");
        return order[i];
      }
    }
    log.push("(g) Coin toss required — awaiting result.");
    return null;
  }

  function runLadder(tied, games, overrides, log) {
    var steps = tied.length === 2
      ? [function () { return stepH2h(tied, games, log); }]
      : [function () { return stepAmongTied(tied, games, log); }];
    steps = steps.concat([
      function () { return stepCommonOpponents(tied, games, log); },
      function () { return stepNextHighestCommon(tied, games, log); },
      function () { return stepSos(tied, games, log); },
      function () { return stepTotalWins(tied, games, log); },
      function () { return stepSportsource(tied, overrides, log); },
      function () { return stepCoinToss(tied, overrides, log); },
    ]);
    for (var i = 0; i < steps.length; i++) {
      var win = steps[i]();
      if (win) return win;
    }
    return null;
  }

  function breakTie(tied, games, overrides) {
    tied = tied.slice().sort();
    var log = [];
    var order = [];
    var events = [];
    var remaining = tied.slice();
    while (remaining.length > 1) {
      var n0 = remaining.length;
      var seeded = runLadder(remaining, games, overrides, log);
      if (seeded === null) {
        log.push("UNRESOLVED: " + remaining.join(", ") +
          " cannot be separated with available data.");
        order = order.concat(remaining);
        return { order: order, log: log, resolved: false, events: events };
      }
      var line = log[log.length - 1];
      var step = line.charAt(0) === "(" ? line.charAt(1) : null;
      events.push({ team: seeded, step: step, line: line });
      order.push(seeded);
      remaining.splice(remaining.indexOf(seeded), 1);
      if (remaining.length > 1 && n0 > 2) {
        log.push("Restarting procedure for remaining tied teams: " +
          remaining.join(", ") + ".");
      }
    }
    order = order.concat(remaining);
    return { order: order, log: log, resolved: true, events: events };
  }

  // Every conference team, whether or not it has a conference result yet.
  // confRecords only knows teams it has evidence for, which is right for
  // ranking and wrong for a table: before the first conference game it knows
  // nobody, so standings() returned an empty list and the Lab's board simply
  // stopped responding — including to the non-conference picks whose whole
  // job is to feed the Non-conf and Overall columns.
  //
  // This is build.py's pad_standings, ported. Same split as the server: the
  // engine ranks only what it can justify, and padding happens at display.
  function confTeams(games) {
    var seen = {};
    games.forEach(function (g) {
      if (!g.conference_game || g.ccg) return;
      seen[g.home] = 1;
      seen[g.away] = 1;
    });
    return Object.keys(seen);
  }

  // WHERE THE UNPLAYED TEAMS GO, which is not the bottom. A team with no
  // conference game is 0-0, and 0-0 is ahead of 0-1 on every standings board
  // anyone has ever published: the sort is winning percentage descending,
  // then losses ascending. Appending the block put the single team that had
  // LOST a conference game above the fourteen that had not, so after the
  // first Saturday of Big 12 play the board read as though Arizona were
  // second in the conference on the strength of losing to BYU.
  //
  // rows arrives in percentage order, so the block belongs immediately above
  // the first team with no conference wins: everything from there down is
  // .000 with at least one loss.
  //
  // Ranks are renumbered to board position afterwards, because the engine's
  // own ranks count only the teams it had evidence for. Left alone they say
  // 1 and 2 on a board where those teams sit first and sixteenth, and
  // data.json, standings.csv and the rank column would each publish a
  // different number for the same row.
  function pad(rows, games) {
    var listed = {};
    rows.forEach(function (r) { listed[r.team] = 1; });
    var missing = confTeams(games).filter(function (t) { return !listed[t]; })
      .sort();
    if (!missing.length) return rows;

    var tally = {};
    missing.forEach(function (t) { tally[t] = [0, 0, 0, 0]; });  // nw nl ow ol
    games.forEach(function (g) {
      if (!g.completed || g.ccg) return;
      if (!hasScore(g)) return;
      var w = winner(g);
      if (!w) return;
      var loser = w === g.home ? g.away : g.home;
      [[w, true], [loser, false]].forEach(function (pair) {
        var t = pair[0], won = pair[1];
        if (!tally[t]) return;
        tally[t][won ? 2 : 3] += 1;
        if (!g.conference_game) tally[t][won ? 0 : 1] += 1;
      });
    });

    var block = missing.map(function (t) {
      return {
        rank: null, team: t, conf_w: 0, conf_l: 0,
        nonconf_w: tally[t][0], nonconf_l: tally[t][1],
        overall_w: tally[t][2], overall_l: tally[t][3],
        tie_group: null, log: null, events: null,
        // One unplayed team slots in at a position of its own on record
        // alone; two or more share it, and nothing about them is settled.
        resolved: missing.length === 1,
        // The marker the display reads. `rank: null` used to serve as it,
        // and cannot any more now that these rows carry a real position.
        unplayed: true,
      };
    });

    var cut = rows.length;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].conf_w === 0) { cut = i; break; }
    }
    var blockRank = cut + 1;
    var out = [];
    rows.slice(0, cut).concat(block, rows.slice(cut)).forEach(function (r, j) {
      // Copied, not renumbered in place: the caller still holds the rows
      // standings() gave it, where rank means "the procedure placed you
      // here" over the teams it had evidence for.
      var c = {};
      for (var k in r) {
        if (Object.prototype.hasOwnProperty.call(r, k)) c[k] = r[k];
      }
      c.rank = c.unplayed ? blockRank : j + 1;
      out.push(c);
    });
    return out;
  }

  // The rank column, honest about what the ladder proved.
  //
  // A tie the procedure could not finish has no order inside its unresolved
  // remainder: the engine hands those teams back alphabetically, which is a
  // storage order and not a placement. Numbering them 1..6 publishes the
  // alphabet as a ranking, so the remainder shares one position ("T1") the
  // way the official board already writes shared positions. Teams the ladder
  // DID seed before running out of data keep their real ranks, and `events`
  // records exactly how many it made.
  //
  // The unplayed block is the same problem arriving from the other side.
  // Those teams are level at 0-0 with nothing to separate them, so they
  // share a position too.
  //
  // This lived in three places: here, in build.py and in app.js. That is
  // exactly the duplication the rest of this file exists to have deleted,
  // and it is load-bearing now, because the matchup card is computed from
  // these labels. Two copies could put a team on the board at one position
  // and in the title game from another.
  function displayRanks(rows) {
    var out = {};
    var i = 0;
    while (i < rows.length) {
      var r = rows[i];
      if (r.unplayed) {
        var n = 0;
        while (i + n < rows.length && rows[i + n].unplayed) n += 1;
        var label = n > 1 ? "T" + r.rank : String(r.rank);
        for (var k = 0; k < n; k++) out[rows[i + k].team] = label;
        i += n;
        continue;
      }
      if (r.resolved || !r.tie_group) {
        out[r.team] = r.rank ? String(r.rank) : "\u2014";
        i += 1;
        continue;
      }
      var grp = rows.filter(function (x) { return x.tie_group === r.tie_group; });
      var seeded = (r.events || []).length;
      grp.forEach(function (x, j) {
        out[x.team] = j < seeded ? String(x.rank) : "T" + grp[seeded].rank;
      });
      i += grp.length;
    }
    return out;
  }

  function standings(games, overrides) {
    var rec = confRecords(games);
    if (Object.keys(rec).length === 0) return [];
    var overall = {};
    var nonconf = {};
    games.forEach(function (g) {
      if (!g.completed) return;
      var w = winner(g);
      [g.home, g.away].forEach(function (t) {
        if (rec[t] && !overall[t]) { overall[t] = [0, 0]; nonconf[t] = [0, 0]; }
      });
      var nc = !g.conference_game && !g.ccg;
      if (w && rec[w]) {
        if (!overall[w]) { overall[w] = [0, 0]; nonconf[w] = [0, 0]; }
        overall[w][0] += 1;
        if (nc) nonconf[w][0] += 1;
      }
      if (w) {
        var loser = w === g.home ? g.away : g.home;
        if (rec[loser]) {
          if (!overall[loser]) { overall[loser] = [0, 0]; nonconf[loser] = [0, 0]; }
          overall[loser][1] += 1;
          if (nc) nonconf[loser][1] += 1;
        }
      }
    });

    var rows = [];
    var rank = 1;
    placementGroups(games).forEach(function (grp) {
      var ordered, log, resolved, tieId, events;
      if (grp.length === 1) {
        ordered = grp; log = null; resolved = true; tieId = null; events = null;
      } else {
        var r = breakTie(grp, games, overrides);
        ordered = r.order; log = r.log; resolved = r.resolved;
        events = r.events;
        tieId = grp.slice().sort().join("+");
      }
      ordered.forEach(function (t, i) {
        var o = overall[t] || [0, 0];
        var n = nonconf[t] || [0, 0];
        rows.push({
          rank: rank + i, team: t,
          conf_w: rec[t][0], conf_l: rec[t][1],
          nonconf_w: n[0], nonconf_l: n[1],
          overall_w: o[0], overall_l: o[1],
          tie_group: tieId,
          log: i === 0 ? log : null,
          events: i === 0 ? events : null,
          resolved: resolved,
        });
      });
      rank += ordered.length;
    });
    return rows;
  }

  var COUNT_WORDS = ["", "one", "two", "three", "four", "five", "six",
    "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
    "fourteen", "fifteen", "sixteen"];

  function countWord(n) {
    return COUNT_WORDS[n] || String(n);
  }

  function cap(s) {
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  /* The two teams that would play for the title if the season ended now, or
   * an honest refusal to name one of them.
   *
   * IT USED TO READ THE TOP TWO ROWS OF standings() AND STOP. standings()
   * ranks only the teams it has conference evidence for, so on the Sunday
   * after the first Big 12 game that board was two rows long: the winner and
   * the loser. The card therefore announced the team that had just lost as
   * the #2 seed, with a green "resolved" badge on it, while fourteen teams
   * with no conference loss sat below them.
   *
   * The same shortcut named an unresolved tie. breakTie hands its remainder
   * back in storage order, so eight teams tied at 1-0 came out of here as
   * "Arizona vs Baylor", which is the alphabet with two logos on it. The
   * warn badge under it is a caption; the logos are the claim, and the claim
   * was made up.
   *
   * So: computed off the padded board, where all sixteen teams are placed by
   * record, and a slot is filled only when displayRanks says one team holds
   * that position outright. A shared position ("T2") fills nothing. What
   * goes in the slot then is the candidate list, and `reason` says which
   * kind of not-knowing it is, because "needs SportSource rating or coin
   * toss" is a true sentence about a tie the ladder could not break and a
   * false one about teams that have not played yet.
   */
  function championship(games, overrides) {
    var ranked = standings(games, overrides);
    if (!ranked.length) return null;
    var rows = pad(ranked, games);
    if (rows.length < 2) return null;
    var labels = displayRanks(rows);

    function held(i) {
      return labels[rows[i].team].charAt(0) !== "T";
    }

    function sharing(i) {
      var label = labels[rows[i].team];
      return rows.filter(function (r) { return labels[r.team] === label; })
        .map(function (r) { return r.team; });
    }

    var groups = placementGroups(games);
    var top = groups[0];
    if (top.length === 2 && held(0) && held(1)) {
      var a = top[0], b = top[1];
      var ra = recordVs(a, toSet([b]), games);
      var rb = recordVs(b, toSet([a]), games);
      if (ra[0] || rb[0]) {
        var one = ra[0] ? a : b;
        var two = ra[0] ? b : a;
        return {
          seed1: one, seed2: two, pending: null, reason: null,
          note: "Two teams tied for first: both play in the championship " +
            "game; " + one + " is the #1 seed by head-to-head win.",
          resolved: true,
        };
      }
    }

    var seed1 = held(0) ? rows[0].team : null;
    var seed2 = seed1 && held(1) ? rows[1].team : null;
    if (seed1 && seed2) {
      return {
        seed1: seed1, seed2: seed2, pending: null, reason: null, note: null,
        resolved: rows[0].resolved && rows[1].resolved,
      };
    }

    // One slot open at most matters: a position nobody holds outright is
    // shared by everyone in the group, and the first open slot is the one
    // the card has to explain.
    var open = seed1 ? 1 : 0;
    var group = sharing(open);
    var row = rows[open];
    var rec = row.conf_w + "\u2013" + row.conf_l;
    var many = countWord(group.length);
    var note = row.unplayed
      ? cap(many) + " teams have not played a conference game yet, so " +
        "nothing separates them and " +
        (seed1 ? "the second seed is open." : "neither seed is settled.")
      : cap(many) + " teams are tied at " + rec + " and the procedure " +
        "cannot separate them with the data available.";
    return {
      seed1: seed1, seed2: seed2, pending: group,
      reason: row.unplayed ? "unplayed" : "unresolved",
      note: note, resolved: false,
    };
  }

  var api = {
    confRecords: confRecords,
    placementGroups: placementGroups,
    breakTie: breakTie,
    standings: standings,
    pad: pad,
    displayRanks: displayRanks,
    championship: championship,
    pct: pct,
    winner: winner,
    hasScore: hasScore,
  };

  global.B12Engine = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);

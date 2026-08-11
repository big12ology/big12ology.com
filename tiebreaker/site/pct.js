/* Win-percentage colour curve, shared by every page that draws a standings
   row. Anchors are kept in sync with winpct_color in build.py — the Python
   side renders the first paint, this side renders everything after it, and
   they have to agree pixel for pixel. */
(function (root) {
  var ANCHORS = [[0.0, 0], [0.30, 20], [0.45, 45], [0.60, 75],
                 [0.70, 95], [0.80, 118], [0.90, 140], [1.0, 168]];

  function color(p) {
    var h = ANCHORS[ANCHORS.length - 1][1];
    if (p <= ANCHORS[0][0]) {
      h = ANCHORS[0][1];
    } else {
      for (var i = 1; i < ANCHORS.length; i++) {
        if (p <= ANCHORS[i][0]) {
          var t = (p - ANCHORS[i - 1][0]) / (ANCHORS[i][0] - ANCHORS[i - 1][0]);
          h = ANCHORS[i - 1][1] + t * (ANCHORS[i][1] - ANCHORS[i - 1][1]);
          break;
        }
      }
    }
    var s = h < 45 ? 100 - (h / 45) * 35 : 65;
    return "hsl(" + Math.round(h) + " " + Math.round(s) + "% var(--pctl))";
  }

  function fmt(p) {
    return p === null ? "—" : p.toFixed(3);
  }

  // Conference win percentage, or null when a team has not played one yet.
  function pct(w, l) {
    return w + l === 0 ? null : w / (w + l);
  }

  /* Against the spread, which is a different quantity and needs a different
     curve. README rule 3: resolution goes where the data lives.

     ANCHORS above put their resolution at the top — 0.60 is hue 75, 0.90 is
     140 — because a conference record really does run from .000 to 1.000 and
     the interesting part is the top of it. Real ATS records do not: beating
     the closing line is close to a coin flip, so a season lands between about
     .400 and .620, which that curve renders as one narrow band of
     yellow-green. Everybody would look identical, which is the opposite of
     what a leaderboard is for.

     Measured over a realistic board (.364 to .621): reusing ANCHORS spends 48
     degrees of hue on it, hues 31 to 79, which is amber to yellow-green and
     nothing else. This curve spends 123, and crosses zero.

     So it diverges around .500 instead, red below and green above, spending
     its whole range on the twenty points either side where the differences
     actually are. The hue path is the same red→amber→green walk as
     divergeHSL in attendance/site/charts.js, and lightness stays on --pctl so
     it inverts with the theme rather than against it.

     No Python counterpart on purpose. Nothing server-side renders the
     pick'em, and an unused mirror is exactly the drift the note at the top of
     this file warns about. */
  var ATS_ANCHORS = [[0.35, 0], [0.45, 25], [0.50, 48],
                     [0.55, 95], [0.65, 140]];

  function ats(p) {
    if (p == null) return "var(--dim)";
    var A = ATS_ANCHORS, h;
    if (p <= A[0][0]) h = A[0][1];
    else if (p >= A[A.length - 1][0]) h = A[A.length - 1][1];
    else {
      for (var i = 1; i < A.length; i++) {
        if (p <= A[i][0]) {
          var t = (p - A[i - 1][0]) / (A[i][0] - A[i - 1][0]);
          h = A[i - 1][1] + t * (A[i][1] - A[i - 1][1]);
          break;
        }
      }
    }
    // Saturation dips at the turn so .500 reads as neutral rather than as a
    // confident amber verdict on a player who is exactly average.
    var d = Math.abs(p - 0.5);
    var s = 30 + Math.min(d / 0.12, 1) * 40;
    return "hsl(" + Math.round(h) + " " + Math.round(s) + "% var(--pctl))";
  }

  /* Odds of reaching a two-team game out of sixteen, which is a third
     quantity again. Kept in sync with ccg_color in build.py: the race card
     is painted server-side on The Race and client-side on The Lab, and the
     same probability has to come out the same colour on both.

     The curve is indexed on p / (spots / teams) — how many times a team's
     odds beat the flat 2-in-16 share — rather than on p itself, so "average"
     always lands in the same place no matter how many seats there are. */
  var CCG_ANCHORS = [[0.0, 0], [0.35, 12], [0.7, 30], [1.0, 45], [1.6, 72],
                     [2.5, 100], [3.5, 128], [5.0, 152], [8.0, 168]];

  function ccg(p, teams, spots) {
    if (!teams) return color(p);
    var base = (spots || 2) / teams;
    var r = (p || 0) / base;
    var A = CCG_ANCHORS, h = A[A.length - 1][1];
    if (r <= A[0][0]) {
      h = A[0][1];
    } else {
      for (var i = 1; i < A.length; i++) {
        if (r <= A[i][0]) {
          var t = (r - A[i - 1][0]) / (A[i][0] - A[i - 1][0]);
          h = A[i - 1][1] + t * (A[i][1] - A[i - 1][1]);
          break;
        }
      }
    }
    var s = h < 45 ? 100 - (h / 45) * 35 : 65;
    return "hsl(" + Math.round(h) + " " + Math.round(s) + "% var(--pctl))";
  }

  /* Matches fmt_prob in build.py, including the always-one-decimal rule —
     a column of these only lines up if every number has the same shape. */
  function prob(p) {
    if (p >= 0.9995) return "100%";
    if (p <= 0) return "0%";
    if (p < 0.001) return "<0.1%";
    return (p * 100).toFixed(1) + "%";
  }

  root.B12PCT = { color: color, fmt: fmt, pct: pct, ats: ats, ccg: ccg,
                  prob: prob, ANCHORS: ANCHORS, ATS_ANCHORS: ATS_ANCHORS,
                  CCG_ANCHORS: CCG_ANCHORS };
})(window);

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

  root.B12PCT = { color: color, fmt: fmt, pct: pct, ats: ats,
                  ANCHORS: ANCHORS, ATS_ANCHORS: ATS_ANCHORS };
})(window);

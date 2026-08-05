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

  root.B12PCT = { color: color, fmt: fmt, pct: pct, ANCHORS: ANCHORS };
})(window);

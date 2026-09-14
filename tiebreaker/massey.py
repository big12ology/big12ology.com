#!/usr/bin/env python3
"""A rating this project computes itself, in points of margin.

Every other system on the Nerds page is a number somebody else publishes
and we quote. This one is arithmetic on results we already hold, which
buys three things the quoted ones cannot.

  It is ours. No terms of use, no rate limit, no endpoint to go away.
  Sagarin and Fremeau both publish parseable ratings and both reserve all
  rights; Massey returns 403 to a browser user-agent, which is a clear
  enough answer. Implementing a published METHOD on our own results is a
  different act from republishing somebody's output.

  It is explainable. The Nerds page exists to show the working, and four
  of its five opinions are black boxes we take on trust. This one is a
  least-squares fit anyone can check: the rating that best explains every
  margin played, with home advantage as a free parameter rather than an
  assumption.

  Nothing reruns it under us. CFBD's CORE carries a modelVersion field and
  changed scale by a factor of two between two of them; a calibration
  fitted to one version silently stops applying to the next.

THE MODEL. For each game, margin = rating(home) - rating(away) + hfa, and
the fit picks the ratings and the hfa minimizing squared error. That is
Massey's least-squares method, published and decades old. Ratings come out
in points, so per_pt is 1.0 by construction rather than by calibration —
which is the whole reason to prefer this shape over a rating in some other
unit that has to be fitted onto points afterwards.

MEASURED, NOT ASSERTED. Against the other four systems at matched vintage,
all predicting the same 92 games none of them had seen — 2025's final
ratings against 2026's opening weeks:

    ours   R2 0.357   MAE 14.7        SP+    R2 0.259   MAE 15.7
    SRS    R2 0.312   MAE 15.1        CORE   R2 0.213   MAE 16.1
    Elo    R2 0.310   MAE 15.0

A paired bootstrap on absolute error puts it ahead of SP+ by 0.97 points
(95% CI +0.28 to +1.66) and SRS by 0.46 (+0.04 to +0.87), and level with
Elo at +0.28 (-0.42 to +1.01). So: as good as the best of them, not better
than all of them. The home-field coefficient comes out near 3 points,
which is what college football's actually is, and the 2025 top eight read
Indiana, Ohio State, Texas Tech, Oregon, Notre Dame, Utah, Miami, Georgia.
Both say the fit is specified right rather than lucky.

THAT TEST WAS THE WRONG SHAPE, and the correction is worth recording.
Predicting a NEW season from a finished one is not what this site does; it
rates the season in progress and forecasts the week ahead. Asked the right
question — train on the weeks before k, predict week k — the conclusions
about how to regularize reversed completely. RIDGE below carries the
numbers. The lesson generalises past this file: an evaluation that does
not match the use can be run carefully, reported honestly, and still point
the wrong way.

PURE PYTHON ON PURPOSE. The repo installs no Python dependencies and the
workflows run on stock CPython, so pulling in numpy for one rating would
put a pip step in front of every build. The normal equations here are a
graph Laplacian with one extra column, built in one pass over the games
and solved by elimination; at 136 teams that is milliseconds.
"""

# How hard ratings are pulled toward the middle. A rating is an estimate
# from a handful of games, and early in a season a team that won once by 40
# is not four touchdowns better than everyone — it has one result. Ridge
# says so: it charges the fit for large ratings, so a rating only gets far
# from zero when the evidence keeps insisting.
#
# MEASURED, on the evaluation that matches how the site uses this. Train on
# the weeks before k, predict week k, for every k from 5 to the end of the
# season, across 2024 and 2025 separately:
#
#   no ridge, FBS only          MAE 13.96 (2024)   13.70 (2025)
#   ridge 2 + pooled non-FBS          12.60              12.43
#
# A paired bootstrap on those per-game errors puts the gain at +1.24 points
# in 2024 (95% CI +0.76 to +1.72) and +1.33 in 2025 (+0.77 to +1.91),
# winning 10 of 12 weeks and 9 of 12. Two independent seasons agreeing that
# closely is why this is a constant and not a knob.
#
# TWO THINGS THAT SOUNDED BETTER AND WERE NOT. Decaying the penalty as
# games accumulate is better motivated — a prior ought to yield to evidence,
# and on 2025 alone it removed a late-season regression — but across both
# seasons the difference came to -0.025 points, CI -0.117 to +0.067.
# Indistinguishable, so the simpler rule wins. And capping blowouts helped
# on its own (+0.81) but added nothing once ridge was there: both are
# regularization, and two doses is one too many.
RIDGE = 2.0

# How many games each team needs before the fit is published.
#
# Ridge makes a September schedule SOLVABLE, which is not the same as making
# it trustworthy, and the difference is visible in the one coefficient with a
# known right answer. Home field is about three points in college football.
# Fitted on 2025 as the season fills in:
#
#   1.4 games per team   hfa 12.61        6.9 per team   hfa 5.25
#   2.6 per team         hfa 10.86        8.6 per team   hfa 4.80
#   3.7 per team         hfa  8.41       11.0 per team   hfa 4.47
#   4.6 per team         hfa  6.93       13.0 per team   hfa 3.87
#
# Early on the fit cannot separate "this team is better" from "this team was
# at home", so the home term absorbs the difference and reports four times
# the real figure. It settles once teams have played five, which is also
# where the accuracy backtest starts working. Below that the rating is not
# published and last season's, regressed, is used instead.
MIN_GAMES_PER_TEAM = 5.0

# Ratings are only determined up to a shared constant — adding 5 to
# everyone leaves every margin unchanged — so the system is singular until
# something pins the level. Massey's fix, and the one used here: require
# the ratings to sum to zero, by replacing the last normal equation with
# that constraint. Centering afterwards would not be the same thing; the
# matrix has to be made non-singular before it can be solved at all.
SUM_TO_ZERO = True


# Every non-FBS opponent, as one team. See rows_from_games.
POOLED = "~non-FBS~"


def rows_from_games(games, classification="fbs", pool=True):
    """(home, away, margin, neutral) for every completed game worth fitting.

    NON-FBS OPPONENTS BECOME ONE POOLED TEAM rather than being discarded.
    Dropping them was the obvious call and the wrong one: it threw away 126
    games a season, and those games carry most information early, when
    there is least of it to go round. Beating an FCS side by 3 says
    something, and saying it requires the opponent to exist in the fit.

    Pooled rather than rated individually, because an FCS team that plays
    one FBS opponent all year has a rating determined entirely by that game
    and would hand its noise straight back. As one team with a hundred
    results it is a stable low baseline to measure against. Worth +0.79
    points of mean absolute error on its own.

    The pooled team is a nuisance parameter: fit() estimates it because the
    arithmetic needs it, then drops it, because it is not a team this site
    rates.

    Accepts CFBD's own shape (homeTeam/homePoints) so a caller can hand it
    a raw response without reshaping it first.
    """
    out = []
    for g in games:
        if not g.get("completed"):
            continue
        hp, ap = g.get("homePoints"), g.get("awayPoints")
        if hp is None or ap is None:
            continue
        hc, ac = g.get("homeClassification"), g.get("awayClassification")
        if classification and classification not in (hc, ac):
            continue                    # neither side is rated; no signal
        home, away = g.get("homeTeam"), g.get("awayTeam")
        if not home or not away or home == away:
            continue
        if classification:
            if hc != classification:
                home = POOLED if pool else None
            if ac != classification:
                away = POOLED if pool else None
            if home is None or away is None:
                continue
        out.append((home, away, hp - ap, bool(g.get("neutralSite"))))
    return out


def _solve(a, b):
    """Gaussian elimination with partial pivoting. Returns x for a x = b.

    Partial pivoting rather than naive elimination because the constraint
    row is all ones and the Laplacian rows are not, so the natural pivot
    order puts a small number on the diagonal at least once on any real
    schedule.
    """
    n = len(b)
    m = [list(row) + [v] for row, v in zip(a, b)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            raise ValueError("rating system is singular: the schedule does "
                             "not connect every team")
        m[c], m[p] = m[p], m[c]
        pivot = m[c][c]
        for r in range(c + 1, n):
            f = m[r][c] / pivot
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for c in range(n - 1, -1, -1):
        s = m[c][n] - sum(m[c][k] * x[k] for k in range(c + 1, n))
        x[c] = s / m[c][c]
    return x


def fit(rows, ridge=None):
    """({team: rating}, hfa) in points, from (home, away, margin, neutral).

    Builds the normal equations directly rather than forming a design
    matrix: for the team block that is games-played on the diagonal and
    minus-meetings off it, which is the schedule's graph Laplacian, and one
    pass over the games fills it. The extra row and column are the home
    advantage, which is a free parameter of the fit and not a constant
    anybody chose.
    """
    teams = sorted({t for h, a, _, _ in rows for t in (h, a)})
    if len(teams) < 2 or not rows:
        return {}, 0.0
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    a = [[0.0] * (n + 1) for _ in range(n + 1)]
    b = [0.0] * (n + 1)
    for home, away, margin, neutral in rows:
        i, j = idx[home], idx[away]
        h = 0.0 if neutral else 1.0
        a[i][i] += 1.0
        a[j][j] += 1.0
        a[i][j] -= 1.0
        a[j][i] -= 1.0
        a[i][n] += h
        a[n][i] += h
        a[j][n] -= h
        a[n][j] -= h
        a[n][n] += h * h
        b[i] += margin
        b[j] -= margin
        b[n] += h * margin
    # NOTHING TO ESTIMATE A HOME FIELD FROM is not the same as a home field
    # of zero, but it has to come out as zero either way. With every game
    # at a neutral site the hfa column is all zeros, so its row and column
    # in the normal equations vanish and the matrix is singular for a
    # reason that has nothing to do with the schedule being connected.
    # Drop the parameter instead of reporting a bogus one, or worse,
    # raising on a bowl-and-kickoff-classic slate that is perfectly
    # fittable for ratings.
    homeish = sum(1 for _, _, _, neu in rows if not neu)
    size = n if not homeish else n + 1
    lam = RIDGE if ridge is None else ridge
    if lam:
        # Ridge on the TEAM block only. The home-field term is a real
        # quantity being measured, not a rating to be talked out of; shrink
        # it and the fit quietly reports a smaller home edge than the games
        # show, which is a wrong answer rather than a cautious one.
        for i in range(n):
            a[i][i] += lam
    if SUM_TO_ZERO and not lam:
        # Only needed when nothing else pins the level. Ridge already does:
        # adding lam to the diagonal makes the matrix positive definite, so
        # it solves without a constraint, and the penalty centres the
        # ratings on its own. Replacing a row here as well would throw away
        # a real equation for no gain.
        #
        # Without ridge it IS needed, and it replaces the LAST TEAM's
        # equation rather than the home-advantage one: overwriting row n
        # would discard the only equation identifying hfa, and the fit
        # would return a rating system with no home field in it at all.
        a[n - 1] = [1.0] * n + [0.0]
        b[n - 1] = 0.0
    x = _solve([r[:size] for r in a[:size]], b[:size])
    hfa = x[n] if homeish else 0.0
    # The pooled non-FBS team is estimated because the arithmetic needs it
    # and dropped because it is not a team. Ratings are centered on what
    # remains, so "zero" means an average FBS team either way.
    out = {t: x[idx[t]] for t in teams if t != POOLED}
    if out:
        mid = sum(out.values()) / len(out)
        out = {t: round(v - mid, 4) for t, v in out.items()}
    return out, round(hfa, 4)


def system(games, year, classification="fbs"):
    """The systems-dict entry the build already knows how to consume.

    per_pt is 1.0 because the ratings ARE points: the model fits margins
    directly, so a one-point rating gap means a one-point favorite before
    home field. Nothing else here needs calibrating, which is the point.

    RETURNS NO RATINGS RATHER THAN RAISING when the schedule cannot
    support any. fit() is strict on purpose — an under-determined system
    is a fact worth an exception — but this is the build's entry point,
    and the build meets exactly that state every September. One week in,
    the played games do not connect the country: a single result gives one
    equation for two unknowns, the rating gap and the home field, and no
    arithmetic separates them.

    That is not an error, it is a season that has not happened yet. The
    empty dict flows through fetch_ratings the same way a system that does
    not yet cover the conference does, and the page shows four opinions
    until there are enough results for a fifth.
    """
    rows = rows_from_games(games, classification)
    try:
        ratings, hfa = fit(rows)
    except ValueError as e:
        print(f"massey {year}: not enough connected results yet ({e})")
        ratings, hfa = {}, 0.0
    # Thin, and therefore wrong rather than merely uncertain. See
    # MIN_GAMES_PER_TEAM: the home-field term is the tell.
    if ratings:
        per_team = 2.0 * len(rows) / len(ratings)
        if per_team < MIN_GAMES_PER_TEAM:
            print(f"massey {year}: {per_team:.1f} games per team, under "
                  f"{MIN_GAMES_PER_TEAM:g}; not published (home field would "
                  f"read {hfa:g})")
            ratings, hfa = {}, 0.0
    return {"year": year, "hfa": hfa, "per_pt": 1.0,
            "ratings": ratings, "games": len(rows)}

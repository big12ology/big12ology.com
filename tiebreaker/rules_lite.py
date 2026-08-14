"""Score arithmetic. NOT the tiebreaking rules — those live in JavaScript.

The Big 12 ladder is site/engine.js, reached through engine.py. This module is
the small residue that stayed in Python, and the line between them is worth
being exact about: nothing in here reads the tiebreaker policy. It asks whether
a row of the feed is a finished game and who scored more, which is arithmetic
about two integers.

WHY IT DID NOT GO WITH THE REST. Five modules — facts.py, feed.py, rotation.py,
scorecard.py and build.py — import the engine for these three functions and for
nothing else. facts.py alone calls has_score over sixteen seasons while
building prose about attendance. Making each of them start a node process and
hold a pipe open to ask "are both scores present" is where one-implementation
stops paying for itself.

AND THE PYTHON VERSION FAILS BETTER, which is the argument that actually
settles it. site/engine.js:11-19 records what happens on the other side: a row
holding one score and a null makes Python raise TypeError, which stops a build
and gets noticed, while JavaScript coerces null to 0, so `28 > null` is true
and winner() quietly hands the game to whichever team has a number posted. The
page renders a final nobody played. Keeping the loud one is not a compromise.

tests/test_engine.py checks these against the engine's own answers for every
game of every committed season, so the two cannot drift.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def has_score(g):
    """Both scores present, which is not the same as the game being over.

    The feed hands out rows with one score filled and the other still null —
    a game in progress, a cancellation, a row CFBD has started but not
    finished — and every comparison below this line assumes two numbers.
    """
    return g.get("home_points") is not None and g.get("away_points") is not None


def winner(g):
    """The team that scored more, or None for a tie or an unplayed game."""
    if not has_score(g):
        return None
    h, a = g["home_points"], g["away_points"]
    if h == a:
        return None
    return g["home"] if h > a else g["away"]


def pct(w, l):
    """Winning percentage, or None for a team that has not played.

    None rather than 0.0, and it is not a nicety: a team at 0-0 has no
    percentage, and a board that prints .000 for it says it is losing. The
    pages render None as a dash. Written as 0.0 here first, and the check
    against the engine caught it on the first run.
    """
    n = w + l
    return w / n if n else None


def load_overrides():
    """Steps (f) and (g) take inputs nobody publishes — the SportSource
    rating and a coin toss — so when a real tie reaches them the values are
    put in overrides.json by hand. Not a rule either: a file read."""
    p = os.path.join(HERE, "overrides.json")
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {}

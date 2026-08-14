#!/usr/bin/env python3
"""The bridge between build.py and the rules, which now live in JavaScript.

    python3 tests/test_engine.py

test_parity.py asks whether the two engines agree. This asks the narrower
question that replaced it in practice: whether engine.py hands site/engine.js
the same questions Python used to answer for itself, and hands the answers back
in the shape the callers already expect.

Those are different failures. A rules disagreement is caught by parity and, in
the end, by test_golden.py. A SHAPE disagreement is not: JavaScript's breakTie
returns {order, log, resolved, events} and Python's returns a four-tuple, so
`order, log, resolved, _ = break_tie(...)` unpacked the dict's keys and ordered
the 2024 tie ["order", "log", "resolved", "events"]. Nothing in the parity test
touches breakTie, and the only reason that surfaced at all is that
build_explainer happens to assert its own worked example. This covers it
directly instead of relying on that.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import engine                                            # noqa: E402
import tiebreaker as tb                                  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: engine {got!r} != tiebreaker {want!r}")


def load(year):
    return json.load(open(os.path.join(ROOT, "data", f"games_{year}.json")))


def seasons():
    for y in range(2011, 2027):
        p = os.path.join(ROOT, "data", f"games_{y}.json")
        if os.path.exists(p):
            yield y, json.load(open(p))


# --- the answers, against the engine that used to give them ----------------
# Every committed season, not a sample: the ladder's rarer steps only appear
# in the seasons that happened to need them, and a port that gets steps (a)
# through (c) right is the easy half.
n = 0
for year, games in seasons():
    check(f"{year} standings", engine.standings(games, {}), tb.standings(games, {}))
    check(f"{year} championship",
          engine.championship(games, {}), tb.championship(games, {}))
    check(f"{year} placement_groups",
          engine.placement_groups(games), tb.placement_groups(games))
    check(f"{year} conf_records", engine.conf_records(games), tb.conf_records(games))
    n += 1

# --- the shapes -------------------------------------------------------------
# break_tie is the one that differs across the boundary, so it is checked on a
# real multi-team tie rather than a synthetic one: 2024's four-way at 7-2.
games = load(2024)
groups = engine.placement_groups(games)
got, want = engine.break_tie(groups[0], games), tb.break_tie(groups[0], games)
check("break_tie tuple", got, want)
if not isinstance(got, tuple) or len(got) != 4:
    fails.append(f"break_tie must return a 4-tuple, got {type(got).__name__}")
else:
    order, log, resolved, events = got
    if not (order and order[0] == "Arizona State"):
        fails.append(f"break_tie unpacked wrong: order starts {order[:2]!r}")
    if not all(isinstance(x, str) for x in order):
        fails.append("break_tie order is not a list of team names")

check("pct", [engine.pct(w, l) for w in range(6) for l in range(6)],
      [tb.pct(w, l) for w in range(6) for l in range(6)])

# --- the failure modes ------------------------------------------------------
# An engine that answers the wrong question quietly is worse than one that is
# down, so the protocol's guards are worth a test of their own.
try:
    engine._call("nonesuch")
    fails.append("an unknown op should raise, not return")
except engine.EngineError as e:
    if "no such op" not in str(e):
        fails.append(f"unknown op raised the wrong thing: {e}")

try:
    engine._call("standings", "not-a-list-of-games")
    fails.append("a malformed request should raise, not return")
except engine.EngineError:
    pass

# The process must survive having been shouted at: a build asks the engine
# hundreds of questions and one bad one must not poison the rest.
check("still answering after two errors",
      engine.championship(games, {}), tb.championship(games, {}))

# --- and it must refuse to run without node ---------------------------------
# Checked by running a child with an empty PATH rather than by reading the
# source, because the thing being tested is that the message arrives instead
# of a FileNotFoundError three hundred lines into a build.
probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, %r); import engine; engine.standings([], {})" % ROOT],
    env={"PATH": "", "HOME": os.environ.get("HOME", "")},
    capture_output=True, text=True)
if probe.returncode == 0:
    fails.append("a build without node should not have succeeded")
elif "needs node" not in (probe.stdout + probe.stderr):
    fails.append(f"missing node gave an unhelpful error:\n{probe.stderr[-400:]}")

if fails:
    print("engine bridge: FAILED")
    for f in fails:
        print(f"  {f}")
    sys.exit(1)
print(f"engine bridge: {n} seasons agree with tiebreaker.py, shapes and "
      f"failure modes hold")

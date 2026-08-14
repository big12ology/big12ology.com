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

import chaos                                             # noqa: E402
import clinch                                            # noqa: E402
import engine                                            # noqa: E402
import odds                                              # noqa: E402
import tiebreaker as tb                                  # noqa: E402

fails = []


def ratings(year):
    """The rating systems, the way build.py loads them."""
    p = os.path.join(ROOT, "data", f"ratings_{year}.json")
    if not os.path.exists(p):
        return {}
    return json.load(open(p)).get("systems", {})


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


# --- clinch, where the enumeration actually engages -------------------------
# A finished season has nothing left to enumerate and a September one is over
# budget, so both ends answer "bounds" and prove nothing about the half of
# clinch.py that had no JavaScript counterpart. The truncations that matter
# are mid-November, where exact mode switches on and the scenario prose — new
# code, with no browser original to check against — is written.
def truncate(games, cutoff):
    out = []
    for g in games:
        g = dict(g)
        if (g["start"] or "9999")[:10] > cutoff:
            g["completed"] = False
            g["home_points"] = g["away_points"] = None
        out.append(g)
    return out


exact_seen = scenarios_seen = 0
for year in (2024, 2025):
    games = load(year)
    for cut in (f"{year}-10-20", f"{year}-11-10", f"{year}-11-16",
                f"{year}-11-18", f"{year}-11-24", f"{year}-12-10"):
        snap = truncate(games, cut)
        got = engine.clinch_analyze(snap, {})
        check(f"clinch {cut}", got, clinch.analyze(snap, {}))
        if got["mode"] == "exact":
            exact_seen += 1
            scenarios_seen += sum(1 for i in got["teams"].values()
                                  if i.get("scenarios"))

if exact_seen < 2:
    fails.append(f"exact enumeration never engaged ({exact_seen} of them) — "
                 f"the truncations above stopped covering the expensive half")
if not scenarios_seen:
    fails.append("no this-week scenario text was produced, so scenarioTexts "
                 "went unchecked")

# --- chaos, across the label bands ------------------------------------------
# Cheap to compute and easy to get subtly wrong: race.js keys its probability
# map on the statuses and chaos.py keyed it on the odds, which agree only
# while both cover the same teams.
for year, cut in ((2024, "2024-10-20"), (2024, "2024-11-24"),
                  (2025, "2025-10-20"), (2025, "2025-11-24")):
    snap = truncate(load(year), cut)
    rows = tb.standings(snap, {})
    cl = clinch.analyze(snap, {})
    sims = odds.simulate(snap, ratings(year), {}, n=400)
    check(f"chaos {cut}", engine.chaos_index(rows, cl, sims),
          chaos.index(rows, cl, sims))

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
print(f"engine bridge: {n} seasons agree with tiebreaker.py, clinch matches "
      f"at 12 truncations ({exact_seen} in exact mode, {scenarios_seen} teams "
      f"with scenario prose), chaos matches, shapes and failure modes hold")

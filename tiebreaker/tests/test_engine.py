#!/usr/bin/env python3
"""The rules engine, against what the engine it replaced used to answer.

    python3 tests/test_engine.py

There is one implementation of the Big 12 procedure now — site/engine.js, the
file the browser loads — so there is nothing left to compare it with. What
takes the place of that comparison is tests/engine_fixture.json: the answers
tiebreaker.py, clinch.py, odds.py and chaos.py gave on the day before they were
deleted, recorded from the modules themselves.

That is a real oracle rather than a circular one, and only because of when it
was taken. It is evidence about the port precisely to the extent that it
predates it. Regenerating it from JavaScript would turn it into a note saying
"the code does what the code does" — so if a check here fails, the question is
what changed in the engine, never whether the fixture needs refreshing.

Three kinds of check, and they fail for different reasons:

  * AGAINST THE FIXTURE — standings, tie logs, the championship pairing,
    clinch statuses, the deterministic half of the odds. These must not move.
  * INVARIANTS — probabilities summing to two berths, a finished season pinned
    at 1 and 0, the same seed reproducing the same run. These hold for any
    correct simulation and cover the sampled half, which no fixture can pin.
  * SHAPES AND FAILURE MODES — the four-tuple breakTie returns, floats staying
    floats, an unknown op raising rather than answering. Cheap, and the source
    of the only bug this port actually had.

rules_lite.py is checked here too. It is the score arithmetic that stayed in
Python, and the point of checking it is that it must keep agreeing with the
engine's own view of what a played game is.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import engine                                            # noqa: E402
import rules_lite as rules                               # noqa: E402

FIXTURE = json.load(open(os.path.join(HERE, "engine_fixture.json")))
fails = []


def normalize(o):
    """Numbers as numbers, not as Python's spelling of them.

    JavaScript has one numeric type, so a rating that is exactly 7 arrives as
    an int where Python held a float, and json.dumps spells those "7" and
    "7.0". Same number; the digest has to say so, or every fixture check
    becomes a test of which language did the arithmetic. Rounded at the
    twelfth decimal, far below anything the model resolves and far above the
    last-bit noise of a different summation order.
    """
    if isinstance(o, bool):
        return o
    if isinstance(o, (int, float)):
        return round(float(o), 12)
    if isinstance(o, dict):
        return {k: normalize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [normalize(v) for v in o]
    return o


def digest(o):
    import hashlib
    return hashlib.sha256(
        json.dumps(normalize(o), sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()[:16]


def check(name, got, want):
    if got != want:
        g, w = repr(got), repr(want)
        if len(g) > 300:
            g, w = g[:300] + "...", w[:300] + "..."
        fails.append(f"{name}:\n    got  {g}\n    want {w}")


def load(year):
    return json.load(open(os.path.join(ROOT, "data", f"games_{year}.json")))


def ratings(year):
    p = os.path.join(ROOT, "data", f"ratings_{year}.json")
    if not os.path.exists(p):
        return {}
    return json.load(open(p)).get("systems", {})


def truncate(games, cutoff):
    out = []
    for g in games:
        g = dict(g)
        if (g["start"] or "9999")[:10] > cutoff:
            g["completed"] = False
            g["home_points"] = g["away_points"] = None
        out.append(g)
    return out


# --- the rules, every committed season --------------------------------------
# Not a sample: the ladder's rarer steps only appear in the seasons that
# happened to need them, and a port that gets (a) through (c) right is the
# easy half. Standings are digested because sixteen seasons of narrative logs
# is a megabyte; the championship pairing and the tie groups are small enough
# to store and read.
seasons = 0
for year, want in sorted(FIXTURE["rules"].items()):
    if not year.isdigit():
        continue
    games = load(int(year))
    check(f"{year} standings", digest(engine.standings(games, {})),
          want["standings"])
    check(f"{year} championship", engine.championship(games, {}),
          want["championship"])
    check(f"{year} placement_groups", engine.placement_groups(games),
          want["placement_groups"])
    check(f"{year} conf_records", digest(engine.conf_records(games)),
          want["conf_records"])
    seasons += 1

# The 2024 four-way at 7-2, in full — the tie the explainer page is built on,
# and the one whose narrative log a reader actually reads.
want = FIXTURE["rules"]["break_tie_2024"]
g24 = load(2024)
order, log, resolved, events = engine.break_tie(
    engine.placement_groups(g24)[0], g24)
check("break_tie 2024 order", order, want["order"])
check("break_tie 2024 log", log, want["log"])
check("break_tie 2024 resolved", resolved, want["resolved"])
check("break_tie 2024 events", events, want["events"])
if not isinstance(engine.break_tie(engine.placement_groups(g24)[0], g24), tuple):
    fails.append("break_tie must return a tuple — JavaScript hands back an "
                 "object and unpacking it yields the dict's keys")

# --- clinch, where the enumeration engages ----------------------------------
# The truncations are mid-November on purpose. A finished season has nothing
# left to enumerate and a September one is over budget, so both ends answer
# "bounds" and say nothing about the expensive half.
exact_seen = scenarios_seen = 0
for cut, want in sorted(FIXTURE["clinch"].items()):
    year = int(cut[:4])
    got = engine.clinch_analyze(truncate(load(year), cut), {})
    check(f"clinch {cut} mode", got["mode"], want["mode"])
    check(f"clinch {cut} week", got["week"], want["week"])
    check(f"clinch {cut} n_outcomes", got["n_outcomes"], want["n_outcomes"])
    check(f"clinch {cut} teams", got["teams"], want["teams"])
    if got["mode"] == "exact":
        exact_seen += 1
        scenarios_seen += sum(1 for i in got["teams"].values()
                              if i.get("scenarios"))
if exact_seen < 2:
    fails.append(f"exact enumeration engaged {exact_seen} times — the "
                 f"truncations have stopped covering the expensive half")
if not scenarios_seen:
    fails.append("no this-week scenario prose was produced, so scenarioTexts "
                 "went unchecked")

# --- chaos ------------------------------------------------------------------
for cut, want in sorted(FIXTURE["chaos"].items()):
    year = int(cut[:4])
    snap = truncate(load(year), cut)
    sims = engine.simulate(snap, ratings(year), {}, n=400)
    got = engine.chaos_index(engine.standings(snap, {}),
                             engine.clinch_analyze(snap, {}), sims)
    # The score is a weighted blend of three components, one of which is
    # built from simulated probabilities, so it is checked as a band rather
    # than a value: the label and the two deterministic components are pinned.
    check(f"chaos {cut} label", got["label"], want["label"])
    check(f"chaos {cut} tangle", round(got["components"]["tangle"], 10),
          round(want["components"]["tangle"], 10))
    check(f"chaos {cut} breadth", round(got["components"]["breadth"], 10),
          round(want["components"]["breadth"], 10))
    if abs(got["score"] - want["score"]) > 3:
        fails.append(f"chaos {cut} score {got['score']} vs {want['score']} — "
                     f"further than resampling explains")

# --- odds: the deterministic half is exact ----------------------------------
# Everything before the first random draw is arithmetic over the ratings, and
# arithmetic does not get a tolerance. If a margin moves the simulation is
# sampling a different model, and no agreement downstream would mean anything.
for year, want in sorted(FIXTURE["odds"].items()):
    y = int(year)
    games, sysd = load(y), ratings(y)
    reg = engine.regress_stale(sysd, y)
    check(f"{year} regress_stale", digest(reg), want["regress_stale"])
    check(f"{year} team_strength", digest(engine.team_strength(reg)),
          want["team_strength"])
    check(f"{year} hfa_points", engine.hfa_points(reg), want["hfa_points"])
    # THE TWO THAT READ THE SEASON, not just its ratings, and so only hold
    # while the season is not being played. The fixture is an oracle about the
    # engine and that is only true if the input is fixed; a finished season is
    # fixed, a live one is not. 2026 opened on August 29 and both of these
    # moved that evening -- ensemble_margin because a result landed,
    # rating_sigma because it is measured off played margins -- with nothing
    # in the engine having changed. Skipping them there keeps the failure
    # meaning "the engine moved", which is the only thing this file is for.
    # The archived seasons still check them, and their data cannot drift.
    if all(g["completed"] for g in games):
        check(f"{year} ensemble_margin",
              digest(engine.ensemble_margin(games, reg)),
              want["ensemble_margin"])
        check(f"{year} rating_sigma", engine.rating_sigma(games),
              want["rating_sigma"])

# --- odds: the sampled half, on its own terms -------------------------------
# No fixture can pin these — a different generator draws a different sample —
# so they are held to what must be true of any correct run.
g26, reg26 = load(2026), engine.regress_stale(ratings(2026), 2026)
sims = engine.simulate(g26, reg26, {}, n=2000)
probs = [v["p_ccg"] for t, v in sims.items() if not t.startswith("_")]
if not 1.9 < sum(probs) < 2.1:
    fails.append(f"probabilities should sum to the two berths, got {sum(probs):.3f}")
if not all(0 <= p <= 1 for p in probs):
    fails.append("a probability escaped [0, 1]")
if not all(isinstance(v["p_ccg"], float) and isinstance(v["exp_w"], float)
           for t, v in sims.items() if not t.startswith("_")):
    fails.append("p_ccg/exp_w must stay floats — standings.csv publishes them "
                 "and JavaScript has no int/float distinction to preserve")
if engine.simulate(g26, reg26, {}, n=300) != engine.simulate(g26, reg26, {}, n=300):
    fails.append("same seed did not reproduce the same simulation — pages.yml "
                 "rebuilds ~1,800 times a season and every board would move")

done25 = engine.simulate(load(2025), reg26, {}, n=200)
at1 = {t for t, v in done25.items() if not t.startswith("_") and v["p_ccg"] == 1}
check("2025 finished: the pair at certainty", at1, {"BYU", "Texas Tech"})
if any(v["p_ccg"] for t, v in done25.items()
       if not t.startswith("_") and t not in at1):
    fails.append("2025 finished: a team outside the title game has odds")

# --- causal leverage: shape and ordering ------------------------------------
snap = truncate(load(2025), "2025-10-11")
gids = [g["id"] for g in snap if not g["completed"] and g["conference_game"]][:4]
lev = engine.causal_leverage(snap, reg26, {}, gids, n=300)
if len(lev) != len(gids):
    fails.append(f"causal_leverage returned {len(lev)} rows for {len(gids)} games")
if lev != sorted(lev, key=lambda r: -r["total"]):
    fails.append("causal_leverage must come back biggest-total first")
for r in lev:
    if not isinstance(r["total"], float):
        fails.append("leverage total must be a float")
    for m in r["movers"]:
        if not (isinstance(m, tuple) and len(m) == 4):
            fails.append(f"mover must be a 4-tuple, got {m!r}")
            break

# --- rules_lite, against the engine's own view ------------------------------
# The score arithmetic that stayed in Python. It has to keep meaning the same
# thing the engine means, or the hub publishes a sentence the standings
# disagree with — which is the bug that made this a shared definition in the
# first place.
games_all, mismatch = 0, 0
for y in range(2011, 2027):
    p = os.path.join(ROOT, "data", f"games_{y}.json")
    if not os.path.exists(p):
        continue
    for g in json.load(open(p)):
        games_all += 1
        if rules.has_score(g) != engine.has_score(g):
            mismatch += 1
        if rules.winner(g) != engine.winner(g):
            mismatch += 1
if mismatch:
    fails.append(f"rules_lite disagrees with the engine on {mismatch} of "
                 f"{games_all} games")
check("rules_lite pct", [rules.pct(w, l) for w in range(8) for l in range(8)],
      [engine.pct(w, l) for w in range(8) for l in range(8)])
if rules.pct(0, 0) is not None:
    fails.append("pct(0, 0) must be None: a team that has not played has no "
                 "percentage, and a board printing .000 says it is losing")

# --- the protocol's own failure modes ---------------------------------------
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
# A build asks the engine hundreds of questions; one bad one must not poison
# the rest.
check("still answering after two errors", engine.championship(g24, {}),
      FIXTURE["rules"]["2024"]["championship"])

# Checked by running a child with an empty PATH rather than by reading the
# source: what is being tested is that the message arrives instead of a
# FileNotFoundError three hundred lines into a build.
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
    print("engine: FAILED")
    for f in fails:
        print(f"  {f}")
    sys.exit(1)
print(f"engine: {seasons} seasons match the recorded answers, clinch matches at "
      f"{len(FIXTURE['clinch'])} truncations ({exact_seen} exact, "
      f"{scenarios_seen} with scenario prose), the odds model is exact where it "
      f"is deterministic and sound where it is not, rules_lite agrees over "
      f"{games_all} games")

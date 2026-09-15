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

The file is therefore never edited. An answer the engine has legitimately
improved on is superseded in memory instead, by a named line in SUPERSEDED
below that asserts the old value before replacing it and carries the reason
with it. The distinction is the whole point: rewriting the file hides the
departure, and listing it keeps the rest of the file worth trusting. Every
correction there was checked against an exhaustive enumeration, not against
the code that produced it.

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

# --- where the engine now answers better than the oracle did ----------------
#
# The file on disk is never edited. It is evidence about the port only because
# it predates the port, and an entry rewritten to match today's JavaScript is
# an entry that has stopped being evidence. What happens instead is here, in
# code: each answer that has legitimately moved is named, the value it used to
# hold is asserted before it is replaced, and the reason travels with it. A
# reader can see exactly how far the engine has walked from the Python, one
# line per step, and a step nobody could justify has nowhere to hide.
#
# Every entry below is the SAME correction, arriving in three places.
# clinch.cut_membership read breakTie's `resolved` flag as whether the ladder
# had done anything at all: one False and the whole tied group went into
# `maybe` together, the team the ladder had already seeded out of them
# included. breakTie keeps a finer record than that -- `events`, one entry per
# seeding it actually made -- and engine.championship() has read it that way
# since the matchup card stopped publishing the alphabet as a pairing. The cut
# reads it now too, so a proof is no longer weaker than the board printed
# beside it.
#
# Conservative is not the same as correct, which is what these three show.
# Each was checked by enumerating every completion of the remaining conference
# games and asking engine.championship() -- untouched by that change, so not a
# witness to itself -- who was actually in the title game:
#
#   * TCU, 2024-11-24, over all 256 completions: never a seed, never sharing a
#     seat. The oracle called that "alive" because it could not finish the tie,
#     not because TCU had a route.
#   * Texas Tech, 2025-11-16 and -18, over all 32,768: in every one of the
#     8,192 completions where BYU beat Cincinnati and Colorado beat Arizona
#     State, a named seed. The oracle needed a third game named as well and
#     offered three different ones to choose from, which is the tell: the
#     third game was never load-bearing, the old cut just could not prove it.
#
# The chaos components move because TCU stops being alive, and for no other
# reason: nine of sixteen alive becomes eight, so breadth is 6/14 where it was
# 7/14 and tangle drops the one tied row TCU was holding open.
SUPERSEDED = []


def supersede(path, was, now, why):
    """Point one recorded answer at a better one, in memory, loudly.

    Raises rather than checks if the fixture no longer holds `was`: that
    means the file has been edited under a correction written against it,
    and quietly replacing whatever is there now would launder the edit.
    """
    node = FIXTURE
    for k in path[:-1]:
        node = node[k]
    if node[path[-1]] != was:
        raise SystemExit(f"supersede {'/'.join(map(str, path))}: the fixture "
                         f"no longer holds {was!r}, so this correction is "
                         f"written against a file that has changed")
    node[path[-1]] = now
    SUPERSEDED.append("/".join(map(str, path)) + " -- " + why)


supersede(("clinch", "2024-11-24", "teams", "TCU", "status"),
          "alive", "eliminated",
          "no route in any of 256 completions")

_TT_WAS = [
    "TCU over Houston + BYU over Cincinnati + Colorado over Arizona State",
    "UCF over Oklahoma State + BYU over Cincinnati + Colorado over Arizona State",
    "Kansas State over Utah + BYU over Cincinnati + Colorado over Arizona State",
]
_TT_NOW = ["BYU over Cincinnati + Colorado over Arizona State"]
for _cut in ("2025-11-16", "2025-11-18"):
    supersede(("clinch", _cut, "teams", "Texas Tech", "scenarios"),
              _TT_WAS, _TT_NOW,
              "the two games that do the work, without the third that never did")

supersede(("chaos", "2024-11-24", "components", "tangle"), 0.5625, 0.5,
          "one fewer tied row held open, downstream of TCU")
supersede(("chaos", "2024-11-24", "components", "breadth"), 0.5, 6 / 14,
          "eight alive of sixteen, not nine, downstream of TCU")


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
# A SEASON THE FIXTURE PREDATES CANNOT TESTIFY ABOUT THE PORT. The oracle is
# only evidence because it was taken before the rewrite — but 2026 had not
# been played when it was taken, so its entry records an empty season rather
# than an answer: standings of nobody, no placement groups, no pairing. That
# was true on the day and stayed true until week 1 finished, at which point
# the check began comparing live results against a snapshot of "nothing has
# happened yet" and failed for exactly the reason it should have.
#
# The entry is not refreshed — refreshing it from the current engine is what
# the docstring above rules out, and it would turn the oracle circular. It is
# retired from the per-season loop and re-checked below as the thing it
# actually asserts: that an unplayed schedule produces empty output. Stated
# against a synthetic emptied season, that is deterministic and stays true in
# December, which the live comparison never could.
UNPLAYED_WHEN_TAKEN = {"2026"}

seasons = 0
for year, want in sorted(FIXTURE["rules"].items()):
    if not year.isdigit() or year in UNPLAYED_WHEN_TAKEN:
        continue
    games = load(int(year))
    check(f"{year} standings", digest(engine.standings(games, {})),
          want["standings"])
    # Every key the fixture recorded, and only those. The answer must not
    # move; the payload around it may grow, and has: the card can now decline
    # to name a seed it has no basis for, which needed `pending` and `reason`
    # beside the two names. A fixture taken before those existed cannot
    # testify about them, so it is not asked to.
    got = engine.championship(games, {})
    # Guarded on BOTH sides. A fixture entry of null is a season that had no
    # pairing when it was taken, and iterating it to pick keys raised a
    # TypeError three frames from anything that named the year — which is how
    # the 2026 placeholder above announced itself.
    check(f"{year} championship",
          None if got is None or want["championship"] is None
          else {k: got.get(k) for k in want["championship"]},
          want["championship"])
    check(f"{year} placement_groups", engine.placement_groups(games),
          want["placement_groups"])
    check(f"{year} conf_records", digest(engine.conf_records(games)),
          want["conf_records"])
    seasons += 1

# What the retired entries actually assert: an unplayed schedule produces
# empty output. Checked against a synthetic emptied season rather than the
# live one, so it is deterministic, and so it keeps holding once the season
# it was taken from is finished — which the live comparison could not.
for year in sorted(UNPLAYED_WHEN_TAKEN):
    want = FIXTURE["rules"][year]
    blank = []
    for g in load(int(year)):
        g = dict(g)
        g["completed"] = False
        g.pop("home_points", None)
        g.pop("away_points", None)
        blank.append(g)
    check(f"{year} unplayed standings", digest(engine.standings(blank, {})),
          want["standings"])
    check(f"{year} unplayed placement_groups", engine.placement_groups(blank),
          want["placement_groups"])
    check(f"{year} unplayed conf_records", digest(engine.conf_records(blank)),
          want["conf_records"])
    check(f"{year} unplayed championship", engine.championship(blank, {}),
          want["championship"])

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
# The oracle was recorded on 2026-08-14, when 2026 had not kicked off. Both
# of its inputs have moved since, so every 2026 entry under "odds" is now
# unreachable, permanently:
#
#   * the games, on August 29, when the season opened in Dublin;
#   * the ratings, on September 1, when the bot committed in-season SP+ into
#     ratings_2026.json (Jacksonville State -8.463 to -13.934, TCU 6.366 to
#     4.77), which moved regress_stale and team_strength with it.
#
# That second one cost five red CI runs. Both digests still reproduce exactly
# from the pre-September ratings file, so the engine had not changed at all --
# the fixture was being handed a different question and marked wrong for
# giving a different answer. hfa_points survived only because that refresh
# happened not to touch the home-field terms, which is luck, not a reason to
# treat it differently.
#
# Refreshing the fixture is not the fix and never will be: it is evidence
# about the port precisely because it predates it, and the modules that
# produced it are deleted. Nor is waiting for the season to end -- December's
# ratings are no closer to August's than today's are. So the season that was
# live when the oracle was taken is not asked, and a failure here keeps
# meaning "the engine moved", which is the only thing this file is for. 2025
# and everything before it still check all five, and their data cannot drift.
# What 2026 covered and nothing else did is picked up on its own terms below.
ORACLE_SEASON = 2026

for year, want in sorted(FIXTURE["odds"].items()):
    y = int(year)
    if y >= ORACLE_SEASON:
        continue
    games, sysd = load(y), ratings(y)
    reg = engine.regress_stale(sysd, y)
    check(f"{year} regress_stale", digest(reg), want["regress_stale"])
    check(f"{year} team_strength", digest(engine.team_strength(reg)),
          want["team_strength"])
    check(f"{year} hfa_points", engine.hfa_points(reg), want["hfa_points"])
    check(f"{year} ensemble_margin",
          digest(engine.ensemble_margin(games, reg)),
          want["ensemble_margin"])
    check(f"{year} rating_sigma", engine.rating_sigma(games),
          want["rating_sigma"])

# --- regressStale, on its own terms -----------------------------------------
# The one thing dropping 2026 would otherwise leave uncovered. Every system
# in ratings_2025.json carries year 2025, so the check above walks only the
# passthrough branch; 2026 is the sole year in the fixture whose ratings mix
# a current season with stale ones, and it is exactly the year that can no
# longer be pinned.
#
# So: fixed input, and properties rather than a recorded answer. Restating
# `mean + STALE_KEEP * (r - mean)` here would be a second copy of the model,
# which is what engine.py exists to prevent -- it would agree with the engine
# by construction and notice nothing. These hold for any regression toward
# the mean, whatever the constant is set to.
stale_in = {
    "Fresh": {"year": 2026, "hfa": 2.0, "ratings": {"A": 10.0, "B": 0.0}},
    "Stale": {"year": 2025, "hfa": 2.0, "ratings": {"A": 10.0, "B": 0.0,
                                                    "C": 5.0}},
}
sr = engine.regress_stale(stale_in, 2026)

check("regressStale leaves the current season alone",
      sr["Fresh"]["ratings"], {"A": 10.0, "B": 0.0})
if "regressed" in sr["Fresh"]:
    fails.append("regressStale marked a current-season system as regressed")
if not sr["Stale"].get("regressed"):
    fails.append("regressStale did not mark a stale system as regressed")

r = sr["Stale"]["ratings"]
mean_in, mean_out = 5.0, sum(r.values()) / len(r)
if abs(mean_out - mean_in) > 1e-9:
    fails.append(f"regressStale moved the mean: {mean_in} -> {mean_out}")
# Toward the mean, not past it and not nowhere. The team already at the mean
# is the one that says the shrink is centered rather than a flat offset.
if not 5.0 < r["A"] < 10.0:
    fails.append(f"regressStale did not pull the leader in: {r['A']}")
if not 0.0 < r["B"] < 5.0:
    fails.append(f"regressStale did not pull the trailer up: {r['B']}")
if abs(r["C"] - 5.0) > 1e-9:
    fails.append(f"regressStale moved a team already at the mean: {r['C']}")
if not r["A"] > r["C"] > r["B"]:
    fails.append("regressStale reordered the teams")
# An empty system has no mean to regress toward, and dividing by zero teams
# is the shape of bug this branch invites.
check("regressStale passes an unrated system through",
      engine.regress_stale({"Empty": {"year": 2025, "hfa": 0, "ratings": {}}},
                           2026)["Empty"]["ratings"], {})

# --- ensembleMargin, on its own terms ---------------------------------------
# The fixture cannot cover this at all, and never could. ensembleMargin skips
# completed and championship games, so a finished season projects nothing and
# 2025's recorded answer is the digest of an empty object -- a check that
# cannot fail. 2026 was the only real one, recorded when no game had been
# played, and its ratings have since moved. Both halves of the fixture's
# coverage here are gone, and only one of them ever existed.
#
# Fixed input again, and relationships rather than restated arithmetic. Two
# systems that disagree about the scale and agree about the game: SP+-like at
# a point per point, Elo-like at 27, both saying the home side is ten points
# better with two points of home field. Anything that reads per_pt wrongly
# lands 27x out; anything that drops hfa breaks the symmetry below.
em_sys = {
    "PointScale": {"year": 2026, "hfa": 2.0, "per_pt": 1.0,
                   "ratings": {"A": 10.0, "B": 0.0}},
    "EloScale": {"year": 2026, "hfa": 54.0, "per_pt": 27.0,
                 "ratings": {"A": 270.0, "B": 0.0}},
}
em_games = [
    {"id": 1, "home": "A", "away": "B", "completed": False},
    {"id": 2, "home": "B", "away": "A", "completed": False},
    {"id": 3, "home": "A", "away": "B", "completed": True},
    {"id": 4, "home": "A", "away": "B", "completed": False, "ccg": True},
    {"id": 5, "home": "Unrated", "away": "AlsoUnrated", "completed": False},
    {"id": 6, "home": "A", "away": "Unrated", "completed": False},
]
em = engine.ensemble_margin(em_games, em_sys)

for gid, why in ((3, "a completed game"), (4, "a championship game"),
                 (5, "a game with neither side rated")):
    if gid in em:
        fails.append(f"ensembleMargin projected {why}")
for gid in (1, 2, 6):
    if gid not in em:
        fails.append(f"ensembleMargin skipped game {gid}, which it can answer")

if em.get(1) is not None:
    # Ten points better plus two of home field, on both scales, so the
    # ensemble is one number rather than an average of two disagreeing ones.
    if abs(em[1] - 12.0) > 1e-9:
        fails.append(f"ensembleMargin put A over B by {em[1]}, not 12 "
                     f"(a system read on the wrong scale looks like this)")
if em.get(1) is not None and em.get(2) is not None:
    # THE HOME-FIELD TERM, pinned without naming it: the same matchup from
    # both ends sums to twice the home edge, whatever that edge is. A margin
    # that quietly gained or lost a point fails here and nowhere else.
    if abs((em[1] + em[2]) - 4.0) > 1e-9:
        fails.append(f"the same game from both ends sums to {em[1] + em[2]}, "
                     f"not twice the 2.0 of home field")
    if not em[2] < 0:
        fails.append("the weaker home side was still favored")
if em.get(6) is not None and em.get(1) is not None and not em[6] > em[1]:
    fails.append("an unrated opponent was not treated as worse than the "
                 "worst rated one")

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
want24 = FIXTURE["rules"]["2024"]["championship"]
check("still answering after two errors",
      {k: engine.championship(g24, {}).get(k) for k in want24}, want24)

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
# Printed on a pass, not only on a failure. A correction the suite carries
# quietly is a correction nobody re-reads, and the list is short on purpose:
# if it ever stops being short, the oracle has stopped being one. Above the
# summary rather than below it, because CI reports each suite by its last line.
if SUPERSEDED:
    print(f"{len(SUPERSEDED)} recorded answers superseded by a better one:")
    for line in SUPERSEDED:
        print(f"  {line}")
print(f"engine: {seasons} seasons match the recorded answers, clinch matches at "
      f"{len(FIXTURE['clinch'])} truncations ({exact_seen} exact, "
      f"{scenarios_seen} with scenario prose), {len(SUPERSEDED)} superseded, "
      f"the odds model is exact where it is deterministic and sound where it "
      f"is not, rules_lite agrees over {games_all} games")

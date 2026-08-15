"""The rules engine, which lives in JavaScript, reached from Python.

    import engine
    rows = engine.standings(games, overrides)

WHY THE RULES ARE NOT IN PYTHON ANY MORE. They were in both, and that is the
problem this removes. tiebreaker.py and site/engine.js were the same seven-step
ladder written twice, kept honest by tests/test_parity.py and by a comment in
each file saying "if you change one, change the other." A rule with two homes
gets fixed in one of them: commit f97dab2 is a single sentence about what
counts as a played game, corrected across four runtimes in eight files, and
site/engine.js:11-19 records that the JavaScript failure mode was the silent
one.

Only one of the two copies can be deleted. The browser's has to stay, because
The Lab re-runs the whole procedure client-side while somebody waits, and
nothing about a build-time engine can serve that. So the browser's copy is the
survivor and the build calls it.

WHAT THIS IS NOT. It is not a port and it is not a second implementation --
site/engine.js is the file the page itself loads, byte for byte. If this module
and the browser ever disagree it is because they were handed different games,
which is a bug about arguments rather than about rules.

The process is started on the first question and answers on a pipe; see
engine/cli.js for why it stays up rather than being spawned per call.
"""
import atexit
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "engine", "cli.js")

_proc = None
_seq = 0


class EngineError(RuntimeError):
    """The engine refused a question, or died holding one."""


def _start():
    """Bring the process up, and prove it is the engine before trusting it."""
    global _proc
    if shutil.which("node") is None:
        sys.exit(
            "build.py needs node on PATH: the Big 12 rules live in\n"
            "  site/engine.js, and this build runs them rather than keeping a\n"
            "  second copy in Python. Node 22 or newer.\n"
            "  (CI pins it with actions/setup-node; locally, any 22.x will do.)")
    _proc = subprocess.Popen(
        ["node", CLI],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=HERE)
    atexit.register(_stop)
    # A handshake, so a broken engine fails here — naming itself — rather than
    # three hundred lines into a build with a confused answer.
    got = _call("ping")
    if "standings" not in got.get("ops", []):
        raise EngineError(f"engine came up without its rules: {got}")


def _stop():
    global _proc
    if _proc is None:
        return
    p, _proc = _proc, None
    try:
        p.stdin.close()
        p.wait(timeout=5)
    except Exception:
        p.kill()


def _call(op, *args):
    global _seq
    if _proc is None:
        _start()
    _seq += 1
    req = json.dumps({"id": _seq, "op": op, "args": list(args)})
    try:
        _proc.stdin.write(req + "\n")
        _proc.stdin.flush()
        line = _proc.stdout.readline()
    except (BrokenPipeError, ValueError) as e:
        raise EngineError(f"the engine went away answering {op}: {e}") from e
    if not line:
        # It died. Its stderr is the only account of why, and without this the
        # symptom is an empty line and a JSON error pointing at nothing.
        err = ""
        try:
            err = _proc.stderr.read() or ""
        except Exception:
            pass
        raise EngineError(f"the engine died answering {op}:\n{err.strip()}")
    res = json.loads(line)
    if res.get("id") != _seq:
        # One dropped or duplicated line and every later answer belongs to an
        # earlier question. Cheap to check, and silent corruption otherwise.
        raise EngineError(
            f"engine replies are out of step: asked {_seq}, heard {res.get('id')}")
    if not res.get("ok"):
        raise EngineError(f"{op}: {res.get('error')}")
    return res["result"]


def standings(games, overrides=None):
    return _call("standings", games, overrides or {})


def championship(games, overrides=None):
    return _call("championship", games, overrides or {})


def placement_groups(games):
    return _call("placementGroups", games)


def break_tie(tied, games, overrides=None):
    """(order, log, resolved, events), as tiebreaker.break_tie returns it.

    The one place the two engines disagree about shape rather than about
    rules: JavaScript hands back {order, log, resolved, events} and Python a
    four-tuple. Unpacked without this, a caller gets the dict's keys — the
    2024 worked example came out ordered ["order", "log", "resolved",
    "events"] and the explainer's own assertion caught it. Converted here, so
    the shape stays whatever the callers already expect and no call site has
    to know which language answered.
    """
    d = _call("breakTie", tied, games, overrides or {})
    return d["order"], d["log"], d["resolved"], d["events"]


def conf_records(games):
    return _call("confRecords", games)


def pad(rows, games):
    """Standings with the teams that have no conference result yet appended.

    The display half, and it reads scores of its own, so tests/test_malformed
    exercises it on the same bad rows the ladder gets."""
    return _call("pad", rows, games)


# The engine's own view of the two predicates rules_lite keeps a Python copy
# of. Not used by the build — routing a two-integer comparison through a pipe
# would be absurd — but tests/test_engine.py checks the copy against these over
# every game of every committed season, so the two cannot drift.

def has_score(g):
    return _call("hasScore", g)


def winner(g):
    return _call("winner", g)


def pct(w, l):
    return _call("pct", w, l)


# ------------------------------------------------------------------- clinch
# clinch.analyze's shape, so callers do not learn which language answered.
# The budget default lives in engine/build-only.js; passing None takes it.

def clinch_analyze(games, overrides=None, budget=None):
    return _call("clinchAnalyze", games, overrides or {}, budget)


def clinch_bounds(games):
    return _call("clinchBounds", games)


def clinch_exact(games, overrides=None, budget=None):
    return _call("clinchExact", games, overrides or {}, budget)


def conf_teams(games):
    return _call("confTeams", games)


def remaining_conf(games):
    """Unplayed conference games — what the enumeration's budget is measured
    against, and how the tests know whether exact mode should have engaged."""
    return _call("remainingConf", games)


def tangle_component(rows, statuses, n_teams):
    return _call("tangleComponent", rows, statuses, n_teams)


def chaos_index(rows, clinch_result, odds_result):
    """chaos.index's shape: {"score", "label", "components"}."""
    return _call("chaosIndex", rows, clinch_result, odds_result)


# --------------------------------------------------------------------- odds
# odds.py's shapes. The tuples matter: leverage's callers unpack
# (team, delta, p_if_home_wins, p_if_home_loses) and JSON has only arrays, so
# they are converted back here rather than at every call site.

def regress_stale(systems, season):
    return _call("regressStale", systems, season)


def ensemble_margin(games, systems):
    """{game_id: expected home margin}. JSON object keys are strings, and
    every caller looks these up by the integer id the games carry."""
    return {int(k): v for k, v in _call("ensembleMargin", games, systems).items()}


def team_strength(systems):
    return _call("teamStrength", systems)


def hfa_points(systems):
    return _call("hfaPoints", systems)


def win_probs(games, systems):
    return {int(k): v for k, v in _call("winProbs", games, systems).items()}


def rating_sigma(games):
    # float() for the same reason simulate() does it: this is written into the
    # forecast records as the model's own description, and a preseason sigma of
    # exactly 7 would be stored as "7" where every earlier week says "7.0".
    return float(_call("ratingSigma", games))


def p_from_margin(m):
    return float(_call("pFromMargin", m))


_CONSTS = None


def constants():
    """The model's own numbers — N_SIMS, SEED, MARGIN_SIGMA, EXACT_BUDGET.

    Asked for rather than restated. build.py publishes N_SIMS and
    MARGIN_SIGMA on the page as "how this was computed", and a Python copy of
    either would be a second definition of exactly the kind this module exists
    to remove: nothing would break if they drifted, the page would simply
    describe a simulation that had not been run. Cached, so printing them
    costs one round trip per build rather than one per page.
    """
    global _CONSTS
    if _CONSTS is None:
        _CONSTS = _call("constants")
    return _CONSTS


def simulate(games, systems, overrides=None, n=None, seed=None, track=None,
             sigma=None):
    opts = {}
    if n is not None:
        opts["n"] = n
    if seed is not None:
        opts["seed"] = seed
    if track is not None:
        opts["track"] = list(track)
    if sigma is not None:
        opts["sigma"] = sigma
    out = _call("simulate", games, systems, overrides or {}, opts)
    if "_cond" in out:
        out["_cond"] = {int(k): v for k, v in out["_cond"].items()}
    # FLOATS, EVEN WHEN THEY ARE WHOLE. JavaScript has one number type, so a
    # probability of exactly 1 comes back as an int and json.dump then writes
    # "1" where the published file has always said "1.0". That is not a
    # different number, but standings.csv is a download people parse and
    # data.json is a documented payload; the schema should not shift because
    # the language underneath changed. odds.simulate always returned floats.
    for t, v in out.items():
        if not t.startswith("_"):
            v["p_ccg"] = float(v["p_ccg"])
            v["exp_w"] = float(v["exp_w"])
    return out


def _movers(rows):
    """[[team, d, pw, pl], ...] -> [(team, d, pw, pl), ...], floats kept float.

    Same reason as simulate(): a delta that lands on a whole number comes back
    as an int, and these are formatted straight onto the page.
    """
    for r in rows:
        r["total"] = float(r["total"])
        r["movers"] = [(t, float(d), float(pw), float(pl))
                       for t, d, pw, pl in r["movers"]]
        r["pair"] = {t: (float(a), float(b)) for t, (a, b) in r["pair"].items()}
    return rows


def leverage(sims, games):
    if "_cond" in sims:
        sims = dict(sims, _cond={str(k): v for k, v in sims["_cond"].items()})
    return _movers(_call("leverage", sims, games))


def causal_leverage(games, systems, overrides=None, gids=(), n=None,
                    seed=None):
    opts = {}
    if n is not None:
        opts["n"] = n
    if seed is not None:
        opts["seed"] = seed
    return _movers(
        _call("causalLeverage", games, systems, overrides or {},
              list(gids), opts))

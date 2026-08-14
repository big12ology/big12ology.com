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


def chaos_index(rows, clinch_result, odds_result):
    """chaos.index's shape: {"score", "label", "components"}."""
    return _call("chaosIndex", rows, clinch_result, odds_result)

"""The basketball rules, run where they are written: site/engine.js.

One copy of the tiebreaker, used by the build and by the browser alike, so
the page and anything interactive on it can never disagree. Each call is
one short Node process: the build asks a handful of questions, not
thousands, and the simulator runs inside a single call.
"""
import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "site", "engine.js")

RUNNER = r"""
const E = require(process.argv[1]);
let input = "";
process.stdin.on("data", (c) => { input += c; });
process.stdin.on("end", () => {
  const { op, args } = JSON.parse(input);
  if (typeof E[op] !== "function") throw new Error("no such engine op: " + op);
  process.stdout.write(JSON.stringify(E[op](...args)));
});
"""


def call(op, *args):
    if shutil.which("node") is None:
        raise SystemExit("basketball/bb_build.py needs node on PATH to run "
                         "site/engine.js")
    r = subprocess.run(["node", "-e", RUNNER, ENGINE],
                       input=json.dumps({"op": op, "args": list(args)}),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"engine {op} failed: {r.stderr.strip()[-600:]}")
    return json.loads(r.stdout)


def engine_games(games):
    """The regular-season conference games, in the shape engine.js reads."""
    return [{"home": g["home"], "away": g["away"], "hp": g["hp"],
             "ap": g["ap"], "neutral": g["neutral"], "final": g["final"],
             "start": g["start"], "id": g["id"]}
            for g in games if g["conf"]]


def standings(games, teams):
    return call("standings", engine_games(games), sorted(teams))


def simulate(games, teams, model, n=10000, seed=20262027):
    return call("simulate", engine_games(games), sorted(teams),
                {"ratings": model.get("ratings", {}), "hfa": model.get("hfa", 0),
                 "sigma": model.get("sigma", 11)}, n, seed)


def certain(games, teams):
    return call("certain", engine_games(games), sorted(teams))


# Games left at or below which the build enumerates every remaining season.
# 2^18 seasons is about nine seconds of Node; 2^16, the last two rounds, two.
EXACT_LIMIT = 18


def exact(games, teams, limit=EXACT_LIMIT):
    """Every seed still possible, from every way the rest can go, or None
    while more than `limit` games are left. See engine.js's exact()."""
    return call("exact", engine_games(games), sorted(teams), limit)


def bracket_odds(order, model, results=None):
    return call("bracketOdds", order,
                {"ratings": model.get("ratings", {}), "hfa": model.get("hfa", 0),
                 "sigma": model.get("sigma", 11)},
                {str(k): v for k, v in (results or {}).items()})


def win_prob(model, home, away, neutral):
    """The same arithmetic as engine.js's winProb, for the few places the
    build needs one number and a Node call would be silly."""
    import math
    r = model.get("ratings", {})
    gap = r.get(home, 0) - r.get(away, 0) + (0 if neutral else model.get("hfa", 0))
    return 0.5 * (1 + math.erf(gap / (model.get("sigma", 11) * math.sqrt(2))))

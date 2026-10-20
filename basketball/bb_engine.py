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

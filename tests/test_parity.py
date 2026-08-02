#!/usr/bin/env python3
"""Prove the JS engine (site/engine.js) matches the Python engine.

Runs the real 2024 and 2025 seasons plus N random completions of the 2026
schedule through both engines and diffs standings (order, records, tie
groups, resolved flags, narrative logs) and the championship projection.

    python3 tests/test_parity.py [N]   # default 25 random scenarios
"""
import copy
import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import tiebreaker as tb

RUNNER = """
const args = process.argv.slice(1).filter(function (a) { return a !== "--"; });
const eng = require(args[args.length - 2]);
const fs = require("fs");
const games = JSON.parse(fs.readFileSync(args[args.length - 1], "utf8"));
const out = games.map(function (scenario) {
  return {
    standings: eng.standings(scenario, {}),
    championship: eng.championship(scenario, {}),
  };
});
process.stdout.write(JSON.stringify(out));
"""


def load(year):
    return json.load(open(os.path.join(ROOT, "data", f"games_{year}.json")))


def random_scenario(schedule, rng):
    games = copy.deepcopy(schedule)
    for g in games:
        if g.get("ccg"):
            continue
        if not g["completed"]:
            g["completed"] = True
            if rng.random() < 0.5:
                g["home_points"], g["away_points"] = 28, 17
            else:
                g["home_points"], g["away_points"] = 17, 28
    return games


def py_result(games):
    rows = tb.standings(games, {})
    return {
        "standings": rows,
        "championship": tb.championship(games, {}),
    }


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    rng = random.Random(12)  # deterministic
    scenarios = [load(2024), load(2025)]
    schedule = load(2026)
    for _ in range(n):
        scenarios.append(random_scenario(schedule, rng))
    # partially-played seasons too: complete only the first k conference games
    for k in (5, 20, 40, 60):
        s = random_scenario(schedule, rng)
        seen = 0
        for g in s:
            if g["conference_game"] and not g.get("ccg"):
                seen += 1
                if seen > k:
                    g["completed"] = False
                    g["home_points"] = g["away_points"] = None
        scenarios.append(s)

    tmp = os.path.join(HERE, "_parity_scenarios.json")
    with open(tmp, "w") as f:
        json.dump(scenarios, f)
    try:
        r = subprocess.run(
            ["node", "-e", RUNNER, "--",
             os.path.join(ROOT, "site", "engine.js"), tmp],
            capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"node failed: {r.stderr[:800]}")
        js = json.loads(r.stdout)
    finally:
        os.remove(tmp)

    bad = 0
    for i, sc in enumerate(scenarios):
        py = py_result(sc)
        label = ["2024", "2025"][i] if i < 2 else f"scenario {i - 1}"
        if py["championship"] != js[i]["championship"]:
            bad += 1
            print(f"[FAIL] {label} championship:\n  py {py['championship']}"
                  f"\n  js {js[i]['championship']}")
            continue
        prows, jrows = py["standings"], js[i]["standings"]
        if len(prows) != len(jrows):
            bad += 1
            print(f"[FAIL] {label}: row count {len(prows)} vs {len(jrows)}")
            continue
        for pr, jr in zip(prows, jrows):
            if (pr["rank"], pr["team"], pr["conf_w"], pr["conf_l"],
                pr["overall_w"], pr["overall_l"], pr["tie_group"],
                pr["resolved"], pr["log"]) != \
               (jr["rank"], jr["team"], jr["conf_w"], jr["conf_l"],
                jr["overall_w"], jr["overall_l"], jr["tie_group"],
                jr["resolved"], jr["log"]):
                bad += 1
                print(f"[FAIL] {label} row {pr['rank']}:\n  py {pr}\n  js {jr}")
                break
    print(f"{len(scenarios) - bad}/{len(scenarios)} scenarios match"
          + (" — PARITY OK" if not bad else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

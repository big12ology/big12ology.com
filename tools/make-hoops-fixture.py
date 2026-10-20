#!/usr/bin/env python3
"""Publish the 2025-26 men's season as a pick'em fixture, for the Worker's
end-to-end basketball test (worker/test/hoops.sim.test.js).

Runs the real publisher (basketball/bb_pickem.py) over the real season, week
by week as the deploy would have, so the files the Worker test imports are
the publisher's own output and not a hand-written imitation of it. There is
no archived the-odds-api feed for that season, so each game's line is what
this site's ratings said, labeled as the-odds-api's so the publisher freezes
it: a stand-in for the market, not a record of it.

    python3 tools/make-hoops-fixture.py   # -> worker/test/fixtures/hoops-2026/
"""
import datetime
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "basketball"))

import bb_pickem                                           # noqa: E402
import bb_ratings                                          # noqa: E402
import bb_season                                           # noqa: E402

SEASON = 2026
OUT = os.path.join(ROOT, "worker", "test", "fixtures", f"hoops-{SEASON}")


def main():
    d = bb_season.load(SEASON)
    model = bb_ratings.load(SEASON)
    r, hfa = model["ratings"], model["hfa"]
    lines = {}
    for g in d["games"]:
        if not g["conf"]:
            continue
        gap = r.get(g["home"], 0) - r.get(g["away"], 0) + (0 if g["neutral"] else hfa)
        spread = round(-gap * 2) / 2              # home's number, half points
        lines[g["id"]] = {"spread": spread, "source": bb_pickem.ODDS,
                          "books": [{"provider": "model", "spread": spread}]}

    shutil.rmtree(OUT, ignore_errors=True)
    bb_pickem.OUT = os.path.join(os.path.dirname(OUT), "_tmp")
    shutil.rmtree(bb_pickem.OUT, ignore_errors=True)
    # Each Monday of the season, as the deploy would have run.
    weeks = bb_pickem.weeks(d["games"])
    starts = sorted(min(bb_pickem._ts(g["start"]) for g in gs)
                    for gs in weeks.values())
    day = starts[0] - datetime.timedelta(days=9)
    end = max(bb_pickem._ts(g["start"]) for gs in weeks.values() for g in gs)
    while day <= end:
        bb_pickem.publish(SEASON, d["games"], lines, now=day, log=lambda *_: None)
        day += datetime.timedelta(days=1)
    bb_pickem.write_scores(SEASON, d["games"])
    # The Kansas City bracket challenge, as the live build would write it once
    # the regular season was over: the real seeds and all fifteen results.
    sys.argv = ["x"]
    import bb_build
    import bb_engine
    st = bb_engine.standings(d["games"], d["teams"])
    order, _ = bb_build.bracket_order(d, st, None, log=lambda *_: None)
    bb_pickem.write_bracket(SEASON, d["games"], order,
                            bb_build.bracket_results(order, d), bb_build.BRACKET,
                            log=lambda *_: None)
    shutil.move(os.path.join(bb_pickem.OUT, str(SEASON)), OUT)
    shutil.rmtree(bb_pickem.OUT, ignore_errors=True)
    n = sorted(os.listdir(OUT))
    games = sum(len(json.load(open(os.path.join(OUT, f)))["games"])
                for f in n if f.startswith("week-"))
    weeks_n = sum(1 for f in n if f.startswith("week-"))
    print(f"{weeks_n} weeks, {games} games, a bracket -> {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()

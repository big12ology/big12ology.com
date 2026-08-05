#!/usr/bin/env python3
"""The season replay must agree with the season.

The frames embedded in The Standings are what the page redraws when someone
scrubs the slider, so a frame that disagrees with the rules engine would put
a standings table on screen that never existed. These checks pin the two
together: every frame carries the whole league, the records only ever grow,
and the last frame is the season as the rest of the site renders it.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import build  # noqa: E402
import clinch as clinch_mod  # noqa: E402
import tiebreaker as tb  # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def season(year):
    games = json.load(open(os.path.join(HERE, "data", f"games_{year}.json")))
    overrides = tb.load_overrides()
    frames = build.season_frames(games, overrides)
    everyone = set(clinch_mod.conf_teams(games))

    check(len(frames) > 1, f"{year}: expected several frames, got {len(frames)}")
    check([f["w"] for f in frames] == sorted(f["w"] for f in frames),
          f"{year}: frames out of order")

    prev = {}
    for f in frames:
        left = {r["t"] for r in f["left"]}
        right = {r["t"] for r in f["right"]}
        check(left == everyone,
              f"{year} wk{f['w']}: official board missing {everyone - left}")
        check(right == everyone,
              f"{year} wk{f['w']}: sorted board missing {everyone - right}")
        check(len(f["right"]) == len(everyone),
              f"{year} wk{f['w']}: sorted board lists a team twice")

        for r in f["right"]:
            was = prev.get(r["t"])
            if was:
                check(r["w"] >= was[0] and r["l"] >= was[1],
                      f"{year} wk{f['w']}: {r['t']} lost a result "
                      f"({was[0]}-{was[1]} -> {r['w']}-{r['l']})")
            prev[r["t"]] = (r["w"], r["l"])

        # A shared position must be shared by everyone holding that record.
        by_pos = {}
        for r in f["left"]:
            by_pos.setdefault(r["p"], []).append(r)
        for pos, group in by_pos.items():
            recs = {(r["w"], r["l"]) for r in group}
            check(len(recs) == 1,
                  f"{year} wk{f['w']}: position {pos} mixes records {recs}")
            check(len(group) == group[0]["n"],
                  f"{year} wk{f['w']}: position {pos} rowspan disagrees "
                  f"with its group size")

    # The final frame is the season, so it has to match the live page.
    rows = tb.standings(games, overrides)
    final = {r["t"]: (r["w"], r["l"]) for r in frames[-1]["right"]}
    for r in rows:
        check(final.get(r["team"]) == (r["conf_w"], r["conf_l"]),
              f"{year}: final frame has {r['team']} at {final.get(r['team'])}, "
              f"standings say {r['conf_w']}-{r['conf_l']}")
    order = [r["t"] for r in frames[-1]["right"]][:len(rows)]
    check(order == [r["team"] for r in rows],
          f"{year}: final frame order differs from the standings")

    chart = build.bump_svg(frames, json.load(
        open(os.path.join(HERE, "data", "teams.json"))))
    check(chart.count("<polyline") == len(everyone),
          f"{year}: movement chart drew {chart.count('<polyline')} lines "
          f"for {len(everyone)} teams")
    # Unquoted SVG attributes swallow the closing slash and silently kill the
    # styling — this caught exactly that once already.
    check("/>" not in chart.replace('"/>', ""),
          f"{year}: movement chart has an unquoted attribute before '/>'")
    return len(frames)


for y in (2024, 2025):
    n = season(y)
    print(f"{y}: {n} frames checked")

if FAIL:
    for m in FAIL:
        print("FAIL:", m)
    sys.exit(1)
print("OK")

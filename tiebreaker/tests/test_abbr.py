#!/usr/bin/env python3
"""Team short labels must be unique, and the same everywhere they appear.

Arizona and Arizona State shipped as ARI and ARI in the tie archive because
that page truncated the name instead of reading teams.json. Two teams sharing
a header on a head-to-head grid makes every cell in those rows and columns
unreadable, and nothing else on the page can tell you which is which.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import build  # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


teams = build.load_teams()

# 1. Every team has one, and no two teams share it.
by_abbr = {}
for name in teams:
    a = build.team_abbr(teams, name)
    # BYU, TCU and UCF are their own abbreviation, so comparing the label to
    # the name proves nothing — check the key is actually there.
    check((teams.get(name) or {}).get("abbr"),
          f"{name} has no abbr in teams.json — it would render its full "
          f"name in a grid header")
    by_abbr.setdefault(a, []).append(name)
for a, names in sorted(by_abbr.items()):
    check(len(names) == 1, f"'{a}' is shared by {', '.join(sorted(names))}")
print(f"{len(teams)} teams, {len(by_abbr)} distinct labels")

# 2. Nothing may reconstruct a label by truncating a name: Arizona and
#    Arizona State collide at three letters and at four.
for mod in ("build.py", "gen_history.py"):
    src = open(os.path.join(HERE, mod)).read()
    for m in re.finditer(r"\[:\d+\]\s*\.upper\(\)", src):
        line = src[:m.start()].count("\n") + 1
        FAIL.append(f"{mod}:{line} builds a label by truncation; use "
                    f"team_abbr() so every grid agrees")

# 3. The generated archive fragment uses them, with no duplicate header.
frag = os.path.join(HERE, "history", "history_body.html")
if os.path.exists(frag):
    heads = re.findall(r"<th title='([^']*)'>([^<]+)</th>", open(frag).read())
    grid = {}
    for name, label in heads:
        grid.setdefault(label, set()).add(name)
    for label, names in sorted(grid.items()):
        check(len(names) == 1,
              f"archive grid: '{label}' labels {', '.join(sorted(names))}")
    for name, label in heads:
        check(label == build.team_abbr(teams, name),
              f"archive grid: {name} shows '{label}', teams.json says "
              f"'{build.team_abbr(teams, name)}'")
    # Row labels carry the full name; a[:12] used to cut three of them
    # off mid-word ("Arizona Stat").
    body = re.findall(r"<td class=teamcell>.*?>([A-Za-z .'&-]+)</td>",
                      open(frag).read())
    for label in body:
        check(label.strip() in teams,
              f"archive grid row label '{label.strip()}' is not a team name "
              f"— it looks truncated")
    print(f"archive grid: {len(heads)} headers and {len(body)} row labels "
          f"match teams.json")
else:
    print("archive fragment absent — skipped grid check")

if FAIL:
    for m in FAIL:
        print("FAIL:", m)
    sys.exit(1)
print("OK")

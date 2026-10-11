#!/usr/bin/env python3
"""The note under a tied team on the fully-broken board.

build.tie_notes reads the ladder back out of the engine's log: which step
placed each team, and every tied team's figure at that step. The figures are
parsed from the sentences the engine writes, so the thing to hold still is
that parse. A step whose wording moved would not fail anywhere else: the row
would lose its chip, or carry a figure for the wrong team, and the page
would build.

So every week of every season on file is run through it, and three things
are asked of each tied group:

  * every team in a tie the ladder ran on has a note, and nobody else does
  * the step on a team's chip is the step the engine says seated it
  * where the deciding step compared figures, the note has one for every
    team still tied, and the team that was seated is the one shown winning

And one of the page: site/why.js draws the same chip and the same panel from
a note that build.py draws on the server, character for character. The
replay repaints the board on load, so a difference between the two is a row
that changes under the reader.
"""
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import engine                                            # noqa: E402
import build                                             # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
        print(f"  [FAIL] {msg}")


def weeks(games):
    """The season as it stood after each conference week, the way
    season_frames cuts it."""
    ws = sorted({g["week"] for g in games if g["completed"]
                 and g["conference_game"] and not g.get("ccg")})
    for w in ws:
        yield w, [g if g["week"] <= w else
                  dict(g, completed=False, home_points=None, away_points=None)
                  for g in games]


seen_steps = {}
samples = []
n_groups = 0
for path in sorted(glob.glob(os.path.join(HERE, "data", "games_2*.json"))):
    year = re.search(r"(\d{4})", os.path.basename(path)).group(1)
    games = json.load(open(path))
    for w, sub in weeks(games):
        rows = build.pad_standings(engine.standings(sub, {}), sub)
        notes = build.tie_notes(rows)
        where = f"{year} wk{w}"
        groups = {}
        for r in rows:
            if r.get("tie_group"):
                groups.setdefault(r["tie_group"], []).append(r)
        tied = {r["team"] for g in groups.values() for r in g}
        check(set(notes) == tied,
              f"{where}: notes for {sorted(set(notes) ^ tied)} do not match "
              f"the teams in a tie")
        for members in groups.values():
            n_groups += 1
            events = members[0].get("events") or []
            names = [m["team"] for m in members]
            for i, ev in enumerate(events):
                note = notes.get(ev["team"])
                if not note:
                    continue
                seen_steps[note["s"]] = seen_steps.get(note["s"], 0) + 1
                check(note["s"] == ev["step"],
                      f"{where}: {ev['team']} was seated on ({ev['step']}) "
                      f"and the chip says ({note['s']})")
                check(note["st"] and note["st"][-1][2] == 1
                      and all(not s[2] for s in note["st"][:-1]),
                      f"{where}: {ev['team']}'s pass does not end on its "
                      f"one deciding step")
                # A step that compares figures writes them after a colon,
                # one per team. If the engine wrote figures, the note has to
                # have read all of them.
                body = ev["line"][4:].partition(" — ")[0]
                wrote = re.search(r": .*\d", body) and "defeated" not in body \
                    and "won the toss" not in body
                if wrote:
                    pool = names[i:]
                    got = [r[0] for r in note["rows"]]
                    check(sorted(got) == sorted(pool),
                          f"{where}: ({ev['step']}) compared {pool} and the "
                          f"note has figures for {got}")
                    check(got[:1] == [ev["team"]] and note["rows"]
                          and note["rows"][0][4] == 1
                          and sum(r[4] for r in note["rows"]) == 1,
                          f"{where}: {ev['team']} was seated and is not the "
                          f"one shown ahead")
                    check(note["v"] == next(r[2] for r in note["rows"]
                                            if r[0] == ev["team"]),
                          f"{where}: {ev['team']}'s chip figure is not its "
                          f"own")
                else:
                    check(note["rows"] == [] and note["v"] == "",
                          f"{where}: ({ev['step']}) compared nothing and "
                          f"the note shows figures: {ev['line']}")
            if members[0].get("resolved") and events:
                last = notes.get(names[-1])
                check(last and last["s"] == events[-1]["step"]
                      and last["lead"].startswith(names[-1] + " is "),
                      f"{where}: {names[-1]}, last of its tie, does not "
                      f"carry the pass that placed it")
            if not members[0].get("resolved"):
                for t in names[len(events):]:
                    check(notes.get(t, {}).get("s") == "?",
                          f"{where}: {t} is in a tie nothing broke and the "
                          f"chip names a step")
        if year in ("2024", "2025"):
            samples += [[n, t] for t, n in notes.items()]

print(f"{n_groups} tied groups across every week on file; deciding steps "
      f"seen: {dict(sorted(seen_steps.items()))}")
check(n_groups > 300, "that is too few groups to have tested anything")

# --- the server and the browser draw the same row ---------------------------
want = [[build.tie_chip(n), build.tie_panel(n, t)] for n, t in samples]
got = json.loads(subprocess.run(
    ["node", "-e",
     "const w = require(process.argv[1]);"
     "const s = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
     "process.stdout.write(JSON.stringify(s.map("
     "([n, t]) => [w.chip(n), w.panel(n, t)])));",
     os.path.join(HERE, "site", "why.js")],
    input=json.dumps(samples), capture_output=True, text=True,
    check=True).stdout)
diff = [i for i, (a, b) in enumerate(zip(want, got)) if a != b]
check(not diff and len(want) == len(got),
      f"why.js and build.py draw {len(diff)} of {len(want)} rows "
      f"differently, first: {want[diff[0]] if diff else ''} "
      f"vs {got[diff[0]] if diff else ''}")
print(f"{len(want)} notes drawn by both build.py and site/why.js")

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

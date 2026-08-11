#!/usr/bin/env python3
"""The generated facts: the two data hazards, and the dated-fact rule.

Run directly, like the rest of tests/ — prints a summary, exits non-zero on
failure. Reads committed data only; no API calls.

Three things here are worth breaking a build over, and all three produce
sentences that are perfectly formed and untrue rather than errors anybody
would notice:

  * 2020 owns every attendance record, because the stadiums were shut by
    public health order rather than by anything about football. A "smallest
    crowd ever" drawn from it is a fact about a pandemic.
  * Kansas State's published capacity is not usable as a denominator —
    attendance/index.html says so at length — so no percentage claim may be
    made about them.
  * A fact carrying `on` is a claim about a calendar date. Shown on any other
    day it is simply false, so the two pools must not overlap and the text
    must not carry the date claim the label is responsible for.
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import facts  # noqa: E402
import rotation as rotation_mod  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def load(year):
    p = os.path.join(DATA, f"games_{year}.json")
    return json.load(open(p)) if os.path.exists(p) else []


def teams():
    p = os.path.join(DATA, "teams.json")
    return list(json.load(open(p))) if os.path.exists(p) else []


# --- build the real thing, once --------------------------------------------
by_year = {y: load(y) for y in range(facts.TB_FIRST, facts.TB_LAST + 1)}
year = 2026
by_year[year] = load(year)
with tempfile.TemporaryDirectory() as tmp:
    out = os.path.join(tmp, "facts.json")
    counts = facts.build(year, by_year, teams(), {}, rotation_mod, out)
    payload = json.load(open(out))

sections = payload["sections"]
every = [f for s in sections.values() for f in s]
check(every, "no facts were generated at all")
check(set(sections) == {"tiebreaker", "schedule", "attendance", "pools"},
      f"unexpected section list: {sorted(sections)}")

# --- hazard one: the pandemic ----------------------------------------------
# 2020 may be MENTIONED, and in three legitimate ways: the line whose whole
# job is to say what it was, and the records that name it in order to exclude
# it ("the smallest crowd outside 2020…"). What it must never be is the
# ANSWER to a superlative, so a mention is allowed only alongside a word that
# holds it at arm's length.
DISCLAIMED = re.compile(r"\b(outside|leaving|left out|excluded)\b", re.I)
covid_claims = [
    f["t"] for f in sections["attendance"]
    if str(facts.COVID) in f["t"]
    and re.search(r"\b(biggest|smallest|best|worst|thinnest|most|least)\b",
                  f["t"], re.I)
    and not DISCLAIMED.search(f["t"])
]
check(not covid_claims,
      f"{len(covid_claims)} attendance superlative(s) drawn from "
      f"{facts.COVID}: {covid_claims[:2]}")

# And the one line that does name it says why, so a reader does not conclude
# the league nearly folded.
named = [f["t"] for f in sections["attendance"]
         if f"In {facts.COVID} the league averaged" in f["t"]]
check(len(named) == 1,
      f"expected exactly one line explaining {facts.COVID}, got {len(named)}")
check(named and "pandemic" in named[0],
      f"the {facts.COVID} line does not say why it is set apart")

# --- hazard two: the capacity nobody trusts --------------------------------
# Named here, not read from facts.NO_PCT. Taking the list from the module
# under test made this vacuous: emptying NO_PCT emptied the loop, and the
# suite passed while every K-State fill-rate claim came back. A test that
# derives its expectation from the code cannot contradict it.
NO_PERCENT_CLAIMS = {"Kansas State"}
check(NO_PERCENT_CLAIMS <= set(facts.NO_PCT),
      f"facts.NO_PCT no longer covers {NO_PERCENT_CLAIMS - set(facts.NO_PCT)}; "
      f"the tracker does not trust that published capacity as a denominator")
# Scoped to the attendance section, because that is where a percentage means
# "of capacity". A conference win rate is also a percentage and is a perfectly
# ordinary thing to say about this team — the first version of this check did
# not distinguish them and failed the moment records grew a percentage.
for team in NO_PERCENT_CLAIMS:
    bad = [f["t"] for f in sections["attendance"]
           if team in f["t"] and re.search(r"\d+\s*%|filled its stadium",
                                           f["t"])]
    check(not bad,
          f"{len(bad)} percent-of-capacity claim(s) about {team}, whose "
          f"published capacity the tracker does not trust: {bad[:2]}")

# The rule has to be capable of failing, or it is decoration. Every other team
# with enough games should be getting exactly the claim K-State is denied.
filled = [f for f in sections["attendance"] if "filled its stadium" in f["t"]]
check(len(filled) >= 8,
      f"only {len(filled)} teams got a fill-rate fact; the K-State exclusion "
      f"is not being tested by anything")

# --- the dated-fact rule ---------------------------------------------------
dated = [f for f in every if f.get("on")]
undated = [f for f in every if not f.get("on")]
check(dated, "no dated facts at all — the 'on this day' path is dead")
for f in dated:
    check(re.fullmatch(r"\d{2}-\d{2}", f["on"]),
          f"malformed date key {f['on']!r}")
    m, d = int(f["on"][:2]), int(f["on"][3:])
    check(1 <= m <= 12 and 1 <= d <= 31, f"impossible date {f['on']!r}")

# The label says "on this day"; the sentence must not, or a fact rendered from
# the undated pool would claim a day it does not have.
said = [f["t"] for f in every if re.search(r"on this day", f["t"], re.I)]
check(not said,
      f"{len(said)} fact(s) say 'on this day' in their own text, which is the "
      f"label's job and is false whenever the label is not shown: {said[:2]}")

# The two pools are disjoint by construction; assert it, because the page
# selects from them independently.
check(len(dated) + len(undated) == len(every), "a fact is in both pools")

# --- shape and hygiene -----------------------------------------------------
for f in every:
    check(isinstance(f.get("t"), str) and f["t"].strip(), f"empty fact: {f}")
    check("  " not in f["t"], f"collapsed whitespace missed: {f['t']!r}")
    check(f["t"].rstrip().endswith((".", "!")),
          f"fact does not end in a full stop: {f['t']!r}")
    check("None" not in f["t"], f"a None leaked into a fact: {f['t']!r}")
    check("{" not in f["t"] and "}" not in f["t"],
          f"an unformatted brace survived: {f['t']!r}")

seen = {}
for f in every:
    seen.setdefault(f["t"], 0)
    seen[f["t"]] += 1
dupes = [t for t, n in seen.items() if n > 1]
check(not dupes, f"{len(dupes)} fact(s) generated more than once: {dupes[:2]}")

# --- the totals the hub card prints ----------------------------------------
# The card and the facts on the same page must count the same thing. They did
# not once: the card counted every row in the CSV and a fact counted the rows
# carrying a crowd figure, so the page said 1,402 and 1,377 at the same time.
totals = facts.attendance_totals()
if totals:
    line = [f["t"] for f in sections["attendance"]
            if "home games are in this tracker" in f["t"]]
    check(len(line) == 1, "the tracker-size fact is missing or duplicated")
    if line:
        check(f"{totals['games']:,}" in line[0],
              f"attendance_totals says {totals['games']:,} games and the fact "
              f"says something else: {line[0]!r}")
        check(f"{totals['fans']:,}" in line[0],
              f"attendance_totals and the fact disagree on the crowd total")
    check(totals["games"] > 0 and totals["fans"] > 0, "empty totals")
    check(totals["seasons"] >= 10,
          f"only {totals['seasons']} attendance seasons found")

# --- enough of them to be worth rotating -----------------------------------
for name, floor in (("tiebreaker", 40), ("attendance", 60), ("schedule", 30)):
    check(counts.get(name, 0) >= floor,
          f"{name} produced {counts.get(name)} facts, under the {floor} that "
          f"makes rotation meaningful — a family probably stopped matching")

print(f"facts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
      + f"; {len(dated)} dated")

if FAIL:
    for m in FAIL:
        print("FAIL:", m)
    sys.exit(1)
print("OK")

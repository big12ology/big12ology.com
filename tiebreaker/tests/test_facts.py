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
import fetch as fetcher  # noqa: E402
import rotation as rotation_mod  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def load(year):
    """Through mark_ccg, exactly as build.py's load_games does.

    Not a detail. CFBD never tagged the 2017-2021 championship games, and
    build.py repairs that on load rather than on fetch — so a test that reads
    data/ raw is grading a season the deploy never sees. Five conference
    records, three championship facts and a tie at the top of 2021 differed
    between the two, and the suite had no way to notice: it was internally
    consistent with the wrong input.
    """
    p = os.path.join(DATA, f"games_{year}.json")
    return fetcher.mark_ccg(json.load(open(p))) if os.path.exists(p) else []


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

# --- nobody who left -------------------------------------------------------
# The rivalries were filtered first and the rest of the facts were not, so the
# front page went on saying "Texas took the 2023 conference season outright at
# 8-0" for a while. The tags are what the filter runs on, and they were built
# from teams.json — which no longer contains Texas, so the departed teams were
# untagged and therefore invisible to a check written to catch them.
DEPARTED = facts.departed(by_year)
check({"Texas", "Oklahoma"} <= DEPARTED,
      f"departed() has stopped seeing the leavers: {sorted(DEPARTED)}")
# The rule is about SUBJECTS, not mentions. A departed school may be the
# opponent in somebody else's record — that fact belongs to the team it is
# about — but nothing may be a fact about the school that left.
for f in every:
    check(f.get("s") not in DEPARTED,
          f"a fact about {f.get('s')!r}, who left: {f['t']!r}")

# Independent of the tags, because the tags are the thing that was broken
# last time: no fact may OPEN with a departed school.
LEFT_RE = re.compile(r"^(Texas|Oklahoma|Missouri|Nebraska)\b(?! State| Tech)")
opens = [f["t"] for f in every if LEFT_RE.match(f["t"])]
check(not opens,
      f"{len(opens)} fact(s) open with a departed school: {opens[:2]}")

# And the narrowing has to have actually happened, or this is the blanket ban
# wearing a different comment: opponent mentions must survive.
kept = [f for f in every if set(f.get("w") or []) & DEPARTED]
check(len(kept) >= 5,
      f"only {len(kept)} facts mention a departed school as an opponent — the "
      f"subject-only rule has collapsed back into banning the name")


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

# --- two numerals may not touch --------------------------------------------
# These sentences are assembled from counts, years, records and crowd figures,
# and nothing in the templates stopped two of them landing side by side. The
# front page carried "5 of its 9 2026 conference games" and "in 2026 8 teams
# get five home games" for a while: both arithmetically right, both unreadable,
# because the eye takes "9 2026" as one token and has to back up.
#
# The fix is facts._num(), which spells counts up to twenty out; this is what
# keeps a new family from skipping it. Digits separated by a word or any
# punctuation are fine — "66, Texas Tech 6" and "8–1 — the best" both read.
COLLIDE = re.compile(r"\d[\d,]*\s+\d")
collisions = [f["t"] for f in every if COLLIDE.search(f["t"])]
check(not collisions,
      f"{len(collisions)} fact(s) put two numerals side by side, which reads "
      f"as one number until it does not: {collisions[:2]}")

# And none of them opens on one. "2023 was the best-attended season" and
# "1,377 home games are in this tracker" both make the reader decide whether
# the digits are a label or a count before there is a verb to go on.
opens_digit = [f["t"] for f in every if f["t"][:1].isdigit()]
check(not opens_digit,
      f"{len(opens_digit)} fact(s) open on a numeral: {opens_digit[:2]}")

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
            if "through the gates" in f["t"]]
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

#!/usr/bin/env python3
"""The one hand-curated file in data/, and the facts built on it.

Everything else in tiebreaker/data/ is fetched. rivalries.json is typed by a
person, which makes it the one file here that can be quietly wrong in a way no
API will ever correct — a misspelt team name simply produces no facts, and a
silently empty rivalry section looks exactly like a rivalry section.

So: every name must be one this build actually knows, and the generator must
still be producing facts from it.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import facts  # noqa: E402
import fetch as fetcher  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


by_year_all = {}
for _y in range(facts.TB_FIRST, facts.TB_LAST + 1):
    _p = os.path.join(DATA, f"games_{_y}.json")
    if os.path.exists(_p):
        by_year_all[_y] = json.load(open(_p))

rivalries = facts.load_rivalries(by_year_all)
check(rivalries, "no rivalries loaded at all")

# --- nobody who left ------------------------------------------------------
# Editorial, and enforced: a Big 12 site does not print the series record
# against the schools that walked out. Opponents who were never in the
# conference are fine — Iowa and Pitt are just neighbors.
gone = facts.departed(by_year_all)
check("Oklahoma" in gone and "Texas" in gone and "Nebraska" in gone,
      f"departed() no longer sees the obvious leavers: {sorted(gone)}")
for r in rivalries:
    bad = set(r.get("teams") or []) & gone
    check(not bad,
          f"{r.get('name')!r} is played against {sorted(bad)}, who left the "
          f"Big 12 — that pairing is deliberately not printed")

# And it has to be the loader doing it, not just the file being tidy: an
# entry added back in good faith must still be dropped.
raw = json.load(open(os.path.join(DATA, "rivalries.json")))["rivalries"]
check(all(not (set(r["teams"]) & gone) for r in raw),
      "rivalries.json itself lists a departed team; the loader hides it, but "
      "the file should not carry it either")

# Every team named anywhere in the committed schedules, which is the set a
# pairing has to match to ever produce a fact.
known = set()
for y in range(facts.TB_FIRST, facts.TB_LAST + 1):
    p = os.path.join(DATA, f"games_{y}.json")
    if not os.path.exists(p):
        continue
    for g in json.load(open(p)):
        known.add(g.get("home"))
        known.add(g.get("away"))
known.discard(None)

b12 = set(json.load(open(os.path.join(DATA, "teams.json"))))

seen = set()
for r in rivalries:
    ts = r.get("teams") or []
    check(len(ts) == 2, f"a rivalry needs exactly two teams: {r}")
    if len(ts) != 2:
        continue
    for t in ts:
        check(t in known,
              f"{t!r} in {r.get('name')!r} is not a team name this build "
              f"knows — check the spelling against data/games_*.json")
    # At least one side has to be a Big 12 team, or it does not belong here.
    check(bool(set(ts) & b12),
          f"{r.get('name')!r} involves no Big 12 team: {ts}")
    key = frozenset(ts)
    check(key not in seen, f"duplicate pairing: {sorted(ts)}")
    seen.add(key)
    check(r.get("name"), f"rivalry with no name: {ts}")
    # Stored the way it reads mid-sentence, so the generator's _cap() is the
    # thing that makes it a sentence opener.
    check(not (r.get("name") or "")[:1].isupper() or
          (r.get("name") or "").split()[0] not in ("The", "A"),
          f"{r['name']!r} should be stored lower-case ('the Holy War'); the "
          f"generator capitalises it where a sentence needs it")
    check("conference" in r, f"{r['name']!r} does not say if it is a Big 12 game")

# And the facts actually come out. Through mark_ccg, the way build.py loads
# it — a championship game between two rivals counts in the series otherwise,
# and the 2017-2021 ones are only tagged by that repair.
by_year = {}
for y in range(facts.TB_FIRST, facts.TB_LAST + 1):
    p = os.path.join(DATA, f"games_{y}.json")
    if os.path.exists(p):
        by_year[y] = fetcher.mark_ccg(json.load(open(p)))
got = facts.rivalry_facts(by_year, 2026, facts.TB_FIRST)
check(len(got) >= 30,
      f"only {len(got)} rivalry facts generated from {len(rivalries)} "
      f"rivalries — a pairing has probably stopped matching")

# No series claim may read as all-time. These are hundred-year rivalries and
# this repository starts in 2011.
for f in got:
    if "have met" in f["t"]:
        check(str(facts.TB_FIRST) in f["t"],
              f"a series record without its window: {f['t']!r}")
    check("None" not in f["t"], f"a None leaked in: {f['t']!r}")

print(f"rivalries: {len(rivalries)} pairings, {len(got)} facts")
if FAIL:
    for m in FAIL:
        print("FAIL:", m)
    sys.exit(1)
print("OK")

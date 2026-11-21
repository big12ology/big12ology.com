#!/usr/bin/env python3
"""No game date is read off the UTC calendar.

`start` is UTC, so an evening kickoff in the States is already tomorrow
there. Slicing the date off it put Oklahoma State at Arizona State, a
Saturday night game, on Sunday in the Lab's pick list, and the empire
label and hub spotlight did the same thing through pretty_date. The rule
the schedule already used (pk_day: the Central calendar, and the
placeholder's own date for a TBD game) is the one every label goes through.
"""
import os
import re
import json
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import build                                             # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok    " if cond else "  FAIL  ") + msg)
    if not cond:
        FAIL.append(msg)


night = {"start": "2026-11-22T03:15:00.000Z", "start_tbd": False}
tbd = {"start": "2026-11-21T05:00:00.000Z", "start_tbd": True}
friday = {"start": "2026-11-14T03:15:00.000Z", "start_tbd": False}

check(build.pretty_date(night["start"]) == "Sat, Nov 21",
      "a 9:15pm Central kickoff is Saturday, not the UTC Sunday")
check(build.pretty_date(friday["start"]) == "Fri, Nov 13",
      "a Friday night game stays on Friday")
check(build.pretty_date("2026-11-21") == "Sat, Nov 21",
      "a plain date is taken as given")
check(build.game_date(night) == build.pretty_date(night["start"]),
      "game_date and pretty_date agree on a timed game")
check(build.game_date(tbd) == "Sat, Nov 21",
      "a TBD placeholder (05:00Z) keeps its own date, not Central's Friday")
check("Sat, Nov 21" in build.kickoff(tbd) and "TBD" in build.kickoff(tbd),
      "the TBD kickoff line shows the real date and no fake hour")

# The built Lab: every game's `day` is the Central calendar day.
lab = os.path.join(HERE, "site", "lab.html")
if os.path.exists(lab):
    html = open(lab).read()
    m = re.search(r"<script id=payload type=application/json>(.*?)</script>",
                  html, re.S)
    games = json.loads(m.group(1).replace("<\\/", "</"))["games"]
    bad = [g for g in games if g.get("start")
           and g.get("day") != (lambda d: f"{d:%m/%d}" if d else "")(
               build.pk_day(g))]
    check(not bad and all("day" in g for g in games),
          f"every Lab game carries its pk_day date ({len(bad)} differ)")

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

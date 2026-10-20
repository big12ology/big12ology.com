#!/usr/bin/env python3
"""Lines from the-odds-api, joined onto the basketball schedule.

Offline: the feed is faked, so no credit is spent. What is checked is the
join, which is where a line goes wrong while looking well formed: a neutral
floor where the feed calls the other side home, a pair of schools whose
names prefix each other, a game that has already tipped, and when a call is
worth making at all.
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import bb_market as market                               # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


def event(home, away, when, spread_home, total=140.5):
    """One feed event with two books, the home side favored by
    -spread_home."""
    def book(title, nudge):
        return {"title": title, "markets": [
            {"key": "spreads", "outcomes": [
                {"name": home, "point": spread_home + nudge, "price": -110},
                {"name": away, "point": -(spread_home + nudge), "price": -110}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "point": total, "price": -110},
                {"name": "Under", "point": total, "price": -110}]}]}
    return {"home_team": home, "away_team": away, "commence_time": when,
            "bookmakers": [book("DraftKings", 0), book("FanDuel", -1)]}


now = datetime.datetime(2027, 2, 1, 12, tzinfo=datetime.timezone.utc)
games = [
    {"id": 1, "home": "Kansas", "away": "Kansas State", "final": False,
     "start": "2027-02-02T01:00:00.000Z"},
    # Neutral floor: the schedule says Arizona is home, the feed says BYU.
    {"id": 2, "home": "Arizona", "away": "BYU", "final": False,
     "start": "2027-02-02T03:00:00.000Z"},
    # Already tipped: never written.
    {"id": 3, "home": "Utah", "away": "Houston", "final": False,
     "start": "2027-02-01T02:00:00.000Z"},
]
raw = [
    event("Kansas Jayhawks", "Kansas State Wildcats", "2027-02-02T01:00:00Z", -7.5),
    event("BYU Cougars", "Arizona Wildcats", "2027-02-02T03:00:00Z", 2.5),
    event("Utah Utes", "Houston Cougars", "2027-02-01T02:00:00Z", 6.0),
    # Nobody on the schedule: dropped.
    event("Utah State Aggies", "Boise State Broncos", "2027-02-02T03:00:00Z", -3.0),
]
got, captured = market.records(raw, games, now, now.isoformat())

print("the join:")
check(set(got) == {"1", "2"}, "the two upcoming Big 12 games are priced, "
                              "and nothing else")
check(got["1"]["spread"] == -8.0 and got["1"]["over_under"] == 140.5
      and len(got["1"]["books"]) == 2,
      "Kansas is the home favorite by the average of two books, -8")
check(got["2"]["spread"] == -2.0,
      "a neutral floor listed the other way round still gives Arizona's "
      "number: BYU +2.5/+1.5 is Arizona -2.5/-1.5, averaging -2")
check("3" not in got, "a game that has already tipped is never written")

print("when to ask:")
check(market.wanted(games, now), "a Big 12 game in the next two days is "
                                 "worth asking about")
far = [dict(g, start="2027-03-01T01:00:00.000Z") for g in games]
check(not market.wanted(far, now), "a month out is not")
check(not market.wanted([dict(g, final=True) for g in games], now),
      "finished games are not")

if FAIL:
    print(f"\n{len(FAIL)} FAILED")
    sys.exit(1)
print("OK")

#!/usr/bin/env python3
"""The-odds-api joins onto the right game, on the right side.

    python3 tests/test_market.py

Two ways this fails silently, both of which produce a record that looks
perfectly well formed and names the wrong team:

  THE FEED'S HOME TEAM IS NOT OURS. On a neutral-site game there is no
  host to be right about, and the two sources pick opposite sides. Both
  neutral games on 2026-09-19 were flipped: the schedule had Arizona State
  at Kansas and West Virginia at Virginia, the feed had them the other way
  round. Spreads are home-relative on both sides of the join, so taking
  the feed's ordering inverts the sign, and a slate freezes a number that
  names the underdog as the favorite.

  ONE SCHOOL NAME IS A PREFIX OF ANOTHER. "Utah State Aggies" starts with
  "Utah ", and the Big 12 is full of pairs that collide this way. Matching
  on first hit gave Utah State a 28-point edge over Utah in a prototype of
  exactly this code.

Everything here is synthetic. A test that called the API would spend from
a 500-credit month, and one already did once.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import json                                              # noqa: E402
import re                                                # noqa: E402
import market                                            # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


FUTURE = "2099-11-07T20:00:00.000Z"
PAST = "2020-11-07T20:00:00.000Z"


def game(gid, home, away, start=FUTURE):
    return {"id": gid, "home": home, "away": away, "start": start}


def event(home, away, kick=FUTURE, spread=None, total=None, ml=None):
    """One feed event. `spread` is the HOME-as-the-feed-sees-it number, so a
    flipped fixture states it from the feed's point of view, which is the
    whole thing under test."""
    mk = []
    if spread is not None:
        mk.append({"key": "spreads", "outcomes": [
            {"name": home, "point": spread},
            {"name": away, "point": -spread}]})
    if total is not None:
        mk.append({"key": "totals", "outcomes": [
            {"name": "Under", "point": total},
            {"name": "Over", "point": total}]})
    if ml is not None:
        mk.append({"key": "h2h", "outcomes": [
            {"name": home, "price": ml[0]}, {"name": away, "price": ml[1]}]})
    return {"commence_time": kick.replace(".000Z", "Z"),
            "home_team": home, "away_team": away,
            "bookmakers": [{"title": "Book", "markets": mk}]}


TMP = tempfile.mkdtemp()
market.ARCHIVE = os.path.join(TMP, "odds")


def run(events, games, year=2099):
    market._get = lambda p, q, k: (events, {})
    market.key = lambda: "stub"
    out, _ = market.fetch(year, games, existing={}, force=True)
    return out


# A neutral-site game the feed lists the other way round. The schedule says
# Arizona State at Kansas; the feed says Kansas at Arizona State with Arizona
# State laying 5.5. Our record must say Kansas +5.5, i.e. a POSITIVE home
# spread, because Kansas is the schedule's home team and is the underdog.
out = run([event("Arizona State Sun Devils", "Kansas Jayhawks",
                 spread=-5.5, total=53.5, ml=(-215, 170))],
          [game(1, "Kansas", "Arizona State")])
rec = out.get("1", {})
check(rec.get("spread") == 5.5,
      f"neutral flip: home spread {rec.get('spread')}, wanted +5.5")
check(rec.get("home_ml") == 170 and rec.get("away_ml") == -215,
      f"neutral flip: moneylines {rec.get('home_ml')}/{rec.get('away_ml')} "
      f"not re-sided")
check(rec.get("over_under") == 53.5, "neutral flip: lost the total")

# The same game the way round both sources agree on. Sign must not move.
out = run([event("Kansas Jayhawks", "Arizona State Sun Devils",
                 spread=5.5, ml=(170, -215))],
          [game(1, "Kansas", "Arizona State")])
check(out.get("1", {}).get("spread") == 5.5, "unflipped: sign moved anyway")

# Prefix collision. Utah is home and favored by 28; "Utah State Aggies" also
# starts with "Utah ", and taking the first hit reads the away number.
out = run([event("Utah Utes", "Utah State Aggies", spread=-28.5)],
          [game(2, "Utah", "Utah State")])
check(out.get("2", {}).get("spread") == -28.5,
      f"prefix: {out.get('2', {}).get('spread')}, wanted -28.5 for Utah")

# ...and flipped as well, which is where first-hit matching and the feed's
# ordering would have to be wrong together to cancel out.
out = run([event("Utah State Aggies", "Utah Utes", spread=28.5)],
          [game(2, "Utah", "Utah State")])
check(out.get("2", {}).get("spread") == -28.5,
      f"prefix flipped: {out.get('2', {}).get('spread')}, wanted -28.5")

# Forward only: a game that has already kicked off is never written, whatever
# the feed says about it.
out = run([event("Baylor Bears", "TCU Horned Frogs", kick=PAST, spread=-3.5)],
          [game(3, "Baylor", "TCU", start=PAST)])
check("3" not in out, "forward only: wrote a line for a game already started")

# A game nobody in our schedule is playing is dropped, not guessed at.
out = run([event("Ohio State Buckeyes", "Michigan Wolverines", spread=-6.5)],
          [game(4, "Baylor", "TCU")])
check(out == {}, f"unrelated game matched something: {out}")

# Kickoff times drift between sources, so the time is a bucket and the names
# decide. A day out still joins; a week out does not.
out = run([event("Baylor Bears", "TCU Horned Frogs",
                 kick="2099-11-08T04:00:00Z", spread=-3.5)],
          [game(5, "Baylor", "TCU")])
check(out.get("5", {}).get("spread") == -3.5,
      "8 hours of drift broke the join")
out = run([event("Baylor Bears", "TCU Horned Frogs",
                 kick="2099-11-14T20:00:00Z", spread=-3.5)],
          [game(5, "Baylor", "TCU")])
check(out == {}, "a week apart joined as the same game")

# A book with nothing priced contributes no record rather than an empty one.
out = run([event("Baylor Bears", "TCU Horned Frogs")],
          [game(6, "Baylor", "TCU")])
check(out == {}, f"a game with no markets still produced a record: {out}")

# The average is over books, and the spread keeps a decimal while the
# moneyline does not: a book posts -386, never -386.1.
ev = event("Baylor Bears", "TCU Horned Frogs", spread=-3.5, ml=(-180, 150))
ev["bookmakers"].append({"title": "Other", "markets": [
    {"key": "spreads",
     "outcomes": [{"name": "Baylor Bears", "point": -2.5},
                  {"name": "TCU Horned Frogs", "point": 2.5}]},
    {"key": "h2h", "outcomes": [{"name": "Baylor Bears", "price": -175},
                                {"name": "TCU Horned Frogs", "price": 145}]}]})
out = run([ev], [game(7, "Baylor", "TCU")])
rec = out.get("7", {})
check(rec.get("spread") == -3.0, f"average spread {rec.get('spread')}")
check(rec.get("home_ml") == -178 and isinstance(rec.get("home_ml"), int),
      f"average moneyline {rec.get('home_ml')!r}, wanted int -178")
check(len(rec.get("books", [])) == 2, "lost a book")

# due(): the gate that keeps a twice-daily refresh inside a 500-credit month.
import datetime                                          # noqa: E402
# Relative to MIN_AGE_HOURS, not fixed hours. Written against a hardcoded
# 2h and 16h these still passed when the gate moved from 4 to 6, but they
# had stopped testing the boundary and would have gone on passing at any
# value between. The knob is meant to be tuned; the test has to follow it.
now = datetime.datetime(2026, 9, 13, 18, 0, tzinfo=datetime.timezone.utc)


def aged(hours):
    stamp = now - datetime.timedelta(hours=hours)
    return {"1": {"source": market.SOURCE, "as_of": stamp.isoformat()}}


check(not market.due(aged(market.MIN_AGE_HOURS - 0.5), now),
      "due: spent a call just inside the gate")
check(market.due(aged(market.MIN_AGE_HOURS + 0.5), now),
      "due: skipped a capture just past the gate")
check(market.due(aged(market.MIN_AGE_HOURS * 4), now),
      "due: skipped a badly stale capture")
check(market.due({}, now), "due: skipped an empty file")
# The gate is per source, which is what lets fetch.py run the CFBD half on
# a much longer clock than this one. A CFBD record must not read as ours.
check(market.due({"1": {"source": "collegefootballdata.com",
                        "as_of": "2026-09-13T16:00:00+00:00"}}, now),
      "due: read a CFBD record's stamp as its own")
check(not market.due({"1": {"source": "cfbd",
                            "as_of": "2026-09-13T16:00:00+00:00"}},
                     now, source="cfbd"),
      "due: ignored the source it was asked about")

# The archive: raw events kept so a re-parse never costs another call, one
# file per week, and a capture that repeats the last one is not stored twice.
ev = event("Baylor Bears", "TCU Horned Frogs", spread=-3.5)
sched = [dict(game(8, "Baylor", "TCU"), week=7)]
run([ev], sched)
shard = os.path.join(market.ARCHIVE, "2099-week-07.json")


def load(path=None):
    """The shard, or {} if it is not there. Defensive so a shard written to
    the wrong name reports as a failed check rather than a traceback."""
    try:
        return json.load(open(path or shard))
    except (OSError, ValueError):
        return {}


def points(book, gid="8"):
    """Every archived spread for one game, in order, or [] if anything on
    the way down is missing. Same reason as load(): a wrong shape here is a
    result to report, not an exception to raise out of the test."""
    out = []
    for cap in book.get(gid) or []:
        try:
            out.append(cap["event"]["bookmakers"][0]["markets"][0]
                       ["outcomes"][0]["point"])
        except (KeyError, IndexError, TypeError):
            out.append(None)
    return out


check(os.path.exists(shard), f"archive: no shard at {shard}")
book = load()
check(len(book.get("8", [])) == 1, "archive: first capture not stored")
check(book.get("8", [{}])[0].get("event", {}).get("home_team")
      == "Baylor Bears",
      "archive: stored our derived record instead of the raw event")

run([ev], sched)
book = load()
check(len(book.get("8", [])) == 1,
      "archive: stored an identical capture twice")

# A LINK THAT APPEARED IS A CHANGE, even when no number did. includeLinks
# arrived on a capture where five of thirteen games had not moved, and a
# numbers-only digest called those identical and stored none of them. The
# feed only carries about seven days, so a link missed that way is not
# recoverable for a game whose line never moves again.
linked = event("Baylor Bears", "TCU Horned Frogs", spread=-3.5)
linked["bookmakers"][0]["link"] = "https://example.test/baylor-tcu"
run([linked], sched)
book = load()
check(len(book.get("8", [])) == 2, "archive: dropped a capture that added a link")
check((book.get("8") or [{}])[-1].get("event", {})
      .get("bookmakers", [{}])[0].get("link") == "https://example.test/baylor-tcu",
      "archive: stored the capture without its link")

# ...but a betslip link churning under an unchanged market is not. Those
# carry marketId/selectionId that the book reissues, and digesting them
# would archive on churn, which is the thing the digest exists to stop.
churn = event("Baylor Bears", "TCU Horned Frogs", spread=-3.5)
churn["bookmakers"][0]["link"] = "https://example.test/baylor-tcu"
churn["bookmakers"][0]["markets"][0]["outcomes"][0]["link"] = "https://x/?mid=99"
run([churn], sched)
check(len(load().get("8", [])) == 2,
      "archive: a reissued betslip id counted as a market move")

moved = event("Baylor Bears", "TCU Horned Frogs", spread=-4.5)
moved["bookmakers"][0]["link"] = "https://example.test/baylor-tcu"
run([moved], sched)
book = load()
check(len(book.get("8", [])) == 3, "archive: did not store a moved line")
check(points(book) == [-3.5, -3.5, -4.5],
      f"archive: captures out of order or overwritten ({points(book)})")

# A week that has passed is a file nothing touches again, which is what keeps
# the season's churn out of every future commit.
run([event("Baylor Bears", "TCU Horned Frogs", spread=-9.5)],
    [dict(game(9, "Baylor", "TCU"), week=8)])
check(points(load())[-1:] == [-4.5],
      "archive: a later week rewrote an earlier week's shard")
check(os.path.exists(os.path.join(market.ARCHIVE, "2099-week-08.json")),
      "archive: week 8 got no shard of its own")

shutil.rmtree(TMP, ignore_errors=True)

# THE OPENER IS NOT THE LINE'S OWN BOOKS, and the card has to say so.
# the-odds-api reports no opening line, so a record sourced from it carries
# one handed down from CFBD's one or two books while sitting beside eight
# that opened nothing. Printed bare that reads as the same market earlier,
# which is the one thing it is not.
import build                                             # noqa: E402

eight = [{"provider": f"B{i}", "spread": -9} for i in range(8)]
check(build.opener_src({"spread_open": -13.5, "spread_open_books": 2,
                        "books": eight}) == " · 2 books",
      "opener: carried-down opener did not name its book count")
check(build.opener_src({"spread_open": -6.5, "spread_open_books": 1,
                        "books": eight}) == " · 1 book",
      "opener: singular book count is not singular")
check(build.opener_src({"spread_open": -13.5, "books": eight})
      == " · other books",
      "opener: a carried opener with no count claimed the line's books")
# ...and stays quiet when the line's own books are the ones that opened it,
# because then the count above it is already the right one.
own = [{"provider": "DraftKings", "spread": -14, "spread_open": -15},
       {"provider": "Bovada", "spread": -14.5, "spread_open": -15}]
check(build.opener_src({"spread_open": -15, "spread_open_books": 2,
                        "books": own}) == "",
      "opener: glossed an opener its own books reported")
check(build.opener_src({"books": eight}) == "",
      "opener: glossed a record with no opener")
check(build.opener_src({"spread_open": -3, "books": 2}) == "",
      "opener: tripped on a legacy integer book count")

# PROVENANCE ON THE OPENER TOO, for the same reason ESPN rows carry `via`:
# the number outlives the record it was written into, and "2 books" does
# not say WHOSE. The count is what fits on the line, so the source rides in
# a title — and only there, because the visible text must stay true without
# it. That is the test for putting anything on hover.
withsrc = build.opener_src({"spread_open": -13.5, "spread_open_books": 2,
                            "spread_open_src": "collegefootballdata.com",
                            "books": eight})
check("title=" in withsrc and "collegefootballdata.com" in withsrc,
      "opener: source recorded but never surfaced")
# EQUAL, not startswith: an earlier version of this check let a mutation
# append " via CFBD" inside the span and still pass, which is exactly the
# drift it exists to catch.
bare = build.opener_src({"spread_open": -13.5, "spread_open_books": 2,
                         "books": eight})
check(bare == " · 2 books", "opener: a record with no source stopped rendering")
visible = re.sub(r"<[^>]+>", "", withsrc)
check(visible == bare,
      f"opener: visible text changed when a source was present "
      f"({visible!r} vs {bare!r})")

# Recorded at fetch time, not invented at render time. Without this the
# display has nothing to name and silently falls back to the bare count.
import fetch as fetcher_                                  # noqa: E402
fetcher_.get = lambda p, k: [
    {"id": 1, "homeConference": "Big 12",
     "lines": [{"provider": "DK", "spread": -7.0, "spreadOpen": -6.5}]},
    {"id": 2, "homeConference": "Big 12",
     "lines": [{"provider": "DK", "spread": -3.0}]}]
fetcher_.key = lambda: "stub"
recs = fetcher_._cfbd_lines(2099)
check(recs["1"].get("spread_open_src") == fetcher_.CFBD_SOURCE,
      "opener: CFBD did not stamp the source on a record that has an opener")
check("spread_open_src" not in recs["2"],
      "opener: stamped a source on a record with no opening line")

# PROVENANCE IN THE DATA, so the committed file says which source supplied a
# broadcast. GitHub serves this repo's Actions run list unauthenticated but
# refuses the logs (403), so a build-log line needed a credential to read;
# a key in the file needs none. Inert on the page by design.
row = {"type": "tv", "outlet": "FS1", "via": "espn.com"}
check("FS1" in build.broadcast({"id": 1, "home": "Kansas", "away": "TCU",
                                "media": [row]}),
      "provenance: the extra key broke the slate's broadcast line")

if FAIL:
    print("market join: FAILED")
    for m in FAIL:
        print("  FAIL:", m)
    sys.exit(1)
print("market join: 41 scenarios: neutral-site flips re-side, prefix "
      "collisions resolve, kicked-off games are never written, and the "
      "credit gate holds; the raw archive dedupes and shards by week")

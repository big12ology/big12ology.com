#!/usr/bin/env python3
"""A gutted CFBD response must not replace a committed file.

    python3 tests/test_fetch_guard.py

An outage arrives as an exception and build.py already routes those to the
committed data. But CFBD can also answer HTTP 200 with an empty or
near-empty list, which raises nothing: before the guard, fetch_season would
overwrite games_<year>.json with it, the deploy's keep step would commit
the wipe, and the no-new-results check would then see nothing due a score
and stop asking, so the empty season outlived every hourly build until the
weekly refresh. This pins _refuse_shrink: fewer than half the committed
rows raises into the caller's existing failure handling, exactly half or
more writes, and a season with nothing committed yet is free to start from
whatever the API says.

Everything here is stubbed. A test that called CFBD would spend from a
1,000-call month.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import fetch as fetcher                                  # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


TMP = tempfile.mkdtemp()
fetcher.DATA = TMP
fetcher.key = lambda: "test-key"


def game(i, completed=True):
    return {"id": i, "week": 1, "homeTeam": "Kansas", "awayTeam": f"Team {i}",
            "startDate": f"2030-09-0{i}T00:00:00Z", "completed": completed,
            "homePoints": 21, "awayPoints": 10}


def commit_games(n):
    """A committed season of n games, via the writer itself so the shape is
    always the one fetch_season reads back."""
    path = os.path.join(TMP, "games_2030.json")
    if os.path.exists(path):
        os.remove(path)
    fetcher.get = lambda p, k: [game(i + 1) for i in range(n)]
    fetcher.fetch_season(2030, force=True)
    return path


def refetch(rows):
    fetcher.get = lambda p, k: rows
    try:
        fetcher.fetch_season(2030, force=True)
        return None
    except RuntimeError as e:
        return e


# An empty 200 against six committed games: refused, file untouched.
path = commit_games(6)
before = open(path).read()
e = refetch([])
check(e is not None, "empty response: fetch_season did not raise")
check("refusing to overwrite" in str(e or ""),
      f"empty response: raised the wrong thing ({e})")
check(open(path).read() == before, "empty response: the committed file moved")

# Two rows against six: under half, same refusal.
e = refetch([game(1), game(2)])
check(e is not None, "2-of-6 response: fetch_season did not raise")
check(open(path).read() == before, "2-of-6 response: the committed file moved")

# Three against six is exactly half, and half is allowed: a cancellation or
# two must never wedge the fetch.
e = refetch([game(1), game(2), game(3)])
check(e is None, f"3-of-6 response: refused a legitimate write ({e})")
check(len(json.load(open(path))) == 3, "3-of-6 response: wrote the wrong rows")

# A season with nothing committed starts from whatever the API says, even
# nothing: that is the preseason bootstrap, not a glitch.
os.remove(path)
e = refetch([])
check(e is None, f"bootstrap: refused an empty first fetch ({e})")
check(json.load(open(path)) == [], "bootstrap: did not write the empty season")

# Lines do not use _refuse_shrink, because they are merged rather than
# overwritten and a merge cannot lose a game. The guarantee the shrink guard
# bought them is now structural: an empty or partial answer writes nothing
# instead of writing everything, so the committed records survive without
# anyone having to raise. Pinned here because it is the same failure the
# rest of this file is about, reached a different way.
lines_path = os.path.join(TMP, "lines_2030.json")
meta_path = os.path.join(TMP, "lines_2030.meta.json")
# fetch_lines reaches for the-odds-api whenever a schedule is on disk, and
# the forward-only scenario below puts one there. Stubbed for the same
# reason CFBD is: an unstubbed run here spent 6 of a 500-credit month, and
# did it through a parser bug that made the call look like it had failed.
fetcher.market_mod.fetch = lambda year, games, existing=None, force=False: (
    {}, {"skipped": "stubbed"})
line = lambda i: {"id": i, "homeConference": "Big 12",     # noqa: E731
                  "lines": [{"provider": "book", "spread": -3.5}]}
fetcher.get = lambda p, k: [line(i + 1) for i in range(4)]
fetcher.fetch_lines(2030)
before = json.load(open(lines_path))
check(len(before) == 4, f"lines: wrote {len(before)} of 4")

# force_cfbd on the scenarios that are about the MERGE, because the CFBD
# half is rate limited too (fetch.CFBD_MIN_AGE_HOURS) and records written a
# moment ago are not stale. Without it these would exercise the gate rather
# than the thing they were written to pin.
fetcher.get = lambda p, k: []
fetcher.fetch_lines(2030, force_cfbd=True)
check(json.load(open(lines_path)) == before, "empty lines: the file moved")

# A partial answer adds what it knows and leaves the rest alone, where the
# overwrite model would have dropped three games and the shrink guard would
# have refused the one real update along with them.
fetcher.get = lambda p, k: [line(9)]
fetcher.fetch_lines(2030, force_cfbd=True)
after = json.load(open(lines_path))
check(len(after) == 5, f"partial lines: {len(after)} records, wanted 5")
check(all(after[k] == v for k, v in before.items()),
      "partial lines: rewrote a record the answer did not mention")

# FORWARD ONLY. A game that has already kicked off is never rewritten, so a
# number a published slate was built from cannot move under it. The schedule
# is what says which those are, so it has to be on disk for the rule to bite.
json.dump([{"id": 1, "start": "2020-01-01T00:00:00.000Z",
            "home": "Baylor", "away": "TCU"}],
          open(os.path.join(TMP, "games_2030.json"), "w"))
fetcher.get = lambda p, k: [{"id": 1, "homeConference": "Big 12",
                             "lines": [{"provider": "book", "spread": 99.0}]}]
fetcher.fetch_lines(2030, force_cfbd=True)
check(json.load(open(lines_path))["1"] == before["1"],
      "forward only: rewrote a line for a game that already kicked off")

check(os.path.exists(meta_path), "lines: no meta sidecar")

# A RUN THAT LEARNED NOTHING WRITES NOTHING. fetch_lines is reached from the
# hourly weekend builds now, and both its sources are rate limited, so most
# of those runs have nothing new to say. If they rewrote the meta sidecar
# anyway its stamp would move every hour and the deploy's keep step would
# commit it, which is several hundred commits a season saying only what time
# it was.
import time                                               # noqa: E402
before_m = os.path.getmtime(meta_path)
before_l = os.path.getmtime(lines_path)
time.sleep(0.01)


def refuse(path, k):
    raise AssertionError("CFBD called inside the gate window")


# Unforced, so the CFBD gate holds: the records were written seconds ago.
# `refuse` makes that assertion rather than assuming it — a gate that
# quietly stopped working would otherwise still pass the mtime checks by
# returning the same rows.
fetcher.get = refuse
fetcher.fetch_lines(2030)
check(os.path.getmtime(meta_path) == before_m,
      "no-op run: rewrote the meta sidecar")
check(os.path.getmtime(lines_path) == before_l,
      "no-op run: rewrote the lines file")

# MEDIA IS DAILY, NOT WEEKLY, and the gate is what makes that one call a
# day rather than one a run: fetch_media is now reached from every run that
# refreshes lines. Broadcast assignments land on a rolling 12-/6-day window,
# so a weekly refetch was up to a week behind one.
mp = os.path.join(TMP, "media_2030.json")
media_row = lambda i: {"id": i, "outlet": "ESPN",          # noqa: E731
                       "mediaType": "tv"}
json.dump([{"id": 1, "week": 1, "startDate": "2030-09-01T00:00:00Z",
            "homeTeam": "Kansas", "awayTeam": "TCU"}],
          open(os.path.join(TMP, "games_2030.json"), "w"))
fetcher.get = lambda p, k: [media_row(1)]
fetcher.fetch_media(2030, force=True)
check(os.path.exists(fetcher.media_meta_path(2030)),
      "media: no sidecar written")
_age = fetcher.media_age_hours(2030)
check(_age is not None and _age < 1,
      f"media: sidecar stamp is not fresh ({_age})")


def refuse_media(path, k):
    raise AssertionError("CFBD called inside the media gate window")


fetcher.get = refuse_media
fetcher.fetch_media(2030)                       # fresh: must not call

# ...and asks again once the stamp is old enough. Not the file's mtime: a CI
# checkout makes every file seconds old, so an mtime gate would skip forever.
stale = (datetime.datetime.now(datetime.timezone.utc)
         - datetime.timedelta(hours=fetcher.MEDIA_MIN_AGE_HOURS + 1))
json.dump({"fetched_at": stale.replace(microsecond=0).isoformat(), "count": 1},
          open(fetcher.media_meta_path(2030), "w"))
hit = []
fetcher.get = lambda p, k: (hit.append(p) or [media_row(1)])
fetcher.fetch_media(2030)
check(len(hit) == 1, f"media: stale file did not refetch ({len(hit)} calls)")

shutil.rmtree(TMP)

if FAIL:
    print("shrink guard: FAILED")
    for m in FAIL:
        print("  FAIL:", m)
    sys.exit(1)
print("shrink guard: 15 scenarios: a 200 with under half the committed rows "
      "raises instead of writing, half survives, an uncommitted season still "
      "bootstraps from nothing, lines merge forward only, and a run "
      "that learned nothing writes nothing; broadcasts refetch daily "
      "on a sidecar stamp, never on mtime")

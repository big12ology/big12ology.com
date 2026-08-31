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

# Lines: four committed games' lines, then an empty answer. Refused, and the
# meta sidecar is not stamped either, so the stale file still reads as stale.
lines_path = os.path.join(TMP, "lines_2030.json")
meta_path = os.path.join(TMP, "lines_2030.meta.json")
line = lambda i: {"id": i, "homeConference": "Big 12",     # noqa: E731
                  "lines": [{"provider": "book", "spread": -3.5}]}
fetcher.get = lambda p, k: [line(i + 1) for i in range(4)]
fetcher.fetch_lines(2030)
before = open(lines_path).read()
os.remove(meta_path)
fetcher.get = lambda p, k: []
try:
    fetcher.fetch_lines(2030)
    check(False, "empty lines: fetch_lines did not raise")
except RuntimeError:
    pass
check(open(lines_path).read() == before, "empty lines: the committed file moved")
check(not os.path.exists(meta_path), "empty lines: stamped the meta sidecar")

shutil.rmtree(TMP)

if FAIL:
    print("shrink guard: FAILED")
    for m in FAIL:
        print("  FAIL:", m)
    sys.exit(1)
print("shrink guard: 6 scenarios: a 200 with under half the committed rows "
      "raises instead of writing, half survives, and an uncommitted season "
      "still bootstraps from nothing")

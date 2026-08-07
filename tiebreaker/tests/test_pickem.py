#!/usr/bin/env python3
"""The pick'em slate publisher: week grouping, the frozen line, the ratchet.

Run directly, like the rest of tests/ — prints a summary, exits non-zero on
failure. Spends no API calls: everything here reads committed data or fixtures.

The three things worth breaking the build over:

  * A week is a weekend. CFBD's week field is not, and a slate that locks at
    its first kickoff is only fair if the games are the same weekend.
  * A published line never moves. That is the whole basis of the game.
  * The lock never moves later, and is never derived from a kickoff nobody set.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pickem  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def load(year):
    return json.load(open(os.path.join(DATA, f"games_{year}.json")))


def lines(year):
    p = os.path.join(DATA, f"lines_{year}.json")
    raw = json.load(open(p)) if os.path.exists(p) else {}
    return {k: (v if isinstance(v, dict) else {"spread": v})
            for k, v in raw.items()}


# --- the week rule matches attendance's, date for date ---------------------
# Restated in pickem.py rather than imported; this is what keeps the two
# honest, the same way test_parity.py keeps engine.js and tiebreaker.py
# together. A copy nobody diffs is just a fork.

att = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                   "attendance", "scripts")
sys.path.insert(0, att)
try:
    from fetch_attendance import display_week as attendance_week
except ImportError as e:                                  # pragma: no cover
    sys.exit(f"cannot import attendance's display_week: {e}")

drift = []
for season in range(2011, 2027):
    d = datetime.date(season, 7, 1)
    while d < datetime.date(season + 1, 2, 1):
        if pickem.display_week(d, season) != attendance_week(d, season):
            drift.append(f"{d} (season {season})")
        d += datetime.timedelta(days=1)
check(not drift,
      f"week rule differs from attendance's on {len(drift)} dates, "
      f"first {drift[:3]}")
print(f"week rule: agrees with attendance's on every date, 2011–2026")


# --- every week is one weekend --------------------------------------------
# The reason CFBD's week field is unusable, stated as a test. 2025 week 1
# under CFBD spans nine days; under this rule the worst week in either season
# is opening week, which really is Thursday through Labor Day.

for season in (2025, 2026):
    games = load(season)
    by_week = {}
    for g in games:
        w = pickem.week_of(g, season)
        if w is not None:
            by_week.setdefault(w, []).append(pickem.kickoff(g))
    worst_w, worst_h = None, 0
    for w, ks in by_week.items():
        span = (max(ks) - min(ks)).total_seconds() / 3600
        if span > worst_h:
            worst_w, worst_h = w, span
        # Opening week is genuinely Thursday-to-Labor-Day. Everything else is
        # a weekend, and 48h is the line between the two.
        limit = 120 if w <= 1 else 48
        check(span <= limit,
              f"{season} week {w} spans {span:.0f}h, over the {limit}h limit")
    print(f"{season}: {len(by_week)} weeks, widest is week {worst_w} "
          f"at {worst_h:.0f}h")

# The specific regression: CFBD's own grouping must be worse, or this rule is
# solving nothing and the extra week numbering is not worth its cost.
cfbd = {}
for g in load(2025):
    cfbd.setdefault(g["week"], []).append(pickem.kickoff(g))
cfbd_worst = max((max(k) - min(k)).total_seconds() / 3600
                 for k in cfbd.values())
check(cfbd_worst > 120,
      f"CFBD's 2025 weeks are tighter than expected ({cfbd_worst:.0f}h) — "
      f"if that is now true, this whole rule may be unnecessary")
print(f"2025: CFBD's widest week is {cfbd_worst:.0f}h, "
      f"ours is {worst_h:.0f}h")


# --- freezing the spread ---------------------------------------------------

for raw, want in [(-6.5, -13), (-6.4, -13), (-12.8, -26), (0.0, 0),
                  (3.2, 6), (7.0, 14), (None, None), (-0.2, 0)]:
    got = pickem.freeze_spread(raw)
    check(got == want, f"freeze_spread({raw}) = {got}, want {want}")
check(all(isinstance(pickem.freeze_spread(x), int)
          for x in (-6.5, 0.0, 3.2)),
      "freeze_spread must return int, not float — the point is to leave "
      "floating point behind")
print("freeze_spread: half-point rounding, integer output")


# --- the slate ------------------------------------------------------------

games25, lines25 = load(2025), lines(2025)
slate = pickem.build_slate(2025, games25, lines25, week=6)
ids = [e["game_id"] for e in slate["games"]]
check(len(ids) == len(set(ids)), "a game appears twice in one slate")
check(slate["lock_at"] == min(e["kickoff_at"] for e in slate["games"]
                              if e["spread_x2"] is not None),
      "lock_at is not the first pickable kickoff")
check(all(e.get("spread_raw") is None or
          e["spread_x2"] == pickem.freeze_spread(e["spread_raw"])
          for e in slate["games"]),
      "a frozen spread does not match its raw value")
print(f"2025 week 6: {slate['pickable_count']}/{slate['game_count']} "
      f"playable, locks {slate['lock_at']}")

# A game lives in exactly one week — the property the database enforces with
# UNIQUE(season, game_id) and the one that makes joining on game_id safe.
seen = {}
for w in sorted({pickem.week_of(g, 2025) for g in games25} - {None}):
    for e in pickem.build_slate(2025, games25, lines25, week=w)["games"]:
        check(e["game_id"] not in seen,
              f"game {e['game_id']} is in weeks {seen.get(e['game_id'])} "
              f"and {w}")
        seen[e["game_id"]] = w
check(len(seen) == len(games25),
      f"{len(seen)} games placed into weeks, {len(games25)} in the season")
print(f"2025: all {len(seen)} games land in exactly one week")


# --- unpickable games are shown, not dropped -------------------------------

games26, lines26 = load(2026), lines(2026)
# Not the auto-selected week: 2026 opens with the single Dublin game, and it
# happens to be one of the seven with a line. Take a week that is actually
# mixed — with lines for 7 of 120 games, most of the season qualifies.
weeks26 = sorted({pickem.week_of(g, 2026) for g in games26} - {None})
mixed = [pickem.build_slate(2026, games26, lines26, week=w) for w in weeks26]
w26 = next((s for s in mixed if s["game_count"] > s["pickable_count"]), None)
check(w26 is not None,
      "2026 has lines for only 7 of 120 games — some week must have "
      "unpickable rows to show")
noline = [e for e in w26["games"] if e.get("unpickable")]
check(all("spread_x2" in e and e["spread_x2"] is None for e in noline),
      "an unpickable game must still carry an explicit null spread")
check(all(e.get("unpickable") in (pickem.NO_LINE, pickem.NO_KICKOFF)
          for e in noline),
      "an unpickable game must say which of the two reasons applies")

# A kickoff nobody has set cannot set the lock, and cannot be picked.
tbd = [g for g in games26 if g.get("start_tbd")]
check(tbd, "2026 should still have unannounced kickoff windows to test")
tbd_week = pickem.week_of(tbd[0], 2026)
s = pickem.build_slate(2026, games26, lines26, week=tbd_week)
tbd_ids = {g["id"] for g in tbd}
for e in s["games"]:
    if e["game_id"] in tbd_ids:
        check(e["spread_x2"] is None and e["unpickable"] == pickem.NO_KICKOFF,
              f"game {e['game_id']} has no announced kickoff but is playable")
if s["lock_at"] is not None:
    check(s["lock_at"] not in [e["kickoff_at"] for e in s["games"]
                               if e["game_id"] in tbd_ids],
          "the lock was taken from a kickoff nobody has set")
print(f"2026: {len(tbd)} unannounced kickoffs, none playable, none locking")


# --- the ratchet ----------------------------------------------------------

tmp = tempfile.mkdtemp()
real_out = pickem.OUT
pickem.OUT = tmp
try:
    T0 = datetime.datetime(2025, 10, 1, tzinfo=datetime.timezone.utc)

    p = pickem.publish_slate(2025, games25, lines25, week=6, now=T0)
    check(p and os.path.exists(p), "first publish wrote nothing")
    first = json.load(open(p))

    # Idempotent: same inputs, no rewrite. The deploy runs ~300 times a
    # season and every one of those is a commit if this is wrong.
    check(pickem.publish_slate(2025, games25, lines25, week=6,
                               now=T0) is None,
          "republishing identical input rewrote the file")
    check(json.load(open(p)) == first, "a no-op publish changed the bytes")

    # A published line is frozen. Move every line in the market and confirm
    # not one of them moves on disk.
    moved = {k: {**v, "spread": (v.get("spread") or 0) + 7}
             for k, v in lines25.items()}
    pickem.publish_slate(2025, games25, moved, week=6, now=T0)
    after = json.load(open(p))
    check([e["spread_x2"] for e in after["games"]] ==
          [e["spread_x2"] for e in first["games"]],
          "a published spread moved when the market did")

    # A game published without a line may gain one, once — nothing was
    # frozen, so nothing is being rewritten.
    thin = {k: v for k, v in lines25.items()}
    victim = first["games"][-1]["game_id"]
    thin.pop(str(victim), None)
    shutil.rmtree(tmp)
    pickem.publish_slate(2025, games25, thin, week=6, now=T0)
    before = json.load(open(p))
    got = [e for e in before["games"] if e["game_id"] == victim][0]
    check(got["spread_x2"] is None and got["unpickable"] == pickem.NO_LINE,
          "the removed line should have published as unpickable")
    pickem.publish_slate(2025, games25, lines25, week=6, now=T0)
    filled = [e for e in json.load(open(p))["games"]
              if e["game_id"] == victim][0]
    check(filled["spread_x2"] is not None,
          "a late line never filled in before the lock")
    print("ratchet: frozen lines hold, missing lines fill once")

    # Not frozen weeks in advance. The refresh cron runs every Tuesday of the
    # year, including the ones months before kickoff, and the first write is
    # the one that sticks — so without a lead-time guard the season opener is
    # frozen in early August on a line that has all summer left to move.
    locked_at = json.load(open(p))["lock_at"]
    shutil.rmtree(tmp, ignore_errors=True)
    far = datetime.datetime.fromtimestamp(
        locked_at - 30 * 86400, datetime.timezone.utc)
    check(pickem.publish_slate(2025, games25, lines25, week=6,
                               now=far) is None,
          "a slate 30 days out was frozen — openers drift most, and this is "
          "the one write that cannot be taken back")
    check(not os.path.exists(p), "nothing should have been written")

    # But once a week is open, later runs still fill it in. The guard is on
    # opening a slate, not on completing one.
    near = datetime.datetime.fromtimestamp(
        locked_at - 4 * 86400, datetime.timezone.utc)
    pickem.publish_slate(2025, games25, thin, week=6, now=near)
    check(os.path.exists(p), "a slate 4 days out should have published")
    pickem.publish_slate(2025, games25, lines25, week=6, now=near)
    late = [e for e in json.load(open(p))["games"]
            if e["game_id"] == victim][0]
    check(late["spread_x2"] is not None,
          "the guard blocked a merge into an already-open week")
    print(f"lead time: opens inside {pickem.LEAD_DAYS} days, not before, "
          f"and keeps filling after")

    # After the lock, nothing. This is the property someone would have to
    # break to change what a player was scored against.
    T_LATE = datetime.datetime.fromtimestamp(locked_at + 3600,
                                             datetime.timezone.utc)
    snapshot = open(p).read()
    pickem.publish_slate(2025, games25, moved, week=6, now=T_LATE)
    check(open(p).read() == snapshot,
          "the slate was rewritten after it locked")

    # And the lock itself only ever moves earlier.
    cur = json.load(open(p))
    older = dict(cur)
    merged, _ = pickem._merge({**cur, "lock_at": cur["lock_at"] - 86400},
                              cur)
    check(merged["lock_at"] == cur["lock_at"] - 86400,
          "lock_at moved later on merge — a locked week would reopen")
    print("ratchet: locked slates are immutable, lock only moves earlier")
finally:
    pickem.OUT = real_out
    shutil.rmtree(tmp, ignore_errors=True)


# --- the scores file ------------------------------------------------------

tmp = tempfile.mkdtemp()
try:
    sp = os.path.join(tmp, "pickem-scores.json")
    check(pickem.write_scores(2025, games25, sp) is True, "first write")
    check(pickem.write_scores(2025, games25, sp) is False,
          "an unchanged scores file was rewritten — that is a commit on "
          "every one of ~300 deploys a season")
    got = json.load(open(sp))
    done = [g for g in games25 if g["completed"]]
    check(len(got["games"]) == len(games25), "not every game is in the file")
    g0 = done[0]
    check(got["games"][str(g0["id"])] ==
          [g0["home_points"], g0["away_points"], True],
          "a completed game's row does not match the season data")
    print(f"scores: {len(got['games'])} games, rewrite-on-change only")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --- the cross-language ATS fixture stays current --------------------------
# worker/test/parity.test.js grades a real season against expectations
# generated here, in Python. That only proves anything while the fixture still
# matches the data it came from — a stale one turns the Worker's most important
# test into a check that two old files agree with each other.

fix_dir = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                       "worker", "test", "fixtures")
sys.path.insert(0, fix_dir)
try:
    import gen_ats_fixture as genfix
except ImportError as e:                                  # pragma: no cover
    sys.exit(f"cannot import the ATS fixture generator: {e}")

payload, _ = genfix.build()
want = genfix.serialise(payload)
if not os.path.exists(genfix.PATH):
    check(False, f"the ATS fixture is missing — run {genfix.PATH}")
else:
    with open(genfix.PATH) as f:
        have = f.read()
    check(have == want,
          "worker/test/fixtures/ats-2025.json no longer matches the data it "
          "was generated from — rerun worker/test/fixtures/gen_ats_fixture.py")
    print(f"ATS fixture: current, {len(payload['games'])} graded games")

# The convention itself, once more, against the module the Worker mirrors.
# freeze_spread is the only thing standing between a market mean and the
# number a season is scored on.
bad = [r for r in payload["games"]
       if pickem.freeze_spread(r["spread_raw"]) != r["spread_x2"]]
check(not bad, f"{len(bad)} fixture rows disagree with freeze_spread")


if FAIL:
    for m in FAIL:
        print("FAIL:", m)
    sys.exit(1)
print("OK")

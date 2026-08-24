#!/usr/bin/env python3
"""Publish the week's pick'em slate, with its lines frozen.

The site has never needed to remember anything. Every page is a projection of
committed data, so a rebuild in December reproduces August exactly. A pick'em
breaks that: the number a player picked against is a fact about a moment, and
`fetch.py:fetch_lines` overwrites `data/lines_<year>.json` wholesale on every
refresh. Nothing keeps the old file, and nothing stamps the new one. By the
time a week is graded, the line it was played on is gone.

So the slate is written down, once, and committed. This is the same argument
`build.py:write_forecast` makes for forecasts — "a rebuild in December cannot
reconstruct what September believed" — applied to the one number the game is
scored against. Git becomes the durable record and the database is a serving
copy of it: lose the database and this replays; lose this and the season is
unreconstructable.

    from pickem import publish_slate
    publish_slate(2026, games, lines)     # -> pickem/2026/week-03.json

Two things it will not do, both of which would be silent corruptions:

  * It will not move a line that has already been published.
  * It will not invent a lock time from a kickoff nobody has set.
"""
import datetime
import json
import os
import zoneinfo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pickem")

# Big 12 footprint runs Mountain to Eastern, and the conference offices keep
# Central. The week a game belongs to is a question about the local calendar —
# a Saturday night kickoff in Provo is Sunday in UTC — so the date is resolved
# here before anything is grouped by it.
CENTRAL = zoneinfo.ZoneInfo("America/Chicago")

# A slate can only be as honest as its lock. Both of these mean "not pickable".
NO_LINE = "no_line"
NO_KICKOFF = "kickoff_tbd"

# How early a week may be frozen. The weekly refresh runs every Tuesday of the
# year, so without this the first slate of the season is published the first
# Tuesday the schedule exists — in 2026 that froze the August 29 opener on a
# line taken three weeks out, and froze it for good, because the whole point of
# this module is that a published line does not move. Openers drift further
# than any other line on the board.
#
# Eight days is the Tuesday before the lock, plus a day of slack for a
# Thursday-night opener and for the cron running a few hours late.
LEAD_DAYS = 8


def display_week(d, season):
    """Week number from a game's local date. Weeks run Tuesday–Monday, so
    Week 1 ends on Labor Day Monday and Week 0 is the weekend before.

    This is `attendance/scripts/fetch_attendance.py:display_week`, restated
    rather than imported: the two subtrees were separate repos, are rsynced
    separately, and one reaching into the other's scripts/ would be a new
    coupling for six lines of arithmetic. tests/test_pickem.py runs both over
    every date in every season and diffs them, the same way test_parity.py
    keeps engine.js and tiebreaker.py honest. If you change one, change both.

    CFBD's own `week` field cannot be used here and this is not a stylistic
    preference. In 2025 its week 1 spans August 23 to September 2 — nine days
    and eighteen games. A slate that locks at its first kickoff would have
    locked all eighteen nine days before the last one played, on lines frozen
    thirteen days earlier. Every week under this rule is a weekend.
    """
    sept1 = datetime.date(season, 9, 1)
    labor_day = sept1 + datetime.timedelta(days=(7 - sept1.weekday()) % 7)
    week1_tuesday = labor_day - datetime.timedelta(days=6)
    return 1 + (d - week1_tuesday).days // 7


def kickoff(game):
    """A game's kickoff as an aware UTC datetime, or None if unparseable."""
    s = game.get("start")
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def week_of(game, season):
    """The pick'em week a game belongs to, by its Central-local date."""
    k = kickoff(game)
    return None if k is None else display_week(k.astimezone(CENTRAL).date(),
                                               season)


def freeze_spread(raw):
    """The home spread, times two, as an integer — or None.

    Two decisions in one line, both about arithmetic that must not drift.

    Doubling removes floating point from scoring. The margin plus the spread
    is compared against zero to find a push, and `-12.8` as a float makes that
    comparison a coin toss with rounding error: 1.1999999999999993 is not
    zero, and neither is anything else you hoped would be.

    Rounding to the half point makes it a line. CFBD returns a mean across
    books to one decimal, so `-12.8` is not a number any book posted, and a
    mean sitting on an arbitrary tenth almost never equals a real margin — the
    averaging quietly abolishes the push.

    Measured over 2025's 120 graded games: scored against the raw mean, one
    push (0.8%). Scored against the frozen half point, five (4.2%). The gain
    is not because half-point lines push — they cannot — but because rounding
    to the nearest half pulls the near-whole means onto whole numbers, which
    are exactly the lines that can. Four results changed.
    """
    return None if raw is None else round(raw * 2)


CONFERENCE = "Big 12"


def build_slate(season, games, lines, week=None):
    """The slate payload for a week, without touching the disk.

    Separate from the writing so tests can exercise the shape and the rules
    without a filesystem, and so the caller can inspect a week before it is
    committed to.
    """
    by_week = {}
    for g in games:
        w = week_of(g, season)
        if w is not None:
            by_week.setdefault(w, []).append(g)

    if week is None:
        # The week being played: the earliest one still holding an unplayed
        # game. Not "the latest week with a result" — that is last week once
        # Saturday ends, and the slate has to exist before anyone can pick it.
        pending = [w for w, gs in by_week.items()
                   if any(not x.get("completed") for x in gs)]
        if not pending:
            return None
        week = min(pending)

    entries = []
    for g in sorted(by_week.get(week, []), key=lambda x: (x["start"], x["id"])):
        k = kickoff(g)
        raw = (lines.get(str(g["id"])) or {}).get("spread")
        spread_x2 = freeze_spread(raw)
        reason = None
        if g.get("start_tbd"):
            # CFBD returns a placeholder hour for an unannounced window, so a
            # game whose kickoff nobody has set still arrives carrying a
            # plausible-looking time. Picking against it would mean locking at
            # an hour that was never real; it is shown, and it is not playable.
            # Note the ordering: no kickoff outranks no line, because a game
            # can have both problems and this is the one that breaks the lock.
            spread_x2, reason = None, NO_KICKOFF
        elif spread_x2 is None:
            reason = NO_LINE
        # Which side is actually in the conference.
        #
        # The pick'em does not care — you pick a SIDE of a game and both sides
        # exist. The survivor pool does, and badly: you spend a team for the
        # season there, and a non-conference visitor plays a Big 12 team once
        # all year. Spending BYU costs eleven more appearances; spending Notre
        # Dame costs nothing, so the whole pool would be played on borrowed
        # opponents. Carried on the frozen slate rather than looked up later,
        # because it has to mean the same thing in December as it did in
        # August.
        sides = []
        if g.get("home_conf") == CONFERENCE:
            sides.append("home")
        if g.get("away_conf") == CONFERENCE:
            sides.append("away")

        e = {
            "game_id": g["id"],
            "home": g["home"],
            "away": g["away"],
            "b12": "both" if len(sides) == 2 else (sides[0] if sides else None),
            # Carried for the same reason b12 is: the slate has to say in
            # December what it said in August. It is here to be read, not
            # scored — a neutral-site game has no host, so the row must join
            # its two teams with "vs" rather than telling the reader that one
            # of them was at home. The feed still fills a home column because
            # it needs one; this is the only thing that knows better.
            "neutral": bool(g.get("neutral_site")),
            "kickoff": g["start"],
            "kickoff_at": int(k.timestamp()) if k else None,
            "spread_x2": spread_x2,
        }
        if reason:
            e["unpickable"] = reason
        else:
            # Kept for audit, never for scoring. If the frozen half-point ever
            # has to be defended, this is what it was derived from. `books`
            # stays a count because the Worker binds it straight into a D1
            # column; the per-book entries ride under their own key, which
            # the import reads past.
            ln = lines.get(str(g["id"])) or {}
            b = ln.get("books")
            e["spread_raw"] = raw
            e["books"] = len(b) if isinstance(b, list) else b
            if isinstance(b, list) and b:
                e["book_lines"] = b
        entries.append(e)

    playable = [e for e in entries if e["spread_x2"] is not None]
    return {
        "season": season,
        "week": week,
        # The whole slate locks at the first kickoff anyone can pick. Games
        # nobody can pick do not get a vote — otherwise an unlined Thursday
        # opener would lock a Saturday slate two days early.
        "lock_at": min((e["kickoff_at"] for e in playable), default=None),
        "status": "published" if playable else "no_contest",
        "game_count": len(entries),
        "pickable_count": len(playable),
        "games": entries,
    }


def _merge(old, new):
    """Fold a fresh reading into a published slate. Returns (payload, changed).

    The rule is narrower than "write once" and wider than "overwrite", and the
    boundary is the point at which a line became a fact someone could act on:

      * A published spread is frozen. Full stop — that is the number the game
        is scored against and moving it rewrites history.
      * A game published without one may gain a line, once. Nothing was frozen,
        so nothing is being changed; a Tuesday with no market that opens by
        Thursday should be playable rather than dead for the week.
      * A game that was not in the file at all may be added.
      * Nothing is ever removed. A game that vanishes upstream is voided at
        scoring time, where it is visible, not deleted here, where it is not.
    """
    by_id = {e["game_id"]: e for e in old.get("games", [])}
    changed = False
    for e in new["games"]:
        prev = by_id.get(e["game_id"])
        if prev is None:
            by_id[e["game_id"]] = e
            changed = True
        elif prev.get("spread_x2") is None and e.get("spread_x2") is not None:
            by_id[e["game_id"]] = e
            changed = True

    games = sorted(by_id.values(), key=lambda x: (x["kickoff"] or "",
                                                  x["game_id"]))
    playable = [e for e in games if e["spread_x2"] is not None]
    lock = min((e["kickoff_at"] for e in playable), default=None)

    # A lock may only ever move earlier. Later means a week that has already
    # started gets reopened, which would let a pick be entered against a game
    # whose result is known.
    if old.get("lock_at") is not None:
        lock = old["lock_at"] if lock is None else min(lock, old["lock_at"])
    if lock != old.get("lock_at"):
        changed = True

    merged = dict(old)
    merged.update({
        "lock_at": lock,
        "status": "published" if playable else "no_contest",
        "game_count": len(games),
        "pickable_count": len(playable),
        "games": games,
    })
    return merged, changed


def publish_slate(season, games, lines, week=None, now=None, republish=False):
    """Write pickem/<season>/week-NN.json. Returns the path, or None.

    Called from the weekly refresh, where the lines have just been refetched.
    Safe to call on every build: after the lock it is a no-op, and before it
    only fills in what was missing.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    slate = build_slate(season, games, lines, week)
    if slate is None:
        print("pickem: no unplayed games — nothing to publish")
        return None

    # Too far out to freeze. Only blocks the first write: once a week is
    # published, later runs still merge into it, so a week that opened inside
    # the window keeps filling in as its remaining lines post.
    lock = slate["lock_at"]
    p_early = os.path.join(OUT, str(season), f"week-{slate['week']:02d}.json")
    if (lock is not None and not os.path.exists(p_early)
            and lock - now.timestamp() > LEAD_DAYS * 86400):
        days = (lock - now.timestamp()) / 86400
        print(f"pickem: week {slate['week']} locks in {days:.0f} days — "
              f"too early to freeze (publishes within {LEAD_DAYS})")
        return None

    out = os.path.join(OUT, str(season))
    p = os.path.join(out, f"week-{slate['week']:02d}.json")
    stamp = now.replace(microsecond=0).isoformat()

    if os.path.exists(p):
        try:
            old = json.load(open(p))
        except (ValueError, OSError):
            old = None               # unreadable: fall through and rewrite
        if old is not None:
            locked = (old.get("lock_at") is not None
                      and now.timestamp() >= old["lock_at"])
            if locked and not republish:
                # Past the lock this file is evidence. Someone has picked
                # against it, and the only reason to rewrite it now is to
                # change what they were playing.
                merged, changed = _merge(old, slate)
                if changed:
                    _warn(f"week {slate['week']} is locked — refusing to "
                          f"republish (use --republish for a correction)")
                else:
                    print(f"pickem: week {slate['week']} locked, unchanged")
                return None
            merged, changed = _merge(old, slate)
            if not changed:
                print(f"pickem: week {slate['week']} unchanged")
                return None
            slate = merged

    slate["published"] = stamp
    os.makedirs(out, exist_ok=True)
    with open(p, "w") as f:
        json.dump(slate, f, indent=1, sort_keys=True)
    lock = slate["lock_at"]
    when = (datetime.datetime.fromtimestamp(lock, datetime.timezone.utc)
            .strftime("%a %d %b %H:%M UTC") if lock else "no lock — no lines")
    print(f"pickem: week {slate['week']} — {slate['pickable_count']} of "
          f"{slate['game_count']} playable, locks {when} -> {p}")
    return p


def write_scores(season, games, path):
    """The results the grader reads: {game_id: [home, away, completed]}.

    data/ is not published — assemble.sh copies site/, not the season files —
    and site/data.json carries standings, not scores. So there is currently no
    way for anything outside this repo to learn that a game finished without
    spending CFBD quota to ask. This is that, and it costs nothing: the build
    already holds the games.

    Sorted and rewritten only on change, because it is emitted on every build
    and the deploy runs roughly 1,800 times a season.
    """
    payload = {
        "season": season,
        "games": {str(g["id"]): [g.get("home_points"), g.get("away_points"),
                                 bool(g.get("completed"))]
                  for g in sorted(games, key=lambda x: x["id"])},
    }
    if os.path.exists(path):
        try:
            if json.load(open(path)) == payload:
                return False
        except (ValueError, OSError):
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    return True


def _warn(msg):
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning::pickem: {msg}")
    print(f"WARNING: pickem: {msg}")

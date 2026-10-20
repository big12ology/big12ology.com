"""Publish the basketball pick'em slates and the results they are graded on.

The football publisher's rules (tiebreaker/pickem.py), on basketball's
calendar. Its docstring makes the case for writing a slate down at all: the
number a player picked against is a fact about a moment, so it is committed
once and git is the record, the database a serving copy. What is reused as
is: freeze_spread (the home spread doubled, rounded to the half point) and
_merge (a published line never moves; a game published without one may gain
it, once; nothing is ever removed; the week's lock only ever moves earlier).

What is basketball's:

  THE WEEK is Monday to Sunday, Central, the conference's office time, and
  week 1 is the week of the first conference game. A basketball week has
  games on four or five days, so the pick'em locks each game at its own tip
  (migration 0015); the week's lock_at is its first tip, which is when the
  card starts filling in and the grader starts reading.

  THE GAMES are the regular-season conference games. Kansas City is the
  bracket challenge, a game of its own.

  THE LINES come only from the-odds-api (bb_market.py), never from CBBD,
  which was too thin to stake a pool on. Books post a basketball game a day
  or two out, so a slate usually publishes without lines and each game gains
  its line as the daily capture reaches it.

  THE IDS are ESPN's event ids, unique across sports, so a basketball game
  can never share an id with a football one in the pools tables.

    basketball/pickem/<league>/<season>/week-NN.json   one per week
    basketball/pickem/<league>/<season>/scores.json    every conference final
    basketball/pickem/<league>/<season>/bracket.json   the Kansas City bracket
                                                        challenge (write_bracket)

The Worker reads both from the repository (worker/src/hoops.js).
"""
import datetime
import json
import os
import sys
import zoneinfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(1, os.path.join(ROOT, "tiebreaker"))

import bb_sport as sport                                 # noqa: E402
from pickem import freeze_spread, _merge                 # noqa: E402

LEAGUE = "wbb" if sport.WOMEN else "mbb"
OUT = os.path.join(HERE, "pickem", LEAGUE)
CENTRAL = zoneinfo.ZoneInfo("America/Chicago")
ODDS = "the-odds-api.com"
# How far ahead a week may be opened: a week is published once its first
# game is this close, and filled in on every build after that.
LEAD = datetime.timedelta(days=8)


def _ts(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def monday(t):
    """The Monday, Central, of the week a tip time falls in."""
    d = t.astimezone(CENTRAL).date()
    return d - datetime.timedelta(days=d.weekday())


def conference(games):
    """The season's pickable universe: regular-season conference games with
    an ESPN id and a tip time."""
    return [g for g in games
            if g.get("conf") and g.get("espn") and _ts(g.get("start") or "")]


def weeks(games):
    """{week number: [games]}, week 1 being the first conference week."""
    conf = conference(games)
    if not conf:
        return {}
    first = min(monday(_ts(g["start"])) for g in conf)
    out = {}
    for g in conf:
        n = 1 + (monday(_ts(g["start"])) - first).days // 7
        out.setdefault(n, []).append(g)
    return out


def entry(g, lines):
    """One game as a slate row, in the shape the Worker imports."""
    t = _ts(g["start"])
    rec = lines.get(g["id"]) or {}
    # The-odds-api only. A game whose tip time is a placeholder is published
    # unpickable too: a lock at an hour nobody set is not one to sell.
    spread = rec.get("spread") if rec.get("source") == ODDS else None
    if g.get("tbd"):
        spread = None
    row = {
        "game_id": int(g["espn"]),
        "home": g["home"], "away": g["away"],
        "kickoff": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "kickoff_at": int(t.timestamp()),
        "spread_x2": freeze_spread(spread),
        "spread_raw": spread,
        "books": (len(rec["books"]) if spread is not None and rec.get("books")
                  else None),
        "b12": "both",
        "neutral": bool(g.get("neutral")),
    }
    if spread is not None and rec.get("books"):
        row["book_lines"] = rec["books"]
    return row


def publish(season, games, lines, now=None, log=print):
    """Open the weeks whose first tip is within LEAD, and fill in the ones
    already open. Returns the week numbers written."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.replace(microsecond=0).isoformat()
    written = []
    for n, gs in sorted(weeks(games).items()):
        first = min(_ts(g["start"]) for g in gs)
        last = max(_ts(g["start"]) for g in gs)
        path = os.path.join(OUT, str(season), f"week-{n:02d}.json")
        exists = os.path.exists(path)
        # A week that has not opened yet and is not close; or one long over
        # that was never opened (a build that did not run that week), which
        # is left unopened rather than published after the fact.
        if not exists and (first - now > LEAD or last < now):
            continue
        new = {"season": season, "week": n, "league": LEAGUE,
               "games": [entry(g, lines) for g in sorted(
                   gs, key=lambda g: (g["start"], g["id"]))]}
        if exists:
            old = json.load(open(path))
        else:
            old = {"season": season, "week": n, "league": LEAGUE,
                   "published": stamp, "games": []}
        merged, changed = _merge(old, new)
        if not changed and exists:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(merged, f, indent=1, sort_keys=True)
        written.append(n)
    if written:
        log(f"basketball {LEAGUE} {season}: slate week(s) {written} written")
    return written


def write_scores(season, games):
    """Every conference final, by ESPN id: {id: [home, away, completed]}.
    Rewritten only on change, as the football file is."""
    payload = {"season": season, "games": {
        str(int(g["espn"])): [g["hp"], g["ap"], bool(g["final"])]
        for g in sorted(conference(games), key=lambda g: int(g["espn"]))}}
    path = os.path.join(OUT, str(season), "scores.json")
    if os.path.exists(path):
        try:
            if json.load(open(path)) == payload:
                return False
        except (OSError, ValueError):
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    return True


# --- the Kansas City bracket challenge ------------------------------------

# Points per game, in bracket order: the first round's four games 1 each, the
# second round's 2, the quarterfinals 4, the semifinals 8, the final 16. A
# perfect bracket is 60, and the champion alone is worth more than the whole
# first two rounds.
POINTS = [1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 8, 8, 16]


def write_bracket(season, games, order, results, bracket, log=print):
    """bracket.json: the seeds, the shape, the points, the lock and the
    results, once the regular season is over and the seeds are fact.

    `order` is the sixteen teams by seed and `results` {game index: winner},
    both from bb_build (bracket_order, bracket_results), so the challenge is
    drawn from exactly the bracket the Bracket page shows. The lock is the
    first Kansas City tip on the schedule; until one is, there is nothing to
    lock at and nothing is written. Rewritten only on change."""
    conf = [g for g in games if g.get("conf")]
    if not conf or not all(g["final"] for g in conf):
        return False
    tips = [_ts(g["start"]) for g in games if g.get("tourney") and g.get("start")]
    tips = [t for t in tips if t]
    if not tips:
        return False
    payload = {
        "season": season, "league": LEAGUE,
        "seeds": list(order),
        "games": [[a, b] for a, b in bracket],
        "points": POINTS,
        "lock_at": int(min(tips).timestamp()),
        "results": {str(k): v for k, v in sorted(results.items())},
    }
    path = os.path.join(OUT, str(season), "bracket.json")
    if os.path.exists(path):
        try:
            old = json.load(open(path))
            # The seeds and the lock are facts once written: a republish may
            # add results and pull the lock earlier, never anything else.
            if old.get("seeds") and old["seeds"] != payload["seeds"]:
                log(f"WARNING: basketball {LEAGUE} {season}: the bracket's "
                    f"seeds changed after publishing; keeping the published "
                    f"ones")
                payload["seeds"] = old["seeds"]
            if old.get("lock_at"):
                payload["lock_at"] = min(old["lock_at"], payload["lock_at"])
            if old == payload:
                return False
        except (OSError, ValueError):
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    log(f"basketball {LEAGUE} {season}: bracket challenge written "
        f"({len(payload['results'])} of 15 results)")
    return True

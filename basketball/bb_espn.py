"""Finals from ESPN, for the hours between a game ending and CBBD saying so.

The football section learned this first (tiebreaker/espn.py for broadcasts,
worker/src/espn.js for finals): ESPN's scoreboard answers in a second, needs
no key, spends nothing from the CFBD/CBBD quota, and carries ESPN's own event
ids, which CBBD hands out as each game's sourceId. So the join is an id, not
a guess at team names.

CBBD STAYS THE RECORD. A final from here only fills a game CBBD has not
finished; the moment CBBD has the game, CBBD's numbers are the ones used.
The worst a bad read can do is show a result a few hours early, and the next
CBBD fetch replaces it.

WHAT IT SAVES is quota as much as time. bb_season asks CBBD for the season
whenever a game is due a final, and on a basketball night that is every
build until CBBD catches up. With ESPN's final in hand, a game is no longer
waiting on anything, so the hourly builds stop spending calls on it and
CBBD's own copy arrives with the daily check.

KEPT, like every other fetch. Each scoreboard is written to data/raw/espn/
and read back on every build, so a final survives the builds that do not ask
for it and a parser fix costs a re-read, not a re-fetch. A date is asked
again only while one of its games is still waiting.

An undocumented endpoint, which is a real cost. Every failure here ends in
"use what CBBD said", never an exception.
"""
import datetime
import json
import os
import re
import subprocess
import zoneinfo

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw", "espn")
API = ("https://site.api.espn.com/apis/site/v2/sports/basketball/"
       "mens-college-basketball/scoreboard")
# The Big 12. Its scoreboard includes every game a member plays, the
# non-conference ones too (checked on 2025-11-20: UCF and Pittsburgh at the
# Legends Classic, Houston and Rider).
GROUP = 8
# ESPN files a game under its Eastern date: a 10 p.m. Pacific tip on the 12th
# is on the 12th's scoreboard, though it is the 13th in UTC.
ET = zoneinfo.ZoneInfo("America/New_York")
# A game is worth asking about two hours after it tips, and not after four
# days, by when one with no final is a postponement and not a late score.
ASK_AFTER = datetime.timedelta(hours=2)
GIVE_UP_AFTER = datetime.timedelta(days=4)


def _parse(ts):
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def path(day):
    return os.path.join(RAW, f"scoreboard-{day}.json")


def waiting(games, now):
    """Games ESPN might have a final for that CBBD does not."""
    out = []
    for g in games:
        if g["final"] or not g.get("espn"):
            continue
        if (g.get("status") or "") in ("canceled", "cancelled", "postponed"):
            continue
        t = _parse(g["start"])
        if t and t + ASK_AFTER <= now <= t + GIVE_UP_AFTER:
            out.append(g)
    return out


def day_of(g):
    return _parse(g["start"]).astimezone(ET).strftime("%Y%m%d")


def _get(day):
    """One scoreboard, or None. Never raises."""
    url = f"{API}?dates={day}&groups={GROUP}&limit=300"
    try:
        r = subprocess.run(["curl", "-sS", "-m", "30", "--fail", url],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None
        body = json.loads(r.stdout)
        return body if isinstance(body.get("events"), list) else None
    except (OSError, ValueError, AttributeError):
        return None


def refresh(games, now=None, log=print):
    """Ask ESPN for every date that has a game waiting on a final, and keep
    what comes back. Returns the number of scoreboards fetched."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    days = sorted({day_of(g) for g in waiting(games, now)})
    got = 0
    for day in days:
        body = _get(day)
        if body is None:
            log(f"WARNING: basketball ESPN scoreboard {day} did not answer; "
                f"waiting on CBBD")
            continue
        # An empty scoreboard where one was kept is a hiccup, not news:
        # stale beats gutted.
        if not body["events"] and os.path.exists(path(day)):
            log(f"WARNING: basketball ESPN scoreboard {day} came back empty; "
                f"keeping the copy on disk")
            continue
        os.makedirs(RAW, exist_ok=True)
        with open(path(day), "w") as f:
            json.dump(body, f, separators=(",", ":"))
        got += 1
    if days:
        log(f"basketball ESPN: {got} of {len(days)} scoreboard(s) read for "
            f"{len(waiting(games, now))} game(s) waiting on a final")
    return got


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _names(team):
    return {_norm(team.get(k)) for k in
            ("location", "shortDisplayName", "displayName", "abbreviation")} - {""}


def kept_finals():
    """{espn id: {home name set, away name set, home score, away score}} for
    every completed game on every kept scoreboard."""
    out = {}
    if not os.path.isdir(RAW):
        return out
    for fn in sorted(os.listdir(RAW)):
        try:
            body = json.load(open(os.path.join(RAW, fn)))
        except (OSError, ValueError):
            continue
        for e in body.get("events") or []:
            try:
                c = e["competitions"][0]
                if not c["status"]["type"]["completed"]:
                    continue
                side = {t["homeAway"]: t for t in c["competitors"]}
                out[str(e["id"])] = {
                    "home": _names(side["home"]["team"]),
                    "away": _names(side["away"]["team"]),
                    "hp": int(side["home"]["score"]),
                    "ap": int(side["away"]["score"]),
                }
            except (KeyError, IndexError, TypeError, ValueError):
                continue
    return out


def overlay(games, log=print):
    """Fill the games CBBD has not finished from the kept ESPN finals, in
    place. Each filled game is marked src="espn". Returns how many."""
    finals = kept_finals()
    n = 0
    for g in games:
        f = finals.get(str(g.get("espn") or ""))
        if g["final"] or not f or f["hp"] == f["ap"]:
            continue
        # Orient by name, not by ESPN's home and away: on a neutral floor
        # the two feeds can list the teams the other way round.
        h, a = _norm(g["home"]), _norm(g["away"])
        if h in f["home"] or a in f["away"]:
            hp, ap = f["hp"], f["ap"]
        elif h in f["away"] or a in f["home"]:
            hp, ap = f["ap"], f["hp"]
        else:
            log(f"WARNING: basketball ESPN {g['espn']}: neither "
                f"{g['home']} nor {g['away']} matches ESPN's teams; skipped")
            continue
        g.update(hp=hp, ap=ap, final=True, status="final", src="espn")
        n += 1
    return n

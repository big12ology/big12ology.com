#!/usr/bin/env python3
"""Broadcast windows from ESPN, to fill the ones CFBD does not carry.

No key, no quota, and no join to write: CFBD hands out ESPN's own event
ids, so `str(game["id"])` is already the key on both sides. That is the
whole reason this is worth having rather than a second paid feed.

WHY IT EXISTS. CFBD's games/media endpoint is the preseason announcement
and little else. Measured on 2026-09-13, a fetch returned a file byte-
identical to the one from 2026-09-01 apart from a single radio row on a
game already played: weeks 1 to 3 were complete, weeks 4 to 13 held one
or two games each. Conferences announce the opening weeks up front and
assign the rest on a rolling 12-/6-day window, and CFBD is not recording
those picks. ESPN is, on the same Saturday: 69 of 71 games.

WHAT IT IS NOT is a replacement. CFBD stays the primary and wins wherever
it has anything, for two reasons. It sometimes has a window ESPN does not
(one game in weeks 3 to 5), and it is the source the rest of this file's
provenance is written against. This fills blanks; it never overrides.

An undocumented endpoint, which is a real cost and worth saying out loud.
The shape can change without notice and nothing here would know until a
page went quiet. Every failure path therefore ends in "keep what CFBD
said" rather than an exception, because a missing broadcast is the least
load-bearing thing on a slate card.
"""
import datetime
import json
import subprocess
import zoneinfo

API = ("https://site.api.espn.com/apis/site/v2/sports/football/"
       "college-football")
SOURCE = "espn.com"

# FBS. Without it the scoreboard answers with every division and the
# response triples for rows that can never join to a Big 12 schedule.
GROUPS = 80

# ESPN's own words for the thing, mapped to CFBD's. A Saturday returns
# only these two: 43 TV and 27 Streaming.
TYPES = {"TV": "tv", "Streaming": "web"}

# ESPN files a game under its EASTERN date, not its UTC one. Northern
# Illinois at Arizona kicks 2026-09-20T02:30Z and sits on the 20260919
# scoreboard, because that is 22:30 the previous evening in New York.
# Querying by UTC date would miss every late West Coast kickoff, which is
# exactly the set most likely to be a streaming-only assignment.
ET = zoneinfo.ZoneInfo("America/New_York")

# How far ahead to bother asking. Networks pick on a 12-/6-day window, so
# beyond that there is nothing to learn and each date is another request.
# Fourteen leaves a day of slack either side of the twelve.
HORIZON_DAYS = 14


def _get(url):
    """One GET, or None. Never raises: see the module docstring."""
    r = subprocess.run(["curl", "-sS", "-m", "30", url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def scoreboard(yyyymmdd):
    """{game_id: [{type, outlet}, ...]} for one Eastern date."""
    blob = _get(f"{API}/scoreboard?dates={yyyymmdd}&groups={GROUPS}&limit=200")
    out = {}
    for e in (blob or {}).get("events") or []:
        rows = []
        comps = e.get("competitions") or [{}]
        # geoBroadcasts rather than broadcasts, which carries the same
        # outlets but no type: "CBSSN" and "ESPN+" arrive there as bare
        # names, and the difference between a channel and a stream is the
        # one thing the slate card actually branches on.
        for b in comps[0].get("geoBroadcasts") or []:
            kind = TYPES.get((b.get("type") or {}).get("shortName"))
            outlet = ((b.get("media") or {}).get("shortName") or "").strip()
            if not kind or not outlet:
                continue
            # `via` is provenance, and it is load-bearing rather than
            # decorative. The media file otherwise records only type and
            # outlet, so nothing in the committed data says which source
            # supplied a row, and the only proof the fallback ever fired
            # was a line in a build log. GitHub serves this repo's Actions
            # run list unauthenticated but refuses the logs themselves
            # (403), so that proof needed a credential to read. A key in
            # the file needs none, and it answers the question for every
            # game, forever, rather than for the one run still in the log
            # retention window.
            #
            # Inert on the page: broadcast() reads type and outlet and
            # ignores the rest. CFBD rows carry no `via` and stay as they
            # are, so the absence of the key means CFBD, which is the
            # right default for every row written before this existed.
            row = {"type": kind, "outlet": outlet, "via": SOURCE}
            if row not in rows:
                rows.append(row)
        if rows:
            out[str(e.get("id"))] = rows
    return out


def teams():
    """{school: abbreviation} for every team ESPN lists, or {} on any failure.

    One request, no key, 762 teams. The join is the school name in ESPN's
    `location` field, which is the name without the mascot and is what CFBD
    calls `school`: checked against the 2026 schedule, all 62 teams matched,
    and against data/teams.json all 16 Big 12 abbreviations agree with CFBD's
    exactly. So this extends that file rather than arguing with it.

    ABBREVIATION AND NOT shortDisplayName, which is the field that looks like
    the answer. ESPN's short forms collapse the -ern adjectives onto the
    cardinal letters — "Northern Illinois" becomes "N Illinois", "Georgia
    Southern" becomes "GA Southern" — and Northern Illinois is not North
    Illinois. Others drop the half that distinguishes them: Bethune-Cookman
    to "Bethune", Coastal Carolina to "Coastal", Long Island University to
    "Long Island". A label that names the wrong team is worse than a long one,
    so the only field taken is the code, which is curated and does not pretend
    to be a name.

    NEARLY unique, and the caller checks. ESPN lists satellite campuses beside
    the programs they belong to, and "Ohio State" and "Ohio State Newark" both
    answer OSU. Neither plays FBS so nothing joins to the wrong one today, but
    that is a fact about this season's list and not a property of the feed, so
    fetch_abbr names any code two schools share rather than trusting it.
    """
    blob = _get(f"{API}/teams?limit=1000")
    out = {}
    for sport in (blob or {}).get("sports") or []:
        for league in sport.get("leagues") or []:
            for row in league.get("teams") or []:
                t = row.get("team") or {}
                school, abbr = t.get("location"), t.get("abbreviation")
                if school and abbr:
                    out[school] = abbr
    return out


def _dates_needed(games, have, now):
    """The Eastern dates holding a game that would render with no window.

    Only games that have not kicked off and are inside the horizon. A
    finished game's broadcast is history nobody is waiting on, and asking
    about November in September buys an empty answer for a whole request.
    """
    horizon = now + datetime.timedelta(days=HORIZON_DAYS)
    dates = {}
    for g in games:
        gid = str(g.get("id"))
        rows = have.get(gid) or []
        # The same test broadcast() makes when it decides to draw nothing.
        # Radio does not count: CFBD started returning a row for it, and a
        # game carrying radio alone still shows an empty slot.
        if any(r.get("type") in ("tv", "web") for r in rows):
            continue
        try:
            kick = datetime.datetime.fromisoformat(
                g["start"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        if not (now < kick <= horizon):
            continue
        dates.setdefault(f"{kick.astimezone(ET):%Y%m%d}", []).append(gid)
    return dates


def fill(games, have, now=None):
    """Broadcast rows for games CFBD left blank: ({game_id: rows}, note).

    One request per date rather than per game, so a Saturday costs one
    call however many Big 12 games are on it. Returns only games that had
    nothing, so a caller can merge without deciding precedence.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    dates = _dates_needed(games, have, now)
    if not dates:
        return {}, "no game inside the window is missing a window"
    found, asked = {}, 0
    for day, gids in sorted(dates.items()):
        asked += 1
        board = scoreboard(day)
        if board is None:
            continue
        for gid in gids:
            rows = board.get(gid)
            if rows:
                found[gid] = rows
    return found, (f"{asked} date(s) asked, {len(found)} of "
                   f"{sum(len(v) for v in dates.values())} blanks filled")

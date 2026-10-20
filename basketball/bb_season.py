"""The season's games, lines and broadcasts, kept on disk and refreshed only
when they could have changed.

CBBD shares a monthly quota with the football section, so a call has to be
worth something. The rules are football's, from tiebreaker/build.py's
pending_results:

  games      when a game should have finished and has no final on file, and
             otherwise once a day, which is what catches a new tip time, a
             postponement or a schedule change between games
  lines      once a day
  broadcasts once a day

A whole season is one call each: /games with conference=Big 12 returns every
game a Big 12 team plays, and /lines and /games/media the same.

WHAT COUNTS. CBBD flags conference games, but its conference tournament games
are flagged too. The regular season is conferenceGame with gameType "STD";
the Big 12 tournament is gameType "TRNMNT" with the tournament named in
gameNotes. Other neutral-site postseason games between two Big 12 teams (the
College Basketball Crown, for one) are neither.
"""
import datetime
import json
import os

import bb_fetch as fetch

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CONF = "Big 12"

DAILY = datetime.timedelta(hours=20)
# A basketball game is about two hours. A final is due an hour after that,
# and a game with no final four days on is a cancellation, not a late score.
SETTLES_AFTER = datetime.timedelta(hours=3)
GIVE_UP_AFTER = datetime.timedelta(days=4)


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def current_season(today=None):
    """CBBD names a season by the year it ends: 2026-27 is 2027. A season
    starts in November, so from August the next one is the live one."""
    today = today or now_utc().date()
    return today.year + 1 if today.month >= 8 else today.year


def label(season):
    return f"{season - 1}–{str(season)[2:]}"


def _parse(ts):
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def is_tournament(g):
    return (g.get("gameType") == "TRNMNT"
            and "big 12" in (g.get("gameNotes") or "").lower())


def normalize(raw, teams):
    """CBBD's games as the site's shape: one row per game a Big 12 team
    plays, oldest first, with what the tiebreaker needs and what a page shows.
    """
    out = []
    for g in raw:
        home, away = g.get("homeTeam"), g.get("awayTeam")
        if home not in teams and away not in teams:
            continue
        both = home in teams and away in teams
        final = g.get("status") == "final" and g.get("homePoints") is not None
        out.append({
            "id": g["id"],
            "start": g.get("startDate"),
            "tbd": bool(g.get("startTimeTbd")),
            "home": home, "away": away,
            "hp": g.get("homePoints") if final else None,
            "ap": g.get("awayPoints") if final else None,
            "final": final,
            "status": g.get("status"),
            "neutral": bool(g.get("neutralSite")),
            # The regular-season conference game, the only kind the
            # standings and the tiebreaker read.
            "conf": bool(both and g.get("conferenceGame")
                         and g.get("gameType") == "STD"),
            "tourney": bool(both and is_tournament(g)),
            "notes": g.get("gameNotes"),
            "season_type": g.get("seasonType"),
            "venue": g.get("venue"), "venue_id": g.get("venueId"),
            "city": g.get("city"), "state": g.get("state"),
            "attendance": g.get("attendance") or None,
        })
    out.sort(key=lambda g: (g["start"] or "", g["id"]))
    return out


def pending(games, now=None):
    """Games that should have a final by now and do not."""
    now = now or now_utc()
    due = []
    for g in games:
        if g["final"] or g.get("status") in ("canceled", "cancelled", "postponed"):
            continue
        t = _parse(g["start"])
        if t and t + SETTLES_AFTER <= now <= t + GIVE_UP_AFTER:
            due.append(g)
    return due


def _meta_path(season):
    return os.path.join(DATA, f"meta_{season}.json")


def _meta(season):
    try:
        return json.load(open(_meta_path(season)))
    except (OSError, ValueError):
        return {}


def _stamp(season, what):
    m = _meta(season)
    m[what] = now_utc().replace(microsecond=0).isoformat()
    with open(_meta_path(season), "w") as f:
        json.dump(m, f, indent=1, sort_keys=True)


def _older_than(season, what, age):
    t = _parse(_meta(season).get(what) or "")
    return t is None or now_utc() - t >= age


def _games_path(season):
    return os.path.join(DATA, f"games_{season}.json")


def teams_for(season):
    """The conference's members that season, from the kept /teams response."""
    raw = fetch.get("teams", {"conference": CONF, "season": season},
                    fresh=not os.path.exists(fetch.raw_path(
                        "teams", {"conference": CONF, "season": season})))
    return {t["school"]: t for t in raw}


def load(season, refresh=False, log=print):
    """The season's games, lines and broadcasts.

    With `refresh`, each of the three is refetched only when its rule above
    says so; without it, nothing is asked and the kept copies are used. A
    failed fetch is never fatal: what is on disk is what the site shows,
    and the log says so."""
    teams = teams_for(season)
    params = {"conference": CONF, "season": season}
    path = _games_path(season)
    have = json.load(open(path)) if os.path.exists(path) else None

    if refresh:
        due = pending(have) if have is not None else []
        if have is None or due or _older_than(season, "games", DAILY):
            why = (f"{len(due)} game(s) due a final" if due else
                   "first fetch" if have is None else "daily schedule check")
            try:
                raw = fetch.get("games", params, fresh=True)
                have = normalize(raw, teams)
                with open(path, "w") as f:
                    json.dump(have, f, indent=1)
                _stamp(season, "games")
                log(f"basketball {season}: games fetched ({why}), "
                    f"{sum(g['final'] for g in have)} of {len(have)} final")
            except Exception as e:
                log(f"WARNING: basketball {season} games fetch failed ({e}); "
                    f"using what is on disk")
        else:
            log(f"basketball {season}: no game due a final, no call made")
        for what, endpoint in (("lines", "lines"), ("media", "games/media")):
            if _older_than(season, what, DAILY):
                try:
                    fetch.get(endpoint, params, fresh=True)
                    _stamp(season, what)
                    log(f"basketball {season}: {what} fetched")
                except Exception as e:
                    log(f"WARNING: basketball {season} {what} fetch failed "
                        f"({e}); using what is on disk")

    if have is None:
        raw = fetch.get("games", params)
        have = normalize(raw, teams)
        with open(path, "w") as f:
            json.dump(have, f, indent=1)

    def kept(endpoint):
        p = fetch.raw_path(endpoint, params)
        return json.load(open(p)) if os.path.exists(p) else []

    lines = {}
    for row in kept("lines"):
        ls = row.get("lines") or []
        # One line per game: the provider that posts most often wins ties,
        # in a fixed order so a rebuild never flips between two books.
        pref = ["ESPN BET", "DraftKings", "Bovada", "William Hill (New Jersey)"]
        ls = sorted(ls, key=lambda l: (pref.index(l["provider"])
                                       if l.get("provider") in pref else 99,
                                       l.get("provider") or ""))
        if ls:
            lines[row["gameId"]] = ls[0]
    media = {}
    for row in kept("games/media"):
        # Television first; a streaming-only game shows its stream.
        bs = row.get("broadcasts") or []
        tv = [b["broadcastName"] for b in bs
              if b.get("broadcastName") and (b.get("broadcastType") or "").upper() == "TV"]
        web = [b["broadcastName"] for b in bs
               if b.get("broadcastName") and b["broadcastName"] not in tv]
        if tv or web:
            media[row["gameId"]] = tv or web
    return {"season": season, "teams": teams, "games": have,
            "lines": lines, "media": media}

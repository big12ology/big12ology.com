"""The women's season, from ESPN: CBBD has no women's data.

Three ESPN reads, all keyless and outside every quota, all kept on disk
trimmed (bb_espn.trim says why trimmed):

  the season    each Big 12 team's schedule, /teams/<id>/schedule, sixteen
                requests, once a day: tip times, venues, TV, crowds and
                results, the role CBBD's /games plays for the men
  fast finals   the Big 12 scoreboard for a date with a game waiting on a
                result, bb_espn.py exactly as the men's section uses it
  every game    the all-Division-I scoreboard, one request per date, for the
                rating fit (bb_ratings.py): about 150 to backfill a season,
                then one or two a day

The games come out in the men's shape (bb_season.normalize), so nothing
downstream knows which sport it is building. ESPN's schedule endpoint does
not carry the conference flag its scoreboard has, so a conference game is
read the way the schedule is built: two Big 12 teams, the regular season,
an ordinary game (not an in-season tournament) on one of their floors. That
gives every team its eighteen.

ESPN's ids for a school are the same in every sport, and the sixteen are
found from one schedule: Kansas's, whose Big 12 opponents are the other
fifteen.
"""
import datetime
import json
import os
import re
import subprocess
import time

import bb_espn as espn
import bb_market as market
import bb_season as base
import bb_sport as sport

RAW = sport.RAW
# ESPN's name for the event changed: "Phillips 66 Big 12 Championship" in
# 2024-25, "Phillips 66 Big 12 Tournament" in 2025-26.
TOURNEY = re.compile(r"Big 12 (Tournament|Championship)")
SEED_TEAM = "2305"            # Kansas: any member's schedule names the rest
PACE = 0.4                    # seconds between requests in a burst


def _get(url):
    try:
        r = subprocess.run(["curl", "-sS", "-m", "45", "--fail", url],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except (OSError, ValueError):
        return None


# --- the season -----------------------------------------------------------

def sched_path(season, tid):
    return os.path.join(RAW, f"schedule-{season}", f"{tid}.json")


def ids_path(season):
    return os.path.join(sport.DATA, f"team_ids_{season}.json")


def fetch_schedule(season, tid):
    body = _get(f"{espn.SITE_API}/teams/{tid}/schedule?season={season}")
    if not body or not isinstance(body.get("events"), list):
        return None
    out = espn.trim(body)
    p = sched_path(season, tid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    # Stale beats gutted: an empty schedule where one was kept is a hiccup.
    if not out["events"] and os.path.exists(p):
        return None
    with open(p, "w") as f:
        json.dump(out, f, separators=(",", ":"), sort_keys=True)
    return out


def kept_schedule(season, tid):
    try:
        return json.load(open(sched_path(season, tid)))
    except (OSError, ValueError):
        return None


def team_ids(season, teams, log=print):
    """{school: ESPN id} for the season's members, from Kansas's schedule."""
    try:
        return json.load(open(ids_path(season)))
    except (OSError, ValueError):
        pass
    body = kept_schedule(season, SEED_TEAM) or fetch_schedule(season, SEED_TEAM)
    if not body:
        return {}
    ids = {"Kansas": SEED_TEAM}
    for e in body["events"]:
        for t in e["competitions"][0]["competitors"]:
            loc = t["team"].get("location")
            if loc in teams:
                ids[loc] = str(t["team"]["id"])
    if len(ids) == len(teams):
        os.makedirs(sport.DATA, exist_ok=True)
        with open(ids_path(season), "w") as f:
            json.dump(ids, f, indent=1, sort_keys=True)
    else:
        log(f"WARNING: women's {season}: found ESPN ids for {len(ids)} of "
            f"{len(teams)} teams: missing {sorted(set(teams) - set(ids))}")
    return ids


def _game(e, teams):
    """One ESPN event in bb_season.normalize's shape, or None."""
    c = e["competitions"][0]
    side = {t.get("homeAway"): t for t in c["competitors"]}
    if "home" not in side or "away" not in side:
        return None
    home = side["home"]["team"].get("location")
    away = side["away"]["team"].get("location")
    st = (c["status"]["type"].get("name") or "").upper()
    done = bool(c["status"]["type"].get("completed"))

    def pts(t):
        try:
            return int(float(t.get("score")))
        except (TypeError, ValueError):
            return None
    hp, ap = pts(side["home"]), pts(side["away"])
    final = done and hp is not None and ap is not None and hp != ap
    neutral = bool(c.get("neutralSite"))
    notes = " ".join(n.get("headline") or "" for n in c.get("notes") or [])
    both = home in teams and away in teams
    kind = (c.get("type") or {}).get("abbreviation")
    reg = (e.get("seasonType") or {}).get("abbreviation") in (None, "reg")
    flagged = c.get("conferenceCompetition")
    tourney = bool(both and TOURNEY.search(notes))
    conf = bool(both and reg and not tourney and (
        flagged if flagged is not None else (kind == "STD" and not neutral)))
    v = c.get("venue") or {}
    addr = v.get("address") or {}
    tv = [b.get("media", {}).get("shortName") or " / ".join(b.get("names") or [])
          for b in c.get("broadcasts") or []]
    return {
        "id": int(e["id"]),
        "start": (e.get("date") or "").replace("Z", ":00.000Z")
        if len(e.get("date") or "") == 17 else e.get("date"),
        "tbd": e.get("timeValid") is False,
        "home": home, "away": away,
        "hp": hp if final else None, "ap": ap if final else None,
        "final": final,
        "status": ("final" if final else "postponed" if "POSTPONED" in st
                   else "canceled" if "CANCEL" in st else "scheduled"),
        "neutral": neutral,
        "conf": conf,
        "tourney": tourney,
        "notes": notes or None,
        "season_type": "postseason" if not reg else "regular",
        "venue": v.get("fullName"), "venue_id": None,
        "city": addr.get("city"), "state": addr.get("state"),
        "attendance": c.get("attendance") or None,
        "espn": str(e["id"]), "hseed": None, "aseed": None,
        "tv": [x for x in tv if x],
    }


def normalize(season, ids, teams):
    seen = {}
    for tid in sorted(ids.values()):
        body = kept_schedule(season, tid)
        for e in (body or {}).get("events") or []:
            try:
                g = _game(e, teams)
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if g and g["id"] not in seen:
                seen[g["id"]] = g
    return sorted(seen.values(), key=lambda g: (g["start"] or "", g["id"]))


def has_season(season):
    return os.path.isdir(os.path.join(RAW, f"schedule-{season}"))


def load(season, refresh=False, log=print):
    """The women's season in the same shape bb_season.load returns."""
    # The same sixteen schools as the men's, whose team records (arena,
    # city, state) are already on disk: no call.
    teams = base.teams_for(season)
    ids = team_ids(season, teams, log=log)
    stamp = "schedules"
    if refresh:
        have = normalize(season, ids, teams)
        espn.refresh(have, log=log)
        if (not has_season(season)
                or base._older_than(season, stamp, base.DAILY)):
            got = 0
            for tid in sorted(ids.values()):
                if fetch_schedule(season, tid):
                    got += 1
                time.sleep(PACE)
            base._stamp(season, stamp)
            log(f"women's {season}: {got} of {len(ids)} team schedules read")
        market.refresh(season, normalize(season, ids, teams), log=log)
    games = normalize(season, ids, teams)
    espn.overlay(games, log=log)
    os.makedirs(sport.DATA, exist_ok=True)
    with open(os.path.join(sport.DATA, f"games_{season}.json"), "w") as f:
        json.dump(games, f, indent=1)
    lines = {}
    for gid, rec in market.load(season).items():
        try:
            lines[int(gid)] = rec
        except ValueError:
            continue
    media = {g["id"]: g["tv"] for g in games if g.get("tv")}
    return {"season": season, "teams": teams, "games": games,
            "lines": lines, "media": media}


# --- every Division I game, for the ratings ---------------------------------

D1_GROUP = 50


def d1_path(day):
    return os.path.join(RAW, "d1", f"{day}.json")


def _slim(body):
    """A day of Division I games cut to what the rating fit reads."""
    out = []
    for e in body.get("events") or []:
        try:
            c = e["competitions"][0]
            side = {t["homeAway"]: t for t in c["competitors"]}
            score = lambda t: (t.get("score") if not isinstance(t.get("score"), dict)
                               else t["score"].get("displayValue"))
            out.append({
                "id": e["id"],
                "home": side["home"]["team"].get("location"),
                "away": side["away"]["team"].get("location"),
                "hid": side["home"]["team"].get("id"),
                "aid": side["away"]["team"].get("id"),
                "hp": score(side["home"]), "ap": score(side["away"]),
                "neutral": bool(c.get("neutralSite")),
                "done": bool(c["status"]["type"].get("completed")),
            })
        except (KeyError, IndexError, TypeError):
            continue
    return out


def d1_games(season, refresh=False, today=None, log=print):
    """Every Division I game of `season` so far, as CBBD-shaped rows for
    bb_ratings.fit. A finished day is read once and kept; with `refresh`,
    the last two days are asked again, since a late final lands there."""
    today = today or datetime.date.today()
    day = datetime.date(season - 1, 11, 1)
    end = min(today - datetime.timedelta(days=1), datetime.date(season, 4, 15))
    rows, asked = [], 0
    while day <= end:
        ds = day.strftime("%Y%m%d")
        p = d1_path(ds)
        recent = (today - day).days <= 2
        if not os.path.exists(p) or (refresh and recent):
            body = espn._get(ds, group=D1_GROUP)
            asked += 1
            if body is not None:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    json.dump(_slim(body), f, separators=(",", ":"))
            time.sleep(PACE)
        try:
            for g in json.load(open(p)):
                if g["done"]:
                    rows.append({"id": g["id"], "homeTeam": g["home"],
                                 "awayTeam": g["away"], "status": "final",
                                 "homePoints": int(float(g["hp"])),
                                 "awayPoints": int(float(g["ap"])),
                                 "neutralSite": g["neutral"]})
        except (OSError, ValueError, TypeError):
            pass
        day += datetime.timedelta(days=1)
    if asked:
        log(f"women's {season}: {asked} Division I scoreboard(s) read")
    return rows

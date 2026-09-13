#!/usr/bin/env python3
"""The market from the-odds-api.com, joined onto this project's game ids.

Needs a key in .env or the environment:  ODDS_API_KEY=...

CFBD carries two distinct books. It reports three provider strings, but
"DraftKings" and "Draft Kings" are the same book arriving twice: the first
is CFBD's own scrape, the second is ESPN's feed passed through, and on
2026-09-13 they disagreed on 9 of the 31 games that had both. Averaging
them weighted DraftKings two thirds against Bovada one third and landed on
numbers no book was posting. This module exists to replace that average
with a real one. The same Saturday returned 12 books, 7 to 12 per game.

WHAT THIS IS NOT: a replacement for the CFBD lines call. Two things keep
that call alive, and both are in fetch.py rather than here.

  Openers. The current-odds endpoint reports what a book is posting now
  and has no concept of where it opened, so `spread_open` can only come
  from CFBD. The game pages print "opened -14.5" under the spread.

  The horizon. Books post college football about a week out, and the feed
  reflects that. A 2026-09-13 call for every NCAAF game through December
  returned 57 events, all of them between the 17th and the 20th, and
  passing commenceTimeTo did not widen it. CFBD, asked the same day, had
  lines on games in weeks 7, 8, 10, 11 and 13. So the look-ahead weeks
  keep their CFBD line and this module fills in the week actually being
  played, which is the week a slate freezes.

CONVENTION: spreads here are the home team's, negative when the home team
is favored, matching CFBD and what worker/src/ats.js expects. That is a
conversion, not a passthrough, and doing it wrong is the failure this
module is most shaped around. See `_assign`.
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
API = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_ncaaf"
SOURCE = "the-odds-api.com"

# Billing is markets x regions, so each of these doubles or triples the
# price of a call. Three markets on one region is 3 credits against 500 a
# month on the free tier.
#
# US ONLY, DELIBERATELY. Adding us2 brings five more books (Fliff, Hard
# Rock Bet, betPARX, Bally Bet, theScore Bet) for double the price, and
# measured against the 2026-09-13 snapshot they were not saying anything
# the seven us books were not: us2's mean sat +0.04 off the us mean with a
# standard deviation of 0.35 across 43 events, which is noise centered on
# zero rather than a correction. On the 13 Big 12 games the spread moved a
# mean of 0.085 points, and freeze_spread's round-to-the-half-point
# collapsed twelve of thirteen to the identical number. The five books
# bought a slightly steadier estimate that rounding then discarded.
#
# The credits went into cadence instead, which was the real gap: at six a
# call the market could only be sampled twice a day, so a noon kickoff
# froze on a 4:30am line.
REGIONS = "us"
MARKETS = "spreads,totals,h2h"
CREDITS_PER_CALL = len(MARKETS.split(",")) * len(REGIONS.split(","))

# How stale the newest capture has to be before another call is worth
# spending. This is the only thing standing between the schedule and the
# quota: pages.yml now asks for a lines refresh from the two daily crons,
# the Tuesday weekly, AND the hourly weekend builds, which is ~305 slots
# in September against a budget that affords ~165 calls.
#
# So the gate sets the real cadence and the crons only set the
# opportunities. Four hours means the market is sampled roughly six times
# a day where the schedule offers it, which on a Saturday is hourly
# builds from midnight to midnight and therefore a capture every four
# hours right through the slate. Simulated over September's slots with
# the observed drift (median 19 minutes late, 130 at the 90th percentile)
# and a 1-in-10 miss rate: ~93 calls, 279 of the free tier's 500.
#
# It also still does the job it was first written for, which was Tuesday:
# the weekly refresh at 07:00 and the daily at 08:30 would otherwise take
# the same snapshot 90 minutes apart.
#
# The tier resets monthly and the season spans four of them, so the figure
# that has to fit is the month, not the season.
MIN_AGE_HOURS = 4


def key():
    k = os.environ.get("ODDS_API_KEY")
    if not k:
        env = os.path.join(HERE, ".env")
        if os.path.exists(env):
            for line in open(env):
                if line.startswith("ODDS_API_KEY="):
                    k = line.split("=", 1)[1].strip()
                    break
    if not k:
        sys.exit("ODDS_API_KEY not set. Put it in .env or export it.")
    return k


def _get(path, params, k):
    """One GET. curl rather than urllib to match fetch.py, and because the
    key goes in a query parameter here: -sS keeps it out of stdout, and
    nothing logs the URL.

    Returns (payload, quota) where quota is whatever the response headers
    said about spend. The API reports remaining credits on every response,
    which is the only reliable ledger: unlike CFBD there is no fixed
    per-run cost to derive one from, because this call is gated on the age
    of the last capture rather than made unconditionally.
    """
    qs = "&".join(f"{a}={b}" for a, b in {**params, "apiKey": k}.items())
    r = subprocess.run(
        ["curl", "-sS", "-m", "60", "-D", "-", "-o", "-",
         f"{API}/{path}?{qs}"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed on {path}: {r.stderr[:150]}")
    # Headers and body arrive on one stream, split by the first blank line.
    # Normalized first because curl prints HTTP/2 headers with bare \n and
    # HTTP/1.1 with \r\n: splitting on \r\n\r\n alone silently swallowed the
    # whole response as headers, left an empty body, and raised "non-JSON"
    # on a request that had already been billed.
    head, _, body = r.stdout.replace("\r\n", "\n").partition("\n\n")
    quota = {}
    for line in head.splitlines():
        a, _, b = line.partition(":")
        if a.strip().lower() in ("x-requests-remaining", "x-requests-used",
                                 "x-requests-last"):
            quota[a.strip().lower()] = b.strip()
    try:
        data = json.loads(body)
    except ValueError:
        raise RuntimeError(f"{SOURCE} returned non-JSON on {path}: "
                           f"{body[:150]}")
    # Auth and quota failures come back as an object, not the expected
    # list. Say so here rather than letting a caller iterate an error dict.
    if isinstance(data, dict):
        raise RuntimeError(f"{SOURCE} refused {path}: "
                           f"{data.get('message', data)}")
    return data, quota


def _ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _is(name, school):
    """Does this feed team name denote this school?

    The feed says "Texas Tech Red Raiders" where the schedule says "Texas
    Tech", so the test is a prefix on a word boundary. On its own that is
    not safe, because "Utah State Aggies" also starts with "Utah " and the
    Big 12 is full of pairs that collide this way: Utah/Utah State,
    Arizona/Arizona State, Kansas/Kansas State, Colorado/Colorado State,
    Texas/Texas Tech, Oklahoma/Oklahoma State. Callers must resolve both
    teams together rather than trusting the first hit. See `_assign`.
    """
    return name == school or name.startswith(school + " ")


def _assign(names, home, away):
    """Map the feed's two team names onto (home, away), or None.

    Both orientations are tried, and this is the point of the function:
    THE FEED'S OWN home_team IS NOT TRUSTED. On neutral-site games the two
    sources disagree about which side is nominally home, because there is
    no host to be right about. Both of the neutral games on 2026-09-19
    were flipped relative to the schedule:

      schedule  Arizona State @ Kansas          feed  Kansas @ Arizona State
      schedule  West Virginia @ Virginia        feed  Virginia @ West Virginia

    Taking the feed's ordering would have inverted the sign on both, which
    is how a slate ends up naming the wrong favorite while looking
    perfectly well formed. So the pairing is matched unordered and the
    spread is read back off the named outcome for the SCHEDULE's home
    team. The feed's ordering is never used for anything.

    The longer school name wins a tie so that "Utah State" is preferred
    over "Utah" for "Utah State Aggies", and the result has to be a
    bijection: if both feed names claim the same school, or either claims
    neither, there is no match and the game is skipped rather than guessed.
    """
    best = None
    for a, b in ((0, 1), (1, 0)):
        if _is(names[a], home) and _is(names[b], away):
            score = len(home) + len(away)
            if best is None or score > best[0]:
                best = (score, {home: names[a], away: names[b]})
    return best[1] if best else None


def _outcome(market, want):
    for o in market.get("outcomes") or []:
        if o.get("name") == want:
            return o
    return None


def _books(event, home_name, away_name):
    """Every book's numbers for one game, in this project's shape.

    Only what CFBD also carried, so load_lines and the book_* helpers in
    build.py keep working unchanged. The feed also gives a price alongside
    each spread and total (-112/-108 rather than an assumed -110) and a
    per-book last_update; neither is kept, because nothing displays them
    and a wider record would have to be migrated the first time it changed.
    """
    out = []
    for b in event.get("bookmakers") or []:
        mk = {m.get("key"): m for m in b.get("markets") or []}
        rec = {"provider": b.get("title")}
        if "spreads" in mk:
            o = _outcome(mk["spreads"], home_name)
            if o and o.get("point") is not None:
                rec["spread"] = o["point"]
        if "totals" in mk:
            # Over and Under carry the same number, so either does; Over
            # is named rather than taken positionally because the feed
            # does not promise an order.
            o = _outcome(mk["totals"], "Over")
            if o and o.get("point") is not None:
                rec["over_under"] = o["point"]
        if "h2h" in mk:
            h, a = _outcome(mk["h2h"], home_name), _outcome(mk["h2h"],
                                                            away_name)
            if h and h.get("price") is not None:
                rec["home_ml"] = h["price"]
            if a and a.get("price") is not None:
                rec["away_ml"] = a["price"]
        if len(rec) > 1:
            out.append(rec)
    return out


def _avg(vals, ndigits=1):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), ndigits) if vals else None


def _avg_ml(vals):
    """Moneylines average to a whole number, because that is the only kind
    a book posts. A spread of -9.2 reads as the middle of a market; a price
    of -386.1 reads as a typo.

    Still an arithmetic mean of American odds, which is not the same as the
    mean of what they imply — the scale is a ratio either side of even
    money, so it is not linear in probability and -110 and +110 do not
    average to zero. That was true of the two-book average this replaces
    and is left alone here rather than changed in passing.
    """
    v = _avg(vals, 0)
    return None if v is None else int(v)


def newest_capture(lines, source=SOURCE):
    """The most recent `as_of` on any record `source` wrote, or None.

    Read from the lines file rather than a sidecar because the file is now
    merged rather than overwritten, so it holds captures of several ages at
    once and one file-level stamp would be a claim about the newest record
    printed against the oldest.

    Takes a source because both halves of the merge are now rate limited
    and they are limited to different rates: the market moves hourly and
    is cheap, CFBD supplies openers that never move again and is metered
    against a much harder cap. Same question, two clocks.
    """
    stamps = [v.get("as_of") for v in (lines or {}).values()
              if isinstance(v, dict) and v.get("source") == source
              and v.get("as_of")]
    if not stamps:
        return None
    try:
        return max(_ts(s) for s in stamps)
    except ValueError:
        return None


def due(lines, now=None, min_age_hours=MIN_AGE_HOURS, source=SOURCE):
    """Is another call worth spending yet? See MIN_AGE_HOURS.

    A file with nothing from `source` in it is always due, which is what
    makes a cold start and a first season work.
    """
    last = newest_capture(lines, source)
    if last is None:
        return True
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - last).total_seconds() >= min_age_hours * 3600


def _digest(event):
    """What a capture says, ignoring when it said it.

    Every book carries a last_update that moves on every call whether or
    not a number did, so comparing whole events would archive an identical
    snapshot twice a day all season. This reduces one event to the numbers,
    which is what a re-parse would ever want to read back.

    PLUS EACH BOOK'S EVENT LINK, and that is not a detail. The numbers-only
    digest silently dropped the links on the first capture that carried
    them: five of thirteen games had not moved since the previous call, so
    five were judged identical and never stored. A field the dedupe cannot
    see is a field that only lands by luck, on whichever capture happens to
    follow a price change, and a game whose line never moves again would
    never have stored one at all.

    The OUTCOME-level links stay out on purpose. Those are addToBetslip
    URLs carrying marketId and selectionId, which the book reissues as its
    market changes, so digesting them would put an unstable value in the
    comparison and archive on churn again. They still ride along inside
    whatever captures do get stored; they are just not what decides.
    """
    rows = []
    for b in event.get("bookmakers") or []:
        rows.append((b.get("title"), "_link", b.get("link"), None, None))
        for m in b.get("markets") or []:
            for o in m.get("outcomes") or []:
                rows.append((b.get("title"), m.get("key"), o.get("name"),
                             o.get("point"), o.get("price")))
    # None sorts against str on the link rows when a book omits one, so the
    # key is stringified rather than compared raw.
    return sorted(rows, key=repr)


ARCHIVE = os.path.join(DATA, "odds")


def archive(year, captured, as_of):
    """Append raw feed events to data/odds/<year>-week-NN.json, deduped.

    Committed, for the same reason data/ is committed at all: pull once,
    save, keep. A join bug or a shape change should cost a re-parse of
    what we already hold, not another call, and the free tier is 500
    credits a month against a season that runs five of them.

    Kept as the feed's own event, not our derived record, because the
    derived record is the thing most likely to be wrong. Team names,
    kickoff and every book's outcomes go in verbatim, so `fetch` can be
    re-run against these files offline and produce a different answer if
    the join changes. Narrowed to games that matched the schedule, which
    is selection rather than parsing: the other 44 events in a Saturday
    response are the rest of FBS and will never join to anything.

    APPEND ONLY, and deduplicated on the numbers rather than the payload,
    so a file grows when the market moves and not when the clock does. A
    capture that repeats the last one costs nothing. That also makes this
    a record of line movement as a side effect: every distinct state a
    game's market passed through, in order, which is not otherwise
    recoverable at this tier.

    SHARDED BY WEEK, and that is about git rather than about lookup. One
    file per season would be rewritten in full by every one of roughly 150
    captures a season, and a whole-file rewrite is a new blob each time.
    Per week, a file stops changing for good once its games kick off, so
    the history carries each week's churn once instead of the season's
    churn compounding. It is also the shape the rest of the project
    already stores a week in (pickem/<year>/week-NN.json).

    Written compact rather than indented, unlike the small hand-read files
    beside it: this one is machine input, and indentation was doubling
    what every commit had to carry.
    """
    by_week = {}
    for gid, (g, event) in captured.items():
        by_week.setdefault(g.get("week"), {})[str(gid)] = event
    added = 0
    for week, events in by_week.items():
        p = os.path.join(ARCHIVE,
                         f"{year}-week-{week:02d}.json"
                         if isinstance(week, int) else f"{year}-week-xx.json")
        try:
            book = json.load(open(p)) if os.path.exists(p) else {}
        except (OSError, ValueError):
            # A corrupt shard must not take the fetch down with it, and
            # must not be silently replaced either: say so, skip it.
            print(f"  odds: {p} unreadable, not archiving this capture")
            continue
        hit = 0
        for gid, event in events.items():
            hist = book.setdefault(gid, [])
            if hist and _digest(hist[-1].get("event", {})) == _digest(event):
                continue
            hist.append({"at": as_of, "event": event})
            hit += 1
        if hit:
            os.makedirs(ARCHIVE, exist_ok=True)
            with open(p, "w") as f:
                json.dump(book, f, separators=(",", ":"), sort_keys=True)
            added += hit
    return added


def fetch(year, games, existing=None, force=False):
    """{game_id: record} for every scheduled game the market has reached.

    `games` is data/games_<year>.json as loaded: the Big 12 schedule, which
    is both the join target and the filter. The feed returns all of FBS and
    everything that does not match a game in here is dropped, so no
    conference test is needed.

    FORWARD ONLY. A game that has already kicked off is never written, so
    nothing this module does can alter a line that was in the file before
    its game started. The endpoint only returns upcoming events anyway, but
    that is the feed's promise rather than ours, and a frozen line is not
    the place to find out the difference: a late-running fetch that catches
    a game in progress would otherwise rewrite the number a published slate
    was built from.

    Returns ({}, quota) without spending a call when the newest capture is
    younger than MIN_AGE_HOURS, unless `force`.
    """
    if not force and not due(existing):
        return {}, {"skipped": "recent capture"}

    # includeLinks costs nothing: the credit formula is markets x regions
    # and this is neither. It adds each book's own event, market and betslip
    # URLs to the response, which go into the archive verbatim and are not
    # otherwise recoverable — the feed carries about seven days, so a link
    # not captured while a game is in the window cannot be asked for later.
    # Nothing reads them yet. They are here because they are free now and
    # would cost a re-fetch that this tier cannot serve.
    raw, quota = _get(f"sports/{SPORT}/odds/",
                      {"regions": REGIONS, "markets": MARKETS,
                       "oddsFormat": "american",
                       "includeLinks": "true"}, key())
    now = datetime.datetime.now(datetime.timezone.utc)
    as_of = now.replace(microsecond=0).isoformat()
    out, skipped, captured = {}, [], {}
    for e in raw:
        try:
            kick = _ts(e["commence_time"])
        except (KeyError, ValueError):
            continue
        names = [e.get("home_team"), e.get("away_team")]
        if not all(names):
            continue
        # Kickoff times drift between sources, and a TBD window can be off
        # by most of a day, so the time is a bucket rather than a key: it
        # narrows the candidates to one Saturday and the names decide.
        cands = [g for g in games
                 if abs((_ts(g["start"]) - kick).total_seconds()) <= 36 * 3600
                 and _assign(names, g["home"], g["away"])]
        if len(cands) != 1:
            # Zero is the normal case: most of FBS is not our schedule.
            # More than one would mean the same two teams twice inside 36
            # hours, which does not happen, and a guess there is worse
            # than a gap.
            if len(cands) > 1:
                skipped.append(f"{names[1]} at {names[0]}: ambiguous")
            continue
        g = cands[0]
        if _ts(g["start"]) <= now:
            continue                      # forward only; see the docstring
        amap = _assign(names, g["home"], g["away"])
        books = _books(e, amap[g["home"]], amap[g["away"]])
        if not books:
            continue
        rec = {
            "spread": _avg(b.get("spread") for b in books),
            "over_under": _avg(b.get("over_under") for b in books),
            "home_ml": _avg_ml(b.get("home_ml") for b in books),
            "away_ml": _avg_ml(b.get("away_ml") for b in books),
            "books": books,
            "source": SOURCE,
            "as_of": as_of,
        }
        if rec["spread"] is None and rec["over_under"] is None:
            continue
        out[str(g["id"])] = {k: v for k, v in rec.items()
                             if v not in (None, [])}
        captured[str(g["id"])] = (g, e)
    for s in skipped:
        print(f"  odds: skipped {s}")
    added = archive(year, captured, as_of)
    if added:
        print(f"  odds: archived {added} changed capture(s) -> "
              f"{os.path.relpath(ARCHIVE, HERE)}/")
    return out, quota

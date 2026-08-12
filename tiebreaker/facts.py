"""A hundred true things per section, generated rather than written.

The front page had four descriptions of four tools and nothing a reader could
learn by standing still. This produces the something: short factual lines the
hub rotates through, one per section, drawn out of the same committed data the
sections themselves are built from.

Generated and not hand-authored, for the reason every other derived thing here
is: a hand-written list of interesting facts is correct on the day it is
written and quietly wrong by the end of the next season, and nothing tells
you. These are recomputed on every build, so a fact that stops being true
stops being published.

Two hazards in the data, both of which produce facts that are arithmetically
correct and factually rubbish:

  2020 is a COVID season. The league averaged 7,435 a game against 45,000 in
  the years either side, because most of those stadiums were shut or capped by
  public health order rather than by demand. Every "smallest crowd" and "worst
  attended season" belongs to it, and every one of those is a fact about a
  pandemic dressed up as a fact about football. It is excluded from records
  and comparisons and given one line that says what it was.

  Kansas State's published capacity is not trustworthy as a denominator —
  attendance/index.html has said so at length since long before this file, the
  short version being that the athletic department's 50,000 has not moved
  through seven stadium projects and the same page claims a bigger crowd than
  that. So K-State gets facts about how many people came, never about what
  fraction of the stadium they filled.

Shape of the output, written to site/facts.json:

    {"season": 2026,
     "sections": {"tiebreaker": [{"t": "...", "on": "09-14"}, ...], ...}}

`on` is an optional MM-DD. The hub prefers a fact whose day is today and
otherwise rotates the rest, which is what makes "on this day" work without
anything on the server knowing what day it is.
"""

import collections
import hashlib
import csv
import datetime
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# The seasons each dataset actually covers. Stated rather than discovered, so
# that a half-fetched file produces a smaller answer instead of a wrong one.
TB_FIRST, TB_LAST = 2011, 2025
ATT_FIRST, ATT_LAST = 2012, 2025

# Not a normal season and not comparable to one. See the module docstring.
COVID = 2020

# Percent-of-capacity claims are not made about this team. See the docstring.
NO_PCT = {"Kansas State"}

# A crowd at or above the published capacity. Several of these schools sell
# standing room, so above 100% is ordinary rather than suspicious — which is
# exactly why the threshold is "full", not "a record".
FULL = 1.0


def _n(x):
    """A number, or None. The CSV carries blanks and the JSON carries nulls."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def _comma(v):
    return f"{int(round(v)):,}"


def _fact(text, on=None):
    f = {"t": " ".join(text.split())}
    if on:
        f["on"] = on
    return f


def _mmdd(iso):
    try:
        return datetime.date.fromisoformat(iso[:10]).strftime("%m-%d")
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- attendance

def _att_rows():
    p = os.path.join(REPO, "attendance", "data", "attendance.csv")
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a, cap = _n(r.get("attendance")), _n(r.get("capacity"))
            season = int(r["season"]) if r.get("season", "").isdigit() else None
            # A zero is a game with no figure, not a game nobody attended.
            if not a or season is None:
                continue
            out.append({
                "season": season, "team": r["team"], "date": r.get("date"),
                "opponent": r.get("opponent"), "att": a, "cap": cap,
                "pct": _n(r.get("pct_of_capacity")),
                "venue": r.get("venue"),
            })
    return out


def attendance_totals():
    """The three numbers the hub's attendance card prints.

    Here and not in build.py so that the card and the facts on the same page
    count the same thing. "A home game" means one this tracker has a crowd
    figure for — a row with a zero is a game whose figure never arrived, not a
    game nobody went to, and counting those inflated the card by 25.
    """
    rows = _att_rows()
    if not rows:
        return None
    return {
        "games": len(rows),
        "fans": int(sum(r["att"] for r in rows)),
        "seasons": len({r["season"] for r in rows}),
        "first": min(r["season"] for r in rows),
    }


def attendance_facts():
    rows = _att_rows()
    if not rows:
        return []
    out = []
    normal = [r for r in rows if r["season"] != COVID]
    by_team = collections.defaultdict(list)
    for r in normal:
        by_team[r["team"]].append(r)

    # --- one per team: how often the place is full -------------------------
    # Every team's fill rate first, so each one can be told how it compares.
    # "19 of 83" is not a fact until you know the league average is 46%.
    rate = {}
    for team, rs in sorted(by_team.items()):
        if team in NO_PCT:
            continue
        wp = [r for r in rs if r["pct"] is not None]
        if len(wp) >= 20:
            rate[team] = len([r for r in wp if r["pct"] >= FULL]) / len(wp)
    league = (sum(rate.values()) / len(rate)) if rate else 0

    for team, rs in sorted(by_team.items()):
        if team not in rate:
            continue
        with_pct = [r for r in rs if r["pct"] is not None]
        full = [r for r in with_pct if r["pct"] >= FULL]
        where = _rank(rate[team], list(rate.values()))
        pct = rate[team] * 100
        if len(full) == len(with_pct):
            out.append(_fact(
                f"{team} has filled its stadium for every one of its "
                f"{len(with_pct)} home games since {ATT_FIRST} — every "
                f"season, every opponent, and the only team in the league "
                f"that can say it."))
        elif full:
            color = f", {where}" if where else ""
            out.append(_fact(
                f"{team} has filled its stadium {len(full)} times in "
                f"{len(with_pct)} home games since {ATT_FIRST} — "
                f"{pct:.0f}%{color}, against a league average of "
                f"{league * 100:.0f}%."))
        else:
            best = max(with_pct, key=lambda r: r["pct"])
            out.append(_fact(
                f"{team} has not filled its stadium once since {ATT_FIRST}, "
                f"the only team in the league that has not. The closest it "
                f"came was {best['pct'] * 100:.0f}% against "
                f"{best['opponent']} in {best['season']}."))

    # --- one per team: the biggest crowd it has drawn ----------------------
    for team, rs in sorted(by_team.items()):
        best = max(rs, key=lambda r: r["att"])
        out.append(_fact(
            f"The biggest crowd {team} has drawn since {ATT_FIRST} is "
            f"{_comma(best['att'])}, against {best['opponent']} in "
            f"{best['season']}."))

    # --- one per team: what an ordinary Saturday looks like ----------------
    avgs = {t: sum(r["att"] for r in rs) / len(rs)
            for t, rs in by_team.items()}
    for team, rs in sorted(by_team.items()):
        where = _rank(avgs[team], list(avgs.values()))
        color = f" — {where}" if where else ""
        S = _subj(team, "avg", opener=True)
        out.append(_fact(
            f"{S['n']} average{S['v']} {_comma(avgs[team])} a home game "
            f"since {ATT_FIRST}, over {len(rs)} games{color}."))

    # --- one per season ----------------------------------------------------
    by_season = collections.defaultdict(list)
    for r in normal:
        by_season[r["season"]].append(r)
    for s_, rs in sorted(by_season.items()):
        avg = sum(r["att"] for r in rs) / len(rs)
        big = max(rs, key=lambda r: r["att"])
        # Who they were playing, not just where. "64,885 at Arizona State" is
        # a stadium capacity; "against Texas A&M" is why anyone turned up.
        when = ""
        try:
            d = datetime.date.fromisoformat(big["date"][:10])
            when = d.strftime(" on %-d %B")
        except (TypeError, ValueError):
            pass
        out.append(_fact(
            f"In {s_} the league averaged {_comma(avg)} a home game. The "
            f"biggest single crowd was {_comma(big['att'])}, "
            f"{big['team']} against {big['opponent']}{when}."))

    # --- league records ----------------------------------------------------
    if normal:
        top = max(normal, key=lambda r: r["att"])
        out.append(_fact(
            f"The biggest crowd in this tracker is {_comma(top['att'])} — "
            f"{top['team']} against {top['opponent']}, {top['season']}."))
        low = min(normal, key=lambda r: r["att"])
        out.append(_fact(
            f"The smallest crowd outside {COVID} is {_comma(low['att'])}: "
            f"{low['team']} against {low['opponent']} in {low['season']}."))
        avgs = {s: sum(r["att"] for r in rs) / len(rs)
                for s, rs in by_season.items()}
        best_s = max(avgs, key=lambda s: avgs[s])
        worst_s = min(avgs, key=lambda s: avgs[s])
        out.append(_fact(
            f"{best_s} was the best-attended season here at "
            f"{_comma(avgs[best_s])} a game; {worst_s} the thinnest at "
            f"{_comma(avgs[worst_s])}, leaving {COVID} out of it."))
        out.append(_fact(
            f"{len(rows):,} home games are in this tracker, back to "
            f"{ATT_FIRST} — {_comma(sum(r['att'] for r in rows))} people "
            f"through the gates."))

    # --- the pandemic, said plainly ----------------------------------------
    covid = [r for r in rows if r["season"] == COVID]
    if covid:
        avg = sum(r["att"] for r in covid) / len(covid)
        out.append(_fact(
            f"In {COVID} the league averaged {_comma(avg)} a home game. It is "
            f"left out of every comparison here: that is a number about a "
            f"pandemic, not about football."))

    # --- on this day -------------------------------------------------------
    # The best-attended game on each calendar date, so a reader who comes back
    # in October gets an October fact. Capped at one per date, biggest first,
    # because a date with fourteen games does not need fourteen entries.
    best_on = {}
    for r in normal:
        d = _mmdd(r["date"])
        if not d:
            continue
        if d not in best_on or r["att"] > best_on[d]["att"]:
            best_on[d] = r
    # The text does NOT say "on this day" — the label the page puts above it
    # does, and only ever on the matching date. A dated fact rendered on the
    # wrong day is simply a false sentence, so the two halves are kept apart:
    # `on` is the whole of the claim about when.
    for d, r in sorted(best_on.items()):
        out.append(_fact(
            f"In {r['season']}, {r['team']} drew {_comma(r['att'])} against "
            f"{r['opponent']}.", on=d))
    return out


# ---------------------------------------------------------------- rivalries

def _cap(text):
    """Capitalise a sentence opener without touching the rest.

    Rivalry names are stored the way they read mid-sentence — "the Holy War" —
    because that is how they appear in most of these facts. str.capitalize()
    would lowercase the War.
    """
    return text[:1].upper() + text[1:] if text else text


# Schools that were in the Big 12 and left. Nebraska is named rather than
# derived because it went in 2011, before the first season this repository
# holds; the rest fall out of the schedules on their own and are picked up
# below, so a future departure needs no edit here.
DEPARTED_SEED = {"Nebraska"}


def departed(games_by_year):
    """Former members: in a conference game once, not in the latest season."""
    by = {}
    for y in sorted(games_by_year):
        here = set()
        for g in games_by_year[y]:
            if g.get("conference_game") and not g.get("ccg"):
                here.add(g.get("home"))
                here.add(g.get("away"))
        if here:
            by[y] = here
    if not by:
        return set(DEPARTED_SEED)
    latest = by[max(by)]
    ever = set().union(*by.values())
    return (ever - latest) | DEPARTED_SEED


def load_rivalries(games_by_year=None):
    """The curated pairings, minus the ones played against people who left.

    Bedlam, the Border War, Colorado-Nebraska and the two Texas games are all
    real and all excluded. It is an editorial call and not a data one: a Big
    12 site printing the series record against the schools that walked out is
    not a fun fact for the people still here. Opponents who were never in the
    conference are untouched — Iowa and Pitt are just neighbors.

    Enforced here as well as absent from the file, so that adding one back to
    the JSON in good faith does not quietly put it on the front page.
    """
    p = os.path.join(HERE, "data", "rivalries.json")
    try:
        with open(p, encoding="utf-8") as f:
            rivalries = json.load(f).get("rivalries") or []
    except (OSError, ValueError):
        return []
    gone = departed(games_by_year or {})
    return [r for r in rivalries
            if not (set(r.get("teams") or []) & gone)]


def _series(games_by_year, a, b):
    """Every meeting between two teams in the committed schedules."""
    met = []
    for y in sorted(games_by_year):
        for g in games_by_year[y]:
            pair = {g.get("home"), g.get("away")}
            if pair == {a, b}:
                met.append((y, g))
    return met


def rivalry_facts(games_by_year, year, first_year):
    """What the named games have actually done, in the years on record.

    The file behind this holds no numbers at all — see data/rivalries.json —
    so everything here is counted from the schedules and states the window it
    counted over. That matters more here than anywhere else in this module:
    these are hundred-year series, and "leads 9-6" would be read as all-time
    by anybody who did not know the data starts in 2011.
    """
    out = []
    for r in load_rivalries(games_by_year):
        a, b = r["teams"]
        name = r.get("name") or f"{a}–{b}"
        met = _series(games_by_year, a, b)
        played = [(y, g) for y, g in met
                  if g.get("home_points") is not None]
        if not played:
            continue

        wins = collections.Counter()
        for y, g in played:
            hp, ap = g["home_points"], g["away_points"]
            if hp == ap:
                continue
            wins[g["home"] if hp > ap else g["away"]] += 1
        wa, wb = wins[a], wins[b]
        lead = (f"{a} leads {wa}–{wb}" if wa > wb
                else f"{b} leads {wb}–{wa}" if wb > wa
                else f"they are level at {wa}–{wa}")
        out.append(_fact(
            f"{a} and {b} have met {_times(len(played))} since {first_year} "
            f"in {name}. In those games {lead}."))

        # The current run, which is the thing a rivalry is actually argued
        # about. Counted from the most recent meeting backwards.
        streak_team, streak = None, 0
        for y, g in reversed(played):
            hp, ap = g["home_points"], g["away_points"]
            if hp == ap:
                break
            w = g["home"] if hp > ap else g["away"]
            if streak_team is None:
                streak_team, streak = w, 1
            elif w == streak_team:
                streak += 1
            else:
                break
        if streak >= 2:
            last = played[-1][0]
            out.append(_fact(
                f"{streak_team} has won the last {streak} meetings in "
                f"{name}, most recently in {last}."))

        if r.get("trophy"):
            holder = streak_team or (a if wa > wb else b)
            last = played[-1][0]
            # "Has it" is only true while the series is live. Colorado and
            # Nebraska last played in 2019; claiming somebody currently holds
            # a trophy contested six years ago is the sort of sentence that
            # is right once and wrong forever after.
            stale = TB_LAST - last > 1
            out.append(_fact(
                f"{a} and {b} play for {r['trophy']}. "
                + (f"{holder} won it last, in {last}." if stale
                   else f"{holder} holds it, on the {last} result.")))

        biggest = max(played, key=lambda t: abs(
            t[1]["home_points"] - t[1]["away_points"]))
        y, g = biggest
        m = abs(g["home_points"] - g["away_points"])
        if m >= 14:
            w = g["home"] if g["home_points"] > g["away_points"] else g["away"]
            l = g["away"] if w == g["home"] else g["home"]
            out.append(_fact(
                f"The most one-sided meeting in {name} since {first_year} was "
                f"{y}: {w} by {m} over {l}."))
    return out


def rivalry_schedule_facts(games_by_year, year):
    """Which named games are on this year's card, and which are not.

    The second half is the interesting one and the reason this lives with the
    schedule rather than with the history. An unbalanced nine-game conference
    schedule means a rivalry can simply not be played, and realignment means
    several of these are not Big 12 games at all any more — neither of which
    is visible on a page that only lists the games that ARE happening.
    """
    out = []
    games = games_by_year.get(year) or []
    on, off = [], []
    for r in load_rivalries(games_by_year):
        a, b = r["teams"]
        hit = next((g for g in games
                    if {g.get("home"), g.get("away")} == {a, b}), None)
        (on if hit else off).append((r, hit))

    for r, g in on:
        where = (f"at {g.get('venue')}" if g.get("neutral_site")
                 and g.get("venue") else
                 f"at {g['home']}")
        out.append(_fact(
            f"{_cap(r.get('name') or '–'.join(r['teams']))} is on the "
            f"{year} card, {where}."))

    missing = [r for r, _ in off if r.get("conference")]
    for r in missing:
        a, b = r["teams"]
        out.append(_fact(
            f"{_cap(r.get('name') or a + '–' + b)} is not played in {year}: "
            f"nine conference games out of fifteen possible opponents means "
            f"the rotation can leave a rivalry out."))

    gone = [r for r, _ in off if not r.get("conference")]
    if gone:
        names = [x.get("name") or "–".join(x["teams"]) for x in gone[:4]]
        out.append(_fact(
            f"Realignment left several of these outside the league: "
            f"{_list(names)} are not Big 12 games, and are not on the {year} "
            f"conference schedule at all."))
    return out


# ---------------------------------------------------------------- schedule

def schedule_facts(year, games, teams, history_years, rotation_mod):
    out = []
    names = sorted(teams)
    if not names:
        return out

    conf = [g for g in games if g.get("conference_game") and not g.get("ccg")]
    out.append(_fact(
        "Sixteen teams, fifteen possible conference opponents, nine games. "
        "Nobody plays everybody, so two teams finishing on the same record "
        "did not attempt the same schedule."))
    out.append(_fact(
        f"The {year} conference season is {len(conf)} games out of the "
        f"{len(names) * 15 // 2} pairings the league could make. The rest "
        f"simply do not happen this year."))

    # --- home and away ------------------------------------------------------
    # Nine games does not divide in two, so somebody gets five at home and
    # somebody gets four, every single year.
    home = collections.Counter(g["home"] for g in conf)
    away = collections.Counter(g["away"] for g in conf)
    fives = sorted(t for t in names if home.get(t, 0) == 5)
    fours = sorted(t for t in names if home.get(t, 0) == 4)
    if fives and fours:
        out.append(_fact(
            f"Nine conference games will not split evenly, so in {year} "
            f"{len(fives)} teams get five home games and the other "
            f"{len(fours)} get four."))
    for t in names:
        h, a = home.get(t, 0), away.get(t, 0)
        if not h + a:
            continue
        S = _subj(t, "home", opener=True)
        # Four or five is the whole story, and it is invisible without the
        # other fifteen: an odd number of games has to fall somewhere, and
        # which side of it you land on is the closest thing the schedule has
        # to luck.
        if h > a and fours:
            color = (f" — the extra home date, in a season {len(fours)} "
                      f"teams finish a game short")
        elif a > h and fives:
            color = (f" — the short straw, while {len(fives)} teams get five")
        else:
            color = ""
        out.append(_fact(
            f"{S['n']} play{S['v']} {h} of {S['its']} {h + a} {year} "
            f"conference games at home{color}."))

    # --- the games that are not in Texas, or America ------------------------
    for g in games:
        if not g.get("neutral_site"):
            continue
        if g["home"] not in teams and g["away"] not in teams:
            continue
        venue = g.get("venue") or "a neutral site"
        out.append(_fact(
            f"{g['away']} and {g['home']} meet at {venue} in {year} — "
            f"neither team's home field."))

    # --- one per team: the three they chose -------------------------------
    nonconf = collections.defaultdict(list)
    for g in sorted(games, key=lambda x: (x.get("start") or "", x["id"])):
        if g.get("conference_game") or g.get("ccg"):
            continue
        if g["home"] in teams:
            nonconf[g["home"]].append(g["away"])
        if g["away"] in teams:
            nonconf[g["away"]].append(g["home"])
    for t, opps in sorted(nonconf.items()):
        if opps:
            S = _subj(t, "nonconf", opener=True)
            out.append(_fact(
                f"{S['n']} play {_list(opps)} out of conference in "
                f"{year}."))

    # --- who the league plays when it is not playing itself -----------------
    outside = collections.Counter()
    for g in games:
        if g.get("conference_game") or g.get("ccg"):
            continue
        if g["home"] in teams:
            outside[g.get("away_conf") or "an FCS conference"] += 1
        if g["away"] in teams:
            outside[g.get("home_conf") or "an FCS conference"] += 1
    for cname, n in outside.most_common(10):
        out.append(_fact(
            f"The Big 12 plays {n} non-conference game"
            f"{'' if n == 1 else 's'} against the {cname} in {year}."))

    try:
        rows, stats = rotation_mod.report(games, names, list(history_years))
    except Exception:                       # data short of a full season
        return out

    # --- one per team: who they miss ---------------------------------------
    for r in rows:
        miss = [m["opponent"] for m in r["missing"]]
        if miss:
            S = _subj(r["team"], "miss", opener=True)
            # Which six you miss is the whole argument about an unbalanced
            # schedule, so say how many of the fifteen that is rather than
            # leaving a reader to count the list.
            out.append(_fact(
                f"{S['n']} {S['does']} not play {_list(miss)} in {year} — "
                f"{len(miss)} of the {len(names) - 1} teams {S['they']} could "
                f"have drawn."))

    # --- first meetings -----------------------------------------------------
    # The league has only been at sixteen since 2023, so "never met before" is
    # still a live category and will keep emptying out. When it does, these
    # simply stop being generated.
    for pair in stats.get("firsts") or []:
        a, b = sorted(pair)
        out.append(_fact(
            f"{a} and {b} meet as conference opponents for the first time "
            f"ever in {year}."))
    for pair in stats.get("never") or []:
        a, b = sorted(pair)
        out.append(_fact(
            f"{a} and {b} have never met as Big 12 opponents, and do not "
            f"in {year} either."))

    # --- the longest anyone has waited --------------------------------------
    for entry in (stats.get("longest") or [])[:12]:
        last, a, b = entry[0], entry[1], entry[2]
        out.append(_fact(
            f"{a} and {b} last met as conference opponents in {last}, and "
            f"are not paired in {year}."))

    ever, total = stats.get("pairs_ever"), stats.get("pairs_total")
    if ever and total:
        out.append(_fact(
            f"Of the {total} pairings this sixteen-team league could produce, "
            f"{ever} have happened at least once. The rotation is still "
            f"working through the rest."))
    return out


_WORDS = {1: "once", 2: "twice"}


def _times(n):
    """"once", "twice", "three times" — never "1 time"."""
    return _WORDS.get(n) or f"{n} times"


def _list(items):
    """a, b and c — the Oxford comma is not the house style elsewhere here."""
    items = list(items)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# -------------------------------------------------------------- tiebreaker

ORD = ["", "", "second-", "third-", "fourth-", "fifth-"]


def _rank(value, others, high_is_good=True):
    """Where `value` sits among `others`, as a phrase — or "" if mid-table.

    The point of a fact is not the number, it is whether the number is any
    good, and "19 of 83" answers that only for somebody who already knows what
    the other fifteen teams did. Only the top and bottom few get a phrase;
    seventh-best of sixteen is not an interesting thing to be told.
    """
    vals = sorted(others, reverse=high_is_good)
    try:
        i = vals.index(value)
    except ValueError:
        return ""
    n = len(vals)
    if i < 5:
        word = "best" if high_is_good else "lowest"
        return f"the {ORD[i + 1]}{word} in the league" if i else \
               f"the {word} in the league"
    if i >= n - 5:
        j = n - i
        word = "lowest" if high_is_good else "best"
        return f"the {ORD[j]}{word} in the league" if j > 1 else \
               f"the {word} in the league"
    return ""


def _span(team, played):
    """The window a claim about this team actually covers.

    A team that has been here the whole time gets "since 2011". Everyone else
    gets the years they were actually in the league, because the alternative
    is a sentence that silently annexes seasons they spent in another
    conference — which is exactly how "Cincinnati's best conference season
    since 2011" came to mean "best of their three".
    """
    ys = played.get(team) or []
    if not ys:
        return ""
    first, last = ys[0], ys[-1]
    if first == TB_FIRST and last == TB_LAST:
        return f"since {TB_FIRST}"
    if last < TB_LAST:                       # they have left
        return f"from {first} to {last}"
    return f"since joining in {first}"


def _was(team, played):
    """Present tense for a current member, past for a departed one."""
    ys = played.get(team) or []
    return "was" if (ys and ys[-1] < TB_LAST) else "is"


def tiebreaker_facts(games_by_year):
    out = []
    titles = collections.Counter()
    appearances = collections.Counter()
    played = collections.defaultdict(list)      # team -> seasons in the league
    for y in sorted(games_by_year):
        here = set()
        for g in games_by_year[y]:
            if g.get("conference_game") and not g.get("ccg"):
                here.add(g["home"])
                here.add(g["away"])
        for t in here:
            played[t].append(y)

    for y in sorted(games_by_year):
        games = games_by_year[y]
        ccg = [g for g in games if g.get("ccg")]
        for g in ccg:
            hp, ap = g.get("home_points"), g.get("away_points")
            appearances[g["home"]] += 1
            appearances[g["away"]] += 1
            if hp is None or ap is None:
                continue
            win, lose = ((g["home"], g["away"]) if hp > ap
                         else (g["away"], g["home"]))
            titles[win] += 1
            out.append(_fact(
                f"{win} beat {lose} {max(hp, ap)}–{min(hp, ap)} in the "
                f"{y} Big 12 championship game."))

    for team, n in sorted(titles.items(), key=lambda t: (-t[1], t[0])):
        out.append(_fact(
            f"{team} has won the Big 12 championship game {_times(n)} since "
            f"the game returned in 2017."))
    for team, n in sorted(appearances.items()):
        if titles.get(team, 0) == 0:
            out.append(_fact(
                f"{team} has reached the Big 12 championship game "
                f"{_times(n)} without winning it."))

    # --- the shape of a conference season ---------------------------------
    for y in sorted(games_by_year):
        conf = [g for g in games_by_year[y]
                if g.get("conference_game") and not g.get("ccg")
                and g.get("home_points") is not None]
        if not conf:
            continue
        rec = collections.defaultdict(lambda: [0, 0])
        for g in conf:
            hp, ap = g["home_points"], g["away_points"]
            w, l = ((g["home"], g["away"]) if hp > ap else (g["away"], g["home"]))
            rec[w][0] += 1
            rec[l][1] += 1
        best = max(rec.items(), key=lambda kv: (kv[1][0] - kv[1][1], kv[1][0]))
        tied = [t for t, (w, l) in rec.items()
                if (w - l) == (best[1][0] - best[1][1])]
        if len(tied) > 1:
            out.append(_fact(
                f"{y} finished with {_list(sorted(tied))} level at the top "
                f"on {best[1][0]}–{best[1][1]}. That is what the seven steps "
                f"are for."))
        else:
            out.append(_fact(
                f"{best[0]} took the {y} conference season outright at "
                f"{best[1][0]}–{best[1][1]}."))

    # --- one per team: fifteen years of conference football ----------------
    alltime = collections.defaultdict(lambda: [0, 0])
    biggest = {}
    for y in sorted(games_by_year):
        for g in games_by_year[y]:
            if not g.get("conference_game") or g.get("ccg"):
                continue
            hp, ap = g.get("home_points"), g.get("away_points")
            if hp is None or ap is None:
                continue
            w, l = ((g["home"], g["away"]) if hp > ap
                    else (g["away"], g["home"]))
            alltime[w][0] += 1
            alltime[l][1] += 1
            margin = abs(hp - ap)
            if y not in biggest or margin > biggest[y][0]:
                biggest[y] = (margin, w, l, max(hp, ap), min(hp, ap))
    # Fourteen of the twenty teams in this data did not play all fifteen
    # seasons — four arrived in 2023, four more in 2024, and Texas, Oklahoma,
    # Missouri and Texas A&M left. "Since 2011" for any of them is a window
    # they were not in the conference for: Cincinnati's "best conference
    # season since 2011" quietly ignored an 8-0 AAC season and a Playoff
    # semi-final, because this file has never held a game they played
    # anywhere else. Every claim below names the span it actually covers.
    pcts = {t: w / (w + l) for t, (w, l) in alltime.items() if w + l >= 20}
    for team, (w, l) in sorted(alltime.items()):
        if w + l < 20:                # too little to be a record
            continue
        where = _rank(pcts[team], list(pcts.values()))
        color = f" — {where}" if where else ""
        S = _subj(team, "record", opener=True)
        verb = (S["is"] if _was(team, played) == "is" else S["was"])
        out.append(_fact(
            f"{S['n']} {verb} {w}–{l} in Big 12 conference games "
            f"{_span(team, played)}, {w / (w + l) * 100:.0f}%{color}."))
    for y, (margin, w, l, hi, lo) in sorted(biggest.items()):
        out.append(_fact(
            f"The most lopsided conference game of {y} was {w} {hi}, "
            f"{l} {lo} — a {margin}-point margin."))

    # --- one per team: the year it went best, and the longest run ----------
    per_season = collections.defaultdict(dict)
    order = collections.defaultdict(list)     # team -> results, in date order
    for y in sorted(games_by_year):
        rec = collections.defaultdict(lambda: [0, 0])
        graded = [g for g in games_by_year[y]
                  if g.get("conference_game") and not g.get("ccg")
                  and g.get("home_points") is not None]
        for g in sorted(graded, key=lambda x: (x.get("start") or "", x["id"])):
            hp, ap = g["home_points"], g["away_points"]
            w, l = ((g["home"], g["away"]) if hp > ap else (g["away"], g["home"]))
            rec[w][0] += 1
            rec[l][1] += 1
            order[w].append((y, True))
            order[l].append((y, False))
        for t, (w, l) in rec.items():
            per_season[t][y] = (w, l)

    for team, seasons in sorted(per_season.items()):
        if len(seasons) < 3:
            continue
        best_y = max(seasons, key=lambda y: (seasons[y][0] - seasons[y][1],
                                             seasons[y][0]))
        w, l = seasons[best_y]
        # How many seasons that is "best of" is the whole of whether it
        # impresses. Three is a note; fifteen is a record.
        n = len(played.get(team) or seasons)
        out.append(_fact(
            f"{team}'s best Big 12 season is {best_y}, at {w}–{l} — the best "
            f"of the {n} {'they have' if _was(team, played) == 'is' else 'they'} "
            f"played {_span(team, played)}."))

    runs = {}
    for team, results in sorted(order.items()):
        run, best, years, best_years = 0, 0, [], []
        for y, won in results:
            if won:
                run += 1
                years.append(y)
            else:
                run, years = 0, []
            if run > best:
                best, best_years = run, list(years)
        if best >= 4:
            # "since 2011" without a date is a claim you cannot look up. A run
            # can also straddle a new year, so the span is reported rather
            # than a single season assumed.
            span = sorted(set(best_years))
            when = (f"in {span[0]}" if len(span) == 1
                    else f"across {span[0]} and {span[-1]}" if len(span) == 2
                    else f"from {span[0]} to {span[-1]}")
            runs[team] = (best, when)

    for team, (best, when) in sorted(runs.items()):
        where = _rank(best, [v[0] for v in runs.values()])
        color = f" — {where}" if where else ""
        out.append(_fact(
            f"{team}'s longest run of Big 12 wins {_span(team, played)} "
            f"{_was(team, played)} {best} in a row, {when}{color}."))

    out.append(_fact(
        "The Big 12 tiebreaker runs seven steps. The last two are a "
        "proprietary computer rating and, after that, a coin toss."))
    out.append(_fact(
        "Every tie in the final standings since 2011 is broken step by step "
        "in the archive — including the ones the league never had to publish."))
    return out


# ------------------------------------------------------------------- pools

def pools_facts(year, lines_count):
    out = [
        _fact("One line for the whole room, frozen the moment the week is "
              "published. Whatever the market does afterwards, everybody is "
              "still playing the number you saw."),
        _fact("A push is a push. Land exactly on the number and it counts as "
              "neither a win nor a loss, and it stays out of your percentage "
              "entirely."),
        _fact("The board shows the chalk: what simply taking every favorite "
              "would have scored. In 2025 that was 56.5%, and most people do "
              "not beat it."),
        _fact("Survivor gives you one team a week and never the same team "
              "twice. Sixteen teams, and the season is longer than that."),
        _fact("Join survivor late and you arrive having already spent the "
              "biggest favorite of every week you missed."),
        _fact("Nobody sees anybody else's picks until the slate locks. The "
              "consensus is withheld for the same reason: it would be worth "
              "following."),
        _fact("Signing in asks your provider for one thing — that you are "
              "you. No email address, no name, no picture."),
    ]
    if lines_count:
        out.append(_fact(
            f"The {year} market has opened on {lines_count} games so far. "
            f"Games without a posted line are shown, and are not pickable."))
    return out


# ------------------------------------------------------- school or nickname

_MASCOTS = None
_SHARED = None


def _mascots():
    """{team: nickname}, and the nicknames more than one team answers to.

    Arizona and Kansas State are both Wildcats; BYU and Houston are both
    Cougars. "The Cougars average 38,000 a game" is not a fact about anybody,
    so those four never get the bare nickname — the guard is here rather than
    in the data because it is a property of the set, not of any one team.
    """
    global _MASCOTS, _SHARED
    if _MASCOTS is None:
        try:
            with open(os.path.join(HERE, "data", "teams.json"),
                      encoding="utf-8") as f:
                raw = json.load(f)
            _MASCOTS = {t: v["mascot"] for t, v in raw.items()
                        if isinstance(v, dict) and v.get("mascot")}
        except (OSError, ValueError):
            _MASCOTS = {}
        counts = collections.Counter(_MASCOTS.values())
        _SHARED = {m for m, n in counts.items() if n > 1}
    return _MASCOTS


def _subj(team, family, opener=False):
    """A team as a sentence subject — the school, or the nickname.

    People do not say "Cincinnati" four times in a paragraph, they say the
    Bearcats once. Alternating needs the grammar to follow: a school is
    singular and a nickname is plural, so every verb and possessive around it
    changes too. The caller gets the forms rather than guessing them.

    The choice is stable for a given team and fact family — deterministic, so
    the build stays reproducible, and keyed on the family so one team is not
    "the Bearcats" in every sentence on the page.
    """
    m = _mascots().get(team)
    # hashlib, NOT hash(): Python randomises string hashing per process, so
    # hash() here would pick different wording on every build and
    # verify-deterministic.sh would start failing at random.
    d = hashlib.sha256(f"{team}|{family}".encode()).digest()[0]
    use = bool(m) and m not in (_SHARED or set()) and d % 100 < 45
    if not use:
        return {"n": team, "is": "is", "was": "was", "has": "has",
                "does": "does", "its": "its", "v": "s", "they": "it"}
    # Stored lower-case because most uses are mid-sentence; `opener` is for
    # the ones that start one.
    return {"n": f"{'The' if opener else 'the'} {m}", "is": "are",
            "was": "were", "has": "have", "does": "do", "its": "their",
            "v": "", "they": "they"}


# ------------------------------------------------------------------- build

def build(year, games_by_year, teams, lines, rotation_mod, out_path):
    """Write facts.json, and return the per-section counts for the log."""
    hist = {y: g for y, g in games_by_year.items() if y <= TB_LAST}
    sections = {
        # Rivalries are split across two sections deliberately: what the
        # series has done is league history, and whether it is being played
        # this year is a fact about the rotation.
        "tiebreaker": tiebreaker_facts(hist)
                      + rivalry_facts(hist, year, TB_FIRST),
        "schedule": schedule_facts(
            year, games_by_year.get(year, []), teams,
            range(TB_FIRST, TB_LAST + 1), rotation_mod)
            + rivalry_schedule_facts(games_by_year, year),
        "attendance": attendance_facts(),
        "pools": pools_facts(year, len(lines or {})),
    }
    # Tag each fact with the teams it names, so the page can avoid printing
    # the same team on two cards at once. Done here, against the real team
    # list, rather than by the client guessing from the sentence: "Kansas"
    # is a substring of "Kansas State", and a longest-first match is the only
    # way that comes out right.
    # Nicknames count as the team. A fact that says "The Bearcats" is about
    # Cincinnati, and if the tag misses that, the no-two-cards-same-team rule
    # on the hub silently stops applying to every fact that used a nickname.
    # Every team the schedules name, not just the current sixteen. Tagging
    # against teams.json alone left Texas and Oklahoma untagged — which made
    # them invisible both to the hub's no-two-cards-same-team rule and to the
    # departed-team filter below, so "Texas took the 2023 conference season"
    # sailed through a check written to stop exactly that.
    every_name = set(teams or [])
    for _y in games_by_year:
        for _g in games_by_year[_y]:
            every_name.add(_g.get("home"))
            every_name.add(_g.get("away"))
    every_name.discard(None)
    names = sorted(every_name, key=len, reverse=True)
    nick = {f"the {m}": t for t, m in _mascots().items()
            if m not in (_SHARED or set())}
    for k in sections:
        for f in sections[k]:
            claimed, rest = [], f["t"]
            at = {}                      # team -> where it is first named
            for phrase, team in sorted(nick.items(), key=lambda kv: -len(kv[0])):
                m = re.search(re.escape(phrase), rest, re.IGNORECASE)
                if m:
                    claimed.append(team)
                    at.setdefault(team, m.start())
                    rest = re.sub(re.escape(phrase), " " * len(phrase), rest,
                                  flags=re.IGNORECASE)
            for n in names:
                i = rest.find(n)
                if i >= 0:
                    claimed.append(n)
                    at[n] = min(at.get(n, i), i)
                    rest = rest.replace(n, " " * len(n))
            if claimed:
                f["w"] = sorted(set(claimed))
                # The team named FIRST is the one the sentence is about. Every
                # family here puts its subject up front — "Texas took the 2023
                # season", "the biggest crowd Baylor has drawn" — and anyone
                # named later is the opponent. That distinction is the whole
                # of which facts a departed school may still appear in.
                f["s"] = min(at, key=lambda t: at[t])

    # Stable order, so an unchanged season rewrites nothing.
    for k in sections:
        sections[k] = sorted(sections[k], key=lambda f: (f.get("on") or "", f["t"]))
    # And nobody who left. Same call as the rivalries, for the same reason and
    # applied to everything: the record of the schools that walked out is not
    # what this site is for. It is done here rather than in each family so
    # that a new family cannot forget.
    # Only the facts ABOUT them. A departed school may still be the opponent
    # in somebody else's record — "the biggest crowd Baylor has drawn is
    # 51,728, against Texas in 2013" is a fact about Baylor, and losing it to
    # keep Texas off the page was throwing away the wrong thing.
    gone = departed(games_by_year)
    for k in sections:
        sections[k] = [f for f in sections[k] if f.get("s") not in gone]

    payload = {"season": year, "sections": sections}
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, indent=1, sort_keys=True))
    return {k: len(v) for k, v in sections.items()}

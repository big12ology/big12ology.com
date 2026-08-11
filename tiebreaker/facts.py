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
import csv
import datetime
import json
import os

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
    for team, rs in sorted(by_team.items()):
        if team in NO_PCT:
            continue
        with_pct = [r for r in rs if r["pct"] is not None]
        if len(with_pct) < 20:
            continue
        full = [r for r in with_pct if r["pct"] >= FULL]
        if len(full) == len(with_pct):
            out.append(_fact(
                f"{team} has filled its stadium for every one of its "
                f"{len(with_pct)} home games since {ATT_FIRST} — every "
                f"season, every opponent."))
        elif full:
            out.append(_fact(
                f"{team} has filled its stadium in {len(full)} of "
                f"{len(with_pct)} home games since {ATT_FIRST}."))
        else:
            best = max(with_pct, key=lambda r: r["pct"])
            out.append(_fact(
                f"{team} has not filled its stadium once since {ATT_FIRST}. "
                f"The closest it came was {best['pct'] * 100:.0f}% against "
                f"{best['opponent']} in {best['season']}."))

    # --- one per team: the biggest crowd it has drawn ----------------------
    for team, rs in sorted(by_team.items()):
        best = max(rs, key=lambda r: r["att"])
        out.append(_fact(
            f"The biggest crowd {team} has drawn since {ATT_FIRST} is "
            f"{_comma(best['att'])}, against {best['opponent']} in "
            f"{best['season']}."))

    # --- one per team: what an ordinary Saturday looks like ----------------
    for team, rs in sorted(by_team.items()):
        avg = sum(r["att"] for r in rs) / len(rs)
        out.append(_fact(
            f"{team} averages {_comma(avg)} a home game since {ATT_FIRST}, "
            f"over {len(rs)} games."))

    # --- one per season ----------------------------------------------------
    by_season = collections.defaultdict(list)
    for r in normal:
        by_season[r["season"]].append(r)
    for s, rs in sorted(by_season.items()):
        avg = sum(r["att"] for r in rs) / len(rs)
        big = max(rs, key=lambda r: r["att"])
        out.append(_fact(
            f"In {s} the league averaged {_comma(avg)} a home game, and the "
            f"biggest single crowd was {_comma(big['att'])} at "
            f"{big['team']}."))

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
    if fives and len(fives) != len(names):
        out.append(_fact(
            f"Nine conference games will not split evenly, so in {year} "
            f"{len(fives)} teams get five home games and the other "
            f"{len(names) - len(fives)} get four."))
    for t in names:
        h, a = home.get(t, 0), away.get(t, 0)
        if h + a:
            out.append(_fact(
                f"{t} plays {h} of its {h + a} {year} conference games at "
                f"home."))

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
            out.append(_fact(
                f"{t}'s {year} non-conference schedule is "
                f"{_series(opps)}."))

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
            out.append(_fact(
                f"{r['team']} does not play {_series(miss)} in {year}."))

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


def _series(items):
    """a, b and c — the Oxford comma is not the house style elsewhere here."""
    items = list(items)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# -------------------------------------------------------------- tiebreaker

def tiebreaker_facts(games_by_year):
    out = []
    titles = collections.Counter()
    appearances = collections.Counter()

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
                f"{y} finished with {_series(sorted(tied))} level at the top "
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
    for team, (w, l) in sorted(alltime.items()):
        if w + l < 20:                # too little to be a record
            continue
        out.append(_fact(
            f"{team} is {w}–{l} in Big 12 conference games between "
            f"{TB_FIRST} and {TB_LAST}."))
    for y, (margin, w, l, hi, lo) in sorted(biggest.items()):
        out.append(_fact(
            f"The most lopsided conference game of {y} was {w} {hi}, "
            f"{l} {lo} — a {margin}-point margin."))

    # --- one per team: the year it went best, and the longest run ----------
    per_season = collections.defaultdict(dict)
    order = collections.defaultdict(list)     # team -> results, in date order
    for y in sorted(games_by_year):
        rec = collections.defaultdict(lambda: [0, 0])
        played = [g for g in games_by_year[y]
                  if g.get("conference_game") and not g.get("ccg")
                  and g.get("home_points") is not None]
        for g in sorted(played, key=lambda x: (x.get("start") or "", x["id"])):
            hp, ap = g["home_points"], g["away_points"]
            w, l = ((g["home"], g["away"]) if hp > ap else (g["away"], g["home"]))
            rec[w][0] += 1
            rec[l][1] += 1
            order[w].append(True)
            order[l].append(False)
        for t, (w, l) in rec.items():
            per_season[t][y] = (w, l)

    for team, seasons in sorted(per_season.items()):
        if len(seasons) < 3:
            continue
        best_y = max(seasons, key=lambda y: (seasons[y][0] - seasons[y][1],
                                             seasons[y][0]))
        w, l = seasons[best_y]
        out.append(_fact(
            f"{team}'s best conference season since {TB_FIRST} is {best_y}, "
            f"at {w}–{l}."))

    for team, results in sorted(order.items()):
        run = best = 0
        for won in results:
            run = run + 1 if won else 0
            best = max(best, run)
        if best >= 4:
            out.append(_fact(
                f"{team}'s longest run of conference wins since {TB_FIRST} "
                f"is {best} in a row."))

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
        _fact("The board shows the chalk: what simply taking every favourite "
              "would have scored. In 2025 that was 56.5%, and most people do "
              "not beat it."),
        _fact("Survivor gives you one team a week and never the same team "
              "twice. Sixteen teams, and the season is longer than that."),
        _fact("Join survivor late and you arrive having already spent the "
              "biggest favourite of every week you missed."),
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


# ------------------------------------------------------------------- build

def build(year, games_by_year, teams, lines, rotation_mod, out_path):
    """Write facts.json, and return the per-section counts for the log."""
    sections = {
        "tiebreaker": tiebreaker_facts(
            {y: g for y, g in games_by_year.items() if y <= TB_LAST}),
        "schedule": schedule_facts(
            year, games_by_year.get(year, []), teams,
            range(TB_FIRST, TB_LAST + 1), rotation_mod),
        "attendance": attendance_facts(),
        "pools": pools_facts(year, len(lines or {})),
    }
    # Stable order, so an unchanged season rewrites nothing.
    for k in sections:
        sections[k] = sorted(sections[k], key=lambda f: (f.get("on") or "", f["t"]))
    payload = {"season": year, "sections": sections}
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, indent=1, sort_keys=True))
    return {k: len(v) for k, v in sections.items()}

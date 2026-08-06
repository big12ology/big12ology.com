#!/usr/bin/env python3
"""Build the Big 12 tiebreaker site.

    python3 build.py            # current season, uses committed data
    python3 build.py 2024       # specific season
    python3 build.py --fetch    # refetch this season's results (one call)
    python3 build.py --fetch --refresh   # ratings and lines too (+9 calls)

Writes site/index.html — fully self-contained, no external requests.
"""
import datetime
import html
import hashlib
import json
import os
import sys

import chaos as chaos_mod
import clinch as clinch_mod
import feed as feed_mod
import fetch as fetcher
import odds as odds_mod
import scorecard as scorecard_mod
import tiebreaker as tb

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")

# team -> logo file key (assets in site/logos/, sourced from Wikimedia; BYU is png)
TEAM_KEY = {
    "Arizona": "ariz", "Arizona State": "asu", "Baylor": "bay", "BYU": "byu",
    "Cincinnati": "cin", "Colorado": "cu", "Houston": "hou",
    "Iowa State": "isu", "Kansas": "ku", "Kansas State": "ksu",
    "Oklahoma State": "okst", "TCU": "tcu", "Texas Tech": "ttu",
    "UCF": "ucf", "Utah": "utah", "West Virginia": "wvu",
}


def load_teams():
    p = os.path.join(HERE, "data", "teams.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def load_ratings(year):
    """Ratings, regressed here rather than at the point of use.

    In August most systems have not published for the new season yet and
    fetch falls back to last year's finals — three of four did in 2026.
    Regressing once, at load, is what keeps the odds, the what-if
    favourites and the strength-of-schedule card describing the same teams:
    Rule 6, the same quantity presented the same way everywhere."""
    p = os.path.join(HERE, "data", f"ratings_{year}.json")
    raw = json.load(open(p)) if os.path.exists(p) else {"systems": {}}
    raw["systems"] = odds_mod.regress_stale(raw.get("systems", {}), year)
    return raw


def load_lines(year):
    p = os.path.join(HERE, "data", f"lines_{year}.json")
    return json.load(open(p)) if os.path.exists(p) else {}


MODEL_ORDER = ["SP+", "FPI", "Elo", "SRS"]


def favorites_for(games, systems):
    """{system: {game_id: {team, margin}}} for every unplayed game (conference
    and non-conference). Margin is converted to scoring points via the
    system's per_pt scale and includes its home-field bump. Opponents the
    system doesn't rate (FCS and lower) get a floor rating well below the
    worst rated team."""
    out = {}
    for name, s in systems.items():
        r, hfa, per = s["ratings"], s["hfa"], s.get("per_pt", 1.0) or 1.0
        floor = min(r.values()) - 10 * per
        m = {}
        for g in games:
            if g.get("ccg") or g["completed"]:
                continue
            hr, ar = r.get(g["home"]), r.get(g["away"])
            if hr is None and ar is None:
                continue
            if hr is None:
                hr = floor
            if ar is None:
                ar = floor
            d = hr - ar + hfa
            m[str(g["id"])] = {
                "team": g["home"] if d >= 0 else g["away"],
                "margin": round(abs(d) / per, 1),
            }
        if m:
            out[name] = m
    return out


# Seasons kept online. The live season sits at the root; finished ones get
# their own directory so their URLs never change once the year is over.
ARCHIVE_YEARS = [2025, 2024]
BASE = ""      # "../" while writing an archived season, so assets resolve
LIVE_YEAR = None


def asset_v(name):
    """Cache-bust by content. A browser that has app.js in cache will not ask
    the server whether it changed, so a deploy can silently keep running the
    old file — this makes the URL change whenever the bytes do."""
    p = os.path.join(SITE, name)
    try:
        h = hashlib.sha1(open(p, "rb").read()).hexdigest()[:8]
    except OSError:
        return name
    return f"{name}?v={h}"


def rebase(html):
    """Point a prebuilt fragment's assets at the shared copies. The tie
    archive is generated once by gen_history.py with paths relative to the
    site root, so an archived season would look for them inside its own
    directory and find nothing."""
    if not BASE:
        return html
    return (html.replace("src='logos/", f"src='{BASE}logos/")
                .replace('src="logos/', f'src="{BASE}logos/')
                .replace("src=logos/", f"src={BASE}logos/"))


def year_href(y):
    """Link from wherever we are now to another season's front page."""
    return BASE if y == LIVE_YEAR else f"{BASE}{y}/"


def load_marks():
    """team -> its entry in logos/SOURCES.json, which is both the registry
    and the provenance record. Conference and non-conference alike: a
    matchup row shows two teams and should not present them two ways."""
    p = os.path.join(SITE, "logos", "SOURCES.json")
    if not os.path.exists(p):
        return {}
    return {e["team"]: e for e in json.load(open(p)) if e.get("team")}


MARKS = load_marks()


def logo_img(team, size=20):
    e = MARKS.get(team)
    if not e:
        return ""
    if e.get("usable") is False:
        # A mark we deliberately do not have. Saying so beats a silent gap,
        # which reads as an oversight, and beats showing something that is
        # not the team's mark. The label carries the reason for screen
        # readers; title carries it for everyone else.
        why = e.get("note") or "no freely-licensed mark is available"
        label = f"No logo for {team} — {why}."
        return (f"<span class='mark nomark' role=img aria-label=\"{esc(label)}\" "
                f"title=\"{esc(label)}\">!</span>")
    ext = (e.get("ext") or ".svg").lstrip(".")
    return (f"<img class=mark src='{BASE}logos/{e['key']}.{ext}' alt='' "
            f"width={size} height={size} loading=lazy>")


def team_color(teams, team, fallback="#888888"):
    c = (teams.get(team) or {}).get("color") or fallback
    return c


# Win-percentage color curve — same visual language as the attendance
# tracker's fill gradient, mapped to the 0..1.000 win% domain with the
# resolution concentrated at the top. Kept in sync with winPctColor in
# site/app.js.
WINPCT_ANCHORS = [(0.0, 0), (0.30, 20), (0.45, 45), (0.60, 75),
                  (0.70, 95), (0.80, 118), (0.90, 140), (1.0, 168)]


def winpct_color(p):
    a = WINPCT_ANCHORS
    h = a[-1][1]
    if p <= a[0][0]:
        h = a[0][1]
    else:
        for i in range(1, len(a)):
            if p <= a[i][0]:
                t = (p - a[i - 1][0]) / (a[i][0] - a[i - 1][0])
                h = a[i - 1][1] + t * (a[i][1] - a[i - 1][1])
                break
    s = 100 - (h / 45) * 35 if h < 45 else 65
    return f"hsl({round(h)} {round(s)}% var(--pctl))"


def fmt_prob(p):
    if p >= 0.9995:
        return "100%"
    if p <= 0 :
        return "0%"
    if p < 0.001:
        return "&lt;0.1%"
    return f"{p * 100:.1f}%".replace(".0%", "%")



def tracker_top(year, active, matchcard=""):
    """The one top: header bar, pill row, matchup card. Styled entirely by
    brand.css (.b12-head/.subnav) — no page may restyle these."""
    years = "".join(
        (f"<span class=yron>{y}</span>" if y == year else
         f"<a href='{year_href(y)}'>{y}</a>")
        for y in [LIVE_YEAR] + ARCHIVE_YEARS)
    blurb = ("Unofficial fan tool. Applies the official Big 12 tiebreaking "
             "procedures to live results after every game."
             if year == LIVE_YEAR else
             f"The {year} season as it finished, with the official "
             "tiebreaking procedures applied to the final results.")
    return f"""<header class=b12-head>
  <div class=hwrap>
    <div>
      <h1>Big 12 Tiebreaker Tracker <span class=yrpills>{years}</span></h1>
      <p>{blurb}</p>
    </div>
  </div>
</header>
{subnav(active)}
<main id=main tabindex="-1">
{matchcard}"""


# Ordered the way someone actually reads the season: the summary, then the
# race, then where everyone stands, then the toys. Every label takes "The" —
# a name that only works without it is a sign the page needs a better name.
SUBNAV_LINKS = [("brief", "./", "The Brief"),
                ("race", "race.html", "The Race"),
                ("standings", "standings.html", "The Standings"),
                ("tracker", "lab.html", "The Lab"),
                ("schedule", "schedule.html", "The Schedule"),
                ("how", "how.html", "The Rules"),
                ("history", "history.html", "The Archive")]


def subnav(active):
    links = "".join(
        f"<a href={href} class={'on' if key == active else 'off'}>{label}</a>"
        for key, href, label in SUBNAV_LINKS)
    return f"<nav class=subnav>{links}</nav>"


def next_conf_week_ids(games):
    """Game ids for the next week that still has unplayed conference games."""
    rem = [g for g in games if g["conference_game"] and not g.get("ccg")
           and not g["completed"]]
    if not rem:
        return [], None
    wk = min(g["week"] for g in rem)
    return [g["id"] for g in rem if g["week"] == wk], wk


def leverage_card(games, sims):
    lev = odds_mod.leverage(sims, games)
    if not lev:
        return ""
    wk = lev[0]["game"]["week"]
    rows = []
    for e in lev[:8]:
        g = e["game"]
        date = pretty_date(g["start"])
        mover_txt = ""
        if e["movers"]:
            t, d = e["movers"][0]
            gain = "+" if d > 0 else ""
            side = g["home"] if d > 0 else g["away"]
            mover_txt = (f"<span class=dim> — biggest swing: {esc(t)} "
                         f"{gain}{d * 100:.0f}% if {esc(side)} wins</span>")
        pct = min(e["total"] * 100, 100)
        rows.append(
            f"<div class=clrow>{logo_img(g['away'], 16)}{esc(g['away'])} "
            f"<span class=dim>at</span> {logo_img(g['home'], 16)}"
            f"{esc(g['home'])} <span class=dim>({date})</span> "
            f"<span class=obar><i style='width:{pct:.0f}%;"
            f"background:{winpct_color(min(e['total'], 1.0))}'></i></span>"
            f"<b class=opct>{e['total'] * 100:.0f}</b>{mover_txt}</div>")
    return (f"<div class=card id=levcard><h2>Games that matter · week {wk}"
            f"</h2>{''.join(rows)}"
            "<p class=note>Title-race leverage: the total swing in "
            "championship-game probability across all sixteen teams between "
            "the two outcomes of each game, from the same simulations as "
            "the race card. 100 = a full berth's worth of probability "
            "moves on this game.</p></div>")


def sos_card(games, systems):
    rem = [g for g in games if g["conference_game"] and not g.get("ccg")
           and not g["completed"]]
    if not rem or not systems:
        return ""
    # ensemble rating per team, normalized to points about the league mean
    teams = sorted({g["home"] for g in rem} | {g["away"] for g in rem})
    ens = {}
    for t in teams:
        vals = []
        for s in systems.values():
            r = s["ratings"].get(t)
            if r is None:
                continue
            per = s.get("per_pt", 1.0) or 1.0
            league = [s["ratings"][x] for x in teams if x in s["ratings"]]
            vals.append((r - sum(league) / len(league)) / per)
        ens[t] = sum(vals) / len(vals) if vals else 0.0
    sched = {t: [] for t in teams}
    for g in rem:
        sched[g["home"]].append(ens[g["away"]])
        sched[g["away"]].append(ens[g["home"]])
    rows = []
    ranked = sorted(teams, key=lambda t: -(sum(sched[t]) / len(sched[t])))
    for i, t in enumerate(ranked):
        avg = sum(sched[t]) / len(sched[t])
        rows.append(
            f"<tr><td>{i + 1}</td><td class=teamcell>{logo_img(t, 16)}"
            f"{esc(t)}</td><td>{len(sched[t])}</td>"
            f"<td>{avg:+.1f}</td></tr>")
    return ("<div class=card id=soscard><h2>Remaining schedule difficulty"
            "</h2><table><thead><tr><th></th><th>Team</th><th>Left</th>"
            "<th>Avg opp vs league</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>"
            "<p class=note>Average remaining conference opponent strength, "
            "in points above or below the league-average team, from the "
            "same rating ensemble as the odds. Positive = harder road.</p>"
            "</div>")


def scorecard_card(games, systems, lines=None):
    tal = scorecard_mod.tally(games, systems, lines)
    played = any(v["w"] + v["l"] > 0 for v in tal.values())
    if not played:
        return ("<div class=card id=modelcard><h2>Model scorecard</h2>"
                "<p class=dim>Which rating system picks Big 12 games best? "
                "The scorecard starts counting with the first kickoff.</p>"
                "</div>")
    rows = []
    for name in sorted(tal, key=lambda n: -(tal[n]["w"] /
                                            max(tal[n]["w"] + tal[n]["l"], 1))):
        v = tal[name]
        tot = v["w"] + v["l"]
        pct = v["w"] / tot if tot else 0
        label = ("<b>Vegas</b> <span class=dim>(closing line)</span>"
                 if name == "Vegas" else esc(name))
        rows.append(
            f"<tr><td>{label}</td><td>{v['w']}–{v['l']}</td>"
            f"<td style='color:{winpct_color(pct)}'>{pct:.3f}</td></tr>")
    return ("<div class=card id=modelcard><h2>Model scorecard</h2>"
            "<table><thead><tr><th>Model</th><th>Favorites</th><th>Pct</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<p class=note>Each system's favorites in completed games "
            "involving Big 12 teams (both sides rated; FCS games skipped). "
            "Judged against the market's closing-line favorites. One "
            "honesty note: the models pick with their currently published "
            "ratings, which have seen the games they're grading — Vegas's "
            "number was locked at kickoff. Respect the house.</p></div>")


def team_abbr(teams, t):
    """The short label for a team, from teams.json.

    Never truncate as a fallback — Arizona and Arizona State collide at three
    letters (both ARI) and at four (both ARIZ), which is the bug this
    replaces. A team missing an abbreviation shows its full name, so the gap
    is visible instead of silently ambiguous.
    """
    return (teams.get(t) or {}).get("abbr") or t


def h2h_card(games, teams, stand_rows):
    """Current-season head-to-head grid: every conference meeting, played or
    scheduled, from the row team's perspective. Ordered by current standings
    (alphabetical before any results)."""
    conf = [g for g in games if g["conference_game"] and not g.get("ccg")]
    if not conf:
        return ""
    if stand_rows:
        order = [r["team"] for r in stand_rows]
    else:
        order = sorted({g["home"] for g in conf} | {g["away"] for g in conf})
    meet = {}
    for g in conf:
        meet[frozenset((g["home"], g["away"]))] = g

    head = "".join(
        f"<th title='{esc(t)}'>{esc(team_abbr(teams, t))}</th>" for t in order)
    body = []
    for a in order:
        cells = []
        for b in order:
            if a == b:
                cells.append("<td class=selfcell aria-hidden=true>&#9587;</td>")
                continue
            g = meet.get(frozenset((a, b)))
            if g is None:
                cells.append("<td class=nomeet>&bull;</td>")
                continue
            date = pretty_date(g["start"])
            if g["completed"] and g["home_points"] is not None:
                mine = g["home_points"] if g["home"] == a else g["away_points"]
                theirs = g["away_points"] if g["home"] == a else g["home_points"]
                won = mine > theirs
                color = winpct_color(1.0 if won else 0.0)
                home_game = g["home"] == a
                cells.append(
                    f"<td style='color:{color}' title='{esc(a)} "
                    f"{'def.' if won else 'lost to'} {esc(b)} "
                    f"{mine}–{theirs} ({date}, "
                    f"{'home' if home_game else 'away'})'>"
                    f"<span class=hatag>{'H' if home_game else 'A'}</span>"
                    f"{'W' if won else 'L'} {mine}–{theirs}</td>")
            else:
                home_game = g["home"] == a
                at = "vs" if home_game else "at"
                cells.append(
                    f"<td class=dim title='{esc(a)} {at} {esc(b)}, "
                    f"{date}'><span class=hatag>{'H' if home_game else 'A'}"
                    f"</span>wk {g['week']}</td>")
        body.append(f"<tr><td class=teamcell>{logo_img(a, 14)}"
                    f"{esc(a)}</td>{''.join(cells)}</tr>")
    return ("<div class=card id=h2hcard><h2>Head-to-head grid</h2>"
            '<div class="tablescroll scrollbox"><table class=h2h>'
            '<thead><tr><th></th>'
            + head + "</tr></thead><tbody>" + "".join(body)
            + "</tbody></table></div>"
            "<p class=note>Every conference meeting this season, read "
            "across: H marks a home game and A an away game, then the row "
            "team's result or the scheduled week. A bullet "
            "means the schedule never pairs them — in a nine-game draw "
            "that's more than a third of the grid, which is why the "
            "tiebreakers exist.</p></div>")


def pad_standings(rows, games):
    """Show all sixteen teams from the first visit, not only the ones with a
    conference result. The engine deliberately ranks only teams it has
    evidence for, so padding happens here, in the display: unplayed teams
    follow the ranked ones in alphabetical order with a dash for rank and
    percentage, carrying whatever non-conference games they have played."""
    listed = {r["team"] for r in rows}
    missing = sorted(t for t in clinch_mod.conf_teams(games) if t not in listed)
    if not missing:
        return rows
    tally = {t: {"nw": 0, "nl": 0, "ow": 0, "ol": 0} for t in missing}
    for g in games:
        if not g["completed"] or g.get("ccg") or g["home_points"] is None:
            continue
        w = tb.winner(g)
        if not w:
            continue
        loser = g["away"] if w == g["home"] else g["home"]
        for t, won in ((w, True), (loser, False)):
            if t not in tally:
                continue
            tally[t]["ow" if won else "ol"] += 1
            if not g["conference_game"]:
                tally[t]["nw" if won else "nl"] += 1
    return rows + [{
        "rank": None, "team": t, "conf_w": 0, "conf_l": 0,
        "nonconf_w": tally[t]["nw"], "nonconf_l": tally[t]["nl"],
        "overall_w": tally[t]["ow"], "overall_l": tally[t]["ol"],
        "tie_group": None, "log": None, "events": None, "resolved": True,
    } for t in missing]


def official_board(games, overrides, display_rows):
    """Positions the way the conference actually keeps them: the tiebreakers
    run only far enough to name the two championship-game participants, and
    every tie below that is simply a tie. Teams sharing a record share a
    position (T3, T3, T3, then 6th)."""
    played = [r for r in display_rows if r["conf_w"] + r["conf_l"] > 0]
    if not played:
        return [{"pos": "—", "teams": [r["team"] for r in display_rows],
                 "rec": "0–0", "tied": True}]
    ccg = tb.championship(games, overrides)
    seeds = [ccg["seed1"], ccg["seed2"]] if ccg else []
    by_team = {r["team"]: r for r in display_rows}
    out = []
    for i, t in enumerate(seeds):
        r = by_team[t]
        out.append({"pos": str(i + 1), "teams": [t],
                    "rec": f"{r['conf_w']}–{r['conf_l']}", "tied": False})
    rest = [r for r in played if r["team"] not in seeds]
    groups = {}
    for r in rest:
        groups.setdefault((r["conf_w"], r["conf_l"]), []).append(r["team"])
    pos = len(seeds) + 1
    for key in sorted(groups, key=lambda k: (-(k[0] / max(k[0] + k[1], 1)),
                                             -k[0])):
        teams = sorted(groups[key])
        out.append({"pos": (f"T{pos}" if len(teams) > 1 else str(pos)),
                    "teams": teams, "rec": f"{key[0]}–{key[1]}",
                    "tied": len(teams) > 1})
        pos += len(teams)
    unplayed = sorted(r["team"] for r in display_rows
                      if r["conf_w"] + r["conf_l"] == 0)
    if unplayed:
        out.append({"pos": "—", "teams": unplayed, "rec": "0–0",
                    "tied": len(unplayed) > 1})
    return out


def season_frames(games, overrides):
    """One snapshot per conference week: both boards exactly as the engine
    would have drawn them that Sunday morning. Computed here, in Python, so
    the replay can never drift from the rules engine the way a second
    client-side implementation would."""
    weeks = sorted({g["week"] for g in games
                    if g["completed"] and g["conference_game"]
                    and not g.get("ccg") and g["home_points"] is not None})
    everyone = sorted(clinch_mod.conf_teams(games))
    out = []
    for w in weeks:
        # Weeks that had not happened yet are unplayed, not missing. Dropping
        # them outright would tell the clinch analysis the season was over.
        sub = []
        for g in games:
            if g["week"] <= w:
                sub.append(g)
                continue
            later = dict(g)
            later["completed"] = False
            later["home_points"] = None
            later["away_points"] = None
            sub.append(later)
        rows = tb.standings(sub, overrides)
        display = pad_standings(rows, sub)
        seen = {r["team"] for r in display}
        display = display + [{
            "rank": None, "team": t, "conf_w": 0, "conf_l": 0,
            "nonconf_w": 0, "nonconf_l": 0, "overall_w": 0, "overall_l": 0,
            "tie_group": None, "log": None, "events": None, "resolved": True,
        } for t in everyone if t not in seen]
        last = max((g["start"] or "" for g in sub
                    if g["week"] == w and g["completed"]), default="")
        cl = clinch_mod.analyze(sub, overrides)["teams"]
        status = {t: cl.get(t, {}).get("status", "alive") for t in everyone}
        left = []
        for b in official_board(sub, overrides, display):
            for i, t in enumerate(b["teams"]):
                r = next(x for x in display if x["team"] == t)
                left.append({"t": t, "p": b["pos"], "w": r["conf_w"],
                             "l": r["conf_l"], "n": len(b["teams"]),
                             "i": i, "s": status[t]})
        out.append({
            "w": w,
            "label": f"Week {w}",
            "date": pretty_date(last, "short") if last else "",
            "left": left,
            "right": [{"t": r["team"], "p": str(r["rank"] or "—"),
                       "w": r["conf_w"], "l": r["conf_l"],
                       "s": status[r["team"]]} for r in display],
        })
    return out


def bump_svg(frames, teams):
    """How the season moved: every team's position, week by week. Positions
    come from the fully-broken board, the only one that gives each team a
    place of its own — the official board would stack four lines on one row."""
    if len(frames) < 2:
        return ""
    n = len(frames)
    W, H = 940, 470
    m = {"l": 46, "r": 116, "t": 26, "b": 34}
    x = lambda i: m["l"] + (i / (n - 1)) * (W - m["l"] - m["r"])
    y = lambda rank: m["t"] + ((rank - 1) / 15) * (H - m["t"] - m["b"])

    grid = "".join(
        f'<line x1="{m["l"]}" y1="{y(r):.1f}" x2="{W - m["r"]}" '
        f'y2="{y(r):.1f}" class="bgrid"/>' for r in range(1, 17))
    grid += "".join(
        f'<text x="{m["l"] - 12}" y="{y(r) + 4:.1f}" class="btick bnum">{r}</text>'
        for r in range(1, 17))
    grid += "".join(
        f'<text x="{x(i):.1f}" y="{H - 10}" class="btick bwk">{f["w"]}</text>'
        for i, f in enumerate(frames))
    grid += (f'<text x="{m["l"] - 12}" y="{m["t"] - 10}" class="btick bnum">'
             f'Pos</text>')

    order = {r["t"]: i for i, r in enumerate(frames[-1]["right"])}
    lines = []
    for team in sorted(order, key=lambda t: order[t]):
        pts = []
        for i, f in enumerate(frames):
            rank = next((j + 1 for j, r in enumerate(f["right"])
                         if r["t"] == team), None)
            if rank:
                pts.append((x(i), y(rank)))
        if not pts:
            continue
        c = team_color(teams, team)
        path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        dots = "".join(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.6" '
                       f'fill="{c}"/>' for px, py in pts)
        ex, ey = pts[-1]
        k = TEAM_KEY.get(team, "")
        ext = "png" if k == "byu" else "svg"
        lines.append(
            f'<g class="bteam"><title>{esc(team)}</title>'
            f'<polyline points="{path}" fill="none" stroke="{c}" '
            f'stroke-width="2.4" stroke-linejoin="round" '
            f'stroke-linecap="round"/>'
            f"{dots}"
            f'<image href="{BASE}logos/{k}.{ext}" x="{ex + 8:.1f}" '
            f'y="{ey - 8:.1f}" width="16" height="16"/>'
            f'<text x="{ex + 28:.1f}" y="{ey + 4:.1f}" class="blabel" '
            f'fill="{c}">{esc(team)}</text></g>')

    return (f"<div class=card><h2>How the season moved</h2>"
            f'<div class="bumpwrap"><svg class="bump" viewBox="0 0 {W} {H}" '
            f'role="img" aria-label="Conference position by week">'
            f"{grid}{''.join(lines)}</svg></div>"
            f"<p class=note>Position after each week, from the fully-broken "
            f"board — the only one that gives every team a place of its own. "
            f"Hover a line to follow one program. Weeks with no Big 12 games "
            f"are skipped, so the spacing is by conference week, not by "
            f"calendar date.</p></div>")


def status_class(state, pos):
    """How a team's championship standing reads in the board: a team that
    can no longer reach the title game is struck through, one that has
    clinched a berth is bold, and the top seed among them is italic too."""
    if state == "eliminated":
        return " st-out"
    if state == "clinched":
        return " st-in st-top" if pos == "1" else " st-in"
    return ""


def standings_page(games, overrides, display_rows, teams):
    """Two boards side by side, drawn with the same chrome as the main
    standings table so the only difference a reader sees is the ordering.
    Non-conference and overall records are left to the Scenarios board —
    at this width the conference record and percentage are the comparison."""
    head = ("<thead><tr><th></th><th>Team</th><th>Conf</th><th>Pct</th>"
            "</tr></thead>")
    by_team = {r["team"]: r for r in display_rows}
    frames = season_frames(games, overrides)
    state = ({r["t"]: r["s"] for r in frames[-1]["right"]} if frames else {})

    def cells(r, pos=None):
        p = tb.pct(r["conf_w"], r["conf_l"])
        c = team_color(teams, r["team"])
        st = status_class(state.get(r["team"]), pos)
        return (f"<td class='teamcell{st}'><span class=cbar "
                f"style='background:{c}'>"
                f"</span>{logo_img(r['team'])}{esc(r['team'])}</td>"
                f"<td>{r['conf_w']}–{r['conf_l']}</td>"
                + ("<td>—</td>" if p is None else
                   f"<td style='color:{winpct_color(p)}'>{p:.3f}</td>"))

    left = []
    for b in official_board(games, overrides, display_rows):
        n = len(b["teams"])
        for i, t in enumerate(b["teams"]):
            span = f" rowspan={n}" if n > 1 else ""
            pos = (f"<td class=posc{span}>{esc(b['pos'])}</td>"
                   if i == 0 else "")
            cls = " class=grpend" if i == n - 1 and n > 1 else ""
            left.append(f"<tr{cls}>{pos}{cells(by_team[t], b['pos'])}</tr>")

    right = "".join(
        f"<tr><td class=posc>{r['rank'] or '—'}</td>"
        f"{cells(r, str(r['rank'] or '—'))}</tr>" for r in display_rows)

    replay = ""
    if len(frames) > 1:
        marks = {t: {"color": team_color(teams, t),
                     "logo": f"{BASE}logos/{TEAM_KEY[t]}."
                             f"{'png' if TEAM_KEY[t] == 'byu' else 'svg'}"}
                 for t in sorted(TEAM_KEY) if t in
                 {r["t"] for r in frames[-1]["right"]}}
        replay = ("<div class=card id=replaycard>"
                  "<h2>Replay the season</h2>"
                  "<div id=replaybar></div>"
                  "<p class=note>Both boards below redraw to the Sunday "
                  "morning after the week you pick. Every frame is the real "
                  "rules engine run against the results that existed at the "
                  "time, so what you see is what the standings actually "
                  "were — not a reconstruction. A team that could still "
                  "reach the championship game that week is plain, "
                  "<b>bold</b> once its berth was clinched and "
                  "<b><i>bold italic</i></b> for the top seed; "
                  "<span class=st-out>struck through</span> means "
                  "eliminated. Rows flash green when a team climbed and red "
                  "when it fell.</p></div>"
                  "<script id=replay-data type=application/json>"
                  + json.dumps({"frames": frames, "teams": marks})
                  + "</script>")

    return f"""{replay}<div class="duo even">
<div class=stack>
<div class=card><h2>As the conference keeps it</h2>
  <div class=tablewrap><table class=stbl>{head}
  <tbody id=board-left>{"".join(left)}</tbody></table></div>
  <p class=note>The Big 12 runs its tiebreaking procedure for one purpose:
  naming the two teams that play in the championship game. Every other tie
  in the standings is left standing, so third place can be shared by four
  programs and the conference simply lists them together. That is why its
  published standings show co-positions instead of an order.</p>
</div>
</div>
<div class=stack>
<div class=card><h2>If every tie were broken</h2>
  <div class=tablewrap><table class=stbl>{head}
  <tbody id=board-right>{right}</tbody></table></div>
  <p class=note>The same procedure carried all the way down, one team per
  position. The conference never publishes this and it decides nothing — it
  is what the rules produce if you ask them to sort the whole league, and it
  is the order the rest of this site uses so every team has a place to
  stand. <a href=how.html>The Rules</a> walks the steps.</p>
</div>
</div>
</div>
{bump_svg(frames, teams)}"""


def clinch_card(games, overrides, systems, stand_rows, sims):
    """The Championship race card: proof-grade clinch/elimination statuses,
    Monte Carlo odds, and the Chaos Index. Computed at build time from real
    results only — what-if picks in the browser don't touch it."""
    res = clinch_mod.analyze(games, overrides)
    teams = res["teams"]
    n_sims = sims.get("_n", 0)

    chaos_html = ""
    if sims:
        cx = chaos_mod.index(stand_rows, res, sims)
        ccolor = ("var(--accent)" if cx["score"] >= 55
                  else "var(--warn)" if cx["score"] >= 35 else "var(--dim)")
        comps = cx["components"]
        chaos_html = (
            f"<div class=chaosband>"
            f"<span class=cnum style='color:{ccolor}'>{cx['score']}</span>"
            f"<div><b>Chaos Index: {cx['label']}</b><br>"
            f"<span class=dim>race entropy {comps['entropy']:.2f} · "
            f"tie tangle {comps['tangle']:.2f} · "
            f"still alive {comps['breadth']:.2f} — "
            f"0 is a decided race, 100 is a sixteen-way pileup</span>"
            f"</div></div>")

    def prob(t):
        # proofs override estimates
        if teams[t]["status"] == "clinched":
            return 1.0
        if teams[t]["status"] == "eliminated":
            return 0.0
        return sims.get(t, {}).get("p_ccg", 0.0) if sims else None

    chips = {
        "clinched": "<span class='tag live'>clinched</span>",
        "alive": "",
    }
    rows, eliminated = [], []
    order = sorted(teams, key=lambda t: (
        -(prob(t) or 0), -teams[t]["w"], t))
    for t in order:
        i = teams[t]
        if i["status"] == "eliminated":
            eliminated.append(t)
            continue
        p = prob(t)
        bar = ""
        if p is not None:
            bar = (f"<span class=obar><i style='width:{p * 100:.1f}%;"
                   f"background:{winpct_color(p)}'></i></span>"
                   f"<b class=opct style='color:{winpct_color(p)}'>"
                   f"{fmt_prob(p)}</b>")
        bits = [chips[i["status"]]]
        if i["destiny"] and i["status"] == "alive":
            bits.append("<span class='tag destiny'>controls own destiny</span>")
        exp = sims.get(t, {}).get("exp_w")
        exptxt = (f" <span class=dim>· {exp:.1f} exp conf wins</span>"
                  if exp is not None and i["r"] > 0 else "")
        scen = ""
        if i["scenarios"]:
            scen = ("<ul class=scen>" + "".join(
                f"<li>{esc(s)}</li>" for s in i["scenarios"][:4]) + "</ul>")
        rows.append(
            f"<div class=clrow>{logo_img(t, 18)}<b>{esc(t)}</b> {bar} "
            f"{' '.join(b for b in bits if b)}"
            f"<span class=dim> {i['w']}–{9 - i['r'] - i['w']}, {i['r']} left"
            f"</span>{exptxt}{scen}</div>")
    body = chaos_html + "".join(rows)
    if eliminated:
        body += (f"<p class='dim elim'>Eliminated: "
                 f"{', '.join(esc(t) for t in eliminated)}</p>")

    notes = []
    if res["mode"] == "exact":
        notes.append(
            f"Clinch/elimination statuses are proven across all "
            f"{res['n_outcomes']:,} remaining outcomes with the full "
            f"official tiebreaker procedure; scenario lines are for the "
            f"upcoming week's games.")
    else:
        notes.append(
            "Clinch/elimination statuses are proven by strict win-count "
            "arithmetic — no tiebreaker can undo them. Exhaustive scenario "
            "math unlocks when the remaining schedule becomes enumerable.")
    if n_sims:
        notes.append(
            f"Percentages are championship-game odds from {n_sims:,} season "
            f"simulations (win probabilities from an ensemble of "
            f"{', '.join(sorted(systems))}); proofs override odds.")
        # Say it when most of the ensemble is running last season's numbers.
        # A reader is entitled to know that August odds rest on how teams
        # finished in December, and how much that has been discounted.
        stale = sorted(n for n, s in systems.items() if s.get("regressed"))
        if stale:
            notes.append(
                f"{', '.join(stale)} "
                f"{'has' if len(stale) == 1 else 'have'} not published for "
                f"this season yet, so {'its' if len(stale) == 1 else 'those'} "
                f"ratings are last season's finals, regressed toward the mean. "
                f"Simulations also draw each team's true strength rather than "
                f"trusting its rating exactly, which is why early-season odds "
                f"sit closer to even than the ratings alone would suggest.")
    return CLINCH_CARD.format(body=body, note=" ".join(notes))


CLINCH_CARD = """<div class=card id=clinchcard>
  <h2>Championship race</h2>
  {body}
  <p class=note>{note} Reflects real results only — what-if picks don't
  change it.</p>
</div>"""



BRIEF_CSS = """
.matchup { display:flex; align-items:center; gap:18px; margin:10px 0 4px;
  flex-wrap:wrap }
.side { display:flex; align-items:center; gap:12px; font-size:24px;
  font-weight:700; border-bottom:4px solid var(--line);
  padding:6px 10px 10px 2px }
.tname { letter-spacing:-.01em }
.vs { color:var(--dim); font-weight:400; font-size:18px; padding:0 6px }
.seed { display:inline-block; background:var(--accent); color:#fff;
  border-radius:6px; font-size:14px; width:22px; height:22px;
  line-height:22px; text-align:center; vertical-align:3px; margin-right:4px }
.badge { font-size:11px; border-radius:20px; padding:2px 9px;
  vertical-align:1px; font-weight:600; letter-spacing:.03em }
.badge.ok { background:#13653626; color:#136536 }
.badge.warn { background:#b4530926; color:var(--warn) }
:root { --bg:#f6f4ef; --panel:#fff; --ink:#1a1c20; --dim:#666d7b;
  --line:#e2ddd2; --accent:#0B6E77; --accent2:#003087; --warn:#b45309;
  --pctl:27%; }  /* lightness set by contrast, not taste: 32%
  put the mid-scale ambers at 3.5:1, below the 4.5:1 WCAG AA
  floor for body text. Hue still carries the meaning. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg:#14161a; --panel:#1d2026;
    --ink:#e8e6e1; --dim:#9aa0aa; --line:#2e323a; --accent:#3FC7CE;
    --accent2:#7aa2ff; --warn:#fbbf24; --pctl:63%; } }
:root[data-theme="dark"] { --bg:#14161a; --panel:#1d2026; --ink:#e8e6e1;
  --dim:#9aa0aa; --line:#2e323a; --accent:#3FC7CE; --accent2:#7aa2ff;
  --warn:#fbbf24; --pctl:63%; }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif }
header { border-bottom:4px solid var(--accent); padding:22px 20px;
  background:var(--panel) }
header h1 { margin:0; font-size:22px } header p { margin:3px 0 0;
  color:var(--dim); font-size:14px } header a { color:var(--accent2);
  text-decoration:none }
main { max-width:var(--chrome-w); margin:0 auto; padding:20px;
  display:grid; gap:18px }
.card { background:var(--panel); border:1px solid var(--line);
  border-radius:10px; padding:16px 18px }
.card h2 { margin:0 0 8px; font-size:14px; text-transform:uppercase;
  letter-spacing:.06em; color:var(--dim) }
.dim { color:var(--dim) } .note { color:var(--dim); font-size:13px }
.mark { vertical-align:-3px; margin-right:6px }
.clrow { padding:7px 0; border-bottom:1px solid var(--line); font-size:14.5px }
.clrow:last-of-type { border-bottom:none }
.obar { display:inline-block; width:100px; height:8px; background:var(--line);
  border-radius:4px; overflow:hidden; vertical-align:1px; margin:0 6px 0 8px }
.obar i { display:block; height:100%; border-radius:4px }
.opct { font-variant-numeric:tabular-nums; font-size:13.5px }
.chaosband { display:flex; align-items:center; gap:14px; border:1px solid
  var(--line); border-radius:8px; padding:10px 14px; margin-bottom:10px;
  font-size:13.5px }
.cnum { font-size:36px; font-weight:800; line-height:1 }
.tag { font-size:10.5px; border-radius:20px; padding:2px 8px; font-weight:700 }
.tag.live { background:#13653626; color:#136536 }
.tag.destiny { background:#b4530926; color:var(--warn) }
.scen { margin:5px 0 2px; padding-left:20px; font-size:13px; color:var(--dim) }
.elim { font-size:13px } ul.games { list-style:none; padding:0; margin:0 }
ul.games li { padding:5px 0; border-bottom:1px solid var(--line);
  font-size:14px } .ccgtag { color:var(--accent); font-weight:700;
  font-size:11px; text-transform:uppercase }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .tag.live { color:#4ade80;
    background:#4ade8024 } }
:root[data-theme="dark"] .tag.live { color:#4ade80; background:#4ade8024 }
"""


def _prev_week_state(games, systems, overrides, last_week):
    """Re-run the season as it stood before `last_week` so the Brief can say
    what actually changed. Same truncated-replay trick the RSS wraps use."""
    prev = [dict(g) for g in games]
    for g in prev:
        if g["week"] >= last_week and not g.get("ccg"):
            g["completed"] = False
            g["home_points"] = g["away_points"] = None
    sims = (odds_mod.simulate(prev, systems, overrides, n=4000)
            if systems else {})
    rows = tb.standings(prev, overrides)
    cl = clinch_mod.analyze(prev, overrides, budget=2)
    cx = chaos_mod.index(rows, cl, sims) if sims else None
    return {"sims": sims, "rows": rows, "chaos": cx, "clinch": cl["teams"]}


def build_brief(year, games, overrides, systems, sims, matchcard,
                canon=None):
    """The Brief: what changed this week. Deliberately not a second copy of
    The Race — this page is movement, not reference."""
    done = [g for g in games if g["completed"] and not g.get("ccg")
            and g["home_points"] is not None]
    stand_rows = tb.standings(games, overrides)
    cl = clinch_mod.analyze(games, overrides)
    cx = chaos_mod.index(stand_rows, cl, sims) if sims else None
    _now = datetime.datetime.now(datetime.timezone.utc)
    stamp = f"{_MON[_now.month - 1]} {_now.day}"
    parts = []

    if not done:
        board = sorted(((v["p_ccg"], t) for t, v in sims.items()
                        if not t.startswith("_")), reverse=True)[:3] if sims else []
        opener = min((g for g in games if g["start"]),
                     key=lambda g: g["start"], default=None)
        lede = "The season has not kicked off yet. "
        if opener:
            lede += (f"It opens {esc(pretty_date(opener['start'], 'long'))} "
                     f"with {esc(opener['away'])} at {esc(opener['home'])}. ")
        if board:
            lede += ("The models make "
                     + ", ".join(f"<b>{esc(t)}</b> {p:.0%}" for p, t in board)
                     + " the likeliest championship-game participants.")
            # The caveat belongs where the numbers are, not only on the full
            # board. In August these rest on December's ratings.
            if any(s.get("regressed") for s in systems.values()):
                lede += (" Preseason odds lean on last season's ratings, "
                         "regressed toward the mean, and allow for how much "
                         "a rating can be wrong about a team this early. ")
        parts.append(
            f"<div class=card><h2>Preseason</h2><p>{lede}</p>"
            f"<p class=note>This page fills with movement — who rose, who "
            f"fell, and what it cost them — once games are played. For the "
            f"full board any time, see <a href=race.html>The Race</a>.</p>"
            f"</div>")
        upcoming = [g for g in games if not g["completed"]][:8]
        if upcoming:
            parts.append("<div class=card><h2>First up</h2><ul class=games>"
                         + "".join(game_row(g) for g in upcoming)
                         + "</ul></div>")
        body = f"<p class=briefstamp>The Brief &middot; {esc(stamp)}</p>" + "".join(parts)
        return build_subpage(
            "The Brief", "brief", body, year, matchcard, canon=canon,
            desc=("The Big 12 championship race with the official "
                  "tiebreaking procedures applied to live results."))

    last_week = max(g["week"] for g in done)
    prev = _prev_week_state(games, systems, overrides, last_week)
    week_games = [g for g in done if g["week"] == last_week]

    lede_bits = [f"<b>Week {last_week}</b> is in the books"]
    if week_games:
        lede_bits[0] += f" &mdash; {len(week_games)} games involving Big 12 teams"
    if cx and prev["chaos"]:
        d = cx["score"] - prev["chaos"]["score"]
        if abs(d) >= 2:
            lede_bits.append(
                f"the Chaos Index {'rose' if d > 0 else 'fell'} {abs(d)} to "
                f"<b>{cx['score']}</b> ({esc(cx['label'].lower())})")
        else:
            lede_bits.append(f"the Chaos Index held at <b>{cx['score']}</b> "
                             f"({esc(cx['label'].lower())})")
    lead = [r for r in stand_rows if r["rank"] == 1]
    if lead:
        r = lead[0]
        lede_bits.append(f"<b>{esc(r['team'])}</b> leads the standings at "
                         f"{r['conf_w']}&ndash;{r['conf_l']}")
    parts.append(f"<div class=card><h2>Week {last_week}</h2>"
                 f"<p>{'; '.join(lede_bits)}.</p></div>")

    if sims and prev["sims"]:
        moves = []
        for t, v in sims.items():
            if t.startswith("_"):
                continue
            was = prev["sims"].get(t, {}).get("p_ccg")
            if was is None:
                continue
            moves.append((v["p_ccg"] - was, t, was, v["p_ccg"]))
        moves.sort(reverse=True)
        up = [m for m in moves if m[0] > 0.005][:5]
        down = [m for m in moves if m[0] < -0.005][-5:][::-1]

        def movement_rows(items):
            out = []
            for d, t, was, now in items:
                col = winpct_color(1.0 if d > 0 else 0.0)
                out.append(
                    f"<div class=clrow>{logo_img(t, 16)}<b>{esc(t)}</b> "
                    f"<span style='color:{col}'>{'+' if d > 0 else ''}"
                    f"{d * 100:.0f} pts</span> <span class=dim>"
                    f"{fmt_prob(was)} &rarr; {fmt_prob(now)} to reach the "
                    f"title game</span></div>")
            return "".join(out)

        if up or down:
            parts.append(
                "<div class=card><h2>What the week cost and bought</h2>"
                + (("<p class=note>Risers</p>" + movement_rows(up)) if up else "")
                + (("<p class=note>Fallers</p>" + movement_rows(down)) if down else "")
                + "<p class=note>Change in championship-game probability "
                  "against the same simulation run on last week's results."
                  "</p></div>")

    news = []
    for t, info in cl["teams"].items():
        before = prev["clinch"].get(t, {}).get("status")
        if info["status"] != before and info["status"] != "alive":
            verb = ("clinched a championship-game berth"
                    if info["status"] == "clinched"
                    else "was eliminated from contention")
            news.append(f"<li><b>{esc(t)}</b> {verb}.</li>")
    if news:
        parts.append("<div class=card><h2>Status changes</h2><ul>"
                     + "".join(news) + "</ul></div>")

    if week_games:
        parts.append(f"<div class=card><h2>Week {last_week} finals</h2>"
                     "<ul class=games>"
                     + "".join(game_row(g) for g in week_games[::-1])
                     + "</ul></div>")

    lev = odds_mod.leverage(sims, games) if sims else []
    if lev:
        items = "".join(
            f"<li>{logo_img(e['away'], 14)}{esc(e['away'])} at "
            f"{logo_img(e['home'], 14)}{esc(e['home'])} <span class=dim>"
            f"&mdash; {e['total'] * 100:.0f} points of championship "
            f"probability in play</span></li>" for e in lev[:3])
        parts.append(f"<div class=card><h2>What to watch next</h2>"
                     f"<ul>{items}</ul><p class=note>Full board on "
                     f"<a href=race.html>The Race</a>.</p></div>")

    body = f"<p class=briefstamp>The Brief &middot; {esc(stamp)}</p>" + "".join(parts)
    return build_subpage(
        "The Brief", "brief", body, year, matchcard, canon=canon,
        desc=("What changed in the Big 12 race this week: who rose, who fell, "
              "what clinched, and what the next slate decides — the official "
              "tiebreaking procedures applied to live results."))


SUBPAGE_EXTRA_CSS = """
table { border-collapse:collapse; width:100%; font-size:14px }
th, td { text-align:left; padding:6px 9px; border-bottom:1px solid
  var(--line); font-variant-numeric:tabular-nums }
th { font-size:11px; text-transform:uppercase; letter-spacing:.05em;
  color:var(--dim) }
thead tr th { border-bottom:2px solid var(--line) }
.teamcell { white-space:nowrap }
.briefstamp { color:var(--dim); font-size:13px; text-align:center; margin:-4px 0 2px }
tr.grpend td { border-bottom:2px solid var(--line) }
.stbl td:last-child, .stbl th:last-child { text-align:right }
.stbl td { height:38px }
.duo.even { grid-template-columns:minmax(0,1fr) minmax(0,1fr) }
.duo.even > .stack { align-content:stretch }
.duo.even .card { height:100% }
.posc { white-space:nowrap; vertical-align:top; color:var(--dim); font-variant-numeric:tabular-nums }
h3.wkhead { font-size:13px; text-transform:uppercase; letter-spacing:.05em;
  color:var(--dim); margin:16px 0 4px }
.duo { display:grid; grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);
  gap:18px; align-items:start }
.duo > .stack { display:grid; gap:18px; align-content:start; min-width:0 }
@media (max-width: 900px) {
  /* .duo.even is more specific than .duo, so it has to be named here too or
     the two boards stay side by side at 166px each on a phone. */
  .duo, .duo.even { grid-template-columns:1fr }
  .duo > .stack, .duo.even > .stack { display:contents }
  .duo.even .card { height:auto }
}
.tablescroll, .tablewrap { overflow-x:auto; position:relative }
/* Same cue as the attendance tracker's season grid: a fade at the live edge
   so a table that runs past the card does not look like it stops there. */
.scrollbox { position:relative }
.scrollbox::after { content:""; position:absolute; top:0; right:0; bottom:0;
  width:38px; pointer-events:none; opacity:1; transition:opacity .18s ease;
  background:linear-gradient(to right, transparent, var(--panel)) }
.scrollbox.at-end::after { opacity:0 }
/* Grid items default to min-width:auto, so a wide child — the
   movement chart, a table — pushes the whole page sideways on a
   phone instead of scrolling inside its own box. */
main > * { min-width:0 }
table.h2h { width:100%; table-layout:auto }
.h2h th, .h2h td { padding:6px 4px; font-size:13px; white-space:nowrap;
  text-align:center }
.h2h td.teamcell, .h2h thead th:first-child { text-align:left;
  padding-left:2px }
.h2h thead th { font-size:12px; letter-spacing:.02em }
.hatag { display:inline-block; min-width:11px; margin-right:4px;
  font-size:10px; font-weight:700; color:var(--dim); vertical-align:1px }
/* The empty cells carry meaning — a third of this grid is pairs the
   schedule never makes — so they need to be visible, not ghosts. */
.selfcell { color:var(--dim); opacity:.75;
  background:color-mix(in srgb, var(--dim) 12%, transparent) }
.nomeet { color:var(--dim); opacity:.75; font-size:15px; line-height:1 }

/* ---- season replay ---- */
#replaybar .rpline { display:flex; align-items:center; gap:10px;
  flex-wrap:wrap }
#replaybar input[type=range] { flex:1 1 200px; min-width:140px;
  accent-color:var(--accent) }
@media (max-width:640px) {
  #replaybar #rp-label { flex:1 0 100%; min-width:0 !important; order:9 }
  #replaybar #rp-now { order:8 }
}
#replaybar #rp-label { font-size:14px; color:var(--dim) }
#replaybar #rp-prev, #replaybar #rp-next { padding:6px 10px }
#replaycard.scrubbed { border-color:var(--accent) }
/* keeps its space so the slider never changes width */
#rp-now.invis { visibility:hidden }
.mv { font-size:11px; margin-left:5px; font-weight:600 }
.mv.up { color:hsl(140 60% var(--pctl)) }
.mv.down { color:hsl(6 70% var(--pctl)) }
tr.moved.up td { animation:flashup 900ms ease-out }
tr.moved.down td { animation:flashdown 900ms ease-out }
@keyframes flashup { from { background:rgba(22,140,80,.16) }
                     to { background:transparent } }
@keyframes flashdown { from { background:rgba(200,16,46,.13) }
                       to { background:transparent } }
@media (prefers-reduced-motion:reduce) {
  tr.moved.up td, tr.moved.down td { animation:none }
}

/* where a team stands in the championship race, at this point in the year */
.st-out, .teamcell.st-out { text-decoration:line-through;
  text-decoration-color:var(--dim); opacity:.62 }
.teamcell.st-in { font-weight:700 }
.teamcell.st-top { font-style:italic }

/* the replay's own controls, same chrome as the Lab's */
.wbtn { font:inherit; font-size:14px; border:1px solid var(--line);
  background:var(--panel); color:var(--ink); border-radius:8px;
  padding:6px 14px; cursor:pointer }
.wbtn:hover { border-color:var(--accent) }

/* ---- how the season moved ---- */
.bumpwrap { overflow-x:auto }
.bump { width:100%; min-width:660px; height:auto; display:block }
.bump .bgrid { stroke:var(--line); stroke-width:1 }
.bump .btick { fill:var(--dim); font-size:11px }
.bump .bnum { text-anchor:end }
.bump .bwk { text-anchor:middle }
.bump .blabel { font-size:11px; font-weight:600 }
.bump .bteam { transition:opacity .12s ease }
.bumpwrap:hover .bteam { opacity:.22 }
.bumpwrap .bteam:hover { opacity:1 }
.bumpwrap .bteam:hover polyline { stroke-width:3.6 }
"""


def build_subpage(title, active, body, year, matchcard,
                  canon=None, desc=None, head=""):
    social = ""
    if canon:
        social = f"""<link rel=canonical href="{canon}">
<meta name=description content="{esc(desc or '')}">
<meta property=og:type content=website>
<meta property=og:site_name content=Big12ology>
<meta property=og:title content="{esc(title)} — Big 12 Tiebreaker Tracker">
<meta property=og:description content="{esc(desc or '')}">
<meta property=og:url content="{canon}">
<meta property=og:image content="https://big12ology.com/tiebreaker/og.png">
<meta property=og:image:width content=1200>
<meta property=og:image:height content=630>
<meta name=twitter:card content=summary_large_image>"""
    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{esc(title)} — Big 12 Tiebreaker Tracker</title>
{social}
<link rel=icon type=image/svg+xml href="{BASE}favicon.svg">
<link rel=icon type=image/png sizes=32x32 href="{BASE}favicon-32.png">
<link rel=apple-touch-icon href="{BASE}favicon-180.png">
<script>(function(){{try{{var t=localStorage.getItem("b12-theme");if(t==="light"||t==="dark"){{document.documentElement.setAttribute("data-theme",t);document.documentElement.style.colorScheme=t;}}else{{document.documentElement.style.colorScheme="light dark";}}}}catch(e){{}}}})();</script>
<link rel=stylesheet href="{BASE}{asset_v("brand.css")}">
<script defer src="{BASE}{asset_v("theme.js")}"></script>
<link rel=alternate type=application/rss+xml href={BASE}feed.xml>
<style>{BRIEF_CSS}{SUBPAGE_EXTRA_CSS}</style>
<script defer src="{BASE}{asset_v("scrollcue.js")}"></script>{head}</head><body>
<a class=skip-link href="#main">Skip to content</a>
<nav class=b12-topbar><a class=b12-brand href="https://big12ology.com/" aria-label="Big12ology home"><picture><source srcset="{BASE}brand/big12ology-compact-dark.svg" media="(prefers-color-scheme: dark)"><img src="{BASE}brand/big12ology-compact-dark.svg" alt="Big12ology"></picture></a>
<a class=on href="https://big12ology.com/tiebreaker/">Tiebreaker</a><a href="https://big12ology.com/attendance/">Attendance</a><span class=b12-right><span class=b12-theme></span></span></nav>
{tracker_top(year, active, matchcard)}
{body}
</main>
<footer class=b12-footer>A Big12ology project · not affiliated with the
Big 12 Conference · <a href=data.json>data.json</a> ·
<a href=standings.csv>standings.csv</a> ·
<a href={BASE}feed.xml>RSS</a> ·
<a href="https://big12ology.com/privacy">Privacy</a></footer>
<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "355e765d921e4b36ad2bf78d509eae6c"}}'></script>
</body></html>"""


def build_schedule_page(games, ctx):
    rem = sorted((g for g in games if not g["completed"]),
                 key=lambda g: (g["week"], g["start"] or ""))
    by_week = {}
    for g in rem:
        by_week.setdefault(g["week"], []).append(g)
    up = ""
    for wk in sorted(by_week):
        up += (f"<h3 class=wkhead>Week {wk}</h3><ul class=games>"
               + "".join(game_row(g) for g in by_week[wk]) + "</ul>")
    upcard = (f"<div class=card><h2>Every remaining game</h2>{up}</div>"
              if up else "")
    done = [g for g in games if g["completed"]
            and g["home_points"] is not None]
    done.sort(key=lambda g: g["start"] or "", reverse=True)
    rescard = ""
    if done:
        rescard = ("<div class=card><h2>Results, newest first</h2>"
                   "<ul class=games>"
                   + "".join(game_row(g) for g in done[:40])
                   + "</ul></div>")
    return ctx["h2hcard"] + ctx["soscard"] + upcard + rescard


def build_race_page(ctx):
    """Two columns on wide screens: the full race board on the left, the
    week's leverage and the model scorecard on the right where they're
    seen without scrolling past sixteen teams."""
    return (f"<div class=duo><div class=stack>{ctx['clinchcard']}</div>"
            f"<div class=stack>{ctx['levcard']}{ctx['modelcard']}</div>"
            f"</div>")


def default_season(today=None):
    today = today or datetime.date.today()
    return today.year if today.month >= 6 else today.year - 1


def esc(s):
    return html.escape(str(s))


_MON = ["January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December"]
_DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
        "Sunday"]


def pretty_date(iso, style="short"):
    """Dates for prose. The year is omitted — readers know what season they
    are looking at. style: 'short' (Sat, Sep 12) or 'long' (Saturday,
    August 29)."""
    if not iso:
        return ""
    try:
        d = datetime.date.fromisoformat(iso[:10])
    except ValueError:
        return iso[:10]
    if style == "long":
        return f"{_DOW[d.weekday()]}, {_MON[d.month - 1]} {d.day}"
    return f"{_DOW[d.weekday()][:3]}, {_MON[d.month - 1][:3]} {d.day}"


def game_row(g):
    hm, am = logo_img(g["home"], 16), logo_img(g["away"], 16)
    if g["completed"] and g["home_points"] is not None:
        hw = g["home_points"] > g["away_points"]
        home = f"<b>{esc(g['home'])} {g['home_points']}</b>" if hw \
            else f"{esc(g['home'])} {g['home_points']}"
        away = f"<b>{esc(g['away'])} {g['away_points']}</b>" if not hw \
            else f"{esc(g['away'])} {g['away_points']}"
        score = f"{am}{away} <span class=dim>at</span> {hm}{home}"
        cls = "done"
    else:
        when = pretty_date(g["start"])
        score = (f"{am}{esc(g['away'])} <span class=dim>at</span> "
                 f"{hm}{esc(g['home'])} <span class=dim>({when})</span>")
        cls = "upcoming"
    tag = "" if g["conference_game"] else " <span class=dim>(non-conf)</span>"
    if g.get("ccg"):
        tag = " <span class=ccgtag>Championship</span>"
    return f"<li class={cls}>{score}{tag}</li>"


def render(year, games):
    overrides = tb.load_overrides()
    teams = load_teams()
    systems = load_ratings(year).get("systems", {})
    closing_lines = load_lines(year)
    track, _wk = next_conf_week_ids(games)
    sims = (odds_mod.simulate(games, systems, overrides, track=track)
            if systems else {})
    favorites = favorites_for(games, systems)
    models = [{"name": n, "year": systems[n].get("year")}
              for n in MODEL_ORDER if n in favorites]
    rows = tb.standings(games, overrides)
    display_rows = pad_standings(rows, games)
    ccg = tb.championship(games, overrides)
    reg = [g for g in games if g["conference_game"] and not g.get("ccg")]
    played = [g for g in games if g["completed"] and g["home_points"] is not None]
    remaining = [g for g in games if not g["completed"]]
    reg_played = [g for g in reg if g["completed"] and g["home_points"] is not None]
    now = datetime.datetime.now(datetime.timezone.utc)

    # -- matchup card -----------------------------------------------------
    if not rows:
        card = ("<div class=card id=matchcard><h2>Season not started</h2>"
                "<p>No conference results yet. The projected championship "
                "matchup will appear after the first Big 12 game.</p></div>")
    else:
        badge = ("<span class='badge ok'>resolved</span>" if ccg["resolved"]
                 else "<span class='badge warn'>needs SportSource rating "
                      "or coin toss</span>")
        status = "Final championship matchup" if not remaining \
            else "Projected championship matchup (if the season ended today)"
        note = f"<p class=note>{esc(ccg['note'])}</p>" if ccg.get("note") else ""
        panels = []
        for seed, t in ((1, ccg["seed1"]), (2, ccg["seed2"])):
            c = team_color(teams, t)
            panels.append(
                f"<div class=side style='border-bottom-color:{c}'>"
                f"{logo_img(t, 56)}<div><span class=seed>{seed}</span> "
                f"<span class=tname>{esc(t)}</span></div></div>")
        card = (f"<div class=card id=matchcard><h2>{status} {badge}</h2>"
                f"<div class=matchup>{panels[0]}<span class=vs>vs</span>"
                f"{panels[1]}</div>{note}</div>")

    # -- standings table --------------------------------------------------
    body = []
    tie_colors = {}
    for r in display_rows:
        tg = r["tie_group"]
        if tg and tg not in tie_colors:
            tie_colors[tg] = len(tie_colors) % 4
        cls = f"tie{tie_colors[tg]}" if tg else ""
        mark = f"<sup>{list(tie_colors).index(tg) + 1}</sup>" if tg else ""
        p = tb.pct(r["conf_w"], r["conf_l"])
        c = team_color(teams, r["team"])
        body.append(
            f"<tr class='{cls}' data-rank={r['rank'] or 99} "
            f"data-w={r['conf_w']} data-l={r['conf_l']}>"
            f"<td>{r['rank'] or '—'}</td>"
            f"<td class=teamcell><span class=cbar style='background:{c}'>"
            f"</span>{logo_img(r['team'])}{esc(r['team'])}{mark}</td>"
            f"<td>{r['conf_w']}–{r['conf_l']}</td>"
            + ("<td>—</td>" if p is None else
               f"<td style='color:{winpct_color(p)}'>{p:.3f}</td>") +
            f"<td class=dimcell>{r['nonconf_w']}–{r['nonconf_l']}</td>"
            f"<td>{r['overall_w']}–{r['overall_l']}</td></tr>")
    sorter = ("<div class=sorter>Sort: "
              "<button class='on' id=sort-pct>Win % (official)</button>"
              "<button id=sort-raw>Raw wins</button></div>")
    # display_rows always carries all sixteen teams, so the table is never
    # hidden — the preseason view is a legitimate 0-0 board.
    table = ("<div id=tablewrap class='tablescroll scrollbox'>" + sorter +
             "<table><thead><tr><th></th><th>Team</th><th>Conf</th>"
             "<th>Pct</th><th>Non-conf</th><th>Overall</th></tr></thead>"
             "<tbody id=stand>"
             + "".join(body) + "</tbody></table></div>")

    # -- tiebreaker narratives ---------------------------------------------
    stories = []
    n = 0
    for r in rows:
        if r["log"] is not None:
            n += 1
            tie_names = r["tie_group"].replace("+", ", ")
            lines = "".join(
                f"<li class=seeded>{esc(x)}</li>" if "seeded." in x
                else f"<li>{esc(x)}</li>" for x in r["log"])
            stories.append(
                f"<details {'open' if n == 1 else ''}><summary><sup>{n}</sup> "
                f"How the {esc(tie_names)} tie breaks</summary>"
                f"<ol class=steps>{lines}</ol></details>")
    stories = "".join(stories) or "<p class=dim>No ties in the standings.</p>"

    # -- games -------------------------------------------------------------
    last_done = played[-12:][::-1]
    next_up = remaining[:12]
    results = "".join(game_row(g) for g in last_done) or "<li class=dim>None yet.</li>"
    upcoming = "".join(game_row(g) for g in next_up) or "<li class=dim>None — regular season complete.</li>"

    # -- client payload for the what-if simulator ---------------------------
    team_meta = {}
    for t, k in TEAM_KEY.items():
        team_meta[t] = {
            "logo": f"{BASE}logos/{k}.{'png' if k == 'byu' else 'svg'}",
            "color": team_color(teams, t),
            "abbr": (teams.get(t) or {}).get("abbr") or t,
        }
    payload = json.dumps({
        "year": year,
        "teams": team_meta,
        "games": games,
        "favorites": favorites,
        "models": models,
        "overrides": overrides,
        # A finished season is safe to rewrite: nothing on the page claims to
        # be live, so every game becomes a lever. The season in progress
        # stays locked to what actually happened.
        "unlocked": year != LIVE_YEAR,
    }).replace("</", "<\\/")

    unlocked = year != LIVE_YEAR
    n_remaining = len([g for g in games
                       if not g.get("ccg")
                       and (unlocked or not g["completed"])])
    model_opts = "".join(
        f"<option value='{esc(m['name'])}'>{esc(m['name'])}"
        f" ({esc(m['year'])})</option>" for m in models)
    whatif = "" if not n_remaining else WHATIF_CARD.format(
        n=n_remaining, model_opts=model_opts,
        blurb=("rewrite any of the {n} games this season and watch the "
               "tiebreakers answer" if unlocked else
               "pick winners for the {n} remaining games, conference and "
               "non-conference").format(n=n_remaining),
        modelrow=("" if not models else
                  '<label class=dim for=w-model>Model</label>'
                  f'<select id=w-model class=wbtn>{model_opts}</select>'
                  '<button id=w-fav class=wbtn>Use favorites for all</button>'),
        clearlabel=("Reset to what happened" if unlocked else "Clear picks"))

    standcard = STAND_CARD.format(
        played=len(reg_played), total=len(reg), table=table, stories=stories)

    site_url = "https://big12ology.com/tiebreaker/"
    page = TEMPLATE.format(
        year=year,
        base=BASE,
        v_engine=asset_v("engine.js"),
        v_pct=asset_v("pct.js"),
        v_scroll=asset_v("scrollcue.js"),
        v_brand=asset_v("brand.css"),
        v_theme=asset_v("theme.js"),
        v_app=asset_v("app.js"),
        canon=(f"{site_url}lab.html" if year == LIVE_YEAR
               else f"{site_url}{year}/lab.html"),
        top=tracker_top(year, "tracker", card),
        whatif=whatif,
        standcard=standcard,
        payload=payload,
        updated=f"{_MON[now.month - 1]} {now.day} at "
                f"{now.strftime('%H:%M')} UTC",
    )
    ctx = {
        "clinchcard": clinch_card(games, overrides, systems, rows, sims),
        "levcard": leverage_card(games, sims) if sims else "",
        "soscard": sos_card(games, systems),
        "modelcard": scorecard_card(games, systems, closing_lines),
        "h2hcard": h2h_card(games, teams, rows),
        "matchcard": card,
        "standingspage": standings_page(games, overrides, display_rows, teams),
        "sims": sims,
    }
    return page, ctx


STAND_CARD = """<div class="card standcard">
  <h2>Conference standings · {played} of {total} games played
  <span id=w-chip class=wchip hidden>what-if</span></h2>
  <progress max={total} value={played}></progress>
  {table}
  <div style="margin-top:14px" id=stories>{stories}</div>
  <p class=note>The conference breaks ties only to name the two
  championship-game participants; positions below that stay tied in its
  official standings. This table sorts every tie for readability — see
  <a href=standings.html>The Standings</a> for both boards side by side.</p>
</div>"""


WHATIF_CARD = """<div class=card id=whatif>
  <h2>What if&hellip; <span class=dim style="text-transform:none">{blurb}</span></h2>
  <div class=wcontrols>
    {modelrow}
    <button id=w-clear class=wbtn>{clearlabel}</button>
    <span id=w-count class=dim></span>
  </div>
  <div id=wgames></div>
  <p class=note id=w-note></p>
</div>"""


TEMPLATE = """<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>The Lab · what-if simulator — Big 12 Tiebreaker Tracker</title>
<meta name=description content="The official Big 12 tiebreaking procedures applied to live results after every game — projected championship matchup, tie narratives, and a what-if simulator.">
<link rel=canonical href="{canon}">
<link rel=icon type=image/svg+xml href="{base}favicon.svg">
<link rel=icon type=image/png sizes=32x32 href="{base}favicon-32.png">
<link rel=apple-touch-icon href="{base}favicon-180.png">
<meta property=og:type content=website>
<meta property=og:site_name content=Big12ology>
<meta property=og:title content="The Lab · Big 12 what-if simulator — {year}">
<meta property=og:description content="The official Big 12 tiebreaking procedures applied to live results after every game — plus a what-if simulator you can run on this season or replay against a finished one.">
<meta property=og:url content="{canon}">
<meta property=og:image content="https://big12ology.com/tiebreaker/og.png">
<meta property=og:image:width content=1200>
<meta property=og:image:height content=630>
<meta name=twitter:card content=summary_large_image>
<script>(function(){{try{{var t=localStorage.getItem("b12-theme");if(t==="light"||t==="dark"){{document.documentElement.setAttribute("data-theme",t);document.documentElement.style.colorScheme=t;}}else{{document.documentElement.style.colorScheme="light dark";}}}}catch(e){{}}}})();</script>
<link rel=stylesheet href="{base}{v_brand}">
<script defer src="{base}{v_theme}"></script>
<link rel=alternate type=application/rss+xml title="Big 12 Tiebreaker Tracker" href={base}feed.xml>
<style>
:root {{
  --bg: #f6f4ef; --panel: #ffffff; --ink: #1a1c20; --dim: #666d7b;
  --line: #e2ddd2; --accent: #0B6E77; --accent2: #003087;
  --ok: #136536; --warn: #b45309;
  --tie0: #fff3f4; --tie1: #eef4ff; --tie2: #f0fdf4; --tie3: #fefce8;
  --pctl: 27%;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14161a; --panel: #1d2026; --ink: #e8e6e1; --dim: #9aa0aa;
    --line: #2e323a; --accent: #3FC7CE; --accent2: #7aa2ff;
    --ok: #4ade80; --warn: #fbbf24;
    --tie0: #2a1d20; --tie1: #1d2330; --tie2: #1d2a22; --tie3: #2a281d;
    --pctl: 63%;
  }}
  .mark {{ background: #f0ede6; border-radius: 4px; padding: 2px; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}

.whyout {{ margin-top: 6px; }}
.whyout p {{ font-size: 15px; margin: 8px 0; }}
.whyhead {{ display: flex; align-items: center; gap: 4px; font-weight: 700;
  font-size: 17px; margin: 6px 0; }}
.evline {{ display: block; background: var(--bg); border-left: 3px solid
  var(--accent); border-radius: 4px; padding: 6px 10px; margin: 6px 0;
  font-size: 13.5px; color: var(--dim); }}
.ladder {{ margin: 10px 0 4px; }}
.roundhead {{ font-size: 13px; text-transform: uppercase; letter-spacing:
  .05em; color: var(--dim); font-weight: 600; margin: 16px 0 6px; }}
.lstep {{ display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px solid
  var(--line); align-items: baseline; }}
.lstep:last-child {{ border-bottom: none; }}
.lstep.skip {{ opacity: .45; }}
.lletter {{ flex: 0 0 22px; height: 22px; border-radius: 6px; background:
  var(--line); color: var(--ink); font-size: 12px; font-weight: 700;
  text-align: center; line-height: 22px; align-self: flex-start; }}
.lbody {{ flex: 1; min-width: 0; }}
.lname {{ font-size: 14px; font-weight: 600; }}
.lchip {{ font-size: 11px; border-radius: 20px; padding: 2px 9px;
  font-weight: 700; letter-spacing: .03em; margin-left: 8px;
  vertical-align: 1px; white-space: nowrap; }}
.lchip.win {{ background: color-mix(in srgb, var(--ok, #136536) 15%,
  transparent); color: #136536; }}
.lchip.lose {{ background: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--accent); }}
.lchip.none {{ background: color-mix(in srgb, var(--dim) 14%, transparent);
  color: var(--dim); }}
.lchip.skip {{ background: none; border: 1px solid var(--line);
  color: var(--dim); font-weight: 500; }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) .lchip.win {{ color: #4ade80;
    background: color-mix(in srgb, #4ade80 14%, transparent); }}
}}
:root[data-theme="dark"] .lchip.win {{ color: #4ade80;
  background: color-mix(in srgb, #4ade80 14%, transparent); }}
.lstep .evline {{ margin: 5px 0 0; }}
main {{ max-width: var(--chrome-w); margin: 0 auto; padding: 20px;
  display: grid; gap: 20px; }}
main > .card, main > .cols {{ max-width: 880px; width: 100%;
  margin: 0 auto; }}
.duo {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 20px; align-items: start; }}
.duo > .stack {{ display: grid; gap: 20px; align-content: start;
  min-width: 0; }}
@media (max-width: 1023px) {{
  .duo {{ grid-template-columns: minmax(0, 1fr); }}
  /* flatten the columns so cards can interleave in reading order */
  .duo > .stack {{ display: contents; }}
  #whatif {{ order: 1; }}
  .standcard {{ order: 2; }}
  #teamwhy {{ order: 3; }}
  .rules {{ order: 4; }}
}}
.card {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 18px 20px; }}
.card h2 {{ margin: 0 0 10px; font-size: 15px; text-transform: uppercase;
  letter-spacing: .06em; color: var(--dim); font-weight: 600; }}

.matchup {{ display: flex; align-items: center; gap: 18px; margin: 10px 0 4px;
  flex-wrap: wrap; }}
.side {{ display: flex; align-items: center; gap: 12px; font-size: 24px;
  font-weight: 700; border-bottom: 4px solid var(--line);
  padding: 6px 10px 10px 2px; }}
.tname {{ letter-spacing: -.01em; }}
.vs {{ color: var(--dim); font-weight: 400; font-size: 18px; padding: 0 6px; }}
.mark {{ vertical-align: -3px; margin-right: 7px; object-fit: contain; }}
.nomark {{ display: inline-block; width: 16px; height: 16px;
  line-height: 16px; text-align: center; border-radius: 4px;
  background: color-mix(in srgb, var(--dim) 18%, transparent);
  color: var(--dim); font-weight: 700; font-size: 12px; cursor: help; }}
.teamcell {{ white-space: nowrap; }}
.cbar {{ display: inline-block; width: 4px; height: 16px; border-radius: 2px;
  margin-right: 8px; vertical-align: -2px; }}
.seed {{ display: inline-block; background: var(--accent); color: #fff;
  border-radius: 6px; font-size: 14px; width: 22px; height: 22px;
  line-height: 22px; text-align: center; vertical-align: 3px; margin-right: 4px; }}
.badge {{ font-size: 11px; border-radius: 20px; padding: 2px 9px;
  vertical-align: 1px; font-weight: 600; letter-spacing: .03em; }}
.badge.ok {{ background: color-mix(in srgb, var(--ok) 15%, transparent); color: var(--ok); }}
.badge.warn {{ background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn); }}
.note {{ color: var(--dim); font-size: 14px; margin: 6px 0 0; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line);
  font-variant-numeric: tabular-nums; }}
th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--dim); }}
.dimcell {{ color: var(--dim); }}
tr.tie0 td {{ background: var(--tie0); }}
tr.tie1 td {{ background: var(--tie1); }}
tr.tie2 td {{ background: var(--tie2); }}
tr.tie3 td {{ background: var(--tie3); }}
sup {{ color: var(--accent); font-weight: 700; }}
details {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px;
  margin: 8px 0; background: var(--panel); }}
summary {{ cursor: pointer; font-weight: 600; }}
.steps {{ margin: 10px 0 4px; padding-left: 22px; }}
.steps li {{ margin: 6px 0; font-size: 14px; }}
.steps li.seeded {{ font-weight: 700; }}
.steps li.seeded::marker {{ font-weight: 700; }}
.cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
@media (max-width: 700px) {{ .cols {{ grid-template-columns: 1fr; }} }}
ul.games {{ list-style: none; padding: 0; margin: 0; }}
ul.games li {{ padding: 6px 0; border-bottom: 1px solid var(--line); font-size: 14px; }}
.dim {{ color: var(--dim); }}
.ccgtag {{ color: var(--accent); font-weight: 700; font-size: 12px;
  text-transform: uppercase; }}
.rules ol {{ padding-left: 22px; }} .rules li {{ margin: 7px 0; font-size: 14px; }}
progress {{ width: 100%; height: 6px; accent-color: var(--accent); }}
.sorter {{ font-size: 13px; color: var(--dim); margin: 10px 0 6px; }}
.sorter button {{ font: inherit; border: 1px solid var(--line); background: none;
  color: var(--dim); border-radius: 20px; padding: 3px 12px; margin-left: 6px;
  cursor: pointer; }}
.sorter button.on {{ background: var(--accent); border-color: var(--accent);
  color: #fff; }}
.wchip {{ background: var(--accent2); color: #fff; font-size: 11px;
  border-radius: 20px; padding: 2px 9px; font-weight: 600;
  letter-spacing: .03em; vertical-align: 1px; text-transform: none; }}
.wcontrols {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 10px; }}
.wbtn {{ font: inherit; font-size: 14px; border: 1px solid var(--line);
  background: var(--panel); color: var(--ink); border-radius: 8px;
  padding: 6px 14px; cursor: pointer; }}
.wbtn:hover {{ border-color: var(--accent); }}
#wgames details {{ padding: 8px 12px; }}
#wgames summary {{ font-size: 14px; }}
main > *, .duo > *, .cols > * {{ min-width: 0; }}
.tablescroll {{ overflow-x: auto; position: relative; }}
.scrollbox {{ position: relative; }}
.scrollbox::after {{ content: ""; position: absolute; top: 0; right: 0;
  bottom: 0; width: 38px; pointer-events: none; opacity: 1;
  transition: opacity .18s ease;
  background: linear-gradient(to right, transparent, var(--panel)); }}
.scrollbox.at-end::after {{ opacity: 0; }}
.wgame {{ display: flex; align-items: center; gap: 8px; padding: 5px 0;
  border-bottom: 1px solid var(--line); flex-wrap: wrap; }}
.wgame .pick {{ flex: 1 1 auto; min-width: 0; }}
.wgame:last-child {{ border-bottom: none; }}
.wgame .at {{ color: var(--dim); font-size: 12px; }}
.wgame .wdate {{ color: var(--dim); font-size: 12px; margin-left: auto; }}
.nctag {{ color: var(--dim); font-size: 10.5px; border: 1px solid var(--line);
  border-radius: 20px; padding: 1px 7px; text-transform: uppercase;
  letter-spacing: .04em; }}
.tag {{ font-size: 11px; border-radius: 20px; padding: 2px 9px;
  font-weight: 700; letter-spacing: .03em; white-space: nowrap; }}
.tag.live {{ background: color-mix(in srgb, var(--ok, #136536) 15%,
  transparent); color: #136536; }}
.tag.out {{ background: color-mix(in srgb, var(--dim) 14%, transparent);
  color: var(--dim); }}
.tag.alive {{ background: color-mix(in srgb, var(--accent2) 14%,
  transparent); color: var(--accent2); }}
.tag.destiny {{ background: color-mix(in srgb, var(--warn) 15%, transparent);
  color: var(--warn); }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) .tag.live {{ color: #4ade80;
    background: color-mix(in srgb, #4ade80 14%, transparent); }}
}}
:root[data-theme="dark"] .tag.live {{ color: #4ade80;
  background: color-mix(in srgb, #4ade80 14%, transparent); }}
.clrow {{ padding: 8px 0; border-bottom: 1px solid var(--line);
  font-size: 15px; }}
.obar {{ display: inline-block; width: 110px; height: 8px;
  background: var(--line); border-radius: 4px; overflow: hidden;
  vertical-align: 1px; margin: 0 6px 0 8px; }}
.chaosband {{ display: flex; align-items: center; gap: 14px;
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px;
  margin-bottom: 12px; font-size: 14px; }}
.cnum {{ font-size: 38px; font-weight: 800; line-height: 1;
  font-variant-numeric: tabular-nums; }}
.obar i {{ display: block; height: 100%; border-radius: 4px; }}
.opct {{ font-variant-numeric: tabular-nums; font-size: 14px; }}
.clrow:last-of-type {{ border-bottom: none; }}
.scen {{ margin: 6px 0 2px; padding-left: 22px; font-size: 13.5px;
  color: var(--dim); }}
.scen li {{ margin: 3px 0; }}
.elim {{ font-size: 13.5px; margin: 10px 0 0; }}
.pick {{ font: inherit; font-size: 13.5px; display: inline-flex;
  align-items: center; gap: 6px; border: 1px solid var(--line);
  background: var(--panel); color: var(--ink); border-radius: 8px;
  padding: 4px 10px; cursor: pointer; min-width: 150px; }}
.pick img {{ margin: 0; }}
.pick .star {{ color: var(--warn); font-size: 12px; margin-left: auto; }}
.pick.sel {{ font-weight: 700; }}
/* the result that actually happened, until you overrule it */
.pick.stands {{ border-color: var(--dim); border-style: dashed;
  color: var(--ink); }}
@media (max-width: 700px) {{
  .pick {{ flex: 1 1 40%; min-width: 0; }}
  .wgame .wdate {{ margin-left: 0; }}
}}
</style>
</head>
<body>
<a class=skip-link href="#main">Skip to content</a>
<nav class=b12-topbar>
  <a class=b12-brand href="https://big12ology.com/" aria-label="Big12ology home"><picture><source srcset="{base}brand/big12ology-compact-dark.svg" media="(prefers-color-scheme: dark)"><img src="{base}brand/big12ology-compact-dark.svg" alt="Big12ology"></picture></a>
  <a class=on href="https://big12ology.com/tiebreaker/">Tiebreaker</a>
  <a href="https://big12ology.com/attendance/">Attendance</a>
  <span class=b12-right><span class=b12-theme></span></span>
</nav>
{top}

<div class=duo>
<div class=stack>

{whatif}

</div>
<div class=stack>

{standcard}

<div class=card id=teamwhy>
  <h2>Why is my team where they are?
  <span id=w-chip2 class=wchip hidden>what-if</span></h2>
  <div class=wcontrols>
    <label class=dim for=team-sel>Team</label>
    <select id=team-sel class=wbtn><option value="">Choose a team&hellip;</option></select>
  </div>
  <div id=team-out class=whyout></div>
  <p class=note>Follows your what-if picks when they're active. Full
  walkthrough of the procedure:
  <a href=how.html>The Rules</a>.</p>
</div>
</div>
</div>
</main>
<script id=payload type=application/json>{payload}</script>
<script src={base}{v_engine}></script>
<script defer src={base}{v_scroll}></script>
<script src={base}{v_pct}></script>
<script src={base}{v_app}></script>
<footer class=b12-footer>
  Results from <a href="https://collegefootballdata.com">collegefootballdata.com</a> ·
  procedure per the <a
  href="https://s3.amazonaws.com/big12sports.com/documents/2025/11/4/Big_12_Football_2024_Tiebreaker_Policy.pdf">official
  Big 12 tiebreaker policy</a> · marks via Wikimedia Commons (provenance in
  <a href="{base}logos/SOURCES.json">SOURCES.json</a>) · last updated {updated}.<br>
  A Big12ology project · not affiliated with the Big 12 Conference; conference
  and team marks belong to their owners and appear for identification only.<br>
  <a href="https://github.com/big12ology">GitHub</a> ·
  <a href={base}feed.xml>RSS</a> ·
  <a href=./>The Brief</a> ·
  <a href=history.html>The Archive</a> ·
  <a href=data.json>Data</a> ·
  <a href="https://big12ology.com/privacy">Privacy</a> ·
  <a href="mailto:dept@big12ology.com">dept@big12ology.com</a>
</footer>
<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "355e765d921e4b36ad2bf78d509eae6c"}}'></script>
</body>
</html>
"""


EXPLAINER = """<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>How the Big 12 tiebreakers work — Big12ology</title>
<meta name=description content="A plain-English walkthrough of the official Big 12 football tiebreaking procedures, with the 2024 four-way tie worked step by step.">
<link rel=canonical href="https://big12ology.com/tiebreaker/how.html">
<link rel=icon type=image/svg+xml href="{base}favicon.svg">
<link rel=icon type=image/png sizes=32x32 href="{base}favicon-32.png">
<link rel=apple-touch-icon href="{base}favicon-180.png">
<script>(function(){{try{{var t=localStorage.getItem("b12-theme");if(t==="light"||t==="dark"){{document.documentElement.setAttribute("data-theme",t);document.documentElement.style.colorScheme=t;}}else{{document.documentElement.style.colorScheme="light dark";}}}}catch(e){{}}}})();</script>
<link rel=stylesheet href="{base}{v_brand}">
<script defer src="{base}{v_theme}"></script>
<meta property=og:type content=article>
<meta property=og:site_name content=Big12ology>
<meta property=og:title content="How the Big 12 tiebreakers actually work">
<meta property=og:description content="The official procedure in plain English, plus the 2024 four-way tie worked step by step by the tracker's rules engine.">
<meta property=og:url content="https://big12ology.com/tiebreaker/how.html">
<meta property=og:image content="https://big12ology.com/tiebreaker/og.png">
<meta name=twitter:card content=summary_large_image>
<style>
:root {{
  --bg: #f6f4ef; --panel: #ffffff; --ink: #1a1c20; --dim: #666d7b;
  --line: #e2ddd2; --accent: #0B6E77; --accent2: #003087;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14161a; --panel: #1d2026; --ink: #e8e6e1; --dim: #9aa0aa;
    --line: #2e323a; --accent: #3FC7CE; --accent2: #7aa2ff;
  }}
}}
:root[data-theme="dark"] {{
    --bg: #14161a; --panel: #1d2026; --ink: #e8e6e1; --dim: #9aa0aa;
    --line: #2e323a; --accent: #3FC7CE; --accent2: #7aa2ff;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 17px/1.65 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}

main {{ max-width: var(--chrome-w); margin: 0 auto;
  padding: 26px 20px 48px; }}
main > p, main > ol, main > ul, main > h2, main > h3, main > table,
main > .aside, main > .worked, main > .backlink {{ max-width: 840px; }}
h2 {{ font-size: 21px; margin: 34px 0 10px; }}
h3 {{ font-size: 17px; margin: 22px 0 8px; }}
p, li {{ font-size: 16.5px; }}
a {{ color: var(--accent2); }}
.lead {{ font-size: 18px; }}
ol.rules > li {{ margin: 10px 0; }}
ol.rules b {{ color: var(--ink); }}
.aside {{ background: var(--panel); border: 1px solid var(--line);
  border-left: 4px solid var(--accent); border-radius: 8px;
  padding: 12px 16px; font-size: 15px; color: var(--dim); margin: 16px 0; }}
.worked {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 18px 22px; margin: 16px 0; }}
.worked ol {{ padding-left: 20px; }}
.worked li {{ margin: 8px 0; font-size: 15px; }}
.worked li.seeded {{ font-weight: 700; }}
.worked .meta {{ color: var(--dim); font-size: 14px; margin: 0 0 10px; }}
table.models {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
table.models th, table.models td {{ text-align: left; padding: 8px 10px;
  border-bottom: 1px solid var(--line); font-size: 15px; vertical-align: top; }}
table.models th {{ font-size: 12px; text-transform: uppercase;
  letter-spacing: .05em; color: var(--dim); }}
.backlink {{ display: inline-block; margin-top: 8px; }}
.card {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 16px 20px; margin: 0 0 18px; }}
.card h2 {{ margin: 0 0 8px; font-size: 14px; text-transform: uppercase;
  letter-spacing: .06em; color: var(--dim); }}
.matchup {{ display: flex; align-items: center; gap: 18px;
  margin: 10px 0 4px; flex-wrap: wrap; }}
.side {{ display: flex; align-items: center; gap: 12px; font-size: 24px;
  font-weight: 700; border-bottom: 4px solid var(--line);
  padding: 6px 10px 10px 2px; }}
.tname {{ letter-spacing: -.01em; }}
.vs {{ color: var(--dim); font-weight: 400; font-size: 18px; padding: 0 6px; }}
.seed {{ display: inline-block; background: var(--accent); color: #fff;
  border-radius: 6px; font-size: 14px; width: 22px; height: 22px;
  line-height: 22px; text-align: center; vertical-align: 3px;
  margin-right: 4px; }}
.badge {{ font-size: 11px; border-radius: 20px; padding: 2px 9px;
  vertical-align: 1px; font-weight: 600; letter-spacing: .03em; }}
.badge.ok {{ background: #13653626; color: #136536; }}
.badge.warn {{ background: #b4530926; color: #b45309; }}
.mark {{ vertical-align: -3px; margin-right: 7px; object-fit: contain; }}
.nomark {{ display: inline-block; width: 16px; height: 16px;
  line-height: 16px; text-align: center; border-radius: 4px;
  background: color-mix(in srgb, var(--dim) 18%, transparent);
  color: var(--dim); font-weight: 700; font-size: 12px; cursor: help; }}
.dim {{ color: var(--dim); }}
.note {{ color: var(--dim); font-size: 13px; }}


</style>
</head>
<body>
<a class=skip-link href="#main">Skip to content</a>
<nav class=b12-topbar>
  <a class=b12-brand href="https://big12ology.com/" aria-label="Big12ology home"><picture><source srcset="{base}brand/big12ology-compact-dark.svg" media="(prefers-color-scheme: dark)"><img src="{base}brand/big12ology-compact-dark.svg" alt="Big12ology"></picture></a>
  <a class=on href="https://big12ology.com/tiebreaker/">Tiebreaker</a>
  <a href="https://big12ology.com/attendance/">Attendance</a>
  <span class=b12-right><span class=b12-theme></span></span>
</nav>
{top}

<p class=lead>Sixteen teams, nine conference games each, no round robin —
which means two teams can finish with identical records having played
completely different schedules. The Big 12 sends its two best conference
winning percentages to the championship game, and when teams tie, a strict
seven-step procedure decides who goes. This site runs that procedure,
verbatim, after every game.</p>

<h2>The two golden rules</h2>
<p>Before any step-counting, two structural rules do a lot of work:</p>
<ul>
  <li><b>Win percentage, not raw wins.</b> The goal is the two best conference
  <i>winning percentages</i> — mid-season, a 5–1 team outranks a 6–2 team.
  (The standings table has a toggle if you want to see it both ways.)</li>
  <li><b>A two-way tie for first between teams that met is no tie at all.</b>
  Both are in the championship game; the head-to-head winner is the #1 seed.
  The procedure below is for everything messier.</li>
</ul>

<h2>The seven steps</h2>
<p>Applied in order; the first step that separates anyone wins. With three or
more tied teams, the moment one team is seeded, the survivors go back to the
top and start over (dropping to the two-team rules once only two remain).</p>
<ol class=rules>
  <li><b>Head-to-head.</b> For two teams: did you play, and who won? For
  three-plus: winning percentage in games among the tied group — with one
  twist: a team that beat every other team in the group is seeded even if the
  group didn't all play each other.</li>
  <li><b>Common opponents.</b> Win percentage against the conference opponents
  every tied team played. Different schedules, same yardstick.</li>
  <li><b>The standings walk.</b> Compare records against the best-placed team
  everyone played, then the next, on down the standings. Tied placement groups
  count as one collective opponent — you're compared against the group, not
  its members individually.</li>
  <li><b>Strength of schedule.</b> Combined conference record of each team's
  nine conference opponents. The tiebreaker equivalent of "who had the harder
  road."</li>
  <li><b>Total wins</b> across the full 12-game season — with at most one win
  over an FCS opponent counting, so a cupcake-heavy September can't decide a
  title.</li>
  <li><b>SportSource Analytics rating.</b> A proprietary metric the conference
  buys; it is not public. When a projection reaches this step, the site flags
  it rather than guessing.</li>
  <li><b>Coin toss.</b> Yes, really. It has never come to this.</li>
</ol>

<h2>A real one: the 2024 four-way tie</h2>
<p>The procedure's baptism by fire came immediately: in its first season at
sixteen teams, Arizona State, BYU, Colorado, and Iowa State all finished
7–2. Two championship-game spots, four teams, none of whom had all played
each other. Here is the full resolution — generated by this site's rules
engine from the 2024 results, matching the conference's official outcome
(Arizona State #1, Iowa State #2):</p>
<div class=worked>
  <p class=meta>Engine output, 2024 season, tie group: Arizona State · BYU ·
  Colorado · Iowa State</p>
  <ol>
{worked_2024}
  </ol>
</div>
<div class=aside><b>A question this example settles.</b> When a step
separates the group but leaves two teams tied at the top — here BYU and Iowa
State at .800 with Colorado at .600 — does Colorado get bounced, with the
other two restarting as a two-team tie? Reasonable, and many conferences do
work that way, but the Big 12 does not: its policy only removes a team from a
multi-team tie by <i>seeding</i> it, and 2024 proves the point. Bouncing
Colorado at step (b) would put <b>BYU</b> in the championship game on step
(c). The conference sent <b>Iowa State</b> — exactly what carrying all three
teams to step (d) produces.</div>

<p>Worth noticing: head-to-head never fired (the four didn't all meet),
Arizona State escaped on <b>common opponents</b>, the standings walk twice
found information but never a <i>single</i> leader, and Iowa State's ticket
was punched by <b>strength of schedule</b> — step four — while BYU and
Colorado never trailed anyone by record. That's the procedure working as
designed: it keeps asking narrower questions until exactly one team has the
better answer.</p>

<h2>The what-if simulator</h2>
<p>Every unplayed conference game on the main page has two buttons. Pick
winners — one game or all seventy-two — and the site replays the entire
procedure on the simulated season instantly, in your browser. The matchup
card, standings, and tie narratives all update; clear your picks and reality
returns. Nothing is uploaded or saved.</p>
<p>The ★ marks each game's favorite under the selected rating model, with the
projected margin (home field included) in the hover text:</p>
<table class=models>
  <tr><th>Model</th><th>What it is</th></tr>
  <tr><td><b>SP+</b></td><td>Bill Connelly's efficiency-based rating —
  the industry-standard public power number.</td></tr>
  <tr><td><b>FPI</b></td><td>ESPN's Football Power Index.</td></tr>
  <tr><td><b>Elo</b></td><td>The chess rating applied to football: beat good
  teams, number goes up.</td></tr>
  <tr><td><b>SRS</b></td><td>Simple Rating System — margin of victory
  adjusted for schedule. The classic.</td></tr>
</table>
<div class=aside>Preseason, the models carry last season's ratings until the
new year's numbers publish — each is labeled with the year it comes
from.</div>

<h2>The Championship race card</h2>
<p>Three layers, in order of certainty. <b>Clinch and elimination
statuses are proofs</b>: early in the season, strict win-count arithmetic
that no tiebreaker can undo; from mid-November, exhaustive enumeration of
every remaining outcome through the full procedure — which is also where the
"clinches with a win + a Utah loss" scenario lines come from.
<b>Percentages are Monte Carlo odds</b>: ten thousand simulations of the
rest of the season, win probabilities from an ensemble of the public rating
models, every simulated season scored with the real tiebreakers. Proofs
always override odds. And the <b>Chaos Index</b> compresses the whole race
into one number from 0 (decided) to 100 (sixteen-way pileup): 60% the
entropy of the championship odds, 25% how many living teams are tangled in
ties, 15% how many teams are still mathematically alive.</p>

<h2>How we know the engine is right</h2>
<p>The conference publishes final standings every year, but they list tied
teams as a block without applying the procedure — 2024's four 7-2 teams are
printed BYU, Arizona State, Iowa State, Colorado, while the teams actually
sent to the championship game were Arizona State and Iowa State. The pairing
itself is the only published artifact that encodes the tiebreakers' result,
so that is what we test against: <b>this engine reproduces all nine
championship-game pairings of the championship era, 2017 through 2025</b>,
from the game results alone. It also reproduces the 2024 four-way tie exactly,
which is the case that pins down the rule discussed above.</p>

<h2>Where the policy is silent, and what we chose</h2>
<p>The published procedure is specific about the ladder and mostly specific
about the mechanics, but a working implementation has to answer a few
questions the text doesn't. In the interest of showing our work:</p>
<ul>
  <li><b>Teams that trail but don't lose outright.</b> The policy removes a
  team from a multi-team tie only by <i>seeding</i> it ("after one team has an
  advantage and is seeded, all remaining teams … repeat the tie-breaking
  procedure"). It never says a team that merely trails at some step drops out.
  We carry the whole group forward — and 2024 confirms it, since bouncing
  Colorado at step (b) would have sent BYU rather than Iowa State.</li>
  <li><b>Whether tied teams count as their own common opponents.</b> Step (b)
  says "all common conference opponents played by all other teams involved in
  the tie." We read the tied teams themselves out of that set, because games
  among them are precisely what step (a) already weighed; counting them twice
  would let step (a) decide step (b).</li>
  <li><b>A standings group that separates without crowning anyone.</b> Step
  (c) says to compare against the next highest placed common opponent
  "proceeding through the standings." When a placement group splits the tied
  teams but leaves two or more still level at the top, we keep walking down
  the standings, and fall through to step (d) only after the whole ladder of
  opponents is exhausted.</li>
  <li><b>The championship game itself.</b> Step (e) counts wins "in a 12-game
  season," so the title game — a thirteenth — is excluded from that count, and
  from conference records everywhere on this site.</li>
</ul>
<p>Every one of these choices is testable, and the nine championship pairings
above are the test.</p>

<h2>Where the data comes from</h2>
<p>Scores arrive from
<a href="https://collegefootballdata.com">collegefootballdata.com</a>; the
procedure text comes from the
<a href="https://s3.amazonaws.com/big12sports.com/documents/2025/11/4/Big_12_Football_2024_Tiebreaker_Policy.pdf">official
Big 12 tiebreaker policy</a>. During the season the site rebuilds itself
hourly through weekend game windows (and daily otherwise), so the projection
updates within about an hour of a final whistle. The rules engine is
validated against the 2024 and 2025 seasons and every displayed conclusion
comes with its step-by-step reasoning — if you think it's wrong, the receipts
are right there. Found a bug anyway? <a
href="mailto:dept@big12ology.com">dept@big12ology.com</a>.</p>

<a class=backlink href="./">← Back to the tracker</a>
</main>
<footer class=b12-footer>
  A Big12ology project · not affiliated with the Big 12 Conference; conference
  and team marks belong to their owners and appear for identification only.<br>
  <a href="https://github.com/big12ology">GitHub</a> ·
  <a href={base}feed.xml>RSS</a> ·
  <a href=./>The Brief</a> ·
  <a href=history.html>The Archive</a> ·
  <a href=data.json>Data</a> ·
  <a href="https://big12ology.com/privacy">Privacy</a> ·
  <a href="mailto:dept@big12ology.com">dept@big12ology.com</a>
</footer>
<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "355e765d921e4b36ad2bf78d509eae6c"}}'></script>
</body>
</html>
"""


def build_explainer(year, matchcard, outdir=None):
    """Render site/how.html. The 2024 worked example is generated live by the
    rules engine from the frozen season data in history/."""
    games = json.load(open(os.path.join(HERE, "history", "games_2024.json")))
    groups = tb.placement_groups(games)
    order, log, resolved, _events = tb.break_tie(groups[0], games)
    assert resolved and order[0] == "Arizona State" and order[1] == "Iowa State", \
        "2024 worked example no longer matches the historical outcome"
    worked = "".join(
        f'    <li class=seeded>{esc(line)}</li>\n' if "seeded." in line
        else f"    <li>{esc(line)}</li>\n" for line in log)
    out = os.path.join(outdir or SITE, "how.html")
    with open(out, "w") as f:
        f.write(EXPLAINER.format(
            worked_2024=worked, base=BASE,
            v_brand=asset_v("brand.css"),
            v_theme=asset_v("theme.js"),
            top=tracker_top(year, "how", matchcard)))
    print(f"built {out}")


def load_games(year, refetch=False, refresh=False):
    """data/ is committed, so a build normally reads it and calls nothing.

    The CFBD key allows 1,000 calls a month. Only one thing changes on the
    hour — the live season's scores — so --fetch buys exactly that, one
    call. Ratings are published weekly and lines drift daily; --refresh
    picks those up and is worth nine more calls on a weekly cron, not every
    hour. Finished seasons and the team list are never refetched at all:
    they cannot change, and they are in the repo."""
    path = os.path.join(HERE, "data", f"games_{year}.json")
    if refetch:
        try:
            games = fetcher.fetch_season(year)
            if refresh:
                fetcher.fetch_ratings(year)
                fetcher.fetch_lines(year)
            if not os.path.exists(os.path.join(HERE, "data", "teams.json")):
                fetcher.fetch_teams()
            return fetcher.mark_ccg(games)
        except Exception as e:
            # A spent quota or an API outage must not take the whole domain
            # down with it — this build deploys the hub and the attendance
            # tracker too. data/ is committed, so fall back to it and say so
            # loudly. Stale scores beat no site.
            warn = f"live fetch failed ({e}) — building from committed data/"
            if os.environ.get("GITHUB_ACTIONS"):
                print(f"::warning::{warn}")
            print(f"WARNING: {warn}")
            if not os.path.exists(path):
                raise
    if not os.path.exists(path):
        return fetcher.mark_ccg(fetcher.fetch_season(year))
    # Repair on load rather than on fetch, so the seasons already committed
    # are correct without spending a call to re-pull them.
    return fetcher.mark_ccg(json.load(open(path)))


def build_season(year, games, outdir, base, feed=True):
    """Write one season's whole page set. `base` is the relative path back to
    the shared assets — empty at the root, "../" inside an archived year."""
    global BASE
    BASE = base
    os.makedirs(outdir, exist_ok=True)
    site_url = "https://big12ology.com/tiebreaker/"
    canon = site_url if year == LIVE_YEAR else f"{site_url}{year}/"
    base = BASE

    page, ctx = render(year, games)
    with open(os.path.join(outdir, "lab.html"), "w") as f:
        f.write(page)

    overrides = tb.load_overrides()
    systems = load_ratings(year).get("systems", {})
    track, _wk = next_conf_week_ids(games)
    sims = (odds_mod.simulate(games, systems, overrides, track=track)
            if systems else {})
    rows = tb.standings(games, overrides)
    display_rows = pad_standings(rows, games)

    yr = f"the {year} Big 12 season"
    pages = [
        ("race.html", "The Race", "race", build_race_page(ctx),
         f"Who is in, who is out and who controls their own fate in {yr} — "
         "clinch and elimination proofs, championship-game odds, and the "
         "Chaos Index.", ""),
        ("standings.html", "The Standings", "standings", ctx["standingspage"],
         "The Big 12 standings as the conference keeps them, with ties left "
         f"standing, next to the same {year} board with every tie broken — "
         "plus a week-by-week replay of how the season moved.",
         f'<script defer src="{base}{asset_v("pct.js")}"></script>'
         f'<script defer src="{base}{asset_v("replay.js")}"></script>'),
        ("schedule.html", "The Schedule", "schedule",
         build_schedule_page(games, ctx),
         f"Every remaining game in {yr} and every result so far, by week.",
         ""),
    ]
    hist_frag = os.path.join(HERE, "history", "history_body.html")
    if os.path.exists(hist_frag):
        pages.append(("history.html", "The Archive", "history",
                      rebase(open(hist_frag).read()),
                      "Every Big 12 tie since 2017, what the tiebreakers "
                      "produced, and where a different reading of the rules "
                      "would have sent a different team to the title game.",
                      ""))
    evergreen = {"history.html", "how.html"}
    for fname, title, active, body, desc, head in pages:
        # Seasons share the tie archive and the rules explainer verbatim, so
        # the archived copies point their canonical at the live one rather
        # than competing with it as duplicates.
        cu = (site_url + fname if fname in evergreen else canon + fname)
        with open(os.path.join(outdir, fname), "w") as f:
            f.write(build_subpage(title, active, body, year,
                                  ctx["matchcard"], canon=cu,
                                  desc=desc, head=head))

    build_explainer(year, ctx["matchcard"], outdir)

    if feed:
        with open(os.path.join(outdir, "feed.xml"), "w") as f:
            f.write(feed_mod.build_feed(games, year, systems, overrides))

    ccg = tb.championship(games, overrides)
    cl = clinch_mod.analyze(games, overrides)
    data = {
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds"),
        "season": year,
        "standings": [{k: v for k, v in r.items() if k != "log"}
                      for r in rows],
        "championship": ccg,
        "race": {t: {"status": i["status"], "destiny": i["destiny"],
                     "p_ccg": (sims.get(t, {}) or {}).get("p_ccg"),
                     "exp_conf_wins": (sims.get(t, {}) or {}).get("exp_w")}
                 for t, i in cl["teams"].items()},
    }
    with open(os.path.join(outdir, "data.json"), "w") as f:
        json.dump(data, f, indent=1)
    write_forecast(year, games, systems, sims)
    with open(os.path.join(outdir, "standings.csv"), "w") as f:
        f.write("rank,team,conf_w,conf_l,nonconf_w,nonconf_l,"
                "overall_w,overall_l,p_ccg\n")
        for r in rows:
            p = (sims.get(r["team"], {}) or {}).get("p_ccg", "")
            f.write(f"{r['rank']},{r['team']},{r['conf_w']},{r['conf_l']},"
                    f"{r['nonconf_w']},{r['nonconf_l']},{r['overall_w']},"
                    f"{r['overall_l']},{p}\n")

    # The Brief is the front door of every season.
    brief = build_brief(year, games, overrides, systems, sims,
                        ctx["matchcard"], canon=canon)
    with open(os.path.join(outdir, "index.html"), "w") as f:
        f.write(brief)
    # brief.html was the Brief's address before it moved to the front door
    with open(os.path.join(outdir, "brief.html"), "w") as f:
        f.write('<!doctype html><meta charset=utf-8>'
                '<meta http-equiv=refresh content="0; url=./">'
                f'<link rel=canonical href="{canon}">'
                '<title>The Brief</title><a href="./">The Brief</a>')
    BASE = ""
    print(f"built {year} -> {outdir}")


def write_forecast(year, games, systems, sims):
    """Keep what we predicted, so it can be graded later.

    Odds are recomputed from scratch every build and thrown away, which is
    fine for serving a page and fatal for ever asking "was the model any
    good?". Nothing that produced a forecast survives the build: the ratings
    file is overwritten each fetch, and a rebuild in December cannot
    reconstruct what September believed. A week not written down here is
    gone for good, so this writes one file per week and nothing grades it
    yet — the grading needs seasons of these first.

    The model description matters as much as the number. A reliability curve
    over forecasts made by four different model configurations, with no
    record of which was which, measures nothing.
    """
    if not sims or year != LIVE_YEAR:
        return                       # archived seasons have nothing to predict
    done = [g for g in games if g["completed"] and not g.get("ccg")]
    week = max((g["week"] for g in done), default=0)
    out = os.path.join(HERE, "forecasts", str(year))
    os.makedirs(out, exist_ok=True)
    payload = {
        "season": year,
        "through_week": week,
        "games_complete": len(done),
        "generated": datetime.datetime.now(datetime.timezone.utc)
                             .replace(microsecond=0).isoformat(),
        "model": {
            "n_sims": odds_mod.N_SIMS,
            "rating_sigma": round(odds_mod.rating_sigma(games), 3),
            "margin_sigma": odds_mod.MARGIN_SIGMA,
            "systems": {n: {"year": s.get("year"),
                            "regressed": s.get("regressed")}
                        for n, s in systems.items()},
        },
        # simulate() mixes bookkeeping into its return — "_n", and "_cond"
        # when leverage is tracked. Select on shape, not on a name blocklist
        # that the next key added would slip past.
        "teams": {t: {"p_ccg": round(v["p_ccg"], 4),
                      "exp_w": round(v["exp_w"], 3)}
                  for t, v in sims.items()
                  if isinstance(v, dict) and "p_ccg" in v},
    }
    p = os.path.join(out, f"week-{week:02d}.json")
    # Overwritten within a week on purpose: the hourly build keeps refining
    # the same week, and what settles is that week's final state.
    with open(p, "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    print(f"forecast: week {week} -> {p}")


def write_discovery(years):
    """A sitemap. Without one a crawler has to guess that
    the archived seasons exist at all — nothing links to 2024 except the
    year pills, and the pages carry no dated signal of their own."""
    site = "https://big12ology.com/tiebreaker/"
    subs = ["", "lab.html", "race.html", "standings.html", "schedule.html"]
    # Listed once, under the live season — every year serves the same bytes.
    evergreen = ["how.html", "history.html"]
    today = datetime.date.today().isoformat()
    urls = []
    for y in years:
        base = site if y == LIVE_YEAR else f"{site}{y}/"
        for p in subs + (evergreen if y == LIVE_YEAR else []):
            # A finished season never changes again; the live one changes
            # after every game.
            freq = "weekly" if y == LIVE_YEAR else "yearly"
            pri = "1.0" if (y == LIVE_YEAR and not p) else (
                "0.8" if y == LIVE_YEAR else "0.5")
            urls.append(f"  <url><loc>{base}{p}</loc>"
                        f"<lastmod>{today}</lastmod>"
                        f"<changefreq>{freq}</changefreq>"
                        f"<priority>{pri}</priority></url>")
    with open(os.path.join(SITE, "sitemap.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(urls) + "\n</urlset>\n")
    # No robots.txt here on purpose: crawlers only read it at the origin
    # root, and this is a project site under /tiebreaker/. The real one
    # lives in the big12ology.github.io repo and points at this sitemap.
    print(f"built sitemap.xml ({len(urls)} urls)")


def main():
    global LIVE_YEAR
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    year = int(argv[0]) if argv else default_season()
    LIVE_YEAR = year
    games = load_games(year, refetch="--fetch" in sys.argv,
                       refresh="--refresh" in sys.argv)
    build_season(year, games, SITE, "")
    # Finished seasons are rebuilt from cached results — no API calls, and
    # their output is deterministic, so a rebuild is a no-op unless the
    # engine itself changed.
    if "--no-archive" not in sys.argv:
        for y in ARCHIVE_YEARS:
            if y == year:
                continue
            build_season(y, load_games(y), os.path.join(SITE, str(y)), "../",
                         feed=False)
    write_discovery([year] + [y for y in ARCHIVE_YEARS if y != year])


if __name__ == "__main__":
    main()

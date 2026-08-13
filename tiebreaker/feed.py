#!/usr/bin/env python3
"""RSS feed for the tiebreaker tracker — derived entirely from current
season data (builds are stateless), engineered so items are stable across
rebuilds:

- guids never change (game ids, status-team-year, chaos-year-week);
- pubDates come from game clocks, not build clocks;
- item bodies are computed by replaying the season up to the item's moment,
  so a rebuild months later regenerates the same text (modulo rating-system
  updates in weekly wraps, which is acceptable drift).
"""
import copy
import datetime
import email.utils
from xml.sax.saxutils import escape

import chaos as chaos_mod
import clinch as clinch_mod
import odds as odds_mod
import tiebreaker as tb

SITE_URL = "https://big12ology.com/tiebreaker/"
FEED_URL = SITE_URL + "feed.xml"
MAX_ITEMS = 60
WRAP_SIMS = 3000


def stamp(iso, offset_hours=0):
    dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt + datetime.timedelta(hours=offset_hours)


def sort_key(iso):
    return iso or "0000"


def game_items(games):
    """One item per completed game, with records as of that game."""
    # tb.has_score rather than a home_points check, because everything below
    # this line assumes both numbers. tb.winner compares them, and the two
    # title strings print them side by side; a row with one score filled in
    # would reach all of that and raise. The feed is the worst place to learn
    # that, too — it is regenerated on every build during the games, which is
    # exactly when a half-written row is sitting in the data.
    done = sorted(
        (g for g in games if g["completed"] and tb.has_score(g)),
        key=lambda g: sort_key(g["start"]))
    conf_rec = {}
    all_rec = {}
    items = []
    for g in done:
        w = tb.winner(g)
        loser = None
        if w:
            loser = g["away"] if w == g["home"] else g["home"]
        for t in (g["home"], g["away"]):
            conf_rec.setdefault(t, [0, 0])
            all_rec.setdefault(t, [0, 0])
        if w:
            all_rec[w][0] += 1
            all_rec[loser][1] += 1
            if g["conference_game"] and not g.get("ccg"):
                conf_rec[w][0] += 1
                conf_rec[loser][1] += 1
        if w == g["home"]:
            title = (f"Final: {g['home']} {g['home_points']}, "
                     f"{g['away']} {g['away_points']}")
        else:
            title = (f"Final: {g['away']} {g['away_points']}, "
                     f"{g['home']} {g['home_points']}")
        if g.get("ccg"):
            title = "Championship " + title
        bits = []
        for t in (w, loser) if w else (g["home"], g["away"]):
            if t is None:
                continue
            ar, cr = all_rec[t], conf_rec[t]
            tag = (f"{t} is {ar[0]}-{ar[1]}"
                   + (f" ({cr[0]}-{cr[1]} Big 12)"
                      if g["conference_game"] and not g.get("ccg") else ""))
            bits.append(tag)
        desc = ". ".join(bits) + "."
        items.append({
            "guid": f"game-{g['id']}",
            "title": title,
            "desc": desc,
            "dt": stamp(g["start"], offset_hours=4),
        })
    return items


def status_items(games, year, overrides):
    """Clinched / eliminated announcements, dated by the team's last final."""
    res = clinch_mod.analyze(games, overrides)
    last_game = {}
    for g in games:
        if not g["completed"] or not g["start"]:
            continue
        for t in (g["home"], g["away"]):
            last_game[t] = max(last_game.get(t, ""), g["start"])
    items = []
    for t, info in res["teams"].items():
        if info["status"] == "clinched":
            title = f"{t} clinches a Big 12 championship-game berth"
        elif info["status"] == "eliminated":
            title = f"{t} eliminated from championship-game contention"
        else:
            continue
        iso = last_game.get(t)
        if not iso:
            continue
        items.append({
            "guid": f"{info['status']}-{year}-{t}",
            "title": title,
            "desc": (f"Status proven by the tracker's "
                     f"{'exhaustive enumeration' if info['method'] == 'exact' else 'win-count bounds'} "
                     f"of the remaining schedule."),
            "dt": stamp(iso, offset_hours=6),
        })
    return items


def weekly_wraps(games, year, systems, overrides):
    """Chaos Index wrap per fully-completed week, via truncated replay."""
    weeks = {}
    for g in games:
        if g.get("ccg"):
            continue
        weeks.setdefault(g["week"], []).append(g)
    items = []
    for w in sorted(weeks):
        wgames = weeks[w]
        if not wgames or not all(g["completed"] for g in wgames):
            continue
        last_iso = max(g["start"] or "" for g in wgames)
        snap = copy.deepcopy(games)
        for g in snap:
            if g["week"] > w:
                g["completed"] = False
                g["home_points"] = g["away_points"] = None
        rows = tb.standings(snap, overrides)
        # bounds-only here: exact enumeration at late-season
        # truncations would cost minutes per build
        cl = clinch_mod.analyze(snap, overrides, budget=2)
        od = odds_mod.simulate(snap, systems, overrides, n=WRAP_SIMS)
        cx = chaos_mod.index(rows, cl, od)
        leaders = sorted(
            ((v["p_ccg"], t) for t, v in od.items()
             if not t.startswith("_")),
            reverse=True)[:3]
        lead_txt = ", ".join(f"{t} {p:.0%}" for p, t in leaders)
        items.append({
            "guid": f"chaos-{year}-week-{w}",
            "title": (f"Week {w} wrap: Chaos Index {cx['score']} "
                      f"({cx['label']})"),
            "desc": (f"Championship-game odds leaders: {lead_txt}. "
                     f"Race entropy {cx['components']['entropy']:.2f}, "
                     f"tie tangle {cx['components']['tangle']:.2f}, "
                     f"alive {cx['components']['breadth']:.2f}."),
            "dt": stamp(last_iso, offset_hours=8),
        })
    return items


def build_feed(games, year, systems, overrides=None):
    items = (game_items(games)
             + status_items(games, year, overrides)
             + weekly_wraps(games, year, systems, overrides))
    if not items:
        opener = min((g["start"] for g in games if g["start"]), default=None)
        if opener:
            items = [{
                "guid": f"preseason-{year}",
                "title": f"The {year} Big 12 Tiebreaker Tracker is live",
                "desc": ("Standings, tiebreakers, championship odds, and the "
                         "what-if simulator — updating automatically after "
                         "every game once the season kicks off."),
                "dt": stamp(opener, offset_hours=-24 * 7),
            }]
    items.sort(key=lambda i: i["dt"], reverse=True)
    items = items[:MAX_ITEMS]

    now = email.utils.format_datetime(
        datetime.datetime.now(datetime.timezone.utc))
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append('<rss version="2.0" '
               'xmlns:atom="http://www.w3.org/2005/Atom"><channel>')
    out.append(f"<title>Big 12 Tiebreaker Tracker — {year}</title>")
    out.append(f"<link>{SITE_URL}</link>")
    out.append("<description>Finals, clinch and elimination calls, and "
               "weekly Chaos Index wraps for the Big 12 championship race. "
               "A Big12ology project.</description>")
    out.append(f"<lastBuildDate>{now}</lastBuildDate>")
    out.append(f'<atom:link href="{FEED_URL}" rel="self" '
               'type="application/rss+xml"/>')
    for i in items:
        out.append(
            "<item>"
            f"<title>{escape(i['title'])}</title>"
            f"<link>{SITE_URL}</link>"
            f"<guid isPermaLink=\"false\">{escape(i['guid'])}</guid>"
            f"<pubDate>{email.utils.format_datetime(i['dt'])}</pubDate>"
            f"<description>{escape(i['desc'])}</description>"
            "</item>")
    out.append("</channel></rss>")
    return "\n".join(out)

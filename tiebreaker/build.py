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
import re
import sys
import zoneinfo

import chaos as chaos_mod
import clinch as clinch_mod
import feed as feed_mod
import facts as facts_mod
import fetch as fetcher
import odds as odds_mod
import pickem as pickem_mod

# The pick'em is finished enough to look at and not finished enough to ship:
# the Worker behind /api/* does not exist yet, so every page of it renders an
# error, and privacy.html still promises "no accounts, no cookies". Until both
# are true this section is built only when asked for.
#
#     B12_PICKEM=1 python3 build.py     # with it
#     python3 build.py                  # without, which is what CI does
#
# Off means all of it: no header link, no pages in dist/, no sitemap, and no
# consensus band on the schedule cards — that last one fetches /api/consensus
# on every schedule page view, which would be a request to a 404. Nothing is
# deleted; the flag is the only thing between here and live.
PICKEM_ENABLED = os.environ.get("B12_PICKEM") == "1"
import scorecard as scorecard_mod
import weather as weather_mod
import rotation as rotation_mod
import swap as swap_mod
import tiebreaker as tb

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
# The schedule section is a sibling of /tiebreaker/ on the
# domain, so its pages reach shared assets through ../tiebreaker/.
SCHEDULE_SITE = os.path.join(HERE, "site_schedule")
# The pools — two season-long games under one roof at /pools/, generated here
# like the schedule. It is the one section whose body is not in the file: the
# shells are static and every number in them arrives from /api/* at runtime,
# because a slate, a lock countdown and a leaderboard are per-viewer and
# per-second. Everything around that body — chrome, nav, footer,
# cache-busting — is the same as everywhere.
#
#   /pools/                the two games, and what they are
#   /pools/pickem/         every game, against the spread
#   /pools/survivor/       one team a week, no repeats
#   /pools/account.html    ONE account for both, so it sits at the top
#
# Pages one level down reach shared assets through ../../tiebreaker/, which is
# what POOLS_UP is for.
POOLS_SITE = os.path.join(HERE, "site_pools")
PICKEM_SITE = os.path.join(POOLS_SITE, "pickem")
SURVIVOR_SITE = os.path.join(POOLS_SITE, "survivor")
# The sections under /pools/. They share the account chip, and none of them
# has a year archive — year_href() would emit 2025/ and 2024/ links to
# directories that do not exist, on every page.
POOL_SECTIONS = ("pools", "pickem", "survivor")

# Enter the survivor pool by this week to be on its leaderboard. The Worker is
# the authority — worker/src/scoring.js RANKED_ENTRY_BY enforces it — and this
# copy exists only so the rules page can state the number. If they ever
# disagree, the rules page is the one that is lying.
SURVIVOR_RANKED_BY = 6

POOLS_UP = "../"          # from /pools/x/ back up to /pools/

# The day the pools open. Only the teaser at /pools/ reads it — nothing gates
# on it, because the gate is which pages assemble.sh publishes, and that is a
# decision a person makes rather than a clock. It is a date and not a
# datetime for the same reason the site never prints a deadline without a
# timezone: "Wednesday" is a promise anyone can keep, and 9am somewhere is
# not.
# The day the pools section opens. Every date the teaser prints is derived
# from this, and pages.yml gates B12_PICKEM on the same day, so moving the
# launch is this line and the one in that workflow — not a hunt through copy.
POOLS_OPEN = datetime.date(2026, 8, 20)

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
    favorites and the strength-of-schedule card describing the same teams:
    Rule 6, the same quantity presented the same way everywhere."""
    p = os.path.join(HERE, "data", f"ratings_{year}.json")
    raw = json.load(open(p)) if os.path.exists(p) else {"systems": {}}
    raw["systems"] = odds_mod.regress_stale(raw.get("systems", {}), year)
    return raw


def load_lines(year):
    """{game_id: {spread, over_under, ...}}, normalizing both file shapes.

    Files written before the market capture hold a bare spread number.
    Rather than migrate them — they would have to be refetched, and the
    quota is the scarce thing here — wrap the old shape on read."""
    p = os.path.join(HERE, "data", f"lines_{year}.json")
    raw = json.load(open(p)) if os.path.exists(p) else {}
    return {k: (v if isinstance(v, dict) else {"spread": v})
            for k, v in raw.items()}


MODEL_ORDER = ["SP+", "FPI", "Elo", "SRS"]
# The four rating systems averaged, and the name the Lab shows for it. Kept
# as a constant because it is both a key in `favorites` and a label on three
# controls. Named rather than described because the list it sits in reads as
# a matchup — the nerds against Vegas — and because the optgroup directly
# beneath it says "In the blend" and lists the four, so the mechanic is still
# on screen for anyone who wants it.
BLEND = "The Nerds"


def model_year(name, systems):
    """The season a rating system's numbers actually come from. Preseason
    that is usually last season: the new year's ratings do not publish until
    late August, and fetch.py falls back a year rather than showing nothing."""
    return ((systems or {}).get(name) or {}).get("year")


def model_label(name, systems):
    """A system's name carries the season behind it — "SP+ (2025)", the same
    way the what-if picker has always labeled them. A bare "SP+" beside a
    2026 schedule reads as a 2026 number, and in August it is not one."""
    y = model_year(name, systems)
    return f"{name} ({y})" if y else name


def market_favorites(games):
    """The market as a model: whoever the spread makes the favorite.

    The Lab was offering four ratings and no line, which is the one opinion
    the site treats as the benchmark everywhere else — the model card scores
    itself against it, the scorecard calls it Vegas, and a reader picking
    winners had no way to say "just take the chalk". The lines are already on
    disk for the slate and the what-if models; this costs nothing to add.

    A pick'em has no favorite to name and is left out, exactly as the
    handicap's pickable_favourite leaves it out.
    """
    out = {}
    for g in games:
        if g.get("ccg") or g["completed"]:
            continue
        sp = (g.get("line") or {}).get("spread")
        if sp is None or sp == 0:
            continue
        out[str(g["id"])] = {
            "team": g["home"] if sp < 0 else g["away"],
            "margin": round(abs(sp), 1),
        }
    return out


def blend_favorites(favorites, games):
    """The four systems averaged into one, the way the race board averages
    them.

    The Lab ran a single rating at a time while the race card and every
    leverage figure on the site run odds.ensemble_margin — an average across
    all four. So the same team's championship chance was quoted twice, from
    two different models, with nothing saying they were different questions:
    23% on the race board and whatever SP+ alone made of it in the Lab.

    Averaging the per-system point margins is exactly what ensemble_margin
    does, so this reproduces the board rather than approximating it.
    favorites_for has already divided each system by its own per_pt scale and
    dropped the sign in favour of naming a team, so the sign is put back
    before averaging and taken off again after.
    """
    by_id = {str(g["id"]): g for g in games}
    out = {}
    for gid, g in by_id.items():
        signed = []
        for m in favorites.values():
            f = m.get(gid)
            if f:
                signed.append(f["margin"] if f["team"] == g["home"]
                              else -f["margin"])
        if signed:
            d = sum(signed) / len(signed)
            out[gid] = {"team": g["home"] if d >= 0 else g["away"],
                        "margin": round(abs(d), 1)}
    return out


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

# WHO THE FORK LISTS BESIDES THE TWO PLAYING. odds.leverage keeps any team
# moving half a point, which is the right threshold for a module that cannot
# know who is asking. A box on a page is a different budget: in a live week
# most of the league clears half a point on any game — seven teams did on the
# only game of week 2, none of them by a point and a half — so a floor that
# low fills both columns with teams whose swing rounds to nothing. A point to
# be listed, and three at most, which is what fits beside the two the box is
# actually about.
MOVER_FLOOR = 0.01
MOVER_LIMIT = 3


def asset_v(name, root=None):
    """Cache-bust by content. A browser that has app.js in cache will not ask
    the server whether it changed, so a deploy can silently keep running the
    old file — this makes the URL change whenever the bytes do.

    `root` is for assets that do not live in site/. The pick'em keeps its own
    app.js and styles.css in site_pickem/ while still reaching back to
    ../tiebreaker/ for the shared ones, so the two cannot be resolved against
    the same directory."""
    p = os.path.join(root or SITE, name)
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


def rebase_from(html, old, new):
    """Repoint asset paths in an already-rendered body from one base to
    another.

    The bodies are built once, with the season's own base, and the schedule
    section then serves the same HTML from a sibling path — so its marks have
    to be walked from `old` to `new` rather than left pointing at a directory
    that does not exist under /schedule/."""
    if old == new:
        return html
    for attr in ("src", "href"):
        for q in ("'", '"', ""):
            html = html.replace(f"{attr}={q}{old}logos/",
                                f"{attr}={q}{new}logos/")
    return html


# One footer for every page on the domain: credits, disclaimer, Privacy and
# the address. Nothing else. It had drifted into five variants, each carrying
# whatever links that page happened to have — data.json, standings.csv, RSS,
# GitHub, section nav — and a footer that differs per page stops being chrome
# and starts being content.
#
# Every link here is root-relative or absolute, deliberately: no {BASE}, so the
# same string is correct at every depth and in both sections. The four
# hand-written pages (hub, attendance, privacy, 404) carry this same markup
# with a {{BUILD_STAMP}} token that assemble.sh fills in.
#
# The RSS feed lost its footer link but keeps <link rel=alternate> in the head,
# so readers still discover it.
POLICY_URL = ("https://s3.amazonaws.com/big12sports.com/documents/2025/11/4/"
              "Big_12_Football_2024_Tiebreaker_Policy.pdf")


def footer():
    """The stamp is left as a {{BUILD_STAMP}} token for assemble.sh to fill.

    Stamping it here instead put two different times on the domain — the
    generated pages said 00:56 and the hand-written ones 00:57, because the
    build and the assemble are separate steps a minute apart. One substitution
    at assemble time is the only way all 37 pages can agree. It also stops the
    committed site/ output from churning on a timestamp every build.

    Two rows, and it takes editing to keep them at two. Every clause here is
    load-bearing — the data license, the policy the whole tracker implements,
    the mark provenance, the non-affiliation — so the length is spent on
    wording rather than on dropping one: "provenance in SOURCES.json" became
    the link alone, "A Big12ology project" went because the domain already
    says so, and the disclaimer stopped saying "conference and team marks"
    when "marks" covers both. Anything added here should buy its line.

    The four hand-written pages — index.html, privacy.html, 404.html and
    attendance/index.html — carry this footer as literal HTML. Nothing checks
    that they still match, so a change here is four edits, not one.
    """
    # The token must sit in a plain string: inside an f-string, {{ }} would
    # collapse to single braces and assemble.sh would never match it.
    return ('<footer class=b12-footer>Results from '
            '<a href="https://collegefootballdata.com">'
            'collegefootballdata.com</a> · procedure per the '
            f'<a href="{POLICY_URL}">official Big 12 tiebreaker policy</a> · '
            'marks via Wikimedia Commons '
            '(<a href="/tiebreaker/logos/SOURCES.json">SOURCES.json</a>) · '
            'updated {{BUILD_STAMP}}.<br>'
            'Not affiliated with the Big 12 Conference; marks belong to their '
            'owners, shown for identification only. · '
            '<a href="/privacy">Privacy</a> · '
            '<a href="mailto:dept@big12ology.com">dept@big12ology.com</a>'
            '</footer>')


def year_href(y, page, year):
    """The same page you are on, in season `y`.

    Changing the season should change the data, not the subject: from the
    2026 rotation, 2024 means the 2024 rotation. Every page exists in every
    season directory, so this can name `page` unconditionally.

    Deliberately not BASE. BASE is the *asset* base, and under /schedule/ it
    points back at ../tiebreaker/ for the shared stylesheet and marks — the
    year pills inherited it and threw you into the tracker's Brief. What is
    wanted is the season base: the live season sits at the section root,
    archived ones one directory below it.
    """
    up = "" if year == LIVE_YEAR else "../"
    return f"{up}{page}" if y == LIVE_YEAR else f"{up}{y}/{page}"


def section_href(section, year):
    """Another section, same season.

    The sections do not share page names — the tracker has eight pages, the
    schedule three — so this lands on the section's front page rather than
    inventing a counterpart. Keeping the year is the part that matters:
    switching sections from 2024 used to land on the live season.

    Root-relative, like the rest of the masthead, so a local preview stays
    local.
    """
    return f"/{section}/" if year == LIVE_YEAR else f"/{section}/{year}/"


def topbar(section, year, base="", acct=False):
    """The masthead, one copy for every page that has one.

    `acct` reserves the signed-in chip. It is emitted empty and filled by
    pickem/app.js once /api/me answers; leaving the box in the markup keeps
    the row from reflowing when it does, and stops a signed-in reader seeing
    "Sign in" for the length of a request."""
    def link(sect, label):
        on = " class=on" if sect == section else ""
        return f'<a{on} href="{section_href(sect, year)}">{label}</a>'

    # Attendance and the pick'em keep no per-year directories — attendance has
    # a season picker on one page, and the pick'em only ever plays the current
    # week — so neither takes a year across, and both are linked flat rather
    # than through section_href, which would invent /pickem/2024/.
    return (f'<nav class=b12-topbar><a class=b12-brand href="/" '
            f'aria-label="Big12ology home"><picture>'
            f'<source srcset="{base}brand/big12ology-compact-dark.svg" '
            f'media="(prefers-color-scheme: dark)">'
            f'<img src="{base}brand/big12ology-compact-dark.svg" '
            f'alt="Big12ology"></picture></a>'
            + link("tiebreaker", "Tiebreaker") + link("schedule", "Schedule")
            + '<a href="/attendance/">Attendance</a>'
            # Always. /pickem/ is a real page either way: the section when
            # it is on, the Coming Soon page when it is not. It was only
            # gated because the link used to point at a 404.
            + f'<a{" class=on" if section in ("pickem", "pools") else ""} '
              f'href="/pools/">Pools</a>'
            + '<span class=b12-right>'
            + ('<span class=b12-acct hidden></span>' if acct else '')
            + '<span class=b12-theme></span></span>'
            '</nav>')


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


# Reaching the championship game is not a win percentage, and coloring it
# with one said something false. Two teams out of sixteen play in it, so the
# BASELINE is 2/16 — 12.5%, not 50%. A team sitting on 12.4% is exactly
# average and was being painted the same deep red as a team on 1%, while
# 30.9% — second in the league and comfortably in the race — looked like a
# failing grade.
#
# So the ramp runs on the ratio to that baseline rather than on the raw
# probability: 1.0x is the neutral yellow and it climbs from there. The hue
# stops are the ones winpct_color already uses, so the two curves still look
# like the same family even though they measure different things.
TEAM_COUNT = 16          # two of sixteen reach the title game
CCG_ANCHORS = [(0.0, 0), (0.35, 12), (0.7, 30), (1.0, 45), (1.6, 72),
               (2.5, 100), (3.5, 128), (5.0, 152), (8.0, 168)]


def ccg_color(p, teams, spots=2):
    """Color for a probability of reaching a `spots`-team game from `teams`."""
    if not teams:
        return winpct_color(p)
    base = spots / float(teams)
    r = (p or 0.0) / base if base else 0.0
    a = CCG_ANCHORS
    h = a[-1][1]
    if r <= a[0][0]:
        h = a[0][1]
    else:
        for i in range(1, len(a)):
            if r <= a[i][0]:
                t = (r - a[i - 1][0]) / (a[i][0] - a[i - 1][0])
                h = a[i - 1][1] + t * (a[i][1] - a[i - 1][1])
                break
    s = 100 - (h / 45) * 35 if h < 45 else 65
    return f"hsl({round(h)} {round(s)}% var(--pctl))"


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
    # One decimal, always. The trailing zero was being stripped, so a column
    # of these read 6.6, 6, 5.6, 5.3 — the digits stopped lining up and 6%
    # looked like a different kind of measurement from 6.6%. Tabular figures
    # only align if every number has the same shape.
    return f"{p * 100:.1f}%"



# The three pages the projected championship matchup belongs on. It used to
# ride the shared top, which put it on the rules explainer, the tie archive,
# the cut line, the ladder and the Lab — pages that are either about how the
# procedure works or about a season other than this one. On those it is a
# headline with no story under it, and it pushed each page's actual subject
# below the fold.
#
# The Brief, The Race and The Standings are the three that are about who is
# playing for the title, which is what the card says.
MATCHCARD_PAGES = {"index.html", "race.html", "standings.html"}


def matchcard_for(fname, year, ctx):
    """The matchup card, on the pages that are about the matchup.

    Live season only. An archived year's card is a settled fact wearing the
    word "projected", and the season it describes finished months ago; the
    archive pages that want that answer state it in their own prose.
    """
    if year != LIVE_YEAR or fname not in MATCHCARD_PAGES:
        return ""
    return ctx["matchcard"]


def tracker_top(year, active, matchcard="", section="tiebreaker", page="",
                up="", yearpills=True, subnavon=True):
    """The one top: header bar, pill row, matchup card. Styled entirely by
    brand.css (.b12-head/.subnav) — no page may restyle these.

    `page` is the file being built, "" for a section's front page; the year
    pills carry it, so a season switch keeps the view.

    `yearpills` is not decoration. year_href() assumes every section has a
    directory per archived season, and the pick'em has none — there is no 2024
    pick'em and never will be. Left on, every page would carry two links to
    directories that do not exist.

    `subnavon` is the same argument for the row of section pills. The Coming
    Soon page is the only thing at /pickem/ while the section is dark, so its
    four pills would all be links to pages that are not there."""
    meta = SECTIONS[section]
    years = "" if not yearpills else "".join(
        (f"<span class=yron>{y}</span>" if y == year else
         f"<a href='{up}{year_href(y, page, year)}'>{y}</a>")
        for y in [LIVE_YEAR] + ARCHIVE_YEARS)
    blurb = (meta["live"] if year == LIVE_YEAR
             else meta["past"].format(year=year))
    return f"""<header class=b12-head>
  <div class=hwrap>
    <div>
      <h1>{meta["title"]} <span class=yrpills>{years}</span></h1>
      <p>{blurb}</p>
    </div>
  </div>
</header>
{subnav(active, section, up) if subnavon else ''}
<main id=main tabindex="-1">
{icon_sprite()}
{matchcard}"""


# Ordered the way someone actually reads the season: the summary, then the
# race, then where everyone stands, then the toys. Every label takes "The" —
# a name that only works without it is a sign the page needs a better name.
# Two sections now, because three of these pages were never about breaking
# ties: the schedule, the draw and the rotation are about the shape of an
# unbalanced sixteen-team season. They live at /schedule/ and reach back to
# ../tiebreaker/ for the shared marks and stylesheet rather than carrying a
# second copy of them.
SECTIONS = {
    "tiebreaker": {
        "title": "Big 12 Tiebreaker Tracker",
        "live": ("Unofficial fan tool. Applies the official Big 12 "
                 "tiebreaking procedures to live results after every game."),
        "past": ("The {year} season as it finished, with the official "
                 "tiebreaking procedures applied to the final results."),
    },
    "schedule": {
        "title": "Big 12 Schedule",
        "live": ("Who plays whom, who misses whom, and what the unbalanced "
                 "draw is worth. Nine conference games out of a possible "
                 "fifteen."),
        "past": ("The {year} conference schedule: every result by week, who "
                 "never met, and what the draw was worth."),
    },
    "survivor": {
        "title": "Big 12 Survivor",
        "live": ("One team a week, picked to win outright. Never the same "
                 "team twice. Lose once and your run is over."),
        "past": "The {year} survivor pool.",
    },
    "pools": {
        "title": "Big 12 Pools",
        "live": ("Two season-long games, one account. Pick every game "
                 "against the spread, or pick one team a week and never "
                 "the same team twice."),
        "past": "The {year} pools.",
    },
    "pickem": {
        "title": "Big 12 Pickem",
        "live": ("Pick every Big 12 game against the spread. One line for "
                 "everyone, frozen when the week is published; the whole "
                 "slate locks at the first kickoff."),
        # Never rendered — the pick'em has no archived seasons — but
        # tracker_top reads it unconditionally.
        "past": "The {year} pick'em.",
    },
}

SCHEDULE_PAGES = {"schedule.html", "matrix.html", "draw.html", "rotation.html"}

# The subset that used to answer at /tiebreaker/ and now lives under
# /schedule/. Only these get a redirect left behind: a "moved" page for an
# address that never existed sends a reader somewhere they were not, and
# leaves the next person looking for a move that never happened.
MOVED_TO_SCHEDULE = {"schedule.html", "draw.html", "rotation.html"}

SUBNAV_LINKS = [("brief", "./", "The Brief"),
                ("race", "race.html", "The Race"),
                ("standings", "standings.html", "The Standings"),
                ("tracker", "lab.html", "The Lab"),

                ("cutline", "cutline.html", "The Cut Line"),
                ("ladder", "ladder.html", "The Ladder"),
                ("how", "how.html", "The Rules"),
                ("history", "history.html", "The Archive")]

SCHEDULE_NAV = [("schedule", "./", "The Schedule"),
                ("matrix", "matrix.html", "The Matrix"),
                ("draw", "draw.html", "The Draw"),
                ("rotation", "rotation.html", "The Rotation")]

# The Card is your own picks and The Board is everyone's, which is the only
# distinction a player needs. The account page is deliberately absent: it is
# reached from the chip in the masthead, and a nav slot that says "Account"
# on a page most readers never sign into is a slot wasted.
#
# One row per game, and only that game's own pages in it — the same shape
# every other section has. The first attempt hung each game's tabs off a
# shared two-game switcher, which put "The Pools" in a row directly under the
# "Pools" link in the topbar and mixed two levels of navigation in one strip.
# Crossing from the pick'em to the survivor pool goes through /pools/ in the
# topbar, exactly as crossing from the tiebreaker to the schedule does.
#
# Relative hrefs, like every other nav: each of these is rendered at exactly
# one depth, so "card.html" always means the same file.
PICKEM_NAV = [("slate", "./", "The Slate"),
              ("card", "card.html", "The Card"),
              ("board", "board.html", "The Board"),
              ("rules", "rules.html", "The Rules")]

SURVIVOR_NAV = [("survivor", "./", "The Pick"),
                ("svpool", "pool.html", "The Pool"),
                ("svrules", "rules.html", "The Rules")]

# The two games, for the pages that sit above both of them at /pools/. The
# hub does not use it — its body is two large cards saying the same thing —
# but the account page does, because it is the one page a player lands on
# from the masthead with no way back into either game.
POOLS_NAV = [("slate", "pickem/", "Pickem"),
             ("survivor", "survivor/", "Survivor")]


def nav_for(section):
    return {"schedule": SCHEDULE_NAV,
            "pickem": PICKEM_NAV,
            "survivor": SURVIVOR_NAV,
            "pools": POOLS_NAV}.get(section, SUBNAV_LINKS)


def subnav(active, section="tiebreaker", prefix=""):
    """`prefix` lifts the links for pages that sit a directory deeper. The
    hrefs are relative, so from /schedule/game/ a bare "matrix.html" points
    at a file that does not exist — and does it silently."""
    links = "".join(
        f"<a href={prefix}{href} class={'on' if key == active else 'off'}>"
        f"{label}</a>" for key, href, label in nav_for(section))
    return f"<nav class=subnav>{links}</nav>"


def simulate_week(games, systems, overrides, track):
    """The baseline run, and two readings of every game it tracks.

    WHAT A RESULT DOES and WHAT IT TEACHES are different numbers, and this
    site needs both. `_lev` asserts each result and runs the season around
    it — the honest answer to "how much does this game matter", and the
    number the Lab has always shown. `_lev_cond` filters the baseline run to
    the seasons the home side won, which folds in the re-rating a result
    would justify — the honest answer to "what will the board say on Sunday".

    Both ride on the sims dict because that is already threaded to every
    caller that wants them, and everything walking it either checks shape or
    skips the underscore keys. Same reason simulate() puts "_n" there.
    """
    sims = odds_mod.simulate(games, systems, overrides, track=track)
    if track:
        sims["_lev"] = odds_mod.causal_leverage(
            games, systems, overrides, track)
        sims["_lev_cond"] = odds_mod.leverage(sims, games)
    return sims


def leverage_of(sims):
    """What the tracked games DO, biggest first."""
    return (sims or {}).get("_lev") or []


def leverage_cond_of(sims):
    """What they would TEACH, keyed by game id. Preview pages only."""
    return {str(e["game"]["id"]): e
            for e in ((sims or {}).get("_lev_cond") or [])}


def next_conf_week_ids(games):
    """Game ids for the next week that still has unplayed conference games."""
    rem = [g for g in games if g["conference_game"] and not g.get("ccg")
           and not g["completed"]]
    if not rem:
        return [], None
    wk = min(g["week"] for g in rem)
    return [g["id"] for g in rem if g["week"] == wk], wk


def fork_block(g, lev, sims, teams, compact=False):
    """THE FORK. Two results, two teams, four numbers — the shape of the
    question a preview is actually asked.

    ONE renderer for both places this appears. The race page's leverage list
    and the game page's race card were describing the same four numbers two
    different ways: a sentence carrying two of them there, this grid here.
    Same data, same design, one function — a reader who learns to read it on
    one page has learned to read it on the other.

    Each cell says what the result MOVES as well as where it lands, because
    a probability with no baseline is a number without a verb: 33% means
    nothing until you know they were on 23%.

    THE REST OF THE LEAGUE IS IN THE SAME BOXES, under a rule. It was a line
    of its own below the fork, which could give a third party its two
    endpoints but not what either one MEANS: 11%/12% is a pair of numbers
    with no verb, and the reader had to hold the column headings in their
    head to work out which was which. Inside the branch it is asking about,
    a team gets the same three things the two playing get — where it lands,
    which way that is, and by how much — and nothing has to be remembered
    from one line to the next. The rule is there because "playing" and
    "watching" are different kinds of fact, not different amounts of one.
    """
    pair = lev.get("pair") or {}
    if len(pair) != 2:
        return ""
    playing = (g["home"], g["away"])
    rest = [m for m in (lev.get("movers") or [])
            if m[0] not in playing and abs(m[1]) >= MOVER_FLOOR][:MOVER_LIMIT]

    def cell(t, p):
        now = (sims.get(t) or {}).get("p_ccg")
        delta = ""
        if now is not None:
            dv = (p - now) * 100
            # ROUNDED, THEN JUDGED. A third of a point is a move to the
            # subtraction and not to a reader, and printing it as "up 0"
            # reads as a bug in the page rather than as a small number. The
            # dash is the honest glyph for it: the result touches this team,
            # but not by anything worth a digit.
            n = round(abs(dv))
            way = "flat" if not n else ("up" if dv > 0 else "down")
            arrow = ("&mdash;" if not n
                     else ("&#9650;" if dv > 0 else "&#9660;"))
            delta = (f"<span class='forkd {way}'>{arrow}"
                     f"{n if n else ''}</span>")
        return (f"<div class=forkcell><span class=forkteam>"
                f"{logo_img(t, 16)}{esc(t)}</span>"
                f"<b class=forkp>{p * 100:.0f}%</b>{delta}</div>")

    cols = []
    for side in playing:
        branch = 0 if side == g["home"] else 1
        cells = [cell(t, pair[t][branch]) for t in playing]
        if rest:
            cells.append("<div class=forkrest>" + "".join(
                cell(t, pw if branch == 0 else pl)
                for t, _d, pw, pl in rest) + "</div>")
        cols.append(
            f"<div class=forkcol style='--fc:{team_color(teams, side)}'>"
            f"<div class=forkhead>If {esc(side)} wins</div>"
            f"{''.join(cells)}</div>")
    cls = "forkgrid compact" if compact else "forkgrid"
    return f"<div class='{cls}'>{''.join(cols)}</div>"


def teach_block(g, lev, cond, teams):
    """THE SECOND STEP, and only a preview page gets it.

    The fork above asserts a result and runs the season around it, so it
    answers what the game DOES. A reader on a preview page is also asking
    something the fork cannot answer: what will this page say on Sunday?
    Those differ, because a win is not only an event, it is evidence. Beat
    Arizona and the model does not merely bank the win — it revises upward
    how good it thinks BYU is, and a better BYU wins more of the eight games
    after it.

    That revision is exactly what leverage() measures and causal_leverage()
    refuses to. So both are computed and both are shown: the fork for the
    result, this for the belief, and the signed number between them for what
    the game teaches, which is a quantity nothing on the site had a name for.

    IT DISAPPEARS WHEN IT IS NOTHING, which is most of the season. The gap
    is a pure function of how unsure the ratings still are: worth four
    points of BYU's swing in week 2 with nothing played, and under one by
    November. A block explaining a difference of zero is a block explaining
    nothing, so a tenth of a point of teaching is no block at all.
    """
    pair = (lev or {}).get("pair") or {}
    cpair = (cond or {}).get("pair") or {}
    if len(pair) != 2 or len(cpair) != 2:
        return ""
    playing = (g["home"], g["away"])
    if max(abs(cpair[t][b] - pair[t][b])
           for t in playing for b in (0, 1)) < 0.01:
        return ""
    cols = []
    for side in playing:
        branch = 0 if side == g["home"] else 1
        cells = []
        for t in playing:
            p = cpair[t][branch]
            dv = (p - pair[t][branch]) * 100
            n = round(abs(dv))
            way = "flat" if not n else ("up" if dv > 0 else "down")
            sign = "&mdash;" if not n else ("+" if dv > 0 else "&minus;")
            cells.append(
                f"<div class=forkcell><span class=forkteam>"
                f"{logo_img(t, 16)}{esc(t)}</span>"
                f"<b class=forkp>{p * 100:.0f}%</b>"
                f"<span class='forkd {way}'>{sign}{n if n else ''}</span>"
                f"</div>")
        cols.append(
            f"<div class=forkcol style='--fc:{team_color(teams, side)}'>"
            f"<div class=forkhead>If {esc(side)} wins</div>"
            f"{''.join(cells)}</div>")
    return (f"<div class=teachlab>And what the result would teach</div>"
            f"<div class='forkgrid teach'>{''.join(cols)}</div>")


def leverage_card(games, sims, teams=None):
    lev = leverage_of(sims)
    if not lev:
        return ""
    wk = lev[0]["game"]["week"]
    rows = []
    for e in lev[:8]:
        g = e["game"]
        date = pretty_date(g["start"])
        # The same fork the game page draws, at list density. It replaced a
        # sentence that named one team and two of the four numbers, which
        # made every game on this list look like it was about whoever the
        # sentence happened to lead with.
        # The same fork carries the rest of the league, under a rule. On the
        # games at the top of this list those are a footnote; on the ones at
        # the bottom they are the story. A week-11 rewind of 2025 has Houston
        # at UCF moving neither team off zero and moving BYU four and a half
        # points — a row that, without them, prints four numbers that are all
        # zero and no reason it was ranked above nothing.
        mover_txt = fork_block(g, e, sims, teams or {}, compact=True)
        pct = min(e["total"] * 100, 100)
        # Same column treatment as the race card: matchup, bar, number,
        # then the swing note on its own line. Run inline it wrapped through
        # the middle of "biggest swing: BYU +26% if BYU wins".
        rows.append(
            f"<div class=clrow><div class=levmain>"
            f"<span class=levgame>{logo_img(g['away'], 16)}{esc(g['away'])} "
            f"<span class=dim>{joiner(g)}</span> {logo_img(g['home'], 16)}"
            f"{esc(g['home'])}</span>"
            f"<span class=levdate>{date}</span>"
            f"<span class=levbar><span class=obar><i style='width:{pct:.0f}%;"
            f"background:{winpct_color(min(e['total'], 1.0))}'></i></span>"
            f"</span>"
            f"<b class=opct>{e['total'] * 100:.0f}</b>"
            f"</div>{mover_txt}</div>")
    return (f"<div class=card id=levcard><h2>Games that matter · week {wk}"
            f"</h2>{''.join(rows)}"
            "<p class=note>Two teams reach the championship game &mdash; "
            "think of that as two seats. The rest of the season is played "
            "out ten thousand times; change who wins one game and different "
            "teams end up in those seats. The number beside each game is how "
            "much of a seat changes hands on it: <b>100 would be a whole "
            "seat</b>, 0 would mean the result decides nothing. "
            "Percentages are how often that team reaches the title game "
            "across those simulated seasons, and the arrow is the move from "
            "where they stand today. Under the rule in each box are the "
            "teams that are not playing but whose own chance that result "
            "moves by a point or more &mdash; same three things, read the "
            "same way. From the same simulations as the race card. "
            "100 = a full berth's worth of probability moves on this "
            "game.</p></div>")


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
        # Same convention as the what-if picker and the game cards: the name
        # carries the season its ratings came from.
        label = ("<b>Vegas</b> <span class=dim>(closing line)</span>"
                 if name == "Vegas" else esc(model_label(name, systems)))
        rows.append(
            f"<tr><td>{label}</td><td>{v['w']}–{v['l']}</td>"
            f"<td style='color:{winpct_color(pct)}'>{pct:.3f}</td></tr>")
    return ("<div class=card id=modelcard><h2>Model scorecard</h2>"
            "<table><thead><tr><th>Model</th><th>Favorites</th><th>Pct</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<p class=note>Each system's favorites in completed games "
            "involving Big 12 teams (both sides rated; FCS games skipped). "
            "Judged against the market's closing-line favorites.</p>"
            + scorecard_caveat(games, systems, tal) + "</div>")


def scorecard_caveat(games, systems, tal):
    """Say how badly the comparison is rigged, and in which direction.

    A rating system grading games it was fitted on is not competing with a
    line that was locked before kickoff. On a finished season that is not a
    caveat, it is the whole result — so when a model 'beats' Vegas here, the
    page has to say why before a reader concludes anything."""
    done = [g for g in games if g["completed"] and not g.get("ccg")]
    remaining = [g for g in games if not g["completed"] and not g.get("ccg")]
    beat = [n for n, v in tal.items()
            if n != "Vegas" and "Vegas" in tal and (v["w"] + v["l"])
            and v["w"] / (v["w"] + v["l"])
            > tal["Vegas"]["w"] / max(tal["Vegas"]["w"] + tal["Vegas"]["l"], 1)]
    finished = done and not remaining
    lead = ("<b>These models are not beating the market.</b> " if beat and finished
            else "<b>Read this before comparing the numbers.</b> ")
    if finished:
        body = ("Every rating here is that season's <em>final</em> published "
                "number, fitted on the games it is being graded against — it "
                "knows how they ended. Vegas's number was locked at kickoff, "
                "before any of them were played. A model finishing above the "
                "closing line in this table is measuring hindsight, not skill."
                + (f" ({', '.join(esc(b) for b in sorted(beat))} "
                   f"{'does' if len(beat) == 1 else 'do'} exactly that here.)"
                   if beat else ""))
    else:
        body = ("The models pick with their currently published ratings, "
                "which have already seen the earlier games they are being "
                "graded on; Vegas's number was locked at each kickoff. The "
                "gap flatters the models and grows the further back the "
                "season runs. Respect the house.")
    missing = sorted({"SP+", "FPI", "Elo", "SRS"} - set(systems))
    gap = (f" {', '.join(esc(m) for m in missing)} "
           f"{'is' if len(missing) == 1 else 'are'} absent: no ratings were "
           f"kept for this season." if missing else "")
    return f"<p class=note>{lead}{body}{gap}</p>"


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
            # The same distinction joiner() draws, in one letter. A neutral
            # site has no host, so calling it H for one team and A for the
            # other says something about the crowd that was not true —
            # Kansas hosted Arizona State at Wembley only in the feed's
            # home column.
            where = ("N" if g.get("neutral_site")
                     else "H" if g["home"] == a else "A")
            place = {"N": "neutral site", "H": "home", "A": "away"}[where]
            if g["completed"] and g["home_points"] is not None:
                mine = g["home_points"] if g["home"] == a else g["away_points"]
                theirs = g["away_points"] if g["home"] == a else g["home_points"]
                won = mine > theirs
                color = winpct_color(1.0 if won else 0.0)
                cells.append(
                    f"<td style='color:{color}' title='{esc(a)} "
                    f"{'def.' if won else 'lost to'} {esc(b)} "
                    f"{mine}–{theirs} ({date}, {place})'>"
                    f"<span class=hatag>{where}</span>"
                    f"{'W' if won else 'L'} {mine}–{theirs}</td>")
            else:
                # Away team first, because that is the order "at" describes:
                # the word names the host, so it can only follow the visitor.
                # This used to read from the row team outwards — "Kansas vs
                # Iowa State" for a home game — which spent "vs" on a home
                # game and left nothing to say about a neutral site. joiner()
                # is the one rule for that word; the cell already says which
                # side of it this row's team is on.
                cells.append(
                    f"<td class=dim title='{esc(g['away'])} {joiner(g)} "
                    f"{esc(g['home'])}, {date}'>"
                    f"<span class=hatag>{where}</span>wk {g['week']}</td>")
        body.append(f"<tr><td class=teamcell>{logo_img(a, 14)}"
                    f"{esc(a)}</td>{''.join(cells)}</tr>")
    return ("<div class=card id=h2hcard><h2>Head-to-head grid</h2>"
            '<div class="tablescroll scrollbox"><table class=h2h>'
            '<thead><tr><th></th>'
            + head + "</tr></thead><tbody>" + "".join(body)
            + "</tbody></table></div>"
            "<p class=note>Every conference meeting this season, read "
            "across: H marks a home game, A an away game and N a neutral "
            "site, then the row team's result or the scheduled week. A bullet "
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


def tie_headline(group):
    """"How the three-way tie breaks — Arizona State, BYU and Texas Tech".

    The names used to sit in front of the noun as a compound modifier: "How
    the Arizona State, BYU, Texas Tech tie breaks", which is not a sentence
    anybody says. A comma list cannot modify a noun — it has to be apposed to
    it. Saying how MANY first is also the more useful word, because a
    three-way tie and a two-way tie break by different steps.

    No serial comma, which is what the rest of the site's prose does.
    """
    names = [n for n in group.split("+") if n]
    words = {2: "two", 3: "three", 4: "four", 5: "five",
             6: "six", 7: "seven", 8: "eight"}
    kind = f"{words.get(len(names), len(names))}-way"
    if len(names) < 2:
        joined = esc(names[0]) if names else ""
        return f"How {joined} is placed"
    listed = [esc(n) for n in names]
    joined = (" and ".join(listed) if len(listed) == 2
              else ", ".join(listed[:-1]) + " and " + listed[-1])
    return f"How the {kind} tie breaks &mdash; {joined}"


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


def clinch_card(games, overrides, systems, stand_rows, sims,
                chip="", tail=None):
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
        # Two scales live in this band and they were run together on one
        # line: the score is 0-100, the three parts it is built from are
        # each 0-1. Reading "0 is a decided race, 100 is a sixteen-way
        # pileup" off the end of "still alive 1.00" makes the parts look
        # like they are on the hundred scale and broken. The gloss belongs
        # with the number it describes, and the parts get a line and a
        # scale of their own.
        chaos_html = (
            f"<div class=chaosband>"
            f"<span class=cnum style='color:{ccolor}'>{cx['score']}</span>"
            f"<div><b>Chaos Index: {cx['label']}</b>"
            f"<div class=chaosscale>0 is a decided race, 100 is a "
            f"sixteen-way pileup</div>"
            f"<div class=chaosparts>Built from three parts, each 0 to 1: "
            f"race entropy {comps['entropy']:.2f}, "
            f"tie tangle {comps['tangle']:.2f}, "
            f"still alive {comps['breadth']:.2f}</div>"
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
        bar = pctcell = ""
        if p is not None:
            c = ccg_color(p, TEAM_COUNT)
            bar = (f"<span class=obar><i style='width:{p * 100:.1f}%;"
                   f"background:{c}'></i></span>")
            pctcell = f"<b class=opct style='color:{c}'>{fmt_prob(p)}</b>"
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
        # Every cell gets its own grid column, including the empty ones —
        # a row with no odds still reserves the bar and percent columns, so
        # "clinched" sits under "clinched" all the way down instead of
        # wherever the team name happened to end.
        rows.append(
            f"<div class=clrow><div class=clmain>"
            f"{logo_img(t, 18)}"
            f"<b class=clteam>{esc(t)}</b>"
            f"<span class=clbar>{bar}</span>"
            f"<span class=clpct>{pctcell}</span>"
            f"<span class=cltags>{' '.join(b for b in bits if b)}</span>"
            f"<span class='dim clrec'>{i['w']}–{9 - i['r'] - i['w']}, "
            f"{i['r']} left{exptxt}</span>"
            f"</div>{scen}</div>")
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
    return CLINCH_CARD.format(
        body=body, note=" ".join(notes), chip=chip,
        tail=CLINCH_TAIL if tail is None else tail)


# The percentage needs saying out loud. It is the chance of reaching the
# championship game — which TWO teams do — so the column adds to about 200%
# and not to 100%. Without that sentence a reader takes 57.2% for "chance to
# win the Big 12", then finds the column summing to twice what it should and
# has no way to tell which of the two readings is wrong.
CLINCH_CARD = """<div class=card id=clinchcard>
  <h2>Championship race{chip}</h2>
  <div id=raceout>{body}
  <p class=note>The percentage is the chance of <b>reaching the championship
  game</b>, not of winning it or of finishing first. Two teams get there, so
  these add up to about 200%. {note} {tail}</p></div>
</div>"""

# Everywhere but The Lab, this card is the build's last word on the race.
CLINCH_TAIL = "Reflects real results only — what-if picks don't change it."

# On The Lab it is the first word instead: the server paints the real season
# so the two pages agree at load — same proofs, same 10,000 simulations — and
# race.js takes the card over the moment a pick makes the season hypothetical.
CLINCH_TAIL_LAB = ("Reflects real results until you pick a game; from then on "
                   "it re-runs in your browser on the season your picks "
                   "describe.")



BRIEF_CSS = """
.matchup { display:flex; align-items:center; gap:18px; margin:10px 0 4px;
  flex-wrap:wrap }
.side { display:flex; align-items:center; gap:12px; font-size:var(--t-headline);
  font-weight:700; border-bottom:4px solid var(--line);
  padding:6px 10px 10px 2px }
.tname { letter-spacing:-.01em }
.vs { color:var(--dim); font-weight:400; font-size:var(--t-subhead); padding:0 6px }
.seed { display:inline-block; background:var(--accent); color:#fff;
  border-radius:6px; font-size:var(--t-label); width:22px; height:22px;
  line-height:22px; text-align:center; vertical-align:3px; margin-right:4px }
.badge { font-size:var(--t-fine); border-radius:20px; padding:2px 9px;
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
  font:var(--t-body)/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif }
header { border-bottom:4px solid var(--accent); padding:22px 20px;
  background:var(--panel) }
header h1 { margin:0; font-size:var(--t-headline) } header p { margin:3px 0 0;
  color:var(--dim); font-size:var(--t-label) } header a { color:var(--accent2);
  text-decoration:none }
main { max-width:var(--chrome-w); margin:0 auto; padding:20px;
  display:grid; gap:18px }
.card { background:var(--panel); border:1px solid var(--line);
  border-radius:10px; padding:16px 18px }
.card h2 { margin:0 0 8px; font-size:var(--t-label); text-transform:uppercase;
  letter-spacing:.06em; color:var(--dim) }
/* A link written into a sentence, rather than one of the components that
   carries its own color. There was no rule for these, so the browser's
   #0000EE and its underline were shipping on the two prose pages in this
   section — the only blue on the domain. The Rules page has always said
   accent2 for exactly this; so does the hub. Classless on purpose: every
   component link in this section carries a class, and a plain descendant
   selector would out-specify all of them. */
.card p a:not([class]), .card li a:not([class]) { color:var(--accent2);
  text-decoration:underline; text-underline-offset:2px;
  text-decoration-thickness:1px }
.dim { color:var(--dim) } .note { color:var(--dim); font-size:var(--t-row) }
/* Several marks carry a white plate inside the artwork itself, so on a dark
   page they read as stray white cards. CSS cannot recolor what is baked
   into the file — what it can do is make the plate look intended: one tile,
   same in both themes, that the artwork's own white sits flush against. */
.mark { vertical-align:-3px; margin-right:6px;
  background:#f0ede6; border-radius:4px; padding:2px }
.clrow { padding:7px 0; border-bottom:1px solid var(--line); font-size:var(--t-copy) }
.movemain { display:grid; align-items:center; gap:0 10px;
  grid-template-columns:22px minmax(110px,148px) 62px auto }
.movepts { text-align:right; font-variant-numeric:tabular-nums }
@media (max-width:640px) {
  .movemain { grid-template-columns:22px 1fr auto; row-gap:2px }
}
.levmain { display:grid; align-items:center; gap:0 10px;
  grid-template-columns:minmax(0,1fr) auto 112px 34px }
.levgame { min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap }
.levdate { color:var(--dim); white-space:nowrap }
.levswing { font-size:var(--t-row); margin-top:2px }
@media (max-width:640px) {
  .levmain { grid-template-columns:minmax(0,1fr) auto; row-gap:3px }
  .levbar { display:none }
}
.clmain { display:grid; align-items:center; gap:0 10px;
  grid-template-columns:22px minmax(110px,148px) 112px 46px auto 1fr }
.clteam { min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap }
.clpct { text-align:right }
/* The chips hold their line — a "clinched" that breaks in half is not a
   chip any more. The record after them does not: it is the longest thing on
   the row and the least load-bearing, so when the row runs out of width it
   is the one that gives. Set nowrap it did the opposite, holding the whole
   card open past the column it was sitting in. */
.cltags { white-space:nowrap }
.clrec { min-width:0 }
@media (max-width:640px) {
  .clmain { grid-template-columns:22px 1fr auto; row-gap:3px }
  .clbar, .clpct { display:none }
}

.clrow:last-of-type { border-bottom:none }
.obar { display:inline-block; width:100px; height:8px; background:var(--line);
  border-radius:4px; overflow:hidden; vertical-align:1px; margin:0 6px 0 8px }
.obar i { display:block; height:100%; border-radius:4px }
.opct { font-variant-numeric:tabular-nums; font-size:var(--t-row) }
.chaosband { display:flex; align-items:center; gap:14px; border:1px solid
  var(--line); border-radius:8px; padding:10px 14px; margin-bottom:10px;
  font-size:var(--t-row) }
.chaosscale { color:var(--dim); font-size:var(--t-meta); margin-top:2px }
.chaosparts { color:var(--dim); font-size:var(--t-meta); margin-top:3px;
  font-variant-numeric:tabular-nums }
.cnum { font-size:var(--t-hero); font-weight:800; line-height:1 }
.tag { font-size:var(--t-micro); border-radius:20px; padding:2px 8px; font-weight:700 }
.tag.live { background:#13653626; color:#136536 }
.tag.destiny { background:#b4530926; color:var(--warn) }
.scen { margin:5px 0 2px; padding-left:20px; font-size:var(--t-row); color:var(--dim) }
.elim { font-size:var(--t-row) } ul.games { list-style:none; padding:0; margin:0 }
ul.games li { padding:5px 0; border-bottom:1px solid var(--line);
  font-size:var(--t-label) } .ccgtag { color:var(--accent); font-weight:700;
  font-size:var(--t-fine); text-transform:uppercase }
/* One non-conference marker for the whole site. It was a pill in the Lab's
   own stylesheet and bare parenthetical text everywhere else, so the same
   fact about the same game looked like two different things depending on
   which page you read it on. Lives here now, where every page can see it. */
.nctag { color:var(--dim); font-size:var(--t-micro); border:1px solid var(--line);
  border-radius:20px; padding:1px 7px; text-transform:uppercase;
  letter-spacing:.04em; white-space:nowrap }
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
                # Columns, like every other row list on this site. Run
                # inline, the team name's length decided where the swing
                # and the before/after landed on each line.
                out.append(
                    f"<div class=clrow><div class=movemain>"
                    f"{logo_img(t, 16)}<b class=clteam>{esc(t)}</b>"
                    f"<span class=movepts style='color:{col}'>"
                    f"{'+' if d > 0 else ''}{d * 100:.0f} pts</span>"
                    f"<span class=dim>{fmt_prob(was)} &rarr; {fmt_prob(now)}"
                    f" to reach the title game</span>"
                    f"</div></div>")
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

    lev = leverage_of(sims)
    if lev:
        items = "".join(
            f"<li>{logo_img(e['away'], 14)}{esc(e['away'])} at "
            f"{logo_img(e['home'], 14)}{esc(e['home'])} <span class=dim>"
            f"&mdash; {e['total'] * 100:.0f} of a title-game seat "
            f"changes hands</span></li>" for e in lev[:3])
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
.drawwrap { overflow-x:auto }
.drawchart { width:100%; min-width:660px; height:auto; display:block;
  margin:12px 0 2px }
.drawchart .dgrid { stroke:var(--line); stroke-width:1 }
.drawchart .dlabel { font-size:var(--t-fine); font-weight:600; fill:var(--ink) }
.drawchart .dval { font-size:var(--t-fine); font-weight:600;
  font-variant-numeric:tabular-nums }
.drawchart .dtick { fill:var(--dim); font-size:var(--t-fine) }
/* No hover-dim here. The bump chart fades its other lines because sixteen
   of them overlap and you need to follow one; sixteen separate bars do not
   overlap, so the same rule just grays out the chart you are reading. */
.drawkey { margin: 6px 0 12px; padding-left: 20px; }
.drawkey li { margin: 6px 0; }
.drawgridtable td.dcell { text-align: right;
  font-variant-numeric: tabular-nums; }
.drawgridtable td.dcell.own { outline: 2px solid var(--ink);
  outline-offset: -2px; font-weight: 700; }
.laddertable td, .cuttable td { vertical-align: middle; }
/* Seven rows of "(a) head-to-head — 28". Stretched across the full chrome
   the count drifted most of a page away from the step it counts, and the
   head was a cell short of the body, so the numbers ran under no header at
   all. Two columns of prose, one of figures, at a width you can read across
   in one go. */
.stepdepth { max-width: 34rem; }
.stepdepth th:last-child, .stepdepth td:last-child { text-align: right;
  font-variant-numeric: tabular-nums; }
.samerec { color: var(--warn); font-weight: 600; font-size: var(--t-meta); }
.misslist { list-style:none; margin:0; padding:0; display:flex;
  flex-wrap:wrap; gap:3px 0 }
/* Each entry is its own little grid too, so the marks, the abbreviations
   and the years each line up in a column rather than sitting wherever the
   previous word ended. */
.misslist li { display:grid; grid-template-columns:19px 44px auto;
  align-items:center; white-space:nowrap; width:144px;
  padding-right:12px; box-sizing:border-box }
.misslist li .mabbr { font-weight:600 }
/* Left, not right: pushed to the far edge of its own cell the year lands
   against the NEXT team's mark, and the row reads "UCF 2024BAY". */
.misslist li .myr { color:var(--dim); font-variant-numeric:tabular-nums;
  padding-left:5px }

.firstlist { list-style: none; margin: 8px 0; padding: 0; font-size: var(--t-subhead); }
.rotationtable { width:100% }
.rotationtable td { vertical-align:middle }
.rotationtable td:first-child { white-space:nowrap }
.warnpill { color: var(--warn); font-weight: 600; font-size: var(--t-meta); }
.drawsum td.num, .drawgridtable td { text-align: right;
  font-variant-numeric: tabular-nums; }
/* A HEADER ALIGNS WITH THE COLUMN IT LABELS. The figures in this table are
   right-aligned and every th was inheriting the table default of left, so
   "vs average" sat a full column-width away from the numbers it named and
   read as the label for whatever was to its left. .stbl and .stepdepth
   already pair their th with their td; this table was the one that did not.
   The spanning headers are centred because each covers a right-aligned
   figure and the team abbreviation beside it, and the pair reads as one
   thing. */
.drawsum th { text-align: right; }
.drawsum th:first-child { text-align: left; }
.drawsum th[colspan] { text-align: center; }
.drawsum td.dim, .drawgridtable td.dim { color: var(--dim); }
.drawgridtable th { font-weight: 600; font-size: var(--t-meta); }
table { border-collapse:collapse; width:100%; font-size:var(--t-label) }
th, td { text-align:left; padding:6px 9px; border-bottom:1px solid
  var(--line); font-variant-numeric:tabular-nums }
th { font-size:var(--t-fine); text-transform:uppercase; letter-spacing:.05em;
  color:var(--dim) }
thead tr th { border-bottom:2px solid var(--line) }
.teamcell { white-space:nowrap }
.briefstamp { color:var(--dim); font-size:var(--t-row); text-align:center; margin:-4px 0 2px }
tr.grpend td { border-bottom:2px solid var(--line) }
.stbl td:last-child, .stbl th:last-child { text-align:right }
.stbl td { height:38px }
.duo.even { grid-template-columns:minmax(0,1fr) minmax(0,1fr) }
.duo.even > .stack { align-content:stretch }
.duo.even .card { height:100% }
.posc { white-space:nowrap; vertical-align:top; color:var(--dim); font-variant-numeric:tabular-nums }
h3.wkhead { font-size:var(--t-row); text-transform:uppercase; letter-spacing:.05em;
  color:var(--dim); margin:16px 0 4px }
/* The week, as cards rather than rows. Sixteen one-line rows read as an
   index of games; the week deserves to look like the week. Two up, not
   four: at the chrome width four columns leave a card too narrow for
   "Bill Snyder Family Stadium", and every line inside a card is written to
   hold one fact and stay on one line. */
/* Same reasoning as the game page: auto-fit columns are still rows, and a
   game with a forecast, a broadcast and a line sat beside one with none left
   the short card padded out to match. column-width keeps the "two up at the
   chrome width, one on a phone" behavior the repeat(auto-fit, minmax(...))
   was chosen for, without pairing the cards into rows. */
.slatelist { columns:30rem; column-gap:10px; margin-top:2px }
.slate { background:var(--bg); border:1px solid var(--line);
  border-radius:10px; padding:11px 13px; min-width:0;
  break-inside:avoid; margin-bottom:10px }
.slateteams { font-size:var(--t-copy); margin-bottom:8px }
.slatemeta { font-size:var(--t-meta); color:var(--dim); display:grid; gap:4px }
  /* Two columns of facts once the card is wide enough for them. Five
     single-line facts stacked made a tall card that was mostly one short
     phrase per row, and the week is sixteen of these. Below 520px the card is
     already narrow enough that a second column would only truncate. */
  @media (min-width:520px) {
    .slatemeta { grid-template-columns:1fr 1fr; gap:4px 14px }
    /* The kickoff spans. It carries two clocks — the venue's and the
       reader's — and is the longest line on the card by some way; halved it
       lost the zone off the end, which is the part that makes it a time
       rather than a number. The other four facts pair up beneath it. */
    .slatemeta > .slatewhen { grid-column:1 / -1 }
  }
.slatemeta > div { display:flex; align-items:center; gap:7px;
  min-width:0; white-space:nowrap }
.slatemeta > div > :not(svg) { overflow:hidden; text-overflow:ellipsis }
.slatewhen time, .slatetv { color:var(--ink) }
.slatewhen time { font-variant-numeric:tabular-nums; font-weight:600 }
.yourtime { color:var(--dim); font-weight:400 }
/* A kickoff a school publishes differently. Marked, not overwritten. */
.timeflag { color:var(--warn); text-decoration:none; cursor:help;
  font-weight:700; margin-left:2px }
.slatewx, .slateline { font-variant-numeric:tabular-nums }
.wxwarn { color:var(--warn) }
/* Decorative, and sized to the line rather than to the icon: they should
   read as marginalia, never as buttons. */
.gi { width:15px; height:15px; flex:0 0 15px; color:var(--dim);
  opacity:.85 }
.slatelinks { display:flex; gap:16px; margin-top:9px; padding-top:8px;
  border-top:1px solid var(--line); font-size:var(--t-meta) }
/* On a slate card that rule separates the links from five rows of facts
   above them. In the Elsewhere card there is nothing above them but the
   heading, which already does the separating — so the rule read as a second
   divider and the extra 17px above it made the card look like it had been
   padded differently from every other card on the page. It had not; it had
   an internal border nobody else had. */
#elsewhere .slatelinks { margin-top:0; padding-top:0; border-top:0 }
.slatelink { display:inline-flex; align-items:center; gap:5px;
  font-weight:600; white-space:nowrap; color:var(--accent);
  text-decoration:none }
.slatelink.dim { color:var(--dim); font-weight:400 }
.slatelink:hover { text-decoration:underline }
.slatelink:visited { color:var(--accent) }
.slatelink.dim:visited { color:var(--dim) }

/* ---- one game ---- */
/* Two columns once there is room. None of these cards needs the full 1120px:
   the market is four short figures and the pick'em split is a single bar, so
   at full width each was mostly gutter and the reader scrolled past
   whitespace to reach the next fact. The back link and the matchup stay full
   width, being the page's heading rather than one of its panels.

   main:has() rather than a class, because build_subpage owns the <main> and
   #gamehead is on every game page and nothing else. Where :has is missing the
   page simply stays one column, which is what it already was. */
/* Columns, not a grid, and the difference is the whole point. A grid puts
   these cards in ROWS, and a row is as tall as its tallest member — so a
   short market card beside a tall model card leaves a hole the height of the
   difference, and the page reads as broken rather than dense. Nothing here
   is a row: the cards are independent and their only relationship is order.
   Flowing them down columns lets each one end where its content ends.
   attendance/site/styles.css does the same thing for its chart panels. */
@media (min-width: 900px) {
  /* 18px, the same rhythm as every other page in the section: main is a
     grid with gap:18px everywhere else, and .duo uses 18 as well. This page
     is the one that reaches for columns instead of a grid, and it arrived
     carrying its own 14 — close enough to look like a mistake rather than a
     choice, which is exactly what it was. */
  main:has(#gamehead) { display:block; columns:2; column-gap:18px }
  /* A card must not be split down the middle of a column break. */
  main:has(#gamehead) > .card { break-inside:avoid; margin-bottom:18px }
  /* The header spans, the way it did when this was a grid. */
  main:has(#gamehead) > .gameback,
  main:has(#gamehead) > #gamehead { column-span:all }
  /* THE LINKS CARD DOES NOT SPAN, and used to. The argument for spanning was
     balancing: a multi-column flow fills in source order and cannot be told
     where to put a given card, so a short card could land under a tall one
     and leave a column looking half-used. Taking it out of the flow entirely
     avoided that.

     What it bought instead was worse to look at. Every other card on the page
     is one column wide; this one became a 907px band holding a single 40px
     link, with the rest of the width empty. It was the only content card that
     did not match its neighbors, and that is what a reader notices — not the
     column balance, which they have nothing to compare against.

     So it flows like everything else. Checked on both shapes this page comes
     in: a non-conference game with three content cards, where it settles under
     the models card, and a conference game with the series card as well, where
     it settles under that. Both balance. The header above still spans, because
     a header genuinely is the full width of the page. */
}
.gameback { margin:2px 0 12px; font-size:var(--t-row) }
.gameback a { color:var(--accent); text-decoration:none }
.gameback a:hover { text-decoration:underline }
/* THE PAGE'S TYPE SCALE, in one place, because it went wrong by being in
   several. The matchup is what this page is ABOUT, and it was 19px at weight
   400 — smaller and lighter than a figure inside a card below it (.mkval,
   20/600), so the eye landed on a spread rather than on the game. The order
   now runs: the matchup, then the figures, then the stadium, then the card
   labels, then the rows.
     matchup   24 / 700    the subject
     figures   20 / 600    .mkval, in the market and venue cards
     stadium   17 / 600    .venname, a name inside a card
     labels    14 / 700    the uppercase h2s
     rows    13.5 / 400    everything else
   Anything added here should be placed against that list rather than sized
   to look right on its own. */
.gamematch { font-size:var(--t-headline); font-weight:700; letter-spacing:-.01em;
  margin-bottom:11px; line-height:1.25 }
/* The joiner carries none of that weight — it is the quietest word in the
   line and was inheriting 24px along with the team names. */
.gamematch .dim { font-size:.7em; font-weight:400 }
.gamematch .nctag, .gamematch .ccgtag { vertical-align:4px }
#gamehead .slatemeta { font-size:var(--t-row) }
/* The stadium, at the weight it deserves on a page about one game. The city
   sits beside it rather than under it: together they are one answer to one
   question, and stacked they read as two facts. */
.venname { font-size:var(--t-subhead); font-weight:600; margin:0 0 12px;
  line-height:1.35 }
.vencity { color:var(--dim); font-weight:400 }
.venname .nctag { vertical-align:2px }
/* Glyphs on the label line rather than beside the figure. The figure is the
   thing being read; a mark next to it competes with it, and the label is
   where a reader goes when the number alone is ambiguous — which is exactly
   when "is that wind or rain?" gets asked. Muted, and never the only signal:
   the words are still there. */
.vengi { color:var(--dim); margin-right:5px; vertical-align:-2px }
.venname .vengi { color:var(--dim); vertical-align:-1px }
#venuecard .mkgrid .dim { display:flex; align-items:center; gap:0 }
/* The market is four figures, and figures want columns rather than a
   paragraph: the number first, what it is underneath. */
/* 150, not 120: at 120 a favourite with a long name broke across two lines
   ("West Virginia" / "-19.5"), which reads as two figures rather than one. */
.mkgrid { display:grid; gap:12px;
  grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  font-variant-numeric:tabular-nums }
.mkval { font-size:var(--t-lead); font-weight:600 }
.mkgrid .dim { font-size:var(--t-meta) }
/* Model against market: the same bar the race card uses, so a reader who
   knows one knows the other. */
/* 58px, not 44. The label track was cut to fit SP+, FPI, ELO and SRS, and
   then the market row arrived with a six-letter word in it: "MARKET" is wider
   than the column, so it ran straight into the team name and printed
   "MARKETTCU". Fixed rather than max-content, because each row is its own
   grid — a track that sizes to its own content stops the bars lining up down
   the card, which is the only reason this is a grid at all. */
.mline { display:grid; grid-template-columns:58px minmax(0,1fr);
  align-items:center; gap:10px; margin:6px 0; font-size:var(--t-row) }
.msys { color:var(--dim); font-size:var(--t-meta); text-transform:uppercase;
  letter-spacing:.04em; overflow:hidden; text-overflow:ellipsis }
/* The season the rating comes from, under its name. Preseason these are last
   year's numbers, and the row has to say so where the name is — the caveat in
   the note below is read after the bars, if at all. */
.msys i { display:block; font-style:normal; font-size:var(--t-micro); opacity:.75;
  letter-spacing:0; font-variant-numeric:tabular-nums }
.mrow { display:grid; align-items:center; gap:10px;
  grid-template-columns:minmax(0,140px) minmax(0,1fr) 42px }
.mname { overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.mbar { background:var(--line); border-radius:4px; height:8px;
  overflow:hidden }
.mbar i { display:block; height:100%; background:var(--accent) }
.mrow.market .mbar i { background:var(--dim) }
.mval { text-align:right; font-variant-numeric:tabular-nums }
.mmarket { border-top:1px solid var(--line); padding-top:8px; margin-top:8px }
.levtotal { margin-bottom:10px; font-size:var(--t-label) }
/* The swing bars that used to sit under the fork are gone, and .swkey is
   what is left of them — the note they were keyed to, which now explains the
   fork instead. A bar sorted its two ends high to low and so could not say
   which RESULT produced which end; inside the fork the branch is the box the
   number stands in. The class name outlived the component and is kept only
   because the note is still the note. */
.swkey { margin-top:10px }
.levtotal b { font-size:var(--t-lead); font-variant-numeric:tabular-nums }
@media (max-width:560px) {
  .mrow { grid-template-columns:minmax(0,1fr) 42px }
  .mrow .mbar { display:none }
}
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
.h2h th, .h2h td { padding:6px 4px; font-size:var(--t-row); white-space:nowrap;
  text-align:center }
.h2h td.teamcell, .h2h thead th:first-child { text-align:left;
  padding-left:2px }
.h2h thead th { font-size:var(--t-meta); letter-spacing:.02em }
.hatag { display:inline-block; min-width:11px; margin-right:4px;
  font-size:var(--t-micro); font-weight:700; color:var(--dim); vertical-align:1px }
/* The empty cells carry meaning — a third of this grid is pairs the
   schedule never makes — so they need to be visible, not ghosts. */
.selfcell { color:var(--dim); opacity:.75;
  background:color-mix(in srgb, var(--dim) 12%, transparent) }
.nomeet { color:var(--dim); opacity:.75; font-size:var(--t-copy); line-height:1 }

/* ---- season replay ---- */
#replaybar .rpline { display:flex; align-items:center; gap:10px;
  flex-wrap:wrap }
#replaybar input[type=range] { flex:1 1 200px; min-width:140px;
  accent-color:var(--accent) }
@media (max-width:640px) {
  #replaybar #rp-label { flex:1 0 100%; min-width:0 !important; order:9 }
  #replaybar #rp-now { order:8 }
}
#replaybar #rp-label { font-size:var(--t-label); color:var(--dim) }
#replaybar #rp-prev, #replaybar #rp-next { padding:6px 10px }
#replaycard.scrubbed { border-color:var(--accent) }
/* keeps its space so the slider never changes width */
#rp-now.invis { visibility:hidden }
.mv { font-size:var(--t-fine); margin-left:5px; font-weight:600 }
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

/* the replay's own controls, same chrome as the Lab's — one rule, two pages,
   so a control never looks like two different kinds of thing on a site where
   the reader crosses between them in a click. Denser than it was and pressable
   rather than merely bordered: a smaller label, a tighter box, a hairline of
   lift, and a press that actually moves. Flex is scoped to button/a because a
   <select> wears this class too and has to keep its native control box. */
.wbtn { font:inherit; font-size:var(--t-row); font-weight:600; line-height:1.5;
  border:1px solid var(--line); background:var(--panel); color:var(--ink);
  border-radius:7px; padding:5px 11px; cursor:pointer; white-space:nowrap }
button.wbtn, a.wbtn { display:inline-flex; align-items:center; gap:6px;
  text-decoration:none; box-shadow:0 1px 0 rgba(0,0,0,.05) }
.wbtn:hover { border-color:var(--accent); background:var(--bg) }
button.wbtn:active { transform:translateY(1px); box-shadow:none }
.wbtn:focus-visible { outline:2px solid var(--accent); outline-offset:2px }
/* Decorative like .gi, but sized to the button and coloured by the job the
   button does rather than by what it depicts: chalk the model lays down is
   warn, anything that only changes the view is accent2, and anything that
   acts on the board itself is accent. Three colours, three meanings. */
.bi { width:14px; height:14px; flex:0 0 14px; color:var(--accent) }
.bi-chalk { color:var(--warn) }
.bi-view { color:var(--accent2) }
/* Related controls sit closer to each other than to the next group, and a
   group is atomic: the row breaks between groups rather than through the
   middle of one. Stretch rather than center, because a native <select> lays
   its text out on its own terms and comes out three pixels shorter than the
   buttons beside it — a row that reads as assembled rather than designed.
   Stretching lets the tallest control set the height and the rest meet it.

   ATOMIC UNTIL IT CANNOT BE. flex:0 0 auto said "never break", and never is
   longer than the row: the chalk group is a select and two buttons that all
   refuse to wrap, so in a card column it simply grew past the card and out
   the side of it. Shrinking and wrapping are last resorts here rather than
   defaults — a flex line takes whole items first, so a group only ever gets
   squeezed once it is alone on its line and still too wide, which is the
   case that used to overflow. That is also why the old max-width:520px rule
   for this is gone: it said the same thing for phones only, and the width
   that matters is the card's, not the window's. */
.wgroup { display:flex; align-items:stretch; gap:6px; flex:0 1 auto;
  flex-wrap:wrap; min-width:0 }
.wgroup > label { display:flex; align-items:center }

/* ---- how the season moved ---- */
.bumpwrap { overflow-x:auto }
.bump { width:100%; min-width:660px; height:auto; display:block }
.bump .bgrid { stroke:var(--line); stroke-width:1 }
.bump .btick { fill:var(--dim); font-size:var(--t-fine) }
.bump .bnum { text-anchor:end }
.bump .bwk { text-anchor:middle }
.bump .blabel { font-size:var(--t-fine); font-weight:600 }
.bump .bteam { transition:opacity .12s ease }
.bumpwrap:hover .bteam { opacity:.22 }
.bumpwrap .bteam:hover { opacity:1 }
.bumpwrap .bteam:hover polyline { stroke-width:3.6 }
"""


def build_subpage(title, active, body, year, matchcard,
                  canon=None, desc=None, head="", section="tiebreaker",
                  page="", up="", subnavon=True):
    sect_title = SECTIONS[section]["title"]
    head_title = (sect_title if title == sect_title
                  else f"{title} — {sect_title}")
    # rel=alternate claims another representation of THIS page. From a
    # pick'em page BASE points at ../tiebreaker/, so the unconditional version
    # advertised the tiebreaker's feed as an alternate form of the slate.
    rss = ("" if section == "pickem"
           else f"<link rel=alternate type=application/rss+xml "
                f"href={BASE}feed.xml>")
    social = ""
    if canon:
        social = f"""<link rel=canonical href="{canon}">
<meta name=description content="{esc(desc or '')}">
<meta property=og:type content=website>
<meta property=og:site_name content=Big12ology>
<meta property=og:title content="{esc(head_title)}">
<meta property=og:description content="{esc(desc or '')}">
<meta property=og:url content="{canon}">
<meta property=og:image content="https://big12ology.com/tiebreaker/og.png">
<meta property=og:image:width content=1200>
<meta property=og:image:height content=630>
<meta name=twitter:card content=summary_large_image>"""
    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{esc(head_title)}</title>
{social}
<link rel=icon type=image/svg+xml href="{BASE}favicon.svg">
<link rel=icon type=image/png sizes=32x32 href="{BASE}favicon-32.png">
<link rel=apple-touch-icon href="{BASE}favicon-180.png">
<script>(function(){{try{{var t=localStorage.getItem("b12-theme");if(t==="light"||t==="dark"){{document.documentElement.setAttribute("data-theme",t);document.documentElement.style.colorScheme=t;}}else{{document.documentElement.style.colorScheme="light dark";}}}}catch(e){{}}}})();</script>
<script>(function(){{try{{var b=localStorage.getItem("b12-cards");if(!b)return;var o=JSON.parse(b);if(!o||o.v!==1||!o.d)return;var l=o.d[location.pathname];if(!l||!l.length)return;var s=l.filter(function(k){{return /^[A-Za-z][\w-]*$/.test(k)}}).map(function(k){{return"#"+k+">*:not(h2):not(h3){{display:none}}#"+k+"{{padding-bottom:8px}}"}}).join("");if(!s)return;var e=document.createElement("style");e.id="b12-precollapse";e.textContent=s;document.head.appendChild(e)}}catch(e){{}}}})();</script>
<link rel=stylesheet href="{BASE}{asset_v("brand.css")}">
<script defer src="{BASE}{asset_v("theme.js")}"></script>
<script src="{BASE}{asset_v("state.js")}"></script>
<script src="{BASE}{asset_v("metrics.js")}"></script>
<script defer src="{BASE}{asset_v("cards.js")}"></script>
{rss}
<style>{BRIEF_CSS}{SUBPAGE_EXTRA_CSS}</style>
<script defer src="{BASE}{asset_v("scrollcue.js")}"></script>{head}</head><body>
<a class=skip-link href="#main">Skip to content</a>
{topbar(section, year, BASE, acct=section in POOL_SECTIONS)}
{tracker_top(year, active, matchcard, section, page, up, subnavon=subnavon,
             yearpills=section not in POOL_SECTIONS)}
{body}
</main>
{footer()}
<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "355e765d921e4b36ad2bf78d509eae6c"}}'></script>
</body></html>"""


# Times are stored as UTC and read by people in six zones. The server writes
# Eastern, which is how the sport publishes a kickoff and is a defensible
# answer with no JavaScript at all; the inline script below rewrites each one
# to the reader's own zone and says which zone that is. A time with no zone
# beside it is the actual failure here — it reads as local and is not.
LOCAL_TIME_JS = """<script>
(function () {
  // The server writes the kickoff in the venue's own zone, because that is
  // how schools publish it and how a reader checks this page against
  // theirs. This adds the reader's own clock beside it, and only when the
  // two actually differ — telling somebody in Tucson that 7pm MST is 7pm
  // their time is noise.
  //
  // It sits in <head>, so the rows do not exist yet; an inline script
  // cannot be deferred, and waiting for the parser is the whole job.
  function localize() {
    var mine;
    try { mine = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) {}
    document.querySelectorAll("time[data-kick]").forEach(function (el) {
      var d = new Date(el.getAttribute("datetime"));
      if (isNaN(d)) return;
      var venue = el.getAttribute("data-tz");
      if (!mine || !venue) return;
      var fmt = function (tz) {
        return new Intl.DateTimeFormat([], {
          hour: "numeric", minute: "2-digit", timeZone: tz
        }).format(d);
      };
      var here, there;
      try { here = fmt(mine); there = fmt(venue); } catch (e) { return; }
      if (here === there) return;
      var span = document.createElement("span");
      span.className = "yourtime";
      span.textContent = " (" + here + " your time)";
      el.insertAdjacentElement("afterend", span);
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", localize);
  } else {
    localize();
  }
})();
</script>"""


# The glyph set, drawn once per page and referenced by <use>. A webfont for
# two dozen icons is absurd — this is a couple of kilobytes, needs no request,
# and inherits currentColor, so both themes and the accent colors come free.
# Paths from Tabler Icons (MIT, https://tabler.io/icons); the same treatment
# the Archivo license gets in fonts/OFL.txt.
ICONS = {
    "clock": "<circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 3'/>",
    "pin": ("<path d='M9 11a3 3 0 1 0 6 0a3 3 0 0 0 -6 0'/>"
            "<path d='M17.657 16.657 13.414 20.9a2 2 0 0 1 -2.827 0l-4.244"
            " -4.243a8 8 0 1 1 11.314 0z'/>"),
    "tv": ("<rect x='3' y='7' width='18' height='13' rx='2'/>"
           "<path d='M16 3l-4 4l-4 -4'/>"),
    # A roof over a floor, for the games where the sky is not part of the
    # story. Tabler's "building-arch", same license as the rest.
    "roof": ("<path d='M3 21h18'/><path d='M4 21v-10a8 8 0 0 1 16 0v10'/>"
             "<path d='M9 21v-9a3 3 0 0 1 6 0v9'/>"),
    # Tabler's "temperature". The sun glyph already means "fine weather" on
    # the slate, so it cannot also mean "this is the temperature" on a card
    # that prints wind and rain beside it.
    "temp": ("<path d='M10 13.5a4 4 0 1 0 4 0v-8.5a2 2 0 0 0 -4 0v8.5'/>"
             "<path d='M10 9h4'/>"),
    "sun": ("<circle cx='12' cy='12' r='4'/><path d='M3 12h1m8 -9v1m8 8h1m-9"
            " 8v1m-6.4 -15.4l.7 .7m12.1 -.7l-.7 .7m0 11.4l.7 .7m-12.1 -.7l-.7"
            " .7'/>"),
    "wind": ("<path d='M5 8h8.5a2.5 2.5 0 1 0 -2.34 -3.24'/>"
             "<path d='M3 12h15.5a2.5 2.5 0 1 1 -2.34 3.24'/>"
             "<path d='M4 16h5.5a2.5 2.5 0 1 1 -2.34 3.24'/>"),
    "rain": ("<path d='M7 18a4.6 4.4 0 0 1 0 -9a5 4.5 0 0 1 11 2h1a3.5 3.5 0"
             " 0 1 0 7h-1'/><path d='M11 20v1m4 -3v1m-8 -1v1'/>"),
    "history": ("<path d='M12 8v4l3 3'/><path d='M3.05 11a9 9 0 1 1 .5 4m-.5"
                " 5v-5h5'/>"),
    "chart": ("<path d='M3 20h18'/><rect x='5' y='12' width='4' height='8'/>"
              "<rect x='10' y='8' width='4' height='12'/>"
              "<rect x='15' y='4' width='4' height='16'/>"),
    "note": ("<rect x='4' y='4' width='16' height='16' rx='2'/>"
             "<path d='M9 8h6M9 12h6M9 16h3'/>"),
    "out": ("<path d='M12 6H6a2 2 0 0 0 -2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2"
            " -2v-6'/><path d='M11 13l9 -9'/><path d='M15 4h5v5'/>"),
    # The Lab's controls. Every one of them is paired with a word, so they are
    # decoration in the same sense the slate's are — but they carry the colour
    # that says which of three jobs a button does, and the ones on a toggle
    # flip with the label so the glyph never contradicts the word beside it.
    "star": ("<path d='M12 17.75l-6.172 3.245l1.179 -6.873l-5 -4.867l6.9"
             " -1.002l3.086 -6.253l3.086 6.253l6.9 1.002l-5 4.867l1.179"
             " 6.873z'/>"),
    # Tabler's "sparkles", for filling in only the gaps: the same chalk as the
    # star, applied where the reader has not spoken.
    "spark": ("<path d='M16 18a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2"
              " 2 0 0 1 -2 2z'/><path d='M16 6a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2"
              " 2 0 0 1 -2 -2a2 2 0 0 1 -2 2z'/><path d='M9 18a6 6 0 0 1 6"
              " -6a6 6 0 0 1 -6 -6a6 6 0 0 1 -6 6a6 6 0 0 1 6 6z'/>"),
    "chevdown": "<path d='M7 7l5 5l5 -5'/><path d='M7 13l5 5l5 -5'/>",
    "chevup": "<path d='M7 11l5 -5l5 5'/><path d='M7 17l5 -5l5 5'/>",
    "filter": ("<path d='M4 4h16v2.172a2 2 0 0 1 -.586 1.414l-4.414"
               " 4.414v7l-6 2v-8.5l-4.48 -4.928a2 2 0 0 1 -.52"
               " -1.345v-2.227z'/>"),
    "filteroff": ("<path d='M8 4h12v2.172a2 2 0 0 1 -.586 1.414l-3.914"
                  " 3.914m-.5 3.5v4l-6 2v-8.5l-4.489 -4.923a2 2 0 0 1 -.511"
                  " -1.34v-2.237'/><path d='M3 3l18 18'/>"),
    "share": ("<path d='M6 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0'/>"
              "<path d='M18 6m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0'/>"
              "<path d='M18 18m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0'/>"
              "<path d='M8.7 10.7l6.6 -3.4'/><path d='M8.7 13.3l6.6 3.4'/>"),
    "check": "<path d='M5 12l5 5l10 -10'/>",
    "eraser": ("<path d='M19 20h-10.5l-4.21 -4.3a1 1 0 0 1 0 -1.41l10 -10a1 1"
               " 0 0 1 1.41 0l5 5a1 1 0 0 1 0 1.41l-9.2 9.3'/>"
               "<path d='M18 13.3l-6.3 -6.3'/>"),
}


def icon_sprite():
    """The whole set, hidden, once per page."""
    syms = "".join(
        f"<symbol id='i-{k}' viewBox='0 0 24 24' fill='none' "
        f"stroke='currentColor' stroke-width='2' stroke-linecap='round' "
        f"stroke-linejoin='round'>{v}</symbol>" for k, v in ICONS.items())
    return (f"<svg class=sprite width=0 height=0 aria-hidden=true "
            f"style='position:absolute'>{syms}</svg>")


def icon(name, cls="gi"):
    """Decorative by contract: the text beside it always says the same
    thing, so a reader who never sees the glyph loses nothing."""
    return (f"<svg class='{cls}' aria-hidden=true><use href='#i-{name}'/>"
            f"</svg>")


def load_time_notes():
    """Games whose kickoff a school publishes differently. Flags, not
    corrections — see data/time-notes.json."""
    p = os.path.join(HERE, "data", "time-notes.json")
    try:
        raw = json.load(open(p))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


TIME_NOTES = load_time_notes()


def time_note(g):
    """A marker beside a contested kickoff. The number stays as fetched —
    everything else on the site is computed from it, and a clock edited in
    one place drifts from the rest in silence. This says the source is
    contested and names the other one."""
    n = TIME_NOTES.get(str(g.get("id")))
    if not n:
        return ""
    said = n.get("published", "")
    try:
        h, m = (int(x) for x in said.split(":"))
        ampm = "AM" if h < 12 else "PM"
        said = f"{h % 12 or 12}:{m:02d} {ampm}"
    except (ValueError, AttributeError):
        pass
    title = (f"{n.get('note', '')} Shown as fetched from "
             f"collegefootballdata.com; {esc(n.get('source', 'the school'))} "
             f"lists {said}.")
    return (f"<abbr class=timeflag title=\"{esc(title)}\" "
            f"aria-label=\"{esc(title)}\">*</abbr>")


def kickoff(g):
    """Kickoff in the venue's own zone, which is how every school and the
    conference publish it — a reader checking this page against a school's
    page should find the same number. The script beside it adds the
    reader's own time when the two differ, so nobody has to do the
    arithmetic either.

    Eastern is the fallback for a venue we have no zone for. CFBD publishes
    a placeholder hour for games with no window announced, so a TBD game
    says so rather than inventing 8pm.
    """
    iso = g.get("start")
    if not iso:
        return "<span class=dim>time TBD</span>"
    if g.get("start_tbd"):
        return (f"<span class=dim>{esc(pretty_date(iso))} · time TBD</span>")
    try:
        when = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "<span class=dim>time TBD</span>"
    tzname = g.get("venue_tz") or "America/New_York"
    try:
        zone = zoneinfo.ZoneInfo(tzname)
    except Exception:
        tzname, zone = "America/New_York", zoneinfo.ZoneInfo("America/New_York")
    local = when.astimezone(zone)
    # %Z gives the zone the venue actually keeps — MST for Arizona in
    # September, where its neighbors are on MDT. That distinction is the
    # whole reason a kickoff gets misread by an hour.
    label = local.strftime("%Z") or "local"
    stamp = (f'<time data-kick datetime="{esc(iso)}" '
             f'data-tz="{esc(tzname)}">'
             f"{_DOW[local.weekday()][:3]} {local.strftime('%-I:%M %p')} "
             f"{esc(label)}</time>")
    return stamp + time_note(g)


def where(g):
    """Venue and city, once the season's data carries them. Fetches before
    the venue fields existed simply have nothing to say here."""
    bits = [g.get("venue"), g.get("venue_city")]
    plain = " · ".join(b for b in bits if b)
    line = " · ".join(esc(b) for b in bits if b)
    if not line:
        return ""
    if g.get("neutral_site"):
        line += " <span class=dim>(neutral)</span>"
        plain += " (neutral)"
    # Wrapped, and this is not decoration. The card's two-column row clips
    # long lines with .slatemeta > div > :not(svg), which is an ELEMENT
    # selector — a bare text node matches nothing, so the venue never
    # truncated and "Bill Snyder Family Stadium · Manhattan, KS" ran straight
    # out of its column and under the broadcast beside it.
    #
    # The title carries the whole string, because the fix that stopped the
    # collision is also what hides the end of the longest names. A tooltip on
    # text that happens to fit is harmless; a stadium a reader cannot finish
    # reading is not. Plain text, assembled separately — the visible line has
    # a span in it by then.
    return (f"<div class=slatewhere>{icon('pin')}"
            f"<span title=\"{esc(plain)}\">{line}</span></div>")


# Above these it stops being weather and starts being a factor in the game,
# so the number takes the warning color and the glyph changes with it. A
# reader scanning sixteen cards should find the miserable one without
# reading a single figure.
WIND_WARN = 20      # mph
RAIN_WARN = 50      # percent


def weather_line(g):
    """The forecast inside sixteen days, the venue's average beyond it.

    Both are three numbers on one line. The average is muted, prefixed with
    the word that makes it not a forecast, and carries the history glyph —
    it is a fact about the place, not a claim about the day, and it must
    never be mistakable for one.
    """
    # Indoors, the line has nothing to report. A dome's forecast is a
    # statement about the parking lot: it does not move a ball, a kicker or a
    # crowd, and printed in the same slot as Lubbock in a crosswind it reads
    # as though it might. Say where the game is instead — which is the one
    # fact the weather line was standing in for.
    #
    # CFBD's flag does not separate a fixed roof from a retractable one, and
    # nothing published says in August whether Mercedes-Benz will be open in
    # September. "Indoors" is what is true of the building either way; a
    # temperature would be a guess about a roof nobody has decided yet.
    if g.get("dome"):
        return (f"<div class='slatewx dim' title='A domed or roofed stadium. "
                f"The forecast is left off because it is not about the "
                f"game.'>{icon('roof')}<span>Indoors</span></div>")

    w = g.get("weather")
    if w:
        wind = w.get("windMph")
        rain = w.get("precipChance")
        windy = wind is not None and round(wind) >= WIND_WARN
        wet = rain is not None and round(rain) >= RAIN_WARN
        parts = [f"{round(w['tempF'])}&deg;F"]
        if wind is not None:
            mph = f"{round(wind)} mph"
            parts.append(f"<span class=wxwarn>{mph}</span>" if windy else mph)
        if rain is not None:
            pct = f"{round(rain)}% rain"
            parts.append(f"<span class=wxwarn>{pct}</span>" if wet else pct)
        glyph = "rain" if wet else ("wind" if windy else "sun")
        cls = "gi wxwarn" if wet or windy else "gi"
        return (f"<div class=slatewx>{icon(glyph, cls)}<span>"
                + ", ".join(parts) + "</span></div>")

    n = g.get("normal")
    if not n:
        return ""
    parts = [f"{n['tempF']}&deg;F"]
    if n.get("windMph") is not None:
        parts.append(f"{n['windMph']} mph")
    if n.get("rainPct") is not None:
        parts.append(f"{n['rainPct']}% rain")
    # The rain figure is the share of days that saw measurable rain, not a
    # chance of rain at kickoff — Miami in September reads 93% and is not
    # wrong. Say which one it is, because the two look identical.
    return (f"<div class='slatewx dim' title='Ten seasons at this venue for "
            f"this two-week window: mean temperature and wind, and the share "
            f"of "
            f"days with measurable rain. Not a forecast.'>"
            f"{icon('history')}<span>Average " + ", ".join(parts)
            + "</span></div>")


def broadcast(g):
    """Who is carrying it. TV first and web after, because one is a channel
    number and the other is an app — and a game on both should not read as
    if it were on two networks. No radio: CFBD's media feed has none."""
    media = g.get("media") or []
    tv = [m["outlet"] for m in media if m.get("type") == "tv"]
    web = [m["outlet"] for m in media if m.get("type") == "web"]
    if not tv and not web:
        return ""
    # One weight for all of them. Bolding the TV window and leaving the
    # stream plain read as two different typefaces rather than as a
    # distinction, and the outlet's own name already says which it is —
    # nobody mistakes ESPN+ for a channel. TV first, streams after.
    names = list(dict.fromkeys(tv + web))
    joined = " / ".join(names)
    return (f"<div class=slatetv>{icon('tv')}"
            f"<span title=\"{esc(joined)}\">{esc(joined)}</span></div>")


def market(g):
    """The line, said the way people say it.

    CFBD stores the home spread — negative means the home team is favored —
    which is a convention, not a sentence. A reader wants a team and a
    number, so name the favorite. A pick'em game has no favorite to name,
    so it says so."""
    ln = g.get("line") or {}
    spread, total = ln.get("spread"), ln.get("over_under")
    bits = []
    if spread is not None:
        if spread == 0:
            bits.append("pick'em")
        else:
            fav = g["home"] if spread < 0 else g["away"]
            bits.append(f"{esc(fav)} {-abs(spread):g}")
    if total is not None:
        bits.append(f"O/U {total:g}")
    if not bits:
        # A card is a fixed shape; a row is not. Saying nothing here leaves
        # a hole that reads as a bug, so the card says what is true.
        return (f"<div class='slateline dim'>{icon('chart')}"
                f"<span>No line posted</span></div>")
    return (f"<div class=slateline title='Average of "
            f"{ln.get('books', 0)} book(s) via collegefootballdata.com'>"
            f"{icon('chart')}<span>" + " · ".join(bits) + "</span></div>")


def espn_link(g):
    """The box score once it exists, the preview until then. Same id either
    way — CFBD's game id is ESPN's."""
    gid = g.get("id")
    if not gid:
        return ""
    played = g["completed"] and g["home_points"] is not None
    kind = "boxscore" if played else "game"
    # Beside our own Preview link, "Game preview" twice would be two names
    # for two different things. The destination is the label.
    label = "ESPN box score" if played else "ESPN"
    return (f"<a class='slatelink dim' target=_blank rel=noopener "
            f"href='https://www.espn.com/college-football/{kind}/_/gameId/"
            f"{gid}'>{icon('out')}{label}</a>")


def game_slug(g):
    """<id>-<away>-at-<home>.html — the id makes it unambiguous and stable,
    the names make a shared link readable."""
    def part(name):
        keep = [c.lower() if c.isalnum() else "-" for c in name]
        return "".join(keep).strip("-").replace("--", "-")
    return f"{g['id']}-{part(g['away'])}-at-{part(g['home'])}.html"


def joiner(g):
    """"at" for a home game, "vs" for a neutral site.

    A neutral-site game has no home team in the sense the word "at" carries —
    Baylor did not travel to Auburn, they both traveled to Atlanta. The
    data still labels one side home, because a feed needs a column for it,
    and printing that as "at" tells the reader something untrue about who
    had the crowd. The hub has said "vs" since it started showing the next
    kickoff; this is the rest of the site agreeing with it.
    """
    return "vs" if g.get("neutral_site") else "at"


def matchup(g, size=18):
    """Both teams with their marks, scored if it has been played."""
    hm, am = logo_img(g["home"], size), logo_img(g["away"], size)
    if g["completed"] and g["home_points"] is not None:
        hw = g["home_points"] > g["away_points"]
        away = (f"{am}<b>{esc(g['away'])}</b> {g['away_points']}" if not hw
                else f"{am}{esc(g['away'])} {g['away_points']}")
        home = (f"{hm}<b>{esc(g['home'])}</b> {g['home_points']}" if hw
                else f"{hm}{esc(g['home'])} {g['home_points']}")
    else:
        away, home = f"{am}{esc(g['away'])}", f"{hm}{esc(g['home'])}"
    tag = ""
    if g.get("ccg"):
        tag = " <span class=ccgtag>Championship</span>"
    elif not g["conference_game"]:
        tag = " <span class=nctag>non-conf</span>"
    return f"{away} <span class=dim>{joiner(g)}</span> {home}{tag}"


def when_line(g):
    """Kickoff, or the word for a game that has already happened."""
    inner = ("<span class=dim>final</span>" if g["completed"]
             else kickoff(g))
    return f"<div class=slatewhen>{icon('clock')}{inner}</div>"


def pickem_line(g):
    """An empty slot for what the pick'em made of this game.

    Sixteen of these to a page, so it is one line and carries no percentages —
    at this size the reading is only "which way, and how far", and two numbers
    would crowd out the stadium name. The full split is on the game page and
    on your card.

    Ships empty and hidden. pickcon.js fills whichever of these it finds, in
    one request for the whole page, and leaves the rest alone: no line, no
    lock, too few cards, or no Worker all mean the row simply is not there.
    """
    if not PICKEM_ENABLED:
        return ""
    if not (g.get("line") or {}).get("spread"):
        return ""
    teams_ = load_teams()
    # Abbreviations only exist for the sixteen members, and team_abbr falls
    # back to the full name for everyone else — which put "COLO" beside
    # "Georgia Tech" in the same row. Either both are short or neither is.
    al, ah = team_abbr(teams_, g["away"]), team_abbr(teams_, g["home"])
    if al == g["away"] or ah == g["home"]:
        al, ah = g["away"], g["home"]
    return (f"<div class=slateline data-pkcon='{g['id']}' hidden "
            f"data-ac='{team_color(teams_, g['away'], '#252932')}' "
            f"data-hc='{team_color(teams_, g['home'], '#252932')}' "
            f"data-al=\"{esc(al)}\" data-hl=\"{esc(ah)}\"></div>")


def slate_card(g, pages=True):
    """One game, with everything a reader needs to go and watch it.

    Sixteen of these are the week. Every line holds one fact and stays on
    one line — the grid is two up rather than four precisely so that the
    longest stadium name still fits.
    """
    # Game pages exist for the live season only, so an archived slate must
    # not offer a Preview link — it would point at a 404, and the page that
    # produced it would look perfectly fine doing so.
    ours = (f"<a class=slatelink href='game/{game_slug(g)}'>"
            f"{icon('note')}Preview</a>" if pages else "")
    return (f"<div class=slate>"
            f"<div class=slateteams>{matchup(g)}</div>"
            f"<div class=slatemeta>{when_line(g)}{where(g)}"
            f"{broadcast(g)}"
            f"{weather_line(g)}{market(g)}</div>"
            f"{pickem_line(g)}"
            f"<div class=slatelinks>{ours}{espn_link(g)}</div></div>")


def slate_week(games):
    """The week the page opens on: the one the next kickoff sits in, and
    once every game is played, the last week of the season."""
    ahead = [g for g in games if not g["completed"] and g.get("start")]
    if ahead:
        return min(ahead, key=lambda g: g["start"])["week"]
    unplayed = [g["week"] for g in games if not g["completed"]]
    if unplayed:
        return min(unplayed)
    return max((g["week"] for g in games), default=None)


def build_schedule_page(games, ctx):
    """This week's games, in the order they kick off. The grid that used to
    open this page answers a different question — which pairings exist at
    all — and now has its own page; this one answers what is on today."""
    wk = slate_week(games)
    week_games = sorted((g for g in games if g["week"] == wk),
                        key=lambda g: (g.get("start") or "", g["home"]))
    if week_games:
        first = next((g.get("start") for g in week_games if g.get("start")),
                     None)
        span = f" <span class=dim>&middot; {esc(pretty_date(first))}</span>" \
            if first else ""
        slate = (f"<div class=card id=slate><h2>Week {wk}{span}</h2>"
                 f"<div class=slatelist>"
                 + "".join(slate_card(g, pages=bool(ctx.get("game_pages")))
                            for g in week_games)
                 + "</div>"
                 "<p class=note>Kickoffs are in the venue's own time zone, "
                 "the way a school publishes them, with your clock beside "
                 "it when the two differ. An asterisk marks a time the "
                 "school lists differently — hover it for both. A forecast "
                 "appears about two weeks out; before that the line reads "
                 "what that venue is usually like at this point in the "
                 "season.</p></div>")
    else:
        slate = ("<div class=card id=slate><h2>No games scheduled</h2>"
                 "<p class=note>The season's schedule has not been "
                 "published yet.</p></div>")

    rem = sorted((g for g in games if not g["completed"] and g["week"] != wk),
                 key=lambda g: (g["week"], g["start"] or ""))
    by_week = {}
    for g in rem:
        by_week.setdefault(g["week"], []).append(g)
    up = ""
    for w in sorted(by_week):
        up += (f"<h3 class=wkhead>Week {w}</h3><ul class=games>"
               + "".join(game_row(g) for g in by_week[w]) + "</ul>")
    upcard = (f"<div class=card><h2>The rest of the season</h2>{up}</div>"
              if up else "")
    done = [g for g in games if g["completed"]
            and g["home_points"] is not None and g["week"] != wk]
    done.sort(key=lambda g: g["start"] or "", reverse=True)
    rescard = ""
    if done:
        rescard = ("<div class=card><h2>Results, newest first</h2>"
                   "<ul class=games>"
                   + "".join(game_row(g) for g in done[:40])
                   + "</ul></div>")
    return slate + upcard + rescard


def model_card(g, ctx):
    """What four rating systems make of it, against what the market makes
    of it. This is the section nobody else can write: the models are all
    public, but nobody lines them up beside the number and says who
    disagrees."""
    favs = ctx.get("favorites") or {}
    gid = str(g.get("id"))
    rows = []
    for name in MODEL_ORDER:
        f = (favs.get(name) or {}).get(gid)
        if f:
            rows.append((name, f["team"], f["margin"]))
    if not rows:
        return ""
    ln = g.get("line") or {}
    spread = ln.get("spread")
    mkt = None
    if spread is not None and spread != 0:
        mkt = (g["home"] if spread < 0 else g["away"], abs(spread))
    top = max([m for _, _, m in rows] + ([mkt[1]] if mkt else [0])) or 1

    def bar(team, margin, cls=""):
        pct = min(100 * margin / top, 100)
        return (f"<div class='mrow {cls}'><span class=mname>{esc(team)}</span>"
                f"<span class=mbar><i style='width:{pct:.0f}%'></i></span>"
                f"<b class=mval>{margin:g}</b></div>")

    # The year sits under the name rather than beside it: this column is 44px
    # and the bar beside it is the point of the card.
    def sys_label(n):
        y = model_year(n, ctx.get("systems"))
        return f"{esc(n)}{f'<i>{esc(y)}</i>' if y else ''}"

    body = "".join(
        f"<div class=mline><span class=msys>{sys_label(n)}</span>"
        f"{bar(t, m)}</div>" for n, t, m in rows)
    if mkt:
        body += (f"<div class='mline mmarket'><span class=msys>Market</span>"
                 f"{bar(mkt[0], mkt[1], 'market')}</div>")
    # The market row names its source here rather than borrowing the one on
    # the card above it. The two cards are independent — a game page shows
    # both, the slate shows neither in text — and a benchmark row every other
    # row is being judged against is the last place to make a reader go
    # looking for where a number came from.
    #
    # And it is only a CLOSING spread once there is nothing left to close.
    # Before kickoff this is the current average, the same figure printed
    # above as "opened -7" moved to -6.8; calling that a closing line
    # described a number that does not exist yet on the one kind of page —
    # a preview — where this card does its most useful work.
    books = (g.get("line") or {}).get("books", 0)
    src = (f"an average of {books} book(s) via collegefootballdata.com"
           if books else "via collegefootballdata.com")
    note = ("Predicted margin in points, each system carrying its own "
            "home-field bump. The year under a name is the season those "
            "ratings come from. The market row is "
            + (f"the closing spread, {src}."
               if g.get("completed")
               else f"the spread as it stands, {src}."))
    # Before a season starts, most of these are last year's numbers pulled
    # toward the mean. A reader comparing against published SP+ deserves to
    # know why ours is smaller rather than assuming one of us is wrong. The
    # labels now say which ones are last season's, so this says what was done
    # to them rather than naming them a second time.
    stale = sorted({n for n in MODEL_ORDER
                    if (ctx.get("systems") or {}).get(n, {}).get("year")
                    not in (None, ctx.get("year"))})
    if stale:
        note += (f" Where that is last season, the rating is regressed toward "
                 f"the mean for how wrong a rating can be about a team this "
                 f"early.")
    if mkt:
        agree = sum(1 for _, t, _ in rows if t == mkt[0])
        if agree == len(rows):
            note += (f" All {len(rows)} systems side with the favorite.")
        elif agree == 0:
            note += " Every system takes the other side."
    return (f"<div class=card id=modelcard><h2>What the models make it</h2>{body}"
            f"<p class=note>{note}</p></div>")


def race_card(g, ctx):
    """What the result does to the championship picture. Conference games
    only — a non-conference result cannot move a conference race."""
    lev = (ctx.get("leverage") or {}).get(str(g.get("id")))
    if not lev or not g.get("conference_game"):
        return ""
    total = lev["total"] * 100
    # d is measured against the home team winning, so a negative number is
    # a team that gains when the away side wins — not one that loses by
    # winning. Say the gain and the result that produces it, or the row
    # reads as nonsense: "Arizona -21% if Arizona wins".
    # Today is never the midpoint between the two branches, and that is the
    # whole insight — it sits nearer whichever branch is likelier. BYU is 23%
    # now, 33% if it wins and 7% if it loses, because it is expected to win:
    # most of the winning branch is already priced in, so the game is worth
    # +10 to BYU rather than the +27 the raw gap suggests. Which is why every
    # cell prints the move as well as the level.
    sims = ctx.get("sims") or {}

    # THE FORK. Two results, two teams, four numbers — the shape of the
    # question a preview is actually asked. A sentence could carry two of
    # them ("33% if BYU wins, 7% if Arizona does") and was carrying exactly
    # two, which made the game look like it was about one team. It is about
    # both, and the other side's number is the half nobody was being shown.
    #
    # Each cell also says what the result MOVES, because a probability with
    # no baseline is a number without a verb: 33% means nothing until you
    # know they were on 23%.
    teams_ = ctx.get("teams") or {}
    # Everybody the result touches, in one component. The teams that are not
    # playing used to sit below this in bars of their own, spanning the two
    # futures with a tick at today — a good drawing of a range, and the wrong
    # answer to the question being asked. A bar sorts its ends high to low,
    # so it could say Texas Tech lives between 57% and 58% and could not say
    # WHICH result put it at which. In the fork the branch is the box the
    # number is standing in, and that ambiguity cannot arise.
    fork = fork_block(g, lev, sims, teams_)
    teach = teach_block(g, lev, (ctx.get("leverage_cond") or {}).get(
        str(g.get("id"))), teams_)

    key = (f"<p class='note swkey'>Two teams reach the championship game "
           f"&mdash; think of that as two seats. The rest of the season is "
           f"played out ten thousand times with this result set and the "
           f"season played around it; the percentages are how often each "
           f"team ends up in a seat, and the arrow is the move from where "
           f"they stand today. <b>100 would be a whole seat</b> changing "
           f"hands. Under the rule in each box are the teams that are not "
           f"playing but whose own chance the result moves by a point or "
           f"more.</p>")
    # Only when the second block is there to be explained. Late in the
    # season it is not, and a paragraph about a distinction the page is no
    # longer drawing is worse than no paragraph.
    why = (f"<p class=note>The second pair of boxes is those same two "
           f"results read the other way. The first asks what the result "
           f"DOES, holding fixed everything the model believes about these "
           f"teams. The second asks what this page will actually say "
           f"afterwards, which is more &mdash; because a win is not only an "
           f"event, it is evidence. Beating a team rated this highly is a "
           f"reason to think better of the winner, and a team thought "
           f"better of wins more of what it plays next. The signed figure "
           f"is that difference: what the game teaches, as against what it "
           f"decides. It shrinks as the ratings settle, and by November "
           f"there is little enough left that these boxes stop "
           f"appearing.</p>") if teach else ""
    return (f"<div class=card id=racecard><h2>What it does to the race</h2>"
            f"<div class=levtotal><b>{total:.0f}</b> "
            f"<span class=dim>of a title-game seat changes hands on this "
            f"result</span></div>{fork}{teach}{key}{why}"
            f"<p class=note>From the same simulations the race card runs. "
            f"100 means a full berth's worth of probability moves on this "
            f"result. Each pair is that team's chance in the two futures, "
            f"not a move from where it stands now &mdash; today's number "
            f"already contains both, weighted by who is likely to "
            f"win.</p></div>")


def series_card(g, ctx):
    """The record between them as conference opponents, and the fact that
    this game is the first tiebreak step if they finish level."""
    wl = ctx.get("series") or {}
    a, b = g["home"], g["away"]
    rec = (wl.get(a) or {}).get(b)
    if not rec or not g.get("conference_game"):
        return ""
    w, l = rec
    if w + l == 0:
        line = f"{esc(a)} and {esc(b)} have never met as conference opponents."
    else:
        line = (f"<b>{esc(a)} {w}&ndash;{l} {esc(b)}</b> as conference "
                f"opponents since 2011.")
    return (f"<div class=card id=seriescard><h2>The series</h2><p>{line}</p>"
            f"<p class=note>If these two finish level in the standings, "
            f"step (a) of the tiebreaker is head-to-head &mdash; which is "
            f"this game. It is the first thing that separates them, before "
            f"any of the six steps below it are read.</p></div>")


def png_size(path):
    """(width, height) from a PNG header, or None.

    Thirty-three bytes and no dependency: a PNG opens with an 8-byte
    signature and then an IHDR chunk whose first two fields are the
    dimensions, big-endian. Adding Pillow to a stdlib-only build to read two
    integers would be the wrong trade by a distance.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return (int.from_bytes(head[16:20], "big"),
            int.from_bytes(head[20:24], "big"))


def jsonld(obj):
    """A JSON-LD block, escaped for the one thing that can break out of it.

    A `</script>` sequence inside a JSON string ends the element early — the
    browser is scanning for the tag before it is parsing JSON — so the slash
    is escaped. Nothing here is attacker-controlled today; team names and
    venues come from a committed file. It is one line and it stops this from
    being the place that matters if that ever stops being true.
    """
    s = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    safe = s.replace("</", "<\\/")
    return f'<script type="application/ld+json">{safe}</script>'


def game_jsonld(g, year, url):
    """SportsEvent for one game, and the trail that gets you to it.

    Every one of these is a real, dated, located event with two named
    competitors, which is exactly the shape search engines already understand
    — and the site has 120 of them a season carrying nothing but prose. The
    fields are the ones schema.org actually defines for a sporting fixture;
    nothing is invented to fill a slot.
    """
    when = g.get("start")
    if when and g.get("start_tbd"):
        # CFBD returns a placeholder hour for an unannounced window. Claiming
        # 04:00 UTC would be a wrong time rather than a missing one, so only
        # the date is published — which is all anybody has decided.
        when = when[:10]
    elif when:
        when = when.replace(".000Z", "Z")

    ev = {
        "@context": "https://schema.org",
        "@type": "SportsEvent",
        "name": f"{g['away']} {joiner(g)} {g['home']}",
        "sport": "American Football",
        "url": url,
        "competitor": [
            {"@type": "SportsTeam", "name": g["away"]},
            {"@type": "SportsTeam", "name": g["home"]},
        ],
    }
    if when:
        ev["startDate"] = when
    if not g.get("neutral_site"):
        ev["homeTeam"] = {"@type": "SportsTeam", "name": g["home"]}
        ev["awayTeam"] = {"@type": "SportsTeam", "name": g["away"]}
    if g.get("venue"):
        ev["location"] = {"@type": "Place", "name": g["venue"]}
    if not g.get("completed"):
        ev["eventStatus"] = "https://schema.org/EventScheduled"
        ev["eventAttendanceMode"] = \
            "https://schema.org/OfflineEventAttendanceMode"

    crumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Big12ology",
             "item": "https://big12ology.com/"},
            {"@type": "ListItem", "position": 2, "name": f"{year} schedule",
             "item": "https://big12ology.com/schedule/"},
            {"@type": "ListItem", "position": 3,
             "name": f"{g['away']} {joiner(g)} {g['home']}"},
        ],
    }
    return jsonld(ev) + jsonld(crumbs)


def venue_card(g):
    """Where it is played and what it will be like there, at the size of a
    card rather than the size of a row.

    The slate says this in three muted words per line because sixteen of them
    have to fit a week on one screen. A preview page has one game on it and
    room to say the thing properly, so it borrows the market card's shape —
    figure first, what it is underneath — and the two cards read as one
    system instead of one card and one leftover.
    """
    name, city = g.get("venue"), g.get("venue_city")
    if not name and not city:
        return ""

    tag = (" <span class=nctag>neutral site</span>"
           if g.get("neutral_site") else "")
    head = (f"<div class=venname>{icon('pin', 'gi vengi')}"
            f"{esc(name or city)}"
            f"{f' <span class=vencity>{esc(city)}</span>' if name and city else ''}"
            f"{tag}</div>")

    cells, note = [], ""
    if g.get("dome"):
        # A roofed stadium gets the headline to itself. There is no figure to
        # print and inventing one — the forecast for the car park — is the
        # thing this card exists to stop.
        cells.append(("Indoors", "roof over the field", "roof"))
        note = ("A domed or roofed stadium, so no forecast is shown: the "
                "weather outside is not part of this game.")
    elif g.get("weather"):
        w = g["weather"]
        wind, rain = w.get("windMph"), w.get("precipChance")
        cells.append((f"{round(w['tempF'])}&deg;F", "at kickoff", "temp"))
        if wind is not None:
            windy = round(wind) >= WIND_WARN
            cells.append((f"<span class=wxwarn>{round(wind)} mph</span>"
                          if windy else f"{round(wind)} mph",
                          "wind", "wind"))
        if rain is not None:
            wet = round(rain) >= RAIN_WARN
            cells.append((f"<span class=wxwarn>{round(rain)}%</span>"
                          if wet else f"{round(rain)}%",
                          "chance of rain", "rain"))
        note = "Forecast for the hour of kickoff, via Open-Meteo."
    elif g.get("normal"):
        n = g["normal"]
        cells.append((f"{n['tempF']}&deg;F", "average temperature", "temp"))
        if n.get("windMph") is not None:
            cells.append((f"{n['windMph']} mph", "average wind", "wind"))
        if n.get("rainPct") is not None:
            # Said in full here, because the card has the room the slate's
            # title attribute did not: this is the share of DAYS that saw
            # rain, not a chance of rain at kickoff. Miami in September reads
            # 93% and is not wrong.
            cells.append((f"{n['rainPct']}%", "of days see rain", "rain"))
        note = ("Ten seasons at this venue for this two-week window &mdash; "
                "a fact about the place, not a forecast for the day.")

    grid = "".join(f"<div><div class=mkval>{v}</div>"
                   f"<div class=dim>{icon(ic, 'gi vengi')}{k}</div></div>"
                   for v, k, ic in cells)
    body = f"<div class=mkgrid>{grid}</div>" if grid else ""
    tail = f"<p class=note>{note}</p>" if note else ""
    return (f"<div class=card id=venuecard><h2>The venue</h2>"
            f"{head}{body}{tail}</div>")


def build_game_page(g, ctx):
    """One game, everything the build already knows about it."""
    back = (f"<div class=gameback><a href='../'>&#8592; Week "
            f"{g['week']}</a></div>")
    # The head answers "which game, and when can I watch it" — the two things
    # somebody arriving from a link wants in the first line. The stadium and
    # what it will be like there is a different question, asked by somebody
    # who has already decided to care, so it gets its own card rather than a
    # fourth row of the banner. It also gives the short column something to
    # hold: on a page with a market and a model card, the venue card is what
    # stops the left column ending halfway up the right one.
    head = (back + f"<div class=card id=gamehead>"
            f"<div class=gamematch>{matchup(g, 22)}</div>"
            f"<div class=slatemeta>{when_line(g)}{broadcast(g)}</div></div>")

    venue = venue_card(g)

    ln = g.get("line") or {}
    mk = ""
    if ln:
        cells = []
        spread = ln.get("spread")
        if spread is not None:
            fav = ("pick'em" if spread == 0
                   else f"{esc(g['home'] if spread < 0 else g['away'])} "
                        f"{-abs(spread):g}")
            op = ln.get("spread_open")
            cells.append((fav, f"opened {-abs(op):g}"
                          if op not in (None, spread) else "spread"))
        if ln.get("over_under") is not None:
            op = ln.get("over_under_open")
            cells.append((f"O/U {ln['over_under']:g}",
                          f"opened {op:g}"
                          if op not in (None, ln["over_under"]) else "total"))
        # A moneyline the book cannot have posted is dropped rather than
        # printed. Coastal Carolina at West Virginia arrived carrying
        # "-100000" for the +19.5 underdog — a price implying it wins 99.9%
        # of the time, sitting in the same card as the spread saying the
        # opposite. Two impossibilities, both structural rather than a
        # judgement about how big a number is allowed to get:
        #
        #   |price| < 100 does not exist. American odds are a stake-to-win
        #   ratio either side of even money, and ±100 IS even money.
        #
        #   The underdog cannot be priced as the favourite. The spread
        #   already named which side is which, so a negative price on the
        #   other one contradicts the number printed beside it. Both sides
        #   negative is the same fault seen from the other end.
        #
        # No magnitude cap: a genuine forty-point favourite is legitimately
        # in the thousands, and a threshold picked to catch this one row
        # would quietly eat those.
        def ml_ok(price, is_fav):
            if price is None or abs(price) < 100:
                return False
            return (price < 0) if is_fav else (price > 0)

        home_fav = spread is not None and spread < 0
        away_fav = spread is not None and spread > 0
        if spread is None or spread == 0:
            # No favourite named, so there is nothing to contradict; only
            # the even-money floor applies.
            home_fav = away_fav = None
        for team, price, label in (
                (g["home"], ln.get("home_ml"), f"{esc(g['home'])} moneyline"),
                (g["away"], ln.get("away_ml"), esc(g["away"]))):
            fav = home_fav if team == g["home"] else away_fav
            ok = (price is not None and abs(price) >= 100 if fav is None
                  else ml_ok(price, fav))
            if ok:
                cells.append((f"{price:+g}", label))
        grid = "".join(f"<div><div class=mkval>{v}</div>"
                       f"<div class=dim>{k}</div></div>" for v, k in cells)
        mk = (f"<div class=card><h2>The market</h2>"
              f"<div class=mkgrid>{grid}</div>"
              f"<p class=note>Average of {ln.get('books', 0)} book(s) via "
              f"collegefootballdata.com.</p></div>")

    # What the public did with that number. Ships hidden and empty; pickcon.js
    # fills it from /api/consensus once the week has locked, and leaves it
    # hidden otherwise — no slate, no lock, no Worker, no card. Placed after
    # the market because that is the number the public was reacting to.
    #
    # Team colors are emitted here rather than fetched: the build already
    # knows them, and a second request for sixteen hex values would be silly.
    teams_ = ctx.get("teams") or {}
    con = ""
    if g.get("line") and PICKEM_ENABLED:
        con = (f"<div class=card id=pickcon hidden "
               f"data-gid='{g['id']}' "
               f"data-away=\"{esc(g['away'])}\" data-home=\"{esc(g['home'])}\" "
               f"data-ac='{team_color(teams_, g['away'], '#252932')}' "
               f"data-hc='{team_color(teams_, g['home'], '#252932')}'>"
               f"<h2>Pickem says</h2><div class=pcbody></div></div>")

    # Market first, then the venue, then the models. The column flow fills in
    # source order, so this is also the balancing: two short cards on the left
    # against the tall model card on the right.
    return (head + mk + venue + model_card(g, ctx) + con + race_card(g, ctx)
            + series_card(g, ctx)
            + f"<div class=card id=elsewhere><h2>Elsewhere</h2>"
              f"<div class=slatelinks>{espn_link(g)}</div></div>")


def series_records(year, teams, games):
    """Every pairing's record as conference opponents.

    Reads fifteen seasons out of history/, so it is worth computing once a
    season and handing round — which is now what happens: render() puts it in
    ctx and the two readers take it from there.

    IT USED TO MEMOISE ITSELF, on (year, id(games)), and that key was wrong in
    two ways at once. It left out `teams`, which the two callers passed
    differently — one a sorted list, the other the raw dict — so the second
    caller silently received whatever the first had computed, and the code was
    correct only because all_time_records sorts its argument and could not
    tell them apart. And id() is an address, which CPython reuses after a
    collection, so a freed season's games could hand its record to a later
    one. Neither had bitten. Both were waiting on a caller being added or
    reordered, which is a poor thing to leave in a cache nobody needed.
    """
    seasons = fetcher.usable_seasons(range(2011, year))
    return rotation_mod.all_time_records(seasons, teams, current=games)


def build_matrix_page(ctx):
    """Who plays whom, as a grid. This and the remaining-difficulty table
    are both about the shape of the draw rather than about this weekend,
    which is why they sit together and away from the week's slate."""
    return ctx["h2hcard"] + ctx["soscard"]


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
        score = f"{am}{away} <span class=dim>{joiner(g)}</span> {hm}{home}"
        cls = "done"
    else:
        when = pretty_date(g["start"])
        score = (f"{am}{esc(g['away'])} "
                 f"<span class=dim>{joiner(g)}</span> "
                 f"{hm}{esc(g['home'])} <span class=dim>({when})</span>")
        cls = "upcoming"
    tag = ("" if g["conference_game"]
           else " <span class=nctag>non-conf</span>")
    if g.get("ccg"):
        tag = " <span class=ccgtag>Championship</span>"
    return f"<li class={cls}>{score}{tag}</li>"


def place_and_forecast(year, games):
    """Give each game its city, its broadcast, its line and — if it is
    close enough for one — its forecast.

    Both read from committed data or a keyless API, so this costs no CFBD
    quota and a finished season costs nothing at all: every game is played,
    so nothing is in forecast range and no request is made.
    """
    venues = fetcher.load_venues()
    if venues:
        for g in games:
            v = venues.get(str(g.get("venue_id")))
            if not v:
                continue
            g["venue_city"] = ", ".join(
                x for x in (v.get("city"), v.get("state")) if x)
            if v.get("tz"):
                g["venue_tz"] = v["tz"]
            # Carried onto the game because that is where every renderer
            # looks; weather.py reads it too, and declines to spend a
            # forecast on a building with a roof.
            if v.get("dome"):
                g["dome"] = True
    # Both already on disk: the market is fetched for the what-if models and
    # the broadcast list on the weekly refresh. Reading them here spends
    # nothing and is the whole reason the slate can show them.
    media = fetcher.load_media(year)
    lines = load_lines(year)
    for g in games:
        gid = str(g.get("id"))
        if gid in media:
            g["media"] = media[gid]
        if gid in lines:
            g["line"] = lines[gid]
    try:
        weather_mod.attach(games, venues)
    except Exception as e:
        # A forecast is the least important thing on the page. It never
        # takes a deploy down with it.
        print(f"weather: skipped ({e})")
    # Then averages, for the games a forecast could not reach. Committed by
    # normals.py, and second on purpose: normal_for declines to answer for a
    # game that already has a forecast, so the order is what enforces "a
    # real forecast always wins".
    normals = weather_mod.load_normals()
    if normals:
        for g in games:
            n = weather_mod.normal_for(g, normals)
            if n:
                g["normal"] = n


def render(year, games):
    place_and_forecast(year, games)
    overrides = tb.load_overrides()
    teams = load_teams()
    systems = load_ratings(year).get("systems", {})
    closing_lines = load_lines(year)
    track, _wk = next_conf_week_ids(games)
    sims = simulate_week(games, systems, overrides, track) if systems else {}
    favorites = favorites_for(games, systems)
    # `kind` is load-bearing, not documentation. payload.favorites is the
    # pick source for the UI and now holds three different things — the four
    # ratings, the blend of them, and the market — while race.js's
    # ensembleMargins averaged EVERY key it found. Adding the blend and Vegas
    # therefore averaged six opinions, double-weighting the blend (which is
    # already their mean) and folding in a line that is not a rating at all,
    # silently moving every probability the Lab draws. Consumers filter on
    # this rather than guessing from a name.
    models = [{"name": n, "year": systems[n].get("year"), "kind": "rating"}
              for n in MODEL_ORDER if n in favorites]
    # The blend goes FIRST, which makes it the default: app.js opens on
    # models[0]. A reader arriving from the race card should see the Lab
    # agree with the number that sent them there, and reach for one system
    # only when they want that system's opinion specifically.
    if len(favorites) > 1:
        favorites[BLEND] = blend_favorites(favorites, games)
        models.insert(0, {"name": BLEND, "year": None, "kind": "blend"})
    # AFTER the blend, deliberately. The blend is the four rating systems,
    # because that is what the race board averages (odds.ensemble_margin) and
    # the whole point of it is to agree with the board. Folding the market in
    # would make the Lab's default disagree with every other number on the
    # site while looking like it had been made more accurate.
    mkt = market_favorites(games)
    if mkt:
        favorites["Vegas"] = mkt
        models.append({"name": "Vegas", "year": None, "kind": "market"})
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
            tie_names = tie_headline(r["tie_group"])
            lines = "".join(
                f"<li class=seeded>{esc(x)}</li>" if "seeded." in x
                else f"<li>{esc(x)}</li>" for x in r["log"])
            stories.append(
                f"<details {'open' if n == 1 else ''}><summary><sup>{n}</sup> "
                f"{tie_names}</summary>"
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
    # Marks for everyone else on the schedule. `teams` is the sixteen and the
    # page counts it — "all sixteen are 0–0", the list of teams not yet
    # placed — so the non-conference marks ride separately. Without them the
    # week list drew a logo beside the Big 12 side of a game and nothing
    # beside its opponent, which is the one thing logo_img() exists to avoid.
    mark_only = {
        t: f"{BASE}logos/{e['key']}.{(e.get('ext') or '.svg').lstrip('.')}"
        for t, e in MARKS.items()
        if t not in team_meta and e.get("usable") is not False
    }
    payload = json.dumps({
        "year": year,
        "teams": team_meta,
        "marks": mark_only,
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
    # Grouped, because the list contains three different KINDS of thing and
    # looked like one: the blend, the four ratings it is made of, and the
    # market, which is not in it. An optgroup is the accessible way to draw
    # that line — a screen reader announces the group, where a horizontal
    # rule would be silent decoration.
    def _opt(m):
        year = f" ({esc(m['year'])})" if m.get("year") else ""
        return f"<option value='{esc(m['name'])}'>{esc(m['name'])}{year}</option>"

    rated = [m for m in models if m.get("kind") == "rating"]
    model_opts = "".join(
        [_opt(m) for m in models if m.get("kind") == "blend"]
        + ([f"<optgroup label='In the blend'>"
            + "".join(_opt(m) for m in rated) + "</optgroup>"] if rated else [])
        + ([f"<optgroup label='Not in the blend'>"
            + "".join(_opt(m) for m in models if m.get("kind") == "market")
            + "</optgroup>"]
           if any(m.get("kind") == "market" for m in models) else []))
    whatif = "" if not n_remaining else WHATIF_CARD.format(
        n=n_remaining, model_opts=model_opts,
        blurb=("rewrite any of the {n} games this season and watch the "
               "tiebreakers answer" if unlocked else
               "pick winners for the {n} remaining games, conference and "
               "non-conference").format(n=n_remaining),
        # Two fills, because they answer two different questions. "All" is the
        # model's whole season and overwrites anything already on the board;
        # "unpicked" fills the gaps around picks the reader has made, which is
        # what somebody who has worked through three weeks by hand and wants
        # the rest as chalk is actually asking for. The labels name whose
        # favorites these are — app.js rewrites them when the model changes.
        modelrow=("" if not models else
                  '<div class=wgroup>'
                  '<label class=dim for=w-model>Model</label>'
                  f'<select id=w-model class=wbtn>{model_opts}</select>'
                  '<button id=w-fav class=wbtn title="Pick every game shown'
                  ' the way this model would, replacing what is there">'
                  f'{icon("star", "bi bi-chalk")}'
                  '<span class=blab>Use favorites for all</span></button>'
                  '<button id=w-favun class=wbtn title="Fill in only the games'
                  ' with no pick yet, and leave your own picks alone">'
                  f'{icon("spark", "bi bi-chalk")}'
                  '<span class=blab>Use favorites for unpicked</span></button>'
                  '</div>'),
        i_chev=icon("chevdown", "bi bi-view"),
        i_filter=icon("filter", "bi bi-view"),
        i_share=icon("share", "bi"),
        # The finished season resets to history rather than to nothing, and
        # the glyph says which of the two this page is doing.
        i_clear=icon("history" if unlocked else "eraser", "bi"),
        clearlabel=("Reset to what happened" if unlocked else "Clear picks"))

    standcard = STAND_CARD.format(
        played=len(reg_played), total=len(reg), table=table, stories=stories)

    site_url = "https://big12ology.com/tiebreaker/"
    page = TEMPLATE.format(
        year=year,
        base=BASE,
        v_engine=asset_v("engine.js"),
        v_pct=asset_v("pct.js"),
        v_race=asset_v("race.js"),
        v_scroll=asset_v("scrollcue.js"),
        v_brand=asset_v("brand.css"),
        v_theme=asset_v("theme.js"),
        v_cards=asset_v("cards.js"),
        v_state=asset_v("state.js"),
        v_metrics=asset_v("metrics.js"),
        v_app=asset_v("app.js"),
        canon=(f"{site_url}lab.html" if year == LIVE_YEAR
               else f"{site_url}{year}/lab.html"),
        topbar=topbar("tiebreaker", year, BASE),
        footer=footer(),
        # No matchup card. The Lab is a place you go to change the season,
        # not to be told what it currently is — and the card is the one
        # thing on the page that does not respond to a single pick.
        top=tracker_top(year, "tracker", "", page="lab.html"),
        whatif=whatif,
        standcard=standcard,
        clinchcard=clinch_card(
            games, overrides, systems, rows, sims,
            chip=' <span id=w-chip3 class=wchip hidden>what-if</span>',
            tail=CLINCH_TAIL_LAB),
        payload=payload,
    )
    ctx = {
        "clinchcard": clinch_card(games, overrides, systems, rows, sims),
        "levcard": leverage_card(games, sims, teams) if sims else "",
        "soscard": sos_card(games, systems),
        "modelcard": scorecard_card(games, systems, closing_lines),
        "h2hcard": h2h_card(games, teams, rows),
        "matchcard": card,
        "standingspage": standings_page(games, overrides, display_rows, teams),
        "sims": sims,
        "systems": systems,
        "year": year,
        # Only the live season gets a page per game; the slate asks before
        # linking to one.
        "game_pages": year == LIVE_YEAR,
        "teams": teams,
        # For the game pages. All three are already computed above for other
        # cards; exposing them here is cheaper than recomputing per game —
        # the series in particular reads fifteen seasons off disk.
        "favorites": favorites,
        "leverage": {str(e["game"]["id"]): e for e in leverage_of(sims)},
        # The same games read the other way, for the preview pages.
        "leverage_cond": leverage_cond_of(sims),
        "series": series_records(year, teams, games),
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


# Three groups, in the order the work happens: lay the model's chalk down,
# change what you are looking at, then do something with the board you built.
# The label of a button that toggles lives in its own span, because app.js has
# to rewrite the word without throwing away the glyph beside it.
WHATIF_CARD = """<div class=card id=whatif>
  <h2>What if&hellip; <span class=dim style="text-transform:none">{blurb}</span></h2>
  <div class=wcontrols>
    {modelrow}
    <div class=wgroup>
      <button id=w-weeks class=wbtn>{i_chev}<span class=blab>Expand all weeks</span></button>
      <button id=w-conf class=wbtn aria-pressed=false title="Show only the games that decide the conference race">{i_filter}<span class=blab>Conference only</span></button>
    </div>
    <div class=wgroup>
      <button id=w-link class=wbtn title="Copy a link to this scenario">{i_share}<span class=blab>Share link</span></button>
      <button id=w-clear class=wbtn>{i_clear}<span class=blab>{clearlabel}</span></button>
    </div>
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
<script>(function(){{try{{var b=localStorage.getItem("b12-cards");if(!b)return;var o=JSON.parse(b);if(!o||o.v!==1||!o.d)return;var l=o.d[location.pathname];if(!l||!l.length)return;var s=l.filter(function(k){{return /^[A-Za-z][\w-]*$/.test(k)}}).map(function(k){{return"#"+k+">*:not(h2):not(h3){{display:none}}#"+k+"{{padding-bottom:8px}}"}}).join("");if(!s)return;var e=document.createElement("style");e.id="b12-precollapse";e.textContent=s;document.head.appendChild(e)}}catch(e){{}}}})();</script>
<link rel=stylesheet href="{base}{v_brand}">
<script defer src="{base}{v_theme}"></script>
<script src="{base}{v_state}"></script>
<script src="{base}{v_metrics}"></script>
<script defer src="{base}{v_cards}"></script>
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
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font:var(--t-body)/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}

.whyout {{ margin-top: 6px; }}
.whyout p {{ font-size: var(--t-copy); margin: 8px 0; }}
.whyhead {{ display: flex; align-items: center; gap: 4px; font-weight: 700;
  font-size: var(--t-subhead); margin: 6px 0; }}
.evline {{ display: block; background: var(--bg); border-left: 3px solid
  var(--accent); border-radius: 4px; padding: 6px 10px; margin: 6px 0;
  font-size: var(--t-row); color: var(--dim); }}
.ladder {{ margin: 10px 0 4px; }}
.roundhead {{ font-size: var(--t-row); text-transform: uppercase; letter-spacing:
  .05em; color: var(--dim); font-weight: 600; margin: 16px 0 6px; }}
.lstep {{ display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px solid
  var(--line); align-items: baseline; }}
.lstep:last-child {{ border-bottom: none; }}
.lstep.skip {{ opacity: .45; }}
.lletter {{ flex: 0 0 22px; height: 22px; border-radius: 6px; background:
  var(--line); color: var(--ink); font-size: var(--t-meta); font-weight: 700;
  text-align: center; line-height: 22px; align-self: flex-start; }}
.lbody {{ flex: 1; min-width: 0; }}
.lname {{ font-size: var(--t-label); font-weight: 600; }}
.lchip {{ font-size: var(--t-fine); border-radius: 20px; padding: 2px 9px;
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
  /* The race card answers the picker directly — "who reaches the title
     game" — so it follows it here as it sits above the standings on the
     wide layout, rather than being read after the table it summarizes. */
  #clinchcard {{ order: 2; }}
  .standcard {{ order: 3; }}
  #teamwhy {{ order: 4; }}
  .rules {{ order: 5; }}
}}
.card {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 18px 20px; }}
.card h2 {{ margin: 0 0 10px; font-size: var(--t-copy); text-transform: uppercase;
  letter-spacing: .06em; color: var(--dim); font-weight: 600; }}

.matchup {{ display: flex; align-items: center; gap: 18px; margin: 10px 0 4px;
  flex-wrap: wrap; }}
.side {{ display: flex; align-items: center; gap: 12px; font-size: var(--t-headline);
  font-weight: 700; border-bottom: 4px solid var(--line);
  padding: 6px 10px 10px 2px; }}
.tname {{ letter-spacing: -.01em; }}
.vs {{ color: var(--dim); font-weight: 400; font-size: var(--t-subhead); padding: 0 6px; }}
/* One tile, both themes — see the note on .mark in the main sheet. */
.mark {{ vertical-align: -3px; margin-right: 7px; object-fit: contain;
  background: #f0ede6; border-radius: 4px; padding: 2px; }}
.nomark {{ display: inline-block; width: 16px; height: 16px;
  line-height: 16px; text-align: center; border-radius: 4px;
  background: color-mix(in srgb, var(--dim) 18%, transparent);
  color: var(--dim); font-weight: 700; font-size: var(--t-meta); cursor: help; }}
.teamcell {{ white-space: nowrap; }}
.cbar {{ display: inline-block; width: 4px; height: 16px; border-radius: 2px;
  margin-right: 8px; vertical-align: -2px; }}
.seed {{ display: inline-block; background: var(--accent); color: #fff;
  border-radius: 6px; font-size: var(--t-label); width: 22px; height: 22px;
  line-height: 22px; text-align: center; vertical-align: 3px; margin-right: 4px; }}
.badge {{ font-size: var(--t-fine); border-radius: 20px; padding: 2px 9px;
  vertical-align: 1px; font-weight: 600; letter-spacing: .03em; }}
.badge.ok {{ background: color-mix(in srgb, var(--ok) 15%, transparent); color: var(--ok); }}
.badge.warn {{ background: color-mix(in srgb, var(--warn) 15%, transparent); color: var(--warn); }}
.note {{ color: var(--dim); font-size: var(--t-label); margin: 6px 0 0; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line);
  font-variant-numeric: tabular-nums; }}
th {{ font-size: var(--t-meta); text-transform: uppercase; letter-spacing: .05em;
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
.steps li {{ margin: 6px 0; font-size: var(--t-label); }}
.steps li.seeded {{ font-weight: 700; }}
.steps li.seeded::marker {{ font-weight: 700; }}
.cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
@media (max-width: 700px) {{ .cols {{ grid-template-columns: 1fr; }} }}
ul.games {{ list-style: none; padding: 0; margin: 0; }}
ul.games li {{ padding: 6px 0; border-bottom: 1px solid var(--line); font-size: var(--t-label); }}
.dim {{ color: var(--dim); }}
.ccgtag {{ color: var(--accent); font-weight: 700; font-size: var(--t-meta);
  text-transform: uppercase; }}
.rules ol {{ padding-left: 22px; }} .rules li {{ margin: 7px 0; font-size: var(--t-label); }}
progress {{ width: 100%; height: 6px; accent-color: var(--accent); }}
.sorter {{ font-size: var(--t-row); color: var(--dim); margin: 10px 0 6px; }}
.sorter button {{ font: inherit; border: 1px solid var(--line); background: none;
  color: var(--dim); border-radius: 20px; padding: 3px 12px; margin-left: 6px;
  cursor: pointer; }}
.sorter button.on {{ background: var(--accent); border-color: var(--accent);
  color: #fff; }}
.wchip {{ background: var(--accent2); color: #fff; font-size: var(--t-fine);
  border-radius: 20px; padding: 2px 9px; font-weight: 600;
  letter-spacing: .03em; vertical-align: 1px; text-transform: none; }}
/* Three jobs, three groups: lay the model's chalk down, change what is on
   screen, do something with the board. Groups wrap as units, so a narrow
   window breaks the row where the meaning already breaks. The gap between
   groups is the wider one. */
.wcontrols {{ display: flex; align-items: center; gap: 8px 16px;
  flex-wrap: wrap; margin-bottom: 10px; }}
/* A group is atomic: the row breaks between groups rather than through the
   middle of one. Stretch rather than center, because a native <select> lays
   its text out on its own terms and comes out three pixels shorter than the
   buttons beside it — a row that reads as assembled rather than designed.
   Stretching lets the tallest control set the height and the rest meet it.

   ATOMIC UNTIL IT CANNOT BE. flex:0 0 auto said "never break", and never is
   longer than the row: the chalk group is a select and two buttons that all
   refuse to wrap, and at 530px of card column it grew straight past the
   card's right edge and under the race board beside it. Shrinking and
   wrapping stay last resorts — a flex line takes whole items first, so a
   group is only squeezed once it is alone on its line and still too wide.
   The old max-width:520px rule said this for phones only, which was the
   wrong measurement: what has to fit is the card, not the window. */
.wgroup {{ display: flex; align-items: stretch; gap: 6px; flex: 0 1 auto;
  flex-wrap: wrap; min-width: 0; }}
.wgroup > label {{ display: flex; align-items: center; }}
/* Same rule as the subpages'. Denser than it was and pressable rather than
   merely bordered: a smaller label, a tighter box, a hairline of lift, and a
   press that actually moves. Flex is scoped to button/a because a <select>
   wears this class too and has to keep its native control box. */
.wbtn {{ font: inherit; font-size: var(--t-row); font-weight: 600;
  line-height: 1.5; border: 1px solid var(--line); background: var(--panel);
  color: var(--ink); border-radius: 7px; padding: 5px 11px; cursor: pointer;
  white-space: nowrap; }}
button.wbtn, a.wbtn {{ display: inline-flex; align-items: center; gap: 6px;
  text-decoration: none; box-shadow: 0 1px 0 rgba(0,0,0,.05); }}
.wbtn:hover {{ border-color: var(--accent); background: var(--bg); }}
button.wbtn:active {{ transform: translateY(1px); box-shadow: none; }}
.wbtn:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
/* Decorative like .gi, but sized to the button and coloured by the job the
   button does rather than by what it depicts: chalk the model lays down is
   warn — the same colour as the ★ beside a favorite in the list — anything
   that only changes the view is accent2, and anything that acts on the board
   itself is accent. Three colours, three meanings. */
.bi {{ width: 14px; height: 14px; flex: 0 0 14px; color: var(--accent); }}
.bi-chalk {{ color: var(--warn); }}
.bi-view {{ color: var(--accent2); }}
#wgames details {{ padding: 8px 12px; }}
#wgames summary {{ font-size: var(--t-label); }}
/* The week's own chalk button, in its summary. Quiet until you are on it —
   sixteen of these down the card should not read as sixteen calls to action. */
#wgames summary {{ display:flex; align-items:center; gap:8px }}
.wkfav {{ margin-left:auto; font: inherit; font-size:var(--t-meta);
  color:var(--dim); background:none; border:1px solid var(--line);
  border-radius:999px; padding:1px 9px; cursor:pointer; line-height:1.6 }}
.wkfav:hover {{ color:var(--accent); border-color:var(--accent) }}
.wkfav:focus-visible {{ outline:2px solid var(--accent);
  outline-offset:2px }}
.wkstar {{ color: var(--warn); margin-right: 4px; }}
/* A link that could not be applied. Stated where the picks would have been,
   because the alternative is a board that silently is not what was sent. */
.wurlwarn {{ border-left: 3px solid var(--warn); padding-left: 10px;
  margin: 0 0 10px; }}
main > *, .duo > *, .cols > * {{ min-width: 0; }}
.tablescroll {{ overflow-x: auto; position: relative; }}
.scrollbox {{ position: relative; }}
.scrollbox::after {{ content: ""; position: absolute; top: 0; right: 0;
  bottom: 0; width: 38px; pointer-events: none; opacity: 1;
  transition: opacity .18s ease;
  background: linear-gradient(to right, transparent, var(--panel)); }}
.scrollbox.at-end::after {{ opacity: 0; }}
/* No wrapping. The row was flex-wrap:wrap with pick buttons that could not
   shrink, so "Southeast Missouri State at Iowa State" pushed the date onto a
   second line and the list lost its rhythm. The two buttons share the space
   and truncate — which is what the pick'em slate already does with the same
   long names — while the tag and the date, both short and both fixed, never
   move. */
.wgame {{ display: flex; align-items: center; gap: 8px; padding: 5px 0;
  border-bottom: 1px solid var(--line); }}
.wgame .pick {{ flex: 1 1 0; min-width: 0; display: flex;
  align-items: center; gap: 6px; overflow: hidden; }}
.wgame .pick .nm {{ overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }}
.wgame .at, .wgame .wdate, .wgame .nctag {{ flex: 0 0 auto; }}
/* The per-game peek. A dotted handle at the end of the row, quiet until
   wanted — 120 of these must not read as 120 buttons. */
.wpeek {{ flex: 0 0 auto; background: none; border: 0; padding: 0 2px;
  cursor: pointer; color: var(--dim); font-size: var(--t-label);
  line-height: 1; border-radius: 4px; }}
.wpeek:hover {{ color: var(--accent); }}
.wpeek:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
/* One line, wrapping only when it must. Each entry is the model's name in
   small caps and what it makes the game — no bars, no colour, nothing that
   competes with the picker above it. */
.wmodels {{ display: flex; flex-wrap: wrap; gap: 3px 12px;
  margin: -2px 0 6px; padding: 5px 8px; font-size: var(--t-meta);
  color: var(--dim); background: var(--bg); border-radius: 6px;
  font-variant-numeric: tabular-nums; }}
/* display:flex on the element beats the hidden attribute's UA display:none,
   so every unopened strip was drawing its padding and background as an empty
   grey bar under all 120 rows. */
.wmodels[hidden] {{ display: none; }}
.wm i {{ font-style: normal; text-transform: uppercase; letter-spacing: .04em;
  opacity: .7; margin-right: 5px; }}
.wgame:last-child {{ border-bottom: none; }}
.wgame .at {{ color: var(--dim); font-size: var(--t-meta); }}
.wgame .wdate {{ color: var(--dim); font-size: var(--t-meta); margin-left: auto; }}
.nctag {{ color: var(--dim); font-size: var(--t-micro); border: 1px solid var(--line);
  border-radius: 20px; padding: 1px 7px; text-transform: uppercase;
  letter-spacing: .04em; }}
/* Holds the chip's width open on a conference row so the dates in a mixed
   week line up. visibility, not opacity: it keeps the box and its width
   while staying out of the accessibility tree, so nothing announces a
   "non-conf" that isn't there. */
.nctag.ghost {{ visibility: hidden; }}
.tag {{ font-size: var(--t-fine); border-radius: 20px; padding: 2px 9px;
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
.movemain {{ display:grid; align-items:center; gap:0 10px;
  grid-template-columns:22px minmax(110px,148px) 62px auto }}
.movepts {{ text-align:right; font-variant-numeric:tabular-nums }}
@media (max-width:640px) {{
  .movemain {{ grid-template-columns:22px 1fr auto; row-gap:2px }}
}}
.levmain {{ display:grid; align-items:center; gap:0 10px;
  grid-template-columns:minmax(0,1fr) auto 112px 34px }}
.levgame {{ min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap }}
.levdate {{ color:var(--dim); white-space:nowrap }}
.levswing {{ font-size:var(--t-row); margin-top:2px }}
@media (max-width:640px) {{
  .levmain {{ grid-template-columns:minmax(0,1fr) auto; row-gap:3px }}
  .levbar {{ display:none }}
}}
.clmain {{ display:grid; align-items:center; gap:0 10px;
  grid-template-columns:22px minmax(110px,148px) 112px 46px auto 1fr }}
.clteam {{ min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap }}
.clpct {{ text-align:right }}
/* The chips hold their line — a "clinched" that breaks in half is not a
   chip any more. The record after them does not: it is the longest thing on
   the row and the least load-bearing, so when the row runs out of width it
   is the one that gives. Set nowrap it did the opposite, holding the whole
   card open past the column it was sitting in. */
.cltags {{ white-space:nowrap }}
.clrec {{ min-width:0 }}
@media (max-width:640px) {{
  .clmain {{ grid-template-columns:22px 1fr auto; row-gap:3px }}
  .clbar, .clpct {{ display:none }}
}}
.clrow {{ padding: 8px 0; border-bottom: 1px solid var(--line);
  font-size: var(--t-copy); }}
.obar {{ display: inline-block; width: 110px; height: 8px;
  background: var(--line); border-radius: 4px; overflow: hidden;
  vertical-align: 1px; margin: 0 6px 0 8px; }}
.chaosband {{ display: flex; align-items: center; gap: 14px;
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px;
  margin-bottom: 12px; font-size: var(--t-label); }}
.cnum {{ font-size: var(--t-hero); font-weight: 800; line-height: 1;
  font-variant-numeric: tabular-nums; }}
.obar i {{ display: block; height: 100%; border-radius: 4px; }}
.opct {{ font-variant-numeric: tabular-nums; font-size: var(--t-label); }}
.clrow:last-of-type {{ border-bottom: none; }}
.scen {{ margin: 6px 0 2px; padding-left: 22px; font-size: var(--t-row);
  color: var(--dim); }}
.scen li {{ margin: 3px 0; }}
.elim {{ font-size: var(--t-row); margin: 10px 0 0; }}
.pick {{ font: inherit; font-size: var(--t-row); display: inline-flex;
  align-items: center; gap: 6px; border: 1px solid var(--line);
  background: var(--panel); color: var(--ink); border-radius: 8px;
  padding: 4px 10px; cursor: pointer; min-width: 150px; }}
.pick img {{ margin: 0; }}
.pick .star {{ color: var(--warn); font-size: var(--t-meta); margin-left: auto; }}
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
{topbar}
{top}

<div class=duo>
<div class=stack>

{whatif}

</div>
<div class=stack>

{clinchcard}

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
<script src={base}{v_race}></script>
<script src={base}{v_app}></script>
{footer}
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
<script>(function(){{try{{var b=localStorage.getItem("b12-cards");if(!b)return;var o=JSON.parse(b);if(!o||o.v!==1||!o.d)return;var l=o.d[location.pathname];if(!l||!l.length)return;var s=l.filter(function(k){{return /^[A-Za-z][\w-]*$/.test(k)}}).map(function(k){{return"#"+k+">*:not(h2):not(h3){{display:none}}#"+k+"{{padding-bottom:8px}}"}}).join("");if(!s)return;var e=document.createElement("style");e.id="b12-precollapse";e.textContent=s;document.head.appendChild(e)}}catch(e){{}}}})();</script>
<link rel=stylesheet href="{base}{v_brand}">
<script defer src="{base}{v_theme}"></script>
<script src="{base}{v_state}"></script>
<script src="{base}{v_metrics}"></script>
<script defer src="{base}{v_cards}"></script>
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
  font:var(--t-subhead)/1.65 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}

main {{ max-width: var(--chrome-w); margin: 0 auto;
  padding: 26px 20px 48px; }}
main > p, main > ol, main > ul, main > h2, main > h3, main > table,
main > .aside, main > .worked, main > .backlink {{ max-width: 840px; }}
h2 {{ font-size: var(--t-lead); margin: 34px 0 10px; }}
h3 {{ font-size: var(--t-subhead); margin: 22px 0 8px; }}
p, li {{ font-size: var(--t-subhead); }}
a {{ color: var(--accent2); }}
.lead {{ font-size: var(--t-subhead); }}
ol.rules > li {{ margin: 10px 0; }}
ol.rules b {{ color: var(--ink); }}
.aside {{ background: var(--panel); border: 1px solid var(--line);
  border-left: 4px solid var(--accent); border-radius: 8px;
  padding: 12px 16px; font-size: var(--t-copy); color: var(--dim); margin: 16px 0; }}
.worked {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 18px 22px; margin: 16px 0; }}
.worked ol {{ padding-left: 20px; }}
.worked li {{ margin: 8px 0; font-size: var(--t-copy); }}
.worked li.seeded {{ font-weight: 700; }}
.worked .meta {{ color: var(--dim); font-size: var(--t-label); margin: 0 0 10px; }}
table.models {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
table.models th, table.models td {{ text-align: left; padding: 8px 10px;
  border-bottom: 1px solid var(--line); font-size: var(--t-copy); vertical-align: top; }}
table.models th {{ font-size: var(--t-meta); text-transform: uppercase;
  letter-spacing: .05em; color: var(--dim); }}
.backlink {{ display: inline-block; margin-top: 8px; }}
.card {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 16px 20px; margin: 0 0 18px; }}
.card h2 {{ margin: 0 0 8px; font-size: var(--t-label); text-transform: uppercase;
  letter-spacing: .06em; color: var(--dim); }}
.matchup {{ display: flex; align-items: center; gap: 18px;
  margin: 10px 0 4px; flex-wrap: wrap; }}
.side {{ display: flex; align-items: center; gap: 12px; font-size: var(--t-headline);
  font-weight: 700; border-bottom: 4px solid var(--line);
  padding: 6px 10px 10px 2px; }}
.tname {{ letter-spacing: -.01em; }}
.vs {{ color: var(--dim); font-weight: 400; font-size: var(--t-subhead); padding: 0 6px; }}
.seed {{ display: inline-block; background: var(--accent); color: #fff;
  border-radius: 6px; font-size: var(--t-label); width: 22px; height: 22px;
  line-height: 22px; text-align: center; vertical-align: 3px;
  margin-right: 4px; }}
.badge {{ font-size: var(--t-fine); border-radius: 20px; padding: 2px 9px;
  vertical-align: 1px; font-weight: 600; letter-spacing: .03em; }}
.badge.ok {{ background: #13653626; color: #136536; }}
.badge.warn {{ background: #b4530926; color: #b45309; }}
/* One tile, both themes — see the note on .mark in the main sheet. */
.mark {{ vertical-align: -3px; margin-right: 7px; object-fit: contain;
  background: #f0ede6; border-radius: 4px; padding: 2px; }}
.nomark {{ display: inline-block; width: 16px; height: 16px;
  line-height: 16px; text-align: center; border-radius: 4px;
  background: color-mix(in srgb, var(--dim) 18%, transparent);
  color: var(--dim); font-weight: 700; font-size: var(--t-meta); cursor: help; }}
.dim {{ color: var(--dim); }}
.note {{ color: var(--dim); font-size: var(--t-row); }}


</style>
</head>
<body>
<a class=skip-link href="#main">Skip to content</a>
{topbar}
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
{footer}
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
        v_cards=asset_v("cards.js"),
        v_state=asset_v("state.js"),
        v_metrics=asset_v("metrics.js"),
            topbar=topbar("tiebreaker", year, BASE),
            footer=footer(),
            top=tracker_top(year, "how", matchcard, page="how.html")))
    print(f"built {out}")


# A game is expected to be final about three and a half hours after kickoff.
# Before that there is nothing to learn; long after it, a game still without a
# result is not coming back, and asking again every twenty minutes for the
# rest of the season is how a quota disappears.
SETTLES_AFTER = datetime.timedelta(hours=3, minutes=30)
GIVE_UP_AFTER = datetime.timedelta(days=4)


def pending_results(games, now=None):
    """Games that should have finished by now and have no result on file.

    THIS IS WHAT MAKES A RUN FREE. The scores file changes only when a game
    ends, so a build already holding every finished result has nothing to ask
    the provider — and most builds are exactly that.

    It matters because the schedule cannot be trusted. Measured on this repo,
    GitHub fires a scheduled run a median 19 minutes late, 130 at the 90th
    percentile, and drops one slot in ten outright. The answer is to run far
    more often than needed and make the extra runs cost nothing, which only
    works if a run with nothing to learn is silent.

    Reading it the safe way round: this answers "could the answer have
    changed", not "has it". A kickoff we do not know — start_tbd carries a
    placeholder hour — counts as pending once that placeholder passes, which
    errs toward asking. Erring toward asking costs one call; erring toward
    silence costs a wrong scoreboard.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    due = []
    for g in games:
        if g.get("completed") and g.get("home_points") is not None:
            continue
        start = g.get("start")
        if not start:
            continue
        try:
            t = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t + SETTLES_AFTER <= now <= t + GIVE_UP_AFTER:
            due.append(g)
    return due


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
        # ASK ONLY WHEN THE ANSWER COULD HAVE MOVED. --refresh is exempt: the
        # weekly run also pulls ratings and lines, which change on their own
        # schedule rather than when a game ends, and it doubles as the
        # guaranteed periodic full refresh that catches a correction to a
        # game this would otherwise have stopped asking about.
        if not refresh and os.path.exists(path):
            try:
                have = json.load(open(path))
            except (OSError, ValueError):
                have = None
            if have is not None:
                due = pending_results(have)
                if not due:
                    print(f"{year}: no game has finished since the last "
                          f"fetch — no call made")
                    return fetcher.mark_ccg(have)
                names = ", ".join(f"{g['away']} at {g['home']}" for g in due[:3])
                print(f"{year}: {len(due)} game(s) due a result "
                      f"({names}{'...' if len(due) > 3 else ''}) — fetching")
        try:
            # Explicit: --fetch is a request for fresh scores, and
            # fetch_season now reads its cache unless told otherwise.
            games = fetcher.fetch_season(year, force=True)
            if refresh:
                fetcher.fetch_ratings(year)
                fetcher.fetch_lines(year)
                # Broadcast windows are announced about two weeks out and
                # move after that. Weekly is the right cadence: on the
                # hourly build it would be 300 calls a month to learn the
                # same thing the Tuesday run already knows.
                fetcher.fetch_media(year, force=True)
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


def build_season(year, games, outdir, base, feed=True, sched_outdir=None,
                 sched_base=None):
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
    # render() has just run exactly this, off the same games, the same
    # ratings and a fixed seed, so a second run is guaranteed to produce the
    # same numbers at full price. That was a wasted three seconds when the
    # week cost one simulation; causal leverage buys two more per tracked
    # game, so on an eight-game Saturday the duplicate is most of a minute.
    sims = ctx.get("sims") or {}
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
         f"This week's Big 12 games in {yr}: kickoff times, where they are "
         "played, the forecast, and a link to every preview.",
         # pickcon.js asks once for the whole week and fills whichever slate
         # cards have a split worth showing. Everything else on this page is
         # static, and stays static if it never answers.
         LOCAL_TIME_JS + (
             f'<script defer src="/tiebreaker/{asset_v("pickcon.js")}">'
             f'</script>' if PICKEM_ENABLED else "")),
        ("matrix.html", "The Matrix", "matrix",
         build_matrix_page(ctx),
         f"The {yr} Big 12 head-to-head grid: who plays whom, home or away "
         "and in which week — and the third of the grid the draw never "
         "pairs at all.",
         ""),
        ("draw.html", "The Draw", "draw",
         build_draw_page(year, games, ctx["systems"], ctx["teams"]),
         f"What the unbalanced {yr} schedule was worth, in wins: every Big "
         "12 team's expected conference record on every other team's slate.",
         ""),
        ("rotation.html", "The Rotation", "rotation",
         build_rotation_page(year, games, ctx["teams"], ctx["series"]),
         f"Who each Big 12 team misses in {yr}, the last time they met as "
         "conference opponents, and every pairing's all-time conference "
         "record — 48 of the 120 pairings sit out a season.",
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
    lad_frag = os.path.join(HERE, "history", "ladder_body.html")
    if os.path.exists(lad_frag):
        pages.append(("ladder.html", "The Ladder", "ladder",
                      rebase(open(lad_frag).read()),
                      "How far down the Big 12 tiebreak ladder each tie has "
                      "actually walked — and why it matters that step six, a "
                      "proprietary rating nobody can inspect, has never "
                      "fired.",
                      ""))
    cut_frag = os.path.join(HERE, "history", "cutline_body.html")
    if os.path.exists(cut_frag):
        pages.append(("cutline.html", "The Cut Line", "cutline",
                      rebase(open(cut_frag).read()),
                      "What conference record it has actually taken to reach "
                      "the Big 12 championship game — and the three seasons a "
                      "team on the identical record stayed home.",
                      ""))
    # Both lists below are the evergreen set and they are independent copies:
    # this one decides the canonical URL, the one in write_discovery decides
    # the sitemap. Updating one and not the other is the standing trap.
    evergreen = {"history.html", "how.html", "cutline.html",
                 "ladder.html"}
    sched_url = "https://big12ology.com/schedule/"
    sched_canon = sched_url if year == LIVE_YEAR else f"{sched_url}{year}/"
    if sched_outdir:
        os.makedirs(sched_outdir, exist_ok=True)

    for fname, title, active, body, desc, head in pages:
        schedule_page = fname in SCHEDULE_PAGES
        if schedule_page and not sched_outdir:
            continue
        # Seasons share the tie archive and the rules explainer verbatim, so
        # the archived copies point their canonical at the live one rather
        # than competing with it as duplicates.
        if schedule_page:
            # The schedule itself is the section's front page, so it is
            # written as index.html and its canonical carries no filename.
            index = fname == "schedule.html"
            out_name = "index.html" if index else fname
            if index:
                title = SECTIONS["schedule"]["title"]
            body = rebase_from(body, base, sched_base)
            target = sched_outdir
            cu = sched_canon if index else sched_canon + fname
            sect = "schedule"
            # A front page is addressed as a directory, so the year pills
            # carry no filename for it.
            pg = "" if index else fname
            BASE_was, globals()["BASE"] = BASE, sched_base
        else:
            target = outdir
            cu = (site_url + fname if fname in evergreen else canon + fname)
            sect = "tiebreaker"
            pg = fname
        try:
            with open(os.path.join(target,
                                   out_name if schedule_page else fname),
                      "w") as f:
                f.write(build_subpage(title, active, body, year,
                                      matchcard_for(fname, year, ctx),
                                      canon=cu, desc=desc,
                                      head=head, section=sect, page=pg))
        finally:
            if schedule_page:
                globals()["BASE"] = BASE_was
        if fname in MOVED_TO_SCHEDULE:
            # The old address keeps working. These pages were linked from
            # the tiebreaker nav for a day and are in the sitemap already.
            with open(os.path.join(outdir, fname), "w") as f:
                f.write(redirect_stub(cu, title))

    # A page per game, for the live season only. Archived seasons are read
    # for results, and the leverage a game page exists to show is settled
    # once a season is decided.
    if sched_outdir and year == LIVE_YEAR:
        gdir = os.path.join(sched_outdir, "game")
        os.makedirs(gdir, exist_ok=True)
        # One directory deeper than the schedule pages, so the climb goes on
        # the front: "../" + "../tiebreaker/". Appending it instead walks
        # out of the site root and every asset 404s in silence.
        BASE_was, globals()["BASE"] = BASE, "../" + sched_base
        try:
            for g in games:
                if not g.get("id"):
                    continue
                slug = game_slug(g)
                body = rebase_from(build_game_page(g, ctx), base, BASE)
                with open(os.path.join(gdir, slug), "w") as f:
                    f.write(build_subpage(
                        f"{g['away']} {joiner(g)} {g['home']}",
                        "schedule", body, year,
                        "", canon=f"{sched_canon}game/{slug}",
                        desc=(f"{g['away']} {joiner(g)} {g['home']}, "
                              f"week {g['week']} "
                              f"of the {year} Big 12 season: kickoff, venue, "
                              f"broadcast, the line, and what four rating "
                              f"models make of it."),
                        # The only script on a game page, and the only place
                        # the schedule section touches /api/*. Deferred and
                        # entirely optional: it fills the consensus card or
                        # leaves it hidden.
                        # The same kickoff line as the slate, so it needs the
                        # same script. Without it a game page printed the
                        # venue's clock alone — "Sat 5:00 PM IST" on a page
                        # whose whole job is telling you when to watch, with
                        # the slate one click away answering it properly.
                        head=(LOCAL_TIME_JS
                              + (f'<script defer '
                                 f'src="/tiebreaker/{asset_v("pickcon.js")}">'
                                 f'</script>' if PICKEM_ENABLED else "")
                              + game_jsonld(g, year,
                                            f"{sched_canon}game/{slug}")),
                        section="schedule", page="", up="../"))
        finally:
            globals()["BASE"] = BASE_was
        print(f"built {len(games)} game pages -> {gdir}")

        # Which game has a page, keyed by the id everything else here is
        # keyed by. The attendance tracker is the reader for this: it holds
        # the same games and the same ids, but nothing that could reproduce a
        # slug — the names in a slug are CFBD's spelling of them, and
        # "Texas A&M" becomes "texas-a-m" by a rule that lives in this file
        # and nowhere near that page. Publishing the answer beats publishing
        # the rule twice.
        #
        # Live season only, because that is when game pages exist. A season
        # roll therefore empties last year's entries out rather than leaving
        # a map to 120 pages that have been deleted.
        with open(os.path.join(gdir, "previews.json"), "w") as f:
            json.dump({"season": year,
                       "games": {str(g["id"]): game_slug(g)
                                 for g in sorted(games,
                                                 key=lambda g: g.get("id") or 0)
                                 if g.get("id")}},
                      f, separators=(",", ":"), sort_keys=True)

    build_explainer(year, matchcard_for("how.html", year, ctx), outdir)

    if feed:
        write_if_unchanged_skip(
            os.path.join(outdir, "feed.xml"),
            feed_mod.build_feed(games, year, systems, overrides),
            re.compile(r"<lastBuildDate>[^<]*</lastBuildDate>"))

    ccg = tb.championship(games, overrides)
    cl = clinch_mod.analyze(games, overrides)
    data = {
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds"),
        "season": year,
        "standings": [{k: v for k, v in r.items() if k != "log"}
                      for r in rows],
        "championship": ccg,
        # The single game that moves the title race most in the next week
        # that HAS a conference game — next_conf_week_ids already skips
        # ahead over a week with none, so this needs no calendar logic of
        # its own. Written here because write_hub runs after this file and
        # reads it; recomputing ten thousand seasons to put one game on the
        # front page would be a poor trade.
        "leverage_top": (lambda L: {
            "away": L[0]["away"], "home": L[0]["home"],
            "start": L[0]["game"].get("start"),
            "total": L[0]["total"],
            "pair": {t: list(v) for t, v in (L[0].get("pair") or {}).items()},
        } if L else None)(leverage_of(sims)),
        "race": {t: {"status": i["status"], "destiny": i["destiny"],
                     "p_ccg": (sims.get(t, {}) or {}).get("p_ccg"),
                     "exp_conf_wins": (sims.get(t, {}) or {}).get("exp_w")}
                 for t, i in cl["teams"].items()},
    }
    write_if_unchanged_skip(
        os.path.join(outdir, "data.json"),
        json.dumps(data, indent=1),
        re.compile(r'"generated": "[^"]*"'))
    write_forecast(year, games, systems, sims)
    with open(os.path.join(outdir, "standings.csv"), "w") as f:
        f.write("rank,team,conf_w,conf_l,nonconf_w,nonconf_l,"
                "overall_w,overall_l,p_ccg\n")
        for r in rows:
            p = (sims.get(r["team"], {}) or {}).get("p_ccg", "")
            f.write(f"{r['rank']},{r['team']},{r['conf_w']},{r['conf_l']},"
                    f"{r['nonconf_w']},{r['nonconf_l']},{r['overall_w']},"
                    f"{r['overall_l']},{p}\n")

    # The Brief is the front door of every season. index.html is its file
    # name, which is what MATCHCARD_PAGES names it by.
    brief = build_brief(year, games, overrides, systems, sims,
                        matchcard_for("index.html", year, ctx), canon=canon)
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


def alltime_h2h_card(year, games, teams, series=None):
    """Every pairing's all-time record as conference opponents.

    This grid belongs on the rotation page because the rotation is what
    shapes it: a format that leaves 48 of the 120 pairings unplayed every
    year produces a ledger full of 1-0s, blanks and lopsided one-game
    "series". It used to sit at the bottom of the tie archive, where the tie
    archive's own styles were the only thing holding it up.

    Same treatment as the current-season grid on the schedule page — one
    card, the site's win-percentage ramp, the same empty-cell marks — rather
    than a second look for the same kind of table.
    """
    seasons = fetcher.usable_seasons(range(2011, year))
    # Handed down from render() so fifteen seasons are read once a build.
    # Optional, because this card is legible on its own and the tests call it
    # that way; absent, it pays for itself.
    wl = series_records(year, teams, games) if series is None else series
    # In August the live season has no results in it, and dating the grid
    # through a season nobody has played yet is a claim about games that do
    # not exist.
    played = any(g["conference_game"] and not g.get("ccg") and g["completed"]
                 for g in games)
    through = year if played else max(seasons, default=year)
    order = sorted(teams)
    head = "".join(f"<th title='{esc(t)}'>{esc(team_abbr(teams, t))}</th>"
                   for t in order)
    body, never, once = [], 0, 0
    for a in order:
        cells = []
        for b in order:
            if a == b:
                cells.append("<td class=selfcell aria-hidden=true>&#9587;</td>")
                continue
            w, l = wl[a][b]
            n = w + l
            if not n:
                never += 1
                cells.append(f"<td class=nomeet title='{esc(a)} and {esc(b)} "
                             f"have never met as conference opponents'>"
                             f"&bull;</td>")
                continue
            if n == 1:
                once += 1
            cells.append(
                f"<td style='color:{winpct_color(w / n)}' title='{esc(a)} "
                f"{w}&ndash;{l} vs {esc(b)} in {n} conference "
                f"meeting{'' if n == 1 else 's'}'>{w}&ndash;{l}</td>")
        body.append(f"<tr><td class=teamcell>{logo_img(a, 14)}{esc(a)}</td>"
                    f"{''.join(cells)}</tr>")
    # Both counts walk the grid twice, once from each team's side.
    pairs = len(order) * (len(order) - 1) // 2
    never, once = never // 2, once // 2
    gap = (f"<b>{never}</b> of the {pairs} pairings "
           f"{'has' if never == 1 else 'have'} still never met in conference "
           f"play" if never else
           f"all {pairs} pairings have now happened at least once")

    # `through` is the last season with results in it, which between January
    # and September is not the season this page is for. Two year pills then
    # render a byte-identical grid, and a table that does not move when you
    # change the year reads as one that failed to load rather than as a
    # ledger nothing has been added to yet. The page already knows which it
    # is — `played`, above — and used to keep it to itself.
    pending = ("" if through == year else
               f" No {year} conference game has been played yet, so this is "
               f"where {through} left it and it matches the {through} page "
               f"exactly; it starts moving again with the first conference "
               f"Saturday.")

    return f"""<div class=card id=alltimeh2h>
<h2>All-time head-to-head</h2>
<p>Every pairing's record as conference opponents, 2011&#8211;{through}, read
across: the row team's wins first. {gap}, and <b>{once}</b> have met exactly
once — the whole series is one Saturday.{pending}</p>
<div class="tablescroll scrollbox"><table class=h2h>
<thead><tr><th></th>{head}</tr></thead>
<tbody>{''.join(body)}</tbody></table></div>
<p class=note>Conference meetings only, and membership as it was at the time —
the same games the table above counts, so a pairing's record and the year it
was last played never disagree. Championship-game rematches are left out,
because the schedule only granted that pairing once. 2020 is not counted, as
everywhere else on this site: teams played eight, nine and ten conference
games that season. Many of these pairs first met in 2023 or 2024 — that's
realignment for you.</p>
</div>"""


def build_rotation_page(year, games, teams, series=None):
    """Who each team misses, and when they last met in conference play.

    Nine games among sixteen teams leaves 48 of the 120 pairings unplayed
    every season — the permanent condition of this format, and the reason
    two teams on the same record did not face the same league."""
    rows, st = rotation_mod.report(
        games, teams, fetcher.usable_seasons(range(2011, year)))
    sitting = st["pairs_total"] - st["pairs_played"]

    def pair_line(a, b):
        return (f"{logo_img(a, 16)}{esc(a)} <span class=dim>and</span> "
                f"{logo_img(b, 16)}{esc(b)}")

    firsts = ""
    if st["firsts"]:
        items = "".join(f"<li>{pair_line(a, b)}</li>" for a, b in st["firsts"])
        remaining = st["pairs_total"] - st["pairs_ever"] - len(st["firsts"])
        firsts = f"""<div class=card id=firstmeet>
<h2>{'The last first meeting' if remaining == 0 and len(st['firsts']) == 1
     else 'Meeting for the first time'}</h2>
<ul class=firstlist>{items}</ul>
<p class=note>Of the {st['pairs_total']} pairings this conference contains,
{st['pairs_ever']} had already happened in conference play.
{'This is the last one that had not. After ' + str(year) +
 ', every pair in the Big 12 will have played as conference opponents.'
 if remaining == 0 and len(st['firsts']) == 1
 else f'{remaining} pairings will still be waiting after {year}.'}</p>
</div>"""

    body = []
    for r in rows:
        cells = "".join(
            f"<li>{logo_img(m['opponent'], 16)}"
            f"<span class=mabbr>{esc(team_abbr(teams, m['opponent']))}</span>"
            + (f"<span class=myr>{m['last']}</span>" if m["last"]
               else "<span class='myr warnpill'>never</span>")
            + "</li>"
            for m in r["missing"])
        body.append(f"<tr><td class=teamcell>{logo_img(r['team'], 16)}"
                    f"{esc(r['team'])}</td>"
                    f"<td><ul class=misslist>{cells}</ul></td></tr>")

    return f"""{firsts}
<div class=card id=rotationcard>
<h2>Who you miss</h2>
<p>Nine conference games among sixteen teams. <b>{sitting}</b> of the
{st['pairs_total']} possible pairings sit out {year} entirely — more than a
third of the league each team never sees. The year beside each name is the
last season the two met <em>as conference opponents</em>.</p>
<div class="tablescroll scrollbox"><table class=rotationtable>
<tbody>{''.join(body)}</tbody></table></div>
<p class=note>Conference meetings only, and membership as it was at the
time: these programs have played each other in non-conference games and
bowls for decades, so "never" here is a fact about the league, not about the
two schools. A Utah–Baylor game in 2015 was not a Big 12 meeting — Utah was
in the Pac-12.</p>
</div>
{alltime_h2h_card(year, games, teams, series)}"""


def draw_color(d, span):
    """The site's win-percentage ramp, re-centerd on zero.

    Same red-to-green anchors every other number here uses, so the page does
    not introduce a second color language — but mapped so that 0 sits at the
    neutral middle, negative runs red and positive runs green. Green means an
    easier road than the same team would average, not a better team."""
    if not span:
        return winpct_color(0.5)
    return winpct_color(max(0.0, min(1.0, 0.5 + d / (2 * span))))


def svg_logo(team, x, y, size=16):
    """A team mark inside an SVG, the way the bump chart does it."""
    e = MARKS.get(team)
    if not e or e.get("usable") is False:
        return ""
    ext = (e.get("ext") or ".svg").lstrip(".")
    return (f'<image href="{BASE}logos/{e["key"]}.{ext}" x="{x:.1f}" '
            f'y="{y:.1f}" width="{size}" height="{size}"/>')


def draw_bars(rows, teams, span):
    """Diverging bars: the schedule column, and only the schedule column.

    Built to the same pattern as the bump chart — fixed geometry inside a
    scrolling wrapper rather than a scaling viewBox, marks via <image>, the
    same 11px type. A chart that sets its own type size is a second visual
    language on a site that has one."""
    # 940 is the bump chart's canvas width. Same width in the same card
    # means the same scale factor, which means 11px type renders at the same
    # apparent size as every other chart here. Drawn at 660 it scaled 1.67x
    # in a card this wide and the labels came out half again too big.
    W, rowH, gap = 940, 20, 6
    m = {"t": 10, "r": 26, "b": 26, "l": 190}
    H = m["t"] + len(rows) * (rowH + gap) + m["b"]
    iw = W - m["l"] - m["r"]
    mid = m["l"] + iw / 2
    # Leave room past the longest bar for its value, so the number sits with
    # the bar instead of in a column of its own.
    scale = ((iw / 2) - 34) / span if span else 0
    out = [f'<line x1="{mid:.1f}" x2="{mid:.1f}" y1="{m["t"] - 2}" '
           f'y2="{H - m["b"] + 2}" class=dgrid />']
    for i, r in enumerate(rows):
        y = m["t"] + i * (rowH + gap)
        cy = y + rowH / 2
        v = r["vs_average"]
        w = max(abs(v) * scale, 1.5)
        x = mid - w if v < 0 else mid
        col = draw_color(v, span)
        out.append(f'<g class=dteam><title>{esc(r["team"])}: {v:+.2f} '
                   f'expected wins vs its own average across all sixteen '
                   f'schedules</title>')
        out.append(svg_logo(r["team"], 8, cy - 8))
        out.append(f'<text x="30" y="{cy + 4:.1f}" class=dlabel>'
                   f'{esc(r["team"])}</text>')
        out.append(f'<rect x="{x:.1f}" y="{y + 3:.1f}" width="{w:.1f}" '
                   f'height="{rowH - 6}" rx="3" fill="{col}"/>')
        vx = (x - 6) if v < 0 else (x + w + 6)
        anchor = "end" if v < 0 else "start"
        out.append(f'<text x="{vx:.1f}" y="{cy + 4:.1f}" class=dval '
                   f'text-anchor="{anchor}" fill="{col}">{v:+.2f}</text>')
        out.append("</g>")
    out.append(f'<text x="{m["l"]}" y="{H - 8}" class=dtick>'
               f'&#8592; harder than this team\'s own average</text>')
    out.append(f'<text x="{W - m["r"]}" y="{H - 8}" text-anchor="end" '
               f'class=dtick>easier &#8594;</text>')
    return (f'<div class=drawwrap><svg class=drawchart viewBox="0 0 {W} {H}" '
            f'role="img" aria-label="Schedule difficulty by team, in expected '
            f'wins above or below what each team would average across all '
            f'sixteen schedules">{"".join(out)}</svg></div>')


def build_draw_page(year, games, systems, teams):
    """What the unbalanced schedule was worth, in wins.

    The page has one job that it originally failed at: making clear that two
    different quantities live in the same table. How many games a team is
    expected to win is mostly a fact about the team. How that compares to the
    same team on every other schedule is the only part that is about the
    schedule."""
    if not systems:
        return ("<div class=card><h2>The draw</h2><p class=note>This page "
                "needs the rating systems, and ratings are only fetched for "
                "the live season — an archived year has none, so there is "
                "nothing here to compute. See the live season for the "
                "current draw.</p></div>")
    m, rows = swap_mod.matrix(games, systems, list(teams))
    if not rows:
        return ("<div class=card><h2>The draw</h2><p class=note>No conference "
                "schedule yet.</p></div>")

    span = max(abs(r["vs_average"]) for r in rows) or 1.0
    spread = rows[-1]["vs_average"] - rows[0]["vs_average"]
    hardest, easiest = rows[0], rows[-1]

    summary = "".join(
        f"<tr><td class=teamcell>{logo_img(r['team'], 16)}{esc(r['team'])}</td>"
        f"<td class=num style='color:{draw_color(r['vs_average'], span)};"
        f"font-weight:700'>{r['vs_average']:+.2f}</td>"
        f"<td class=num>{r['own']:.1f}</td>"
        f"<td class=num>{r['hardest'][0]:.1f}</td>"
        f"<td class=dim>{esc(team_abbr(teams, r['hardest'][1]))}</td>"
        f"<td class=num>{r['easiest'][0]:.1f}</td>"
        f"<td class=dim>{esc(team_abbr(teams, r['easiest'][1]))}</td></tr>"
        for r in rows)

    head = "".join(f"<th title='{esc(t)}'>{esc(team_abbr(teams, t))}</th>"
                   for t in sorted(m))
    body = []
    for t in sorted(m):
        vals = [v for v in m[t].values() if v is not None]
        avg = sum(vals) / len(vals) if vals else 0
        # Color each cell against that team's OWN average, not against the
        # whole grid. Colored on raw wins the grid would just restate which
        # teams are good, in color, and the schedule question would vanish.
        cells = []
        for owner in sorted(m):
            v = m[t][owner]
            if v is None:
                cells.append("<td class=dim>&middot;</td>")
                continue
            own = " own" if owner == t else ""
            cells.append(
                f"<td class='dcell{own}' style='background:"
                f"{draw_color(v - avg, span)}' "
                f"title='{esc(t)} on {esc(owner)}&#39;s schedule: {v:.1f} "
                f"wins, {v - avg:+.1f} vs its own average'>{v:.1f}</td>")
        body.append(f"<tr><td class=teamcell>{logo_img(t, 16)}"
                    f"{esc(team_abbr(teams, t))}</td>{''.join(cells)}</tr>")

    return f"""<div class=card id=drawlede>
<h2>What the draw was worth</h2>
<p>Sixteen teams play nine conference games out of a possible fifteen.
Nobody plays everybody, so two teams finishing 7&#8209;2 did not attempt the
same thing — and the standings cannot tell you which of them had the harder
road. This can.</p>
<p><b>{esc(hardest['team'])} drew the hardest schedule in the league and
{esc(easiest['team'])} drew the easiest.</b> Between them sits
<b>{spread:.2f}</b> of an expected win — worth about one game every other
season, which is roughly the margin that decides a championship-game seat.</p>
{draw_bars(rows, teams, span)}
<p class=note>Each bar is that team's own schedule measured against what the
<em>same team</em> would average across all sixteen schedules. Same roster,
same strength, sixteen different roads: only the schedule changes, so only
the schedule is being measured.</p>
</div>

<div class=card id=drawsummary>
<h2>How to read the numbers</h2>
<p>Two different things live in the table below, and telling them apart is
the whole trick.</p>
<ul class=drawkey>
<li><b>vs average</b> is the schedule. A team's own draw minus what it would
average across all sixteen. Negative is a harder road than typical. <b>This
is the only column about scheduling</b>, and the one the table is sorted
by.</li>
<li><b>Own draw</b> is mostly the team. Texas Tech expects 7.8 conference
wins and Oklahoma State 2.3, and that gap is about how good they are, not
about who they play. Comparing this column down the page tells you very
little.</li>
<li><b>Hardest</b> and <b>easiest slate</b> are the extremes behind the first
column: the schedule that would cost this team the most wins, and the one
that would cost it least, with whose schedule it is.</li>
</ul>
<div class="tablescroll scrollbox"><table class=drawsum>
<thead><tr><th>Team</th><th>vs average</th><th>Own draw</th>
<th colspan=2>Hardest slate</th><th colspan=2>Easiest slate</th></tr></thead>
<tbody>{summary}</tbody></table></div>
</div>

<div class=card id=drawgrid>
<h2>Every team on every schedule</h2>
<div class="tablescroll scrollbox"><table class=drawgridtable>
<thead><tr><th></th>{head}</tr></thead>
<tbody>{''.join(body)}</tbody></table></div>
<p class=note>Read a row: that team's expected wins on each column's
schedule. Color is <em>within the row</em> — green where that schedule is
easier than the row's own average, red where it is harder. Coloring by raw
wins instead would have just restated which teams are good, in color, and
lost the question entirely. The outlined diagonal is the schedule each team
actually drew.</p>
<p class=note><b>A team cannot play itself.</b> Where a borrowed schedule
contains the borrower, it faces the team it borrowed from instead — Houston
on Texas Tech's slate still plays Texas Tech. Without that substitution,
borrowing the best team's schedule would quietly delete the best team from
your season and score it as an easier road; it inflated this spread by
roughly half a win. Win probabilities come from the same rating ensemble as
the championship odds.</p>
</div>"""


def redirect_stub(to, title):
    """A page that moved. Meta-refresh plus a canonical, and a real link for
    anything that honors neither."""
    return ('<!doctype html><meta charset=utf-8>'
            f'<meta http-equiv=refresh content="0; url={to}">'
            f'<link rel=canonical href="{to}">'
            '<meta name=robots content="noindex, follow">'
            f'<title>{esc(title)} — moved</title>'
            f'<p><a href="{to}">{esc(title)} has moved to {to}</a></p>')


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
    #
    # But only when something actually moved. The odds are deterministic, so
    # between results the payload is identical apart from `generated` — and
    # rewriting it anyway would hand CI a one-line diff to commit on every
    # hourly build, roughly three hundred a month, every one of them saying
    # nothing. Compare without the timestamp and leave the file alone.
    if os.path.exists(p):
        try:
            old = json.load(open(p))
            if {k: v for k, v in old.items() if k != "generated"} == \
               {k: v for k, v in payload.items() if k != "generated"}:
                print(f"forecast: week {week} unchanged")
                return
        except (ValueError, OSError):
            pass                     # unreadable: fall through and rewrite
    with open(p, "w") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    print(f"forecast: week {week} -> {p}")


def _live_games_for_sitemap():
    """The live season's games, read straight off disk: write_discovery runs
    after the build and does not carry the season it just rendered."""
    p = os.path.join(HERE, "data", f"games_{LIVE_YEAR}.json")
    try:
        return [g for g in json.load(open(p)) if g.get("id")]
    except (OSError, ValueError):
        return []


# Every pick'em body is a shell. The parts that vary per viewer and per second
# are empty here and filled by app.js; the parts that are true for everybody —
# what the page is, what to do without JavaScript, where the data lives — are
# in the markup, so a reader with a dead network or a blocked script still gets
# a page that explains itself rather than a blank panel.
#
# The two live regions are separate on purpose. role=status is polite and
# carries save progress; role=alert is assertive and carries only the lock and
# a lost session. A lock is not a polite update, and "Saved" must never
# interrupt someone mid-sentence.
PICKEM_LIVE_REGIONS = """
<p id=savestate class=pk-savestate role=status aria-live=polite aria-atomic=true></p>
<p id=alertstate class=pk-alertstate role=alert></p>"""

PICKEM_NOSCRIPT = """
<noscript><p class=note><b>The picker needs JavaScript.</b> The rules and this
week's slate are readable without it:
<a href="/pools/pickem/rules.html">The Rules</a>, or
<a href="/api/slate">the slate as JSON</a>.</p></noscript>"""

PICKEM_SLATE_BODY = f"""
<div class=card id=lockcard hidden>
  <p class=pk-lockline>Locks at first kickoff &mdash;
    <time id=lockat datetime=""></time>
    <span id=cd class=pk-cd aria-hidden=true></span></p>
  <p class=pk-slatecount id=slatecount></p>
</div>
{PICKEM_LIVE_REGIONS}
<p class=pk-signedout id=signedout hidden><a href="/pools/account.html">Sign in</a>
  to make your picks.</p>
<p class=pk-hint id=signedin hidden>Picks save automatically and lock at
  kickoff.</p>
<p class=pk-signedout id=needsname hidden>Choose a display name before you can
  pick &mdash; <a href="/pools/account.html">it takes a moment</a>.</p>
<form id=slateform>
  <div id=slate class=pk-slate>
    <p class=note id=slateload>Loading this week's slate&hellip;</p>
  </div>
</form>
{PICKEM_NOSCRIPT}
<span class=sr-only role=status id=cdsr></span>"""

PICKEM_CARD_BODY = f"""
<div class="card pk-fitcard">
  <div class=pk-boardhead>
    <h2>Your card</h2>
    <label class=pk-wksel hidden>Week
      <select id=cardwk aria-label="Which week to show"></select>
    </label>
  </div>
  <p class=pk-cardseason id=cardseason hidden></p>
  <p class=note id=cardnote>Loading&hellip;</p>
  <div id=card></div>
</div>
{PICKEM_NOSCRIPT}"""

PICKEM_BOARD_BODY = f"""
<div class=card id=roomcard hidden>
  <h2>The room</h2>
  <div id=roombody></div>
</div>
<div class=pk-duo>
  <div class=card>
    <div class=pk-boardhead>
      <h2>The Board</h2>
      <label class=pk-wksel>Week
        <select id=wksel aria-label="Which week to show"></select>
      </label>
    </div>
    <p class=note id=boardnote>Loading&hellip;</p>
    <div class=table-wrap><div class=table-scroll>
      <table id=board></table>
    </div></div>
  </div>
  <div class=card id=histcard hidden>
    <h2>Week by week</h2>
    <p class=note id=histnote></p>
    <div id=hist></div>
  </div>
</div>
{PICKEM_NOSCRIPT}"""

PICKEM_ACCOUNT_BODY = f"""
{PICKEM_LIVE_REGIONS}
<div class=card id=signin hidden>
  <h2>Sign in</h2>
  <p class=note>We ask your provider for one thing: that you are you. Not your
  email address, not your name, not your picture. What becomes public is the
  display name you choose and your record.
  <a href="/privacy">What we store</a>.</p>
  <p class=pk-signins>
    <a class=wbtn href="/api/auth/login/google?return_to=/pools/pickem/">Continue with Google</a>
    <a class=wbtn href="/api/auth/login/github?return_to=/pools/pickem/">Continue with GitHub</a>
  </p>
  <p class=note>Signing in sets one cookie. Nothing else.</p>
</div>
<div class=card id=welcome hidden>
  <h2>Two things before you can pick</h2>
  <ol class=pk-steps>
    <li><b>Choose a display name.</b> It is what everyone sees beside your
    record, and it has to be one nobody else has taken.</li>
    <li><b>Tell us who you follow.</b> Optional, and only used to pick out
    your own rows on your own screen.</li>
  </ol>
</div>
<div class=card id=named hidden>
  <h2>Display name</h2>
  <form id=nameform novalidate>
    <label for=dname>Display name</label>
    <input id=dname name=name type=text maxlength=20 required autocomplete=off
           spellcheck=false aria-describedby="dnamehelp dnameerr">
    <p id=dnamehelp class=note>2&ndash;20 characters. <b>This is public</b> &mdash;
    it is what everyone sees beside your record on The Board.</p>
    <p id=dnameerr class=pk-fielderr role=alert hidden></p>
    <button type=submit class=wbtn>Save name</button>
  </form>
</div>
<div class=card id=teamcard hidden>
  <h2>Your team</h2>
  <form id=teamform>
    <fieldset id=teampick class=pk-teamgrid>
      <legend>Who do you follow?</legend>
    </fieldset>
    <p class=note><b>This is public</b> &mdash; your team's mark appears
    beside your name on The Board, which is half the fun of reading it. It is
    not a pick and it does not affect scoring. Saved as soon as you choose
    one; you can change it whenever you like.</p>
    <noscript><button type=submit class=wbtn>Save team</button></noscript>
  </form>
  <p class=pk-onward id=onward hidden><a href="/pools/pickem/">Go to the slate
    &rarr;</a></p>
</div>
<div class=card id=acctinfo hidden>
  <h2>Signed in</h2>
  <div id=acctbody></div>
</div>
{PICKEM_NOSCRIPT}"""

PICKEM_RULES = """
<div class=card>
<h2>How it works</h2>
<p>Every game involving a Big 12 team is on the slate. Pick a side against the
spread. The whole week locks at the first kickoff, and what you picked becomes
public then — not before.</p>
<h3>The line does not move</h3>
<p>The spread is taken from the market when the week is published and written
down. Everyone plays the same number, and it is the number you are scored
against even if the market moves ten points afterwards. It is rounded to the
nearest half point, because a cross-book average sitting on an arbitrary tenth
is not a line anybody could have bet.</p>
<h3>Push</h3>
<p>Land exactly on the number and it is a push: not a win, not a loss, and
counted in neither half of your percentage. Whole-number lines are the only
ones that can do this. Over the 2025 season five of 120 games would have
pushed.</p>
<h3>Games you cannot pick</h3>
<p>A game with no posted line is shown and grayed out. So is one whose kickoff
has not been announced — the lock is the first kickoff, and a time nobody has
set cannot be part of that. If a line appears before the lock, the game opens
up.</p>
<h3>A game that is not played</h3>
<p>Cancelled or abandoned, it is void for everyone: no win, no loss, out of the
percentage entirely. A voided opener does not reopen a week that has already
locked.</p>
<h3>Weeks</h3>
<p>A pick'em week is a weekend, Tuesday to Monday. It is not the week number on
the tiebreaker or schedule pages: those follow the data provider, whose 2025
&ldquo;week&nbsp;1&rdquo; ran nine days and eighteen games. Opening week is the
one genuine exception here too, running Thursday to Labor Day.</p>
<h3>The board</h3>
<p>Ranked by wins, then by percentage. A new account is hidden from the public
board until it has completed one scored week &mdash; long enough that a
throwaway account is not worth making, short enough that a real player waits
once.</p>
<h3>The chalk</h3>
<p>The bottom row of the board, and <b>not a player</b>. It is what you would
have scored by taking the favorite in every single game, every week, without
thinking about any of them. It has nothing to do with what anybody picked.</p>
<p>It is there because finishing above the field only tells you the field had
a bad week. Finishing above the chalk means your picks knew something the
spread did not, which is the only version of this that is hard. Games with no
favorite &mdash; a line of exactly zero &mdash; are skipped, because there is
nothing for it to take.</p>
<h3>The room</h3>
<p>The other bottom row, and also not a player. It is the side <em>most people
took</em> on every game, scored as if one person had made all of those picks.
Where the chalk asks whether you beat the market, the room asks whether you
beat everybody else put together &mdash; which is a real question, because a
crowd is often better than the average person in it and occasionally much
worse.</p>
<p>Every card counts toward it, exactly as they count toward the split shown
on each game, so you can add up what you can see and arrive at this row. A game
where the picks land <b>exactly even</b> is left out: the room genuinely had no
opinion, and inventing one for it would just make this a worse copy of the
chalk.</p>
<h3>Signing in</h3>
<p>Google or GitHub, and we ask them for one thing: that you are you. No email
address, no name, no picture. What is public is the display name you choose and
your record. <a href="/privacy">What we store</a>.</p>
</div>"""


PICKEM_SOON_CSS = """
  .soonhero { text-align:center; padding:6px 0 2px }
  .soonkick { display:inline-block; font-size:var(--t-meta); letter-spacing:.08em;
    text-transform:uppercase; color:var(--accent); font-weight:700;
    border:1px solid var(--accent); border-radius:999px; padding:4px 13px }
  /* The section's own h2 is a small uppercase gray label, which is right for
     a card heading and wrong for the one line this page exists to say. */
  .soonhero h2 { font-size:clamp(25px,4.2vw,38px); line-height:1.14;
    margin:14px auto 10px; max-width:20ch; letter-spacing:-.01em;
    text-transform:none; color:var(--ink); font-weight:800 }
  .soonhero p { max-width:56ch; margin:0 auto; font-size:var(--t-body);
    color:var(--dim) }
  .soonwhen { margin-top:18px; font-size:var(--t-copy) }
  .soonwhen b { color:var(--ink) }
  .soonshot { margin:0; border:1px solid var(--line); border-radius:10px;
    overflow:hidden; background:var(--panel) }
  /* max-width, not width. The slate shot is 1008 wide and fills; the chart
     is 500 and would have been stretched to double its size, which on a 2x
     asset is exactly the point at which it stops being sharp. */
  .soonshot img { display:block; max-width:100%; height:auto; margin:0 auto }
  /* Above the fold on a phone, so it must not be lazy — but the tag is
     written once for all three, and the first is the one that matters. */
  .soonshot:first-child img { background:var(--bg) }
  .soonshot figcaption { padding:10px 14px; font-size:var(--t-row);
    color:var(--dim); border-top:1px solid var(--line) }
  .soonshot figcaption b { color:var(--ink); font-weight:600 }
  .soonrow { display:grid; gap:14px; margin:16px 0 }
  .soonwhat { display:grid; gap:14px; margin-top:4px;
    grid-template-columns:repeat(auto-fit,minmax(230px,1fr)) }
  .soonwhat h3 { font-size:var(--t-copy); margin:0 0 5px }
  .soonwhat p { margin:0; font-size:var(--t-label); color:var(--dim) }
  .soonfoot { text-align:center; color:var(--dim); font-size:var(--t-label);
    margin:22px 0 2px }
"""


# The survivor pool's one page. A shell like every pick'em body above: the
# picker, the run and the pool all arrive from /api/*, and what is baked in
# is only what is true for everybody. The rules live in the markup at the
# bottom of the page — they are static text, and a reader with no JavaScript
# should still come away knowing what the game is.
SURVIVOR_BODY = """
<div class="card" id=svlock hidden>
  <p class=pk-lockline>Week <span id=svweek></span> locks
    <time id=lockat datetime=""></time>
    <span class=pk-cd aria-hidden=true>&nbsp;&middot;&nbsp;<span id=cd></span></span>
    <span id=cdsr class=sr-only aria-live=polite></span>
  </p>
  <p class=pk-slatecount id=svstanding></p>
</div>

<div class="card pk-handicap" id=svhandicap hidden></div>

<p class=pk-signedout id=svsignedout hidden><a href="/pools/account.html">Sign
in</a> to play the survivor pool. The rules are one sentence: one team a week
to win its game outright, no team twice, and a loss or a forgotten week is the
end of your run.</p>
<p class=pk-signedout id=svneedsname hidden><a href="/pools/account.html">Choose
a display name</a> before picking &mdash; a run with nobody's name on it
cannot go on the board.</p>
<p id=savestate class=pk-savestate role=status aria-live=polite aria-atomic=true></p>
<p id=alertstate class=pk-alertstate role=alert></p>

<div class=card>
  <h2>This week's pick</h2>
  <p class=note id=svnote>Loading&hellip;</p>
  <form id=svform>
    <div id=svslate class=pk-slate></div>
  </form>
</div>

<div class=card id=svusedcard hidden>
  <h2>Your run</h2>
  <ul class=pk-svrun id=svused></ul>
</div>

<div class=card>
  <h2>The Pool</h2>
  <p class=note id=svboardnote>Loading&hellip;</p>
  <div class=table-wrap><div class=table-scroll>
    <table id=svboard></table>
  </div></div>
  <p class=note>One team a week, picked to win the game &mdash; the spread
  plays no part. A team can be used once a season; a canceled game hands it
  back. Lose, or let a week lock without a pick, and your run is over. Rank
  is wins, with the living above the dead on a tie. You can join any week:
  the weeks before your first pick simply never happened for you.</p>
</div>

<noscript><p class=note><b>The picker needs JavaScript.</b> The rules are in
the card above, and the pool stands as
<a href="/api/survivor/board">JSON</a>.</p></noscript>"""


# The pool as its own page, because a leaderboard buried under a picker is a
# leaderboard nobody reads — and because the standings are the thing a person
# who is NOT playing wants to look at. The picker keeps a short version; this
# is the full one, with the graveyard.
SURVIVOR_POOL_BODY = """
<div class=card id=svsumcard hidden>
  <h2>Where it stands</h2>
  <div id=svsummary></div>
</div>

<div class=card>
  <h2>The Pool</h2>
  <p class=note id=svboardnote>Loading&hellip;</p>
  <div class=table-wrap><div class=table-scroll>
    <table id=svboard></table>
  </div></div>
  <p class=note>Everyone alive is above everyone who is out, and wins order
  each group &mdash; the last one standing wins a survivor pool, so a run that
  ended does not sit above one that has not. A run that ended shows the week it
  ended and the team that ended it. <a href="rules.html">The rules</a> explain
  the handicap that lets people join all season.</p>
</div>

<div class=card id=svlatecard hidden>
  <h2>Playing, not ranked</h2>
  <p class=note id=svlatenote></p>
  <div class=table-wrap><div class=table-scroll>
    <table id=svlate></table>
  </div></div>
</div>

<div class=card id=svgravecard hidden>
  <h2>What went wrong</h2>
  <p class=note>The teams that ended a run, and how many they took with
  them. A survivor pool is decided by these far more than by the wins.</p>
  <div id=svgrave></div>
</div>

<noscript><p class=note><b>This page needs JavaScript.</b> The pool stands as
<a href="/api/survivor/board">JSON</a>, and
<a href="rules.html">the rules are here</a>.</p></noscript>"""


SURVIVOR_RULES = """
<div class=card>
<h2>How it works</h2>
<p>Pick <b>one</b> team a week to <b>win its game outright</b>. Not to cover a
spread &mdash; to win. Get it right and you are through to next week. Get it
wrong and your run is over for the season.</p>
<p>The catch, and the whole game: <b>you may use a team once</b>. Spending
Texas Tech in September is a week you cannot spend them in November.</p>

<h3>Big 12 teams only</h3>
<p>The card shows every game a Big 12 team plays, opponent and all &mdash; but
the team you pick has to be <b>one of the sixteen</b>. The visitors are there
so you can see who your team is up against, not to be spent.</p>
<p>The reason is the only reason that matters here: a pick has to cost you
something. Spending BYU costs you BYU for the eleven other weeks they play.
Spending a non-conference visitor costs nothing, because they play a Big 12
team once all season and never appear again. Left open, the whole pool would
be survived on borrowed opponents, and nobody's roster would ever run
thin.</p>

<h3>The spread plays no part</h3>
<p>It is shown beside each game, because it is the best one-number guess at
who wins and by how much, and you would want to know. It has nothing to do
with whether your pick survives. A three-point favorite that wins by four is
exactly as good as a thirty-point favorite that wins by four &mdash; one of
those covered and one did not, and the pool does not care which.</p>

<h3>A team is used once a season</h3>
<p>Once a week locks with your pick on it, that team is spent whether it won
or lost. The one exception is a game that is never played: a canceled or
abandoned game is void, and a voided week hands the team back for you to use
again.</p>

<h3>Missing a week ends your run</h3>
<p>Once you have made your first pick you are in, and every week after that
needs one. A week that locks with no pick from you is the same as a loss.
There is no submit button &mdash; a pick saves the moment you choose it &mdash;
but there is no reminder either, so the week the slate locks while you are
away is the week it ends.</p>

<h3>You can join whenever you like</h3>
<p>Weeks before your first pick did not happen for you &mdash; there is no
catching up to do and nothing counted against you before you arrived. Two
things follow from joining late, and both are knowable before you sign up.</p>

<h3>Join late and the chalk is already spent</h3>
<p>A survivor pool gets hard in November for one reason: everyone burned the
safe teams in September. Somebody walking in at week six with a completely
fresh roster would not be playing the same game as the people who have been
picking around five spent teams since August.</p>
<p>So <b>you arrive having already spent the biggest favorite of every week
you missed</b>. Join at week six and the week one through five chalk is gone
from your board &mdash; the same teams the careful players spent first. If two
weeks shared a favorite, the second week costs you the next biggest instead,
so the handicap is always exactly one team per week missed.</p>
<p>It is worked out from the posted lines, which are frozen when the slate is
published and never change afterwards. That means you can see the exact price
before you join, and nobody can argue about it later.</p>

<h3>To play for the season, be in by week {RANKED_BY}</h3>
<p>Entry never closes, but the leaderboard does. Almost everybody is out by
December, so without a cutoff one person could join in the last week, win once,
be the only run still alive, and take the season on a single pick.</p>
<p>Enter by week {RANKED_BY} and you are on the leaderboard.
Enter after it and you play the same game under the same handicap, and the pool
shows your run &mdash; you are just not in the running for the season. Joining
in November is for the run, not the title.</p>
<p>This is about when you <b>entered</b>, not how long you lasted. Somebody who
started in August and lost in week two played the season and stays on the
board, at the bottom of it.</p>

<h3>A game with no line cannot be picked</h3>
<p>Same rule as the pick'em, for a plumbing reason worth stating: only games
with a posted line are graded, and a pick that can never be graded is a player
who can never be eliminated. If it is on the card, it is pickable.</p>

<h3>The lock</h3>
<p>The whole week locks at the first kickoff, exactly as the pick'em does
&mdash; not at each game's own start. One clock for the week, so nobody picks
on Saturday night knowing what happened at noon.</p>

<h3>Ranking</h3>
<p><b>Everyone still alive is above everyone who is out</b>, however deep the
ended runs went. That is what a survivor pool is: the last one standing wins
it. Wins order each group, so the longest live run leads the living and the
longest ended run leads the dead.</p>
<p>A run that ended keeps its wins &mdash; getting six weeks deep and losing is
a better season than getting two weeks deep and losing, and the board says
so.</p>

<h3>What is public</h3>
<p>The same as the pick'em: your display name, your record, and after a week
locks, the team you picked. Nothing before the lock &mdash; the pool cannot
show you what everyone else is on while you can still change your mind.
<a href="/privacy">Here is everything we store</a>.</p>
</div>"""
SURVIVOR_RULES = SURVIVOR_RULES.replace("{RANKED_BY}",
                                        str(SURVIVOR_RANKED_BY))


def season_opener(games):
    """The first kickoff of the season, for the teaser to name a date.

    Read out of the schedule rather than typed in, because a hardcoded date
    on a "coming soon" page is a promise that rots on its own.
    """
    live = [g for g in games
            if g.get("start") and not g.get("start_tbd")]
    if not live:
        return None
    first = min(live, key=lambda g: g["start"])
    try:
        t = datetime.datetime.fromisoformat(
            first["start"].replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(datetime.timezone.utc)


def write_hub(year, games, lines, sims_race, lev_top=None):
    """The numbers the hand-written hub puts in its hero and on its cards.

    index.html is not generated — it is hand-written at the repo root and has
    been since before there was a build — and the argument for keeping it that
    way is that it is the one page with no data on it. That stopped being
    true the moment it was asked to show some.

    So the page stays hand-written and the FACTS come from here, filled into
    {{HUB_*}} tokens by tools/assemble.sh in exactly the way {{BUILD_STAMP}}
    already is. The markup where a person edits it, the numbers where the data
    is; neither has to know much about the other.

    Every value is derived from committed data, never from the clock. "The
    next game" is the first one with no result, not the first one after now —
    which is what keeps two assembles a minute apart byte-identical, and is
    the property tools/verify-deterministic.sh exists to hold. The one
    time-dependent thing on the page, the countdown, is computed in the
    browser from an ISO instant this writes down.
    """
    live = [g for g in games if g.get("start") and not g.get("start_tbd")]
    nxt = None
    for g in sorted(live, key=lambda x: (x["start"], x["id"])):
        if not g.get("completed"):
            nxt = g
            break

    hub = {"season": year}
    if nxt:
        sp = (lines.get(str(nxt["id"])) or {}).get("spread")
        hub["next"] = {
            "home": nxt["home"], "away": nxt["away"],
            "kickoff": nxt["start"],
            # The home number, same convention as everywhere else: negative
            # means the home side is giving points. Rounded to the half at
            # which a line is actually quoted.
            "spread": (round(float(sp) * 2) / 2) if sp is not None else None,
            "neutral": bool(nxt.get("neutral_site")),
        }

    # Preseason there are no standings to project from, so the two most likely
    # to reach the title game IS the projection — it is what the simulator
    # says, and it is the same p_ccg the tracker prints on every team row.
    ranked = sorted(((t, (v or {}).get("p_ccg") or 0)
                     for t, v in (sims_race or {}).items()),
                    key=lambda x: (-x[1], x[0]))
    if len(ranked) >= 2 and ranked[0][1] > 0:
        hub["ccg"] = [{"team": t, "p": p} for t, p in ranked[:2]]

    hub["counts"] = {
        "teams": len(load_teams() or {}),
        "games": len(games),
        "scheduled": len(live),
    }

    # The attendance figures the hub card prints. Derived here rather than
    # typed into index.html, because they were typed in once and immediately
    # disagreed with the generated facts on the same page — the card counted
    # every row in the CSV and the fact counted the rows that carry a figure.
    # One definition, used by both: a home game this tracker has a crowd for.
    att = facts_mod.attendance_totals()
    if att:
        hub["attendance"] = att
    # No timestamp in it, so an unchanged season rewrites nothing at all.
    # The week's biggest game, drawn with the same component the section
    # pages use. Rendered here rather than in assemble.sh because the fork
    # needs team marks and team colours, and this is the only side that has
    # them — the shell script gets finished HTML and substitutes it.
    if lev_top and lev_top.get("pair") and len(lev_top["pair"]) == 2:
        # Read back through load_ratings, so `regressed` here means what it
        # means to the simulation — the flag regress_stale actually set, not
        # a second guess at staleness from the raw file.
        systems_ = load_ratings(year).get("systems", {})
        g = {"home": lev_top["home"], "away": lev_top["away"],
             "start": lev_top.get("start"), "neutral_site": False}
        lev = {"pair": {t: tuple(v) for t, v in lev_top["pair"].items()}}
        sims = {t: {"p_ccg": (v or {}).get("p_ccg")}
                for t, v in (sims_race or {}).items()}
        was, globals()["BASE"] = BASE, "/tiebreaker/"
        try:
            fork = fork_block(g, lev, sims, load_teams())
        finally:
            globals()["BASE"] = was
        hub["spotlight"] = {
            "away": lev_top["away"], "home": lev_top["home"],
            "when": pretty_date(lev_top.get("start")),
            "total": round((lev_top.get("total") or 0) * 100),
            # WHERE THE ARROWS POINT FROM. Every cell in the fork prints a
            # move from today, and on the race page today is the list of
            # sixteen numbers sitting beside it. The hub has no such list —
            # its only probabilities are the top two in the hero, which are
            # rarely the two teams playing — so on the front page each arrow
            # was a delta from a figure that appeared nowhere on the page.
            "now": {t: round((sims.get(t) or {}).get("p_ccg") or 0.0, 4)
                    for t in (lev_top["home"], lev_top["away"])},
            # HOW MUCH OF THE ENSEMBLE IS THIS SEASON'S. In August most
            # systems have not published yet and load_ratings regresses last
            # year's finals toward the mean rather than trusting them — which
            # is the right handling and invisible from the front page, where
            # "an ensemble of four public rating models" reads as four
            # opinions about the 2026 teams. Counted rather than described,
            # so the sentence stops apologising on its own once they publish.
            "models": {"total": len(systems_), "stale": sum(
                1 for s in systems_.values() if s.get("regressed"))},
            "html": fork,
        }

    write_if_unchanged_skip(os.path.join(SITE, "hub.json"),
                            json.dumps(hub, indent=1, sort_keys=True))
    print(f"built hub numbers -> {SITE}/hub.json")


POOLS_HOME_BODY = """
<div class=card>
  <h2>Two games, one account</h2>
  <p class=note>Sign in once and you are in both. The same display name, the
  same team beside it, one set of rules about what is public and when.</p>
</div>
<div class=poolgrid>
  <a class="card poolcard" href="pickem/">
    <h2>Pickem</h2>
    <p>Pick <b>every</b> Big 12 game against the spread. One line for
    everyone, frozen when the week is published and never moved again. The
    whole slate locks at the first kickoff.</p>
    <p class=note>Scored week by week against the field, the chalk and the
    room.</p>
  </a>
  <a class="card poolcard" href="survivor/">
    <h2>Survivor</h2>
    <p>Pick <b>one</b> team a week to win, and never the same team twice. Get
    it wrong and you are out. The hard part is not this week &mdash; it is
    which teams you have left in November.</p>
    <p class=note>One pick, one life, the whole season.</p>
  </a>
</div>
"""

POOLS_HOME_CSS = """
  .poolgrid { display:grid; gap:14px; margin-top:14px;
    grid-template-columns:repeat(auto-fit,minmax(280px,1fr)) }
  .poolcard { display:block; text-decoration:none; color:inherit }
  .poolcard h2 { color:var(--accent) }
  .poolcard:hover { border-color:var(--accent) }
  .poolcard p { margin:0 0 8px }
  .poolcard p:last-child { margin:0 }
"""


def build_pools_home(year):
    """The roof over the two games.

    It exists because /pools/ has to be somewhere real: it is in the nav, it
    is in the sitemap, and it is where a redirect from the old flat /pickem/
    would otherwise dead-end. What it says is the one thing neither game can
    say for itself — that they share an account.
    """
    global BASE
    prev, BASE = BASE, "../tiebreaker/"
    os.makedirs(POOLS_SITE, exist_ok=True)
    head = (f'<link rel=stylesheet href="{asset_v("styles.css", POOLS_SITE)}">'
            f'<style>{POOLS_HOME_CSS}</style>'
            f'<script defer src="{asset_v("app.js", POOLS_SITE)}"></script>')
    html = build_subpage("Pools", "pools", POOLS_HOME_BODY, year, "",
                         canon="https://big12ology.com/pools/",
                         desc="Two season-long Big 12 games, one account: a "
                              "pick'em against the spread and a survivor "
                              "pool.",
                         head=head, section="pools", page="",
                         subnavon=False)
    with open(os.path.join(POOLS_SITE, "index.html"), "w") as f:
        f.write(html)
    BASE = prev
    print(f"built pools home -> {POOLS_SITE}/index.html")


def build_pools_soon(year, games):
    """The page that stands at /pickem/ until the real thing is switched on.

    It is generated, not hand-written, for the same reasons the rest of the
    section is: one chrome, one footer, the cache-busting, and a place in the
    required-file manifest. And the screenshots are real — captured from the
    running build against a real slate, real scoring and a real chart — which
    is the entire argument the page is making. Mock-ups on a coming-soon page
    are how a coming-soon page stops being believed.
    """
    global BASE
    prev, BASE = BASE, "../tiebreaker/"
    os.makedirs(POOLS_SITE, exist_ok=True)

    # Two dates, and they are not the same one. The doors open two weeks
    # before there is anything to pick, which is deliberate: an account and a
    # display name are the two things nobody wants to be doing at 4:55 on the
    # Thursday the first slate locks.
    # The eyebrow carries the opening date, so this sentence carries the other
    # one. It deliberately does not say "week one": the pool numbers a week
    # zero, and the first kickoff of the season is in it.
    opener = season_opener(games)
    when = (f"Sign in from <b>{POOLS_OPEN.strftime('%A, %B %-d')}</b>. The "
            f"first slate locks with the season opener, <b>"
            f"{opener.strftime('%A, %B %-d')}</b>." if opener else
            f"Sign in from <b>{POOLS_OPEN.strftime('%A, %B %-d')}</b>.")

    # Real screenshots of the running build, at their real dimensions, so the
    # browser reserves the right space and nothing jumps as they load.
    #
    # MEASURED from the files, not typed in beside them. They were typed in,
    # and the day the chart was re-cropped from 444 to 421 only the slate's
    # number got updated — leaving the page reserving 23px it did not need and
    # jumping when the image landed, which is the exact failure these
    # attributes exist to prevent. A number that describes a file should be
    # read off the file.
    shots = [
        ("slate", "The Slate",
         "Every Big 12 game, one frozen line, and a clock. Pick a side and it "
         "saves as you go &mdash; there is no submit button to forget."),
        ("chart", "Week by week",
         "Your season against the shape of the field, drawn in your team's "
         "colors. <b>The chalk</b> is what taking every favorite would have "
         "scored, and it is harder to beat than it sounds."),
        ("room", "The room",
         "What everybody else picked, on every game, scored as if one person "
         "had made all of it. Beating the field is one thing; beating the "
         "field put together is another."),
    ]
    figs = []
    for k, t, c in shots:
        wh = png_size(os.path.join(POOLS_SITE, "shots", f"{k}.png"))
        if not wh:
            print(f"::warning::shots/{k}.png missing; teaser figure skipped")
            continue
        # Shot at 2x for retina, presented at half that.
        w, h = wh[0] // 2, wh[1] // 2
        figs.append(
            f'<figure class=soonshot><img src="shots/{k}.png" width={w} '
            f'height={h} loading="lazy" decoding="async" '
            f'alt="{esc(t)}, from the pick&rsquo;em under construction.">'
            f'<figcaption><b>{t}.</b> {c}</figcaption></figure>')
    figs = "".join(figs)

    body = f"""
<div class=card>
  <div class=soonhero>
    <span class=soonkick>Opens {POOLS_OPEN.strftime('%B %-d')}</span>
    <h2>Pick every Big 12 game against the spread.</h2>
    <p>One line for everyone, frozen the moment the week is published. The
    whole slate locks at the first kickoff, and what you picked becomes public
    then &mdash; not before.</p>
    <p class=soonwhen>{when}</p>
  </div>
</div>

<div class=soonrow>{figs}</div>

<div class=card>
  <h2>What makes it different</h2>
  <div class=soonwhat>
    <div>
      <h3>The line never moves</h3>
      <p>Taken from the market when the week goes up and written down.
      Everyone plays the same number, whatever the market does afterwards.</p>
    </div>
    <div>
      <h3>A push is a push</h3>
      <p>Land exactly on the number and it counts as neither a win nor a
      loss, and it stays out of your percentage entirely.</p>
    </div>
    <div>
      <h3>Two benchmarks, not just a rank</h3>
      <p>Beating the field only means the field had a bad week. The board
      also shows the chalk and the room, which are harder.</p>
    </div>
  </div>
</div>

<p class=soonfoot>No account needed to look. When it opens, signing in asks
your provider for one thing &mdash; that you are you. No email address, no
name, no picture. <a href="/privacy">What we store</a>.</p>
"""

    html = build_subpage("Pools", "pools", body, year, "",
                         canon="https://big12ology.com/pools/",
                         desc="A weekly Big 12 pick'em against the spread, "
                              "coming soon: one frozen line for everyone, and "
                              "a board that shows the chalk and the crowd.",
                         head=f"<style>{PICKEM_SOON_CSS}</style>",
                         section="pools", page="", subnavon=False)
    with open(os.path.join(POOLS_SITE, "soon.html"), "w") as f:
        f.write(html)
    BASE = prev
    print(f"built pools teaser -> {POOLS_SITE}/soon.html")


def build_pickem(year):
    """The five pick'em shells.

    Nothing here knows a score, a spread or a player. The body of each page is
    an empty target that pickem/app.js fills from /api/*, which is the whole
    reason this section is generated rather than rendered: the deploy runs on
    the tiebreaker's clock — driven by the CFBD quota, not by this game — and
    a slate baked in at build time would be wrong the moment anyone signed in
    or a lock elapsed.

    What generation does buy is everything around that body. One chrome, one
    footer, the content-hash cache busting, and a place in assemble.sh's
    required-file manifest. Hand-writing these would have made the fifth copy
    of a masthead this project deliberately consolidated into one.
    """
    global BASE
    # Two levels down now (/pools/pickem/x.html), so the shared assets are two
    # levels up. This is the whole cost of nesting the games under one roof.
    prev, BASE = BASE, "../../tiebreaker/"
    os.makedirs(PICKEM_SITE, exist_ok=True)
    os.makedirs(POOLS_SITE, exist_ok=True)

    # styles.css after the inherited BRIEF_CSS so it can override; app.js is
    # one classic script with no imports, so this hash is the whole truth
    # about the code version. pct.js comes from the tiebreaker for the shared
    # color ramp rather than a second copy of the same curve.
    # The stylesheet and the client live at /pools/, not inside either game:
    # both games share them, and a second copy is a second thing to keep in
    # step. POOLS_UP walks back out of the game's directory to reach them.
    head = (f'<link rel=stylesheet '
            f'href="{POOLS_UP}{asset_v("styles.css", POOLS_SITE)}">'
            f'<script defer src="{BASE}{asset_v("pct.js")}"></script>'
            f'<script defer '
            f'src="{POOLS_UP}{asset_v("app.js", POOLS_SITE)}"></script>')

    # Color, mark and abbreviation for every team the site knows, written as
    # a real file rather than served from /api/*. The build already has all of
    # it, it never changes between deploys, and a page that must ask the Worker
    # before it can draw a logo is a page that loses its logos when the Worker
    # is down. Paths carry the pick'em's BASE, so they resolve to
    # ../tiebreaker/logos/ exactly as the stylesheet does.
    marks = {}
    for t, e in (MARKS or {}).items():
        if e.get("usable") is False:
            continue
        ext = (e.get("ext") or ".svg").lstrip(".")
        marks[t] = f"{BASE}logos/{e['key']}.{ext}"
    tmeta = load_teams()
    meta = {}
    for t in set(list(marks) + list(tmeta)):
        row = {}
        if marks.get(t):
            row["logo"] = marks[t]
        if tmeta.get(t, {}).get("color"):
            row["color"] = tmeta[t]["color"]
        ab = team_abbr(tmeta, t) if t in tmeta else None
        if ab:
            row["abbr"] = ab
        # Which of the 63 are actually in the conference. The account page
        # offers those sixteen and nothing else: data/teams.json is the
        # membership list, and the rest are here only because they turn up on
        # a schedule.
        if t in tmeta:
            row["b12"] = True
        if row:
            meta[t] = row
    with open(os.path.join(POOLS_SITE, "teams.json"), "w") as f:
        json.dump(meta, f, sort_keys=True, indent=1)

    canon = "https://big12ology.com/pools/pickem/"
    pages = [
        ("index.html", "slate", "The Slate", PICKEM_SLATE_BODY,
         canon, "Pick every Big 12 game against the spread. One frozen line "
                "for everyone; the slate locks at the first kickoff.", True),
        ("card.html", "card", "The Card", PICKEM_CARD_BODY,
         None, None, False),
        ("board.html", "board", "The Board", PICKEM_BOARD_BODY,
         canon + "board.html", "The Big 12 pick'em leaderboard: every "
                               "player's record against the spread.", True),
        ("rules.html", "rules", "The Rules", PICKEM_RULES,
         canon + "rules.html", "How the Big 12 pick'em works: frozen lines, "
                               "pushes, voids, and when the week locks.", True),
    ]
    for fname, active, title, body, url, desc, indexed in pages:
        html = build_subpage(title, active, body, year, "", canon=url,
                             desc=desc, head=head, section="pickem",
                             page=fname if fname != "index.html" else "")
        if not indexed:
            # Your own card and your own account are per-viewer and empty to
            # anyone else. Same directive redirect_stub already uses.
            html = html.replace("<meta charset=utf-8>",
                                "<meta charset=utf-8>"
                                '<meta name=robots content="noindex, follow">')
        with open(os.path.join(PICKEM_SITE, fname), "w") as f:
            f.write(html)

    # The survivor pool. Same depth as the pick'em pages, so it shares their
    # head verbatim, and its own section so its nav is its own three pages.
    os.makedirs(SURVIVOR_SITE, exist_ok=True)
    svcanon = "https://big12ology.com/pools/survivor/"
    for fname, active, title, body, url, desc in [
        ("index.html", "survivor", "The Pick", SURVIVOR_BODY, svcanon,
         "The Big 12 survivor pool: one team a week to win outright, "
         "no team twice, last streak standing."),
        ("pool.html", "svpool", "The Pool", SURVIVOR_POOL_BODY,
         svcanon + "pool.html",
         "The Big 12 survivor pool standings: who is still alive, who went "
         "out and on which team."),
        ("rules.html", "svrules", "The Rules", SURVIVOR_RULES,
         svcanon + "rules.html",
         "How the Big 12 survivor pool works: one team a week to win "
         "outright, no team twice, and what happens if you miss a week."),
    ]:
        sv = build_subpage(title, active, body, year, "", canon=url,
                           desc=desc, head=head, section="survivor",
                           page=fname if fname != "index.html" else "survivor")
        with open(os.path.join(SURVIVOR_SITE, fname), "w") as f:
            f.write(sv)

    # One account for both games, so it sits above them rather than inside
    # one. Written with the pools' own BASE, being a level shallower.
    BASE = "../tiebreaker/"
    acct_head = (f'<link rel=stylesheet '
                 f'href="{asset_v("styles.css", POOLS_SITE)}">'
                 f'<script defer src="{BASE}{asset_v("pct.js")}"></script>'
                 f'<script defer src="{asset_v("app.js", POOLS_SITE)}"></script>')
    acct = build_subpage("Your account", "account", PICKEM_ACCOUNT_BODY, year,
                         "", desc=None, head=acct_head, section="pools",
                         page="account.html")
    acct = acct.replace("<meta charset=utf-8>",
                        "<meta charset=utf-8>"
                        '<meta name=robots content="noindex, follow">')
    with open(os.path.join(POOLS_SITE, "account.html"), "w") as f:
        f.write(acct)

    BASE = prev
    print(f"built pools -> {POOLS_SITE}")


def write_if_unchanged_skip(path, text, ignore=None):
    """Write `text` to `path`, unless the only difference is a timestamp.

    The same argument write_forecast() makes for its own file, applied to the
    two others that carry a clock: a build that rewrites a file whose content
    did not change hands CI a diff to commit on every hourly run — three
    hundred a month, every one of them saying nothing — and buries the one
    build that did change something.

    `ignore` is a compiled regex whose matches are blanked before comparing,
    so the timestamp is excluded from the question without being excluded from
    the file.
    """
    if os.path.exists(path):
        try:
            old = open(path, encoding="utf-8").read()
            a, b = (old, text) if ignore is None else \
                   (ignore.sub("", old), ignore.sub("", text))
            if a == b:
                return False
        except OSError:
            pass                     # unreadable: fall through and rewrite
    with open(path, "w") as f:
        f.write(text)
    return True


def write_discovery(years):
    """A sitemap. Without one a crawler has to guess that
    the archived seasons exist at all — nothing links to 2024 except the
    year pills, and the pages carry no dated signal of their own."""
    site = "https://big12ology.com/tiebreaker/"
    sched = "https://big12ology.com/schedule/"
    subs = ["", "lab.html", "race.html", "standings.html"]
    sched_subs = ["", "matrix.html", "draw.html", "rotation.html"]
    # Listed once, under the live season — every year serves the same bytes.
    evergreen = ["how.html", "history.html", "cutline.html",
                 "ladder.html"]
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
    # The schedule section gets its own sitemap at its own root, because a
    # sitemap only speaks for the path it is served from.
    sched_urls = []
    for y in years:
        base = sched if y == LIVE_YEAR else f"{sched}{y}/"
        for p in sched_subs:
            freq = "weekly" if y == LIVE_YEAR else "yearly"
            pri = "0.9" if (y == LIVE_YEAR and not p) else (
                "0.7" if y == LIVE_YEAR else "0.4")
            sched_urls.append(f"  <url><loc>{base}{p}</loc>"
                              f"<lastmod>{today}</lastmod>"
                              f"<changefreq>{freq}</changefreq>"
                              f"<priority>{pri}</priority></url>")
    # A page per game of the live season. They are real pages with real
    # content, and nothing links to most of them once the week turns over,
    # so the sitemap is how they are found at all.
    for g in _live_games_for_sitemap():
        sched_urls.append(
            f"  <url><loc>{sched}game/{game_slug(g)}</loc>"
            f"<lastmod>{today}</lastmod><changefreq>weekly</changefreq>"
            f"<priority>0.5</priority></url>")

    def write_map(path, entries):
        with open(path, "w") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                    + "\n".join(entries) + "\n</urlset>\n")

    write_map(os.path.join(SITE, "sitemap.xml"), urls)
    os.makedirs(SCHEDULE_SITE, exist_ok=True)
    write_map(os.path.join(SCHEDULE_SITE, "sitemap.xml"), sched_urls)

    # A third, for the same reason as the second: a sitemap only speaks for
    # the path it is served from. Only the three pages that say the same thing
    # to everyone are listed — The Card and the account page are per-viewer
    # and carry noindex. The Rules is the section's real indexable content,
    # since it is the one page whose body is in the file rather than fetched.
    pool = "https://big12ology.com/pools/"
    pick_urls = [
        f'  <url><loc>{pool}</loc><lastmod>{today}</lastmod>'
        f'<changefreq>daily</changefreq><priority>0.8</priority></url>',
        f'  <url><loc>{pool}pickem/</loc><lastmod>{today}</lastmod>'
        f'<changefreq>daily</changefreq><priority>0.8</priority></url>',
        f'  <url><loc>{pool}pickem/rules.html</loc><lastmod>{today}</lastmod>'
        f'<changefreq>monthly</changefreq><priority>0.7</priority></url>',
        f'  <url><loc>{pool}pickem/board.html</loc><lastmod>{today}</lastmod>'
        f'<changefreq>weekly</changefreq><priority>0.6</priority></url>',
        f'  <url><loc>{pool}survivor/</loc><lastmod>{today}</lastmod>'
        f'<changefreq>daily</changefreq><priority>0.8</priority></url>',
        f'  <url><loc>{pool}survivor/rules.html</loc><lastmod>{today}</lastmod>'
        f'<changefreq>monthly</changefreq><priority>0.7</priority></url>',
        f'  <url><loc>{pool}survivor/pool.html</loc><lastmod>{today}</lastmod>'
        f'<changefreq>weekly</changefreq><priority>0.6</priority></url>',
    ]
    os.makedirs(POOLS_SITE, exist_ok=True)
    if PICKEM_ENABLED:
        write_map(os.path.join(POOLS_SITE, "sitemap.xml"), pick_urls)
    else:
        # Dark, the only thing at /pools/ is the teaser, so it is the only
        # thing worth listing.
        write_map(os.path.join(POOLS_SITE, "sitemap.xml"), pick_urls[:1])
        pick_urls = pick_urls[:1]
    # No robots.txt here on purpose: crawlers only read it at the origin
    # root, and this is a project site under /tiebreaker/. The real one
    # lives at the repo root and points at this sitemap.
    print(f"built sitemap.xml ({len(urls)} tiebreaker, "
          f"{len(sched_urls)} schedule, {len(pick_urls)} pickem)")


def main():
    global LIVE_YEAR
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    year = int(argv[0]) if argv else default_season()
    LIVE_YEAR = year
    games = load_games(year, refetch="--fetch" in sys.argv,
                       refresh="--refresh" in sys.argv)
    build_season(year, games, SITE, "", sched_outdir=SCHEDULE_SITE,
                 sched_base="../tiebreaker/")

    # The hub's numbers, read back out of the season that was just written
    # rather than recomputed here — the simulator is the expensive part of
    # this build and running it twice to put two team names on the front page
    # would be a poor trade.
    try:
        with open(os.path.join(SITE, "data.json"), encoding="utf-8") as f:
            blob = json.load(f)
            write_hub(year, games, load_lines(year), blob.get("race"),
                      blob.get("leverage_top"))
    except OSError as e:
        print(f"::warning::hub numbers not written: {e}")

    # And a few hundred true things about the four sections, for the hub to
    # rotate through. Generated on every build for the reason the rest of this
    # file is: a hand-written list of interesting facts goes quietly stale a
    # season after it is written.
    try:
        counts = facts_mod.build(
            year, {y: load_games(y) for y in
                   range(facts_mod.TB_FIRST, facts_mod.TB_LAST + 1)}
                  | {year: games},
            load_teams(), load_lines(year), rotation_mod,
            os.path.join(SITE, "facts.json"))
        print("built facts -> " + ", ".join(
            f"{k} {v}" for k, v in sorted(counts.items())))
    except Exception as e:                       # never fatal to a deploy
        print(f"::warning::facts not written: {e}")

    # The pick'em, which is the one part of this build that writes down a fact
    # instead of deriving one. The slate is published on the weekly refresh —
    # the only run that has just fetched the market — and frozen there. The
    # scores file is rewritten every build, because that is what grades it.
    if year == LIVE_YEAR:
        if PICKEM_ENABLED:
            if "--refresh" in sys.argv or "--republish" in sys.argv:
                pickem_mod.publish_slate(year, games, load_lines(year),
                                         republish="--republish" in sys.argv)
            pickem_mod.write_scores(year, games,
                                    os.path.join(SITE, "pickem-scores.json"))
            build_pickem(year)
            build_pools_home(year)
        # Built either way. It is what stands at /pickem/ while the section
        # is dark, and building it on every run means it cannot rot into
        # something that no longer compiles by the time it is needed.
        build_pools_soon(year, games)
        # /pickem/ shipped and was indexed before the games were grouped, so
        # it does not get to disappear. Meta-refresh plus a canonical, which
        # is all a static host can offer and is what the tiebreaker's own
        # moved pages already use.
        #
        # Where it points depends on what is actually built. Dark, the only
        # thing under /pools/ is the teaser, so a stub aimed at
        # /pools/pickem/ bounced every reader of the old URL into a 404 —
        # which is what production has been doing since the section was
        # grouped, because nothing checked that a redirect lands on a page
        # that exists.
        os.makedirs(os.path.join(POOLS_SITE, "_moved"), exist_ok=True)
        with open(os.path.join(POOLS_SITE, "_moved", "pickem.html"), "w") as f:
            f.write(redirect_stub(
                "/pools/pickem/" if PICKEM_ENABLED else "/pools/", "Pickem"))
    # Finished seasons are rebuilt from cached results — no API calls, and
    # their output is deterministic, so a rebuild is a no-op unless the
    # engine itself changed.
    if "--no-archive" not in sys.argv:
        for y in ARCHIVE_YEARS:
            if y == year:
                continue
            build_season(y, load_games(y), os.path.join(SITE, str(y)), "../",
                         feed=False,
                         sched_outdir=os.path.join(SCHEDULE_SITE, str(y)),
                         sched_base="../../tiebreaker/")
    write_discovery([year] + [y for y in ARCHIVE_YEARS if y != year])


if __name__ == "__main__":
    main()

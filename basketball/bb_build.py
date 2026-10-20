#!/usr/bin/env python3
"""Big 12 men's basketball: the /basketball/ section.

    python3 basketball/bb_build.py            # build from what is on disk
    python3 basketball/bb_build.py --fetch    # refresh first, if anything is due

A sibling of the football sections, not a part of them. It borrows the page
shell (masthead, section nav, footer, stylesheet and marks) from
tiebreaker/build.py so the site still reads as one thing, and keeps its own
data, rules and pages. The rules are site/engine.js; the data is season.py.

Pages land in basketball/site/ and tools/assemble.sh serves them at
/basketball/. Shared assets are reached at ../season/, the way the
/schedule/ section reaches them.
"""
import datetime
import json
import os
import shutil
import sys
import zoneinfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# The football build is imported for its page shell. Its modules (build,
# engine, fetch) are on the path after this directory's, and nothing here
# shares a name with them: everything basketball is bb_*.
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(ROOT, "tiebreaker"))

import build as tb                                       # noqa: E402
import bb_engine as engine                               # noqa: E402
import bb_season as season_mod                           # noqa: E402
import bb_ratings as ratings_mod                         # noqa: E402
import empire as empire_mod                              # noqa: E402

SITE = os.path.join(HERE, "site")
SECTION = "basketball"
# The schedule half of the section: the same directory, but its own tab in
# the sport bar and its own pills, as football's /schedule/ is to /season/.
SCHED = "basketball-schedule"

# THE SECTION REGISTERS ITSELF with the shared shell, here and only in this
# process, rather than living in tiebreaker/build.py: the football build
# never draws a basketball page, and keeping the registration beside the
# pages it serves keeps the two sections' files apart.
tb.SECTIONS[SECTION] = {
    "title": "Big 12 Men’s Basketball",
    "live": ("Unofficial fan tool. The official Big 12 tournament seeding and "
             "tiebreaker procedure, applied to every result as it comes in."),
    "past_text": ("The {year} season as it finished, with the official "
                  "seeding procedure applied."),
}
tb.SECTIONS[SCHED] = {
    "title": "Big 12 Men’s Basketball Schedule",
    "live": ("Every conference game with its tip time, TV and line, and who "
             "meets twice in the eighteen-game draw."),
    "past_text": ("The {year} conference schedule: every result, and who "
                  "met twice."),
}
ATT = "basketball-attendance"
tb.SECTIONS[ATT] = {
    "title": "Big 12 Men\u2019s Basketball Attendance",
    "live": ("Every Big 12 home crowd, against the seats each arena "
             "actually has."),
    "past_text": ("Every Big 12 home crowd in {year}, against the seats each "
                  "arena has."),
}
ATT_NAV = [("attendance", "attendance.html", "The Crowds")]
for _k in (SECTION, SCHED, ATT):
    tb.SECTIONS[_k]["past"] = tb.SECTIONS[_k]["past_text"]
NAV = [("standings", "./", "The Standings"),
       ("race", "race.html", "The Race"),
       ("bracket", "bracket.html", "The Bracket"),
       ("teams", "teams.html", "The Teams"),
       ("ratings", "ratings.html", "The Ratings"),
       ("lab", "lab.html", "The Lab"),
       ("empire", "empire.html", "The Empire"),
       ("rules", "rules.html", "The Rules")]
SCHED_NAV = [("schedule", "schedule.html", "The Schedule"),
             ("matrix", "matrix.html", "The Matrix")]
_nav_for = tb.nav_for
tb.nav_for = lambda section: {SECTION: NAV, SCHED: SCHED_NAV,
                               ATT: ATT_NAV}.get(section) or _nav_for(section)
# No year pills yet: they link to football seasons' directories, and the
# basketball archive keeps its own (Phase 4).
_tracker_top = tb.tracker_top


# What is being built, for the pieces of the shared shell that need to know:
# the live season, the archived ones beside it, and how far below the section
# root this season's pages sit ("" for the live season, "../" for one in
# its own directory).
CTX = {"live": None, "archive": [], "season": None, "deep": ""}


def season_pills(page, up):
    """The season pills, written here rather than by the shared shell: its
    pills are football's years and football's directories. A season keeps
    the page you are on, except a game page, which no other season has, so
    those land on that season's schedule."""
    if not CTX["archive"]:
        return ""
    target = "schedule.html" if page.startswith("game/") else page
    out = []
    for s in [CTX["live"]] + CTX["archive"]:
        lab = season_mod.label(s)
        if s == CTX["season"]:
            out.append(f"<span class=yron>{lab}</span>")
            continue
        rel = target if s == CTX["live"] else f"{s}/{target}"
        out.append(f"<a href='{up}{CTX['deep']}{rel}'>{lab}</a>")
    return "".join(out)


def _tracker_top_bb(year, active, matchcard="", section="tiebreaker", page="",
                    up="", yearpills=True, subnavon=True):
    mine = section in (SECTION, SCHED, ATT)
    html = _tracker_top(year, active, matchcard, section, page, up,
                        yearpills=yearpills and not mine, subnavon=subnavon)
    if mine:
        html = html.replace("<span class=yrpills></span>",
                            f"<span class=yrpills>{season_pills(page, up)}</span>", 1)
    return html


tb.tracker_top = _tracker_top_bb
URL = "https://big12ology.com/basketball/"

esc = tb.esc

# Where a game is played decides the clock it is shown in. CBBD carries the
# state, not a zone, so this is each state's main zone; the few split states
# are split nowhere near a Big 12 floor. Kansas City's T-Mobile Center is
# Missouri.
STATE_TZ = {
    "AZ": "America/Phoenix", "UT": "America/Denver", "CO": "America/Denver",
    "NM": "America/Denver", "ID": "America/Boise", "MT": "America/Denver",
    "WY": "America/Denver", "NV": "America/Los_Angeles",
    "CA": "America/Los_Angeles", "OR": "America/Los_Angeles",
    "WA": "America/Los_Angeles", "TX": "America/Chicago",
    "OK": "America/Chicago", "KS": "America/Chicago", "IA": "America/Chicago",
    "MO": "America/Chicago", "NE": "America/Chicago", "LA": "America/Chicago",
    "AR": "America/Chicago", "MN": "America/Chicago", "WI": "America/Chicago",
    "IL": "America/Chicago", "AL": "America/Chicago", "MS": "America/Chicago",
    "TN": "America/Chicago", "SD": "America/Chicago", "ND": "America/Chicago",
    "HI": "Pacific/Honolulu", "AK": "America/Anchorage",
    "PR": "America/Puerto_Rico", "BS": "America/Nassau",
}
EASTERN = "America/New_York"

DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
       "Sunday"]
MON = ["January", "February", "March", "April", "May", "June", "July",
       "August", "September", "October", "November", "December"]


# --- small pieces -----------------------------------------------------------

def team_href(team, teams, up=""):
    return f"{up}team/{tb.team_slug(team)}" if team in teams else None


def name(team, teams, size=18, up="", logo=True):
    """A team named the way every football page names one: mark, then name,
    the name a link to its page here when it is a Big 12 team."""
    href = team_href(team, teams, up)
    label = esc(team)
    if href:
        label = f"<a class=teamlink href='{href}'>{label}</a>"
    return f"{tb.logo_img(team, size) if logo else ''}{label}"


def zone_of(g, teams):
    st = g.get("state") or (teams.get(g["home"]) or {}).get("currentState")
    return STATE_TZ.get(st or "", EASTERN)


def local_dt(g, teams):
    t = season_mod._parse(g["start"] or "")
    return t.astimezone(zoneinfo.ZoneInfo(zone_of(g, teams))) if t else None


def tip(g, teams):
    """The tip time in the arena's own zone, rewritten by LOCAL_TIME_JS to
    the reader's when the two differ. CBBD's TBD placeholder is midnight
    Eastern, which is a date and not a time."""
    when = local_dt(g, teams)
    if not when:
        return "<span class=dim>time TBD</span>"
    if g["tbd"]:
        return "<span class=dim>time TBD</span>"
    return (f'<time data-kick datetime="{esc(g["start"])}" '
            f'data-tz="{esc(zone_of(g, teams))}">'
            f"{when.strftime('%-I:%M %p')} {esc(when.strftime('%Z'))}</time>")


def game_day(g):
    """The date a game is played on, in Eastern, which is how the sport
    lists a slate. A TBD placeholder is midnight Eastern and lands right."""
    t = season_mod._parse(g["start"] or "")
    return t.astimezone(zoneinfo.ZoneInfo(EASTERN)).date() if t else None


def long_date(d):
    return f"{DOW[d.weekday()]}, {MON[d.month - 1]} {d.day}"


def joiner(g):
    return "vs" if g["neutral"] else "at"


def line_text(g, lines):
    """The spread as "Kansas -6.5", favorite named. CBBD's spread is the
    home side's number."""
    ln = lines.get(g["id"])
    if not ln or ln.get("spread") is None:
        return ""
    s = float(ln["spread"])
    if s == 0:
        return "Pick'em"
    fav = g["home"] if s < 0 else g["away"]
    return f"{esc(fav)} −{abs(s):g}"


def score_text(g):
    if not g["final"]:
        return ""
    win_home = g["hp"] > g["ap"]
    w, l = (g["home"], g["away"]) if win_home else (g["away"], g["home"])
    hi, lo = max(g["hp"], g["ap"]), min(g["hp"], g["ap"])
    return f"<b>{esc(w)}</b> {hi}–{lo}"


def game_slug(g):
    """<CBBD id>-<away>-at-<home>.html, "vs" on a neutral floor: the id
    keeps it unique and stable, the names make it readable."""
    return (f"{g['id']}-{tb.slug_part(g['away'])}-{joiner(g)}-"
            f"{tb.slug_part(g['home'])}.html")


def game_href(g, up=""):
    return f"{up}game/{game_slug(g)}"


def overall(games, team):
    w = l = 0
    for g in games:
        if not g["final"] or team not in (g["home"], g["away"]):
            continue
        won = (g["hp"] > g["ap"]) == (g["home"] == team)
        w += won
        l += not won
    return w, l


# --- the standings ----------------------------------------------------------

BANDS = [(1, 4, "double bye"), (5, 8, "bye"), (9, 16, "first round")]


def band(seed):
    return next(b for b in BANDS if b[0] <= seed <= b[1])


def standings_body(d, st):
    teams, games = d["teams"], d["games"]
    started = any(g["conf"] and g["final"] for g in games)
    rows = []
    for r in st["rows"]:
        ow, ol = overall(games, r["team"])
        lead = st["rows"][0]
        gb = ((lead["w"] - r["w"]) + (r["l"] - lead["l"])) / 2
        note = ""
        if r["tied"] and started:
            decided = [e for e in r["log"] if e.get("winner") == r["team"]
                       or e["step"] in ("mini round-robin", "draw")]
            if decided:
                note = (f"<div class=bbnote>{esc(decided[-1]['text'])}</div>")
        b = band(r["seed"]) if started else (0, 0, "none")
        pct_txt = "" if r["pct"] is None else f"{r['pct']:.3f}"
        gb_txt = "" if not started or gb == 0 else f"{gb:g}"
        rows.append(
            f"<tr class='bb-{b[2].replace(' ', '')}'>"
            f"<td class=posc>{r['seed'] if started else ''}</td>"
            f"<td class=teamcell>{name(r['team'], teams, 20)}{note}</td>"
            f"<td>{r['w']}–{r['l']}</td>"
            f"<td>{pct_txt}</td>"
            f"<td class=dim>{gb_txt}</td>"
            f"<td class=dim>{r['rw']}–{r['rl']}</td>"
            f"<td class=dim>{ow}–{ol}</td></tr>")
    if not started:
        first = min((game_day(g) for g in games if g["conf"]), default=None)
        lead = (f"Conference play starts {long_date(first)}. Until then every "
                f"team is 0–0 and level, so the table is alphabetical and "
                f"shows the record so far against everyone else."
                if first else "No conference games are scheduled yet.")
    else:
        champs = [esc(c) for c in st["champions"]]
        done = all(g["final"] for g in games if g["conf"])
        who = (champs[0] if len(champs) == 1 else
               ", ".join(champs[:-1]) + " and " + champs[-1])
        if done:
            lead = (f"Regular-season champion: {who}." if len(champs) == 1
                    else f"Regular-season co-champions: {who}.")
        else:
            lead = (f"Leading the conference: {who}." if len(champs) == 1
                    else f"Level at the top: {who}.")
    return f"""<div class=card><h2>Big 12 standings · {season_mod.label(d['season'])}</h2>
  <p class=bblead>{lead}</p>
  <div class=tablewrap><table class=stbl><thead><tr><th>Seed</th><th>Team</th>
  <th>Conf</th><th>Pct</th><th>GB</th><th>Road</th><th>Overall</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table></div>
  <p class=note>Seeded for the conference tournament in Kansas City with the
  Big 12's own procedure, applied to the results so far: seeds 1 to 4 get a
  double bye, 5 to 8 a bye, and 9 to 16 play the first round. Every tie is
  broken, because every seed matters; the regular-season title itself is
  never tiebroken, so a share of first is a share of the championship.
  <a href=rules.html>The Rules</a> walks the procedure.</p>
</div>"""


# --- the schedule -----------------------------------------------------------

def game_row(g, d, up="", dated=False):
    """One game as a list row. `dated` adds the day, for a list that is not
    already grouped under one."""
    teams = d["teams"]
    tv = d["media"].get(g["id"]) or []
    bits = []
    day = game_day(g)
    if dated and day:
        bits.append(f"{DOW[day.weekday()][:3]} {MON[day.month - 1][:3]} {day.day}")
    if g["final"]:
        bits.append(score_text(g))
    else:
        bits.append(tip(g, teams))
        if tv:
            bits.append(esc(" / ".join(tv[:2])))
        ln = line_text(g, d["lines"])
        if ln:
            bits.append(ln)
    tag = ""
    if g["tourney"]:
        tag = " <span class=bbtag>Big 12 tournament</span>"
    elif g["conf"]:
        tag = " <span class=bbtag>Big 12</span>"
    return (f"<li class='{'bbconf' if g['conf'] or g['tourney'] else ''}'>"
            f"<span class=bbmatch>{name(g['away'], teams, 16, up)} {joiner(g)} "
            f"{name(g['home'], teams, 16, up)}{tag}</span>"
            f"<a class=bbmeta href='{game_href(g, up)}'>"
            f"{' · '.join(b for b in bits if b)}</a></li>")


def by_day(games):
    days = {}
    for g in games:
        days.setdefault(game_day(g), []).append(g)
    return sorted(days.items(), key=lambda kv: kv[0] or datetime.date.min)


def schedule_body(d):
    games = d["games"]
    today = datetime.datetime.now(zoneinfo.ZoneInfo(EASTERN)).date()
    soon = [g for g in games if game_day(g) and
            today <= game_day(g) <= today + datetime.timedelta(days=6)]
    soon_head = "The next seven days"
    if not soon:
        # Nothing this week, which is the whole offseason and the gap before
        # opening night: show the next day that has games instead of an empty
        # card, and say how far off it is.
        ahead = sorted({game_day(g) for g in games
                        if game_day(g) and game_day(g) > today})
        if ahead:
            soon = [g for g in games if game_day(g) == ahead[0]]
            days = (ahead[0] - today).days
            soon_head = f"Next up, in {days} days"
    recent = [g for g in games if g["final"] and game_day(g) and
              today - datetime.timedelta(days=6) <= game_day(g) < today]

    def days_html(gs, newest_first=False):
        out = []
        days = by_day(gs)
        if newest_first:
            days = days[::-1]
        for day, gg in days:
            out.append(f"<h3 class=bbday>{long_date(day)}</h3>"
                       f"<ul class=bbgames>{''.join(game_row(g, d) for g in gg)}</ul>")
        return "".join(out)

    months = {}
    for g in games:
        if g["conf"] or g["tourney"]:
            day = game_day(g)
            months.setdefault((day.year, day.month), []).append(g)
    conf = "".join(
        f"<details{' open' if (y, m) == (today.year, today.month) else ''}>"
        f"<summary>{MON[m - 1]} {y} <span class=dim>· {len(gs)} games</span></summary>"
        f"{days_html(gs)}</details>"
        for (y, m), gs in sorted(months.items()))
    return f"""<div class="duo even">
<div class=stack><div class=card><h2>{soon_head}</h2>
  {days_html(soon) or "<p class=dim>No more games this season.</p>"}
</div></div>
<div class=stack><div class=card><h2>The last seven days</h2>
  {days_html(recent, newest_first=True) or "<p class=dim>No results in the last seven days.</p>"}
</div></div>
</div>
<div class=card><h2>The conference schedule</h2>
  {conf}
  <p class=note>Times are the arena's, with yours beside them when they
  differ. Lines are the posted spread, favorite named; TV is the national
  or regional window CollegeBasketballData lists. Lines are the average of
  every US book the-odds-api.com carries once the books have posted a game,
  and CollegeBasketballData's before that.</p>
</div>"""


# --- the teams --------------------------------------------------------------

def teams_body(d, st):
    teams = d["teams"]
    cards = []
    seed_of = {r["team"]: r for r in st["rows"]}
    for t in sorted(teams):
        r = seed_of[t]
        ow, ol = overall(d["games"], t)
        cards.append(
            f"<a class=bbteam href='team/{tb.team_slug(t)}'>"
            f"{tb.logo_img(t, 28)}<span><b>{esc(t)}</b>"
            f"<span class=dim>{ow}–{ol} overall · {r['w']}–{r['l']} Big 12"
            f"</span></span></a>")
    return (f"<div class=card><h2>The sixteen</h2><div class=bbteams>"
            f"{''.join(cards)}</div></div>")


def team_body(d, st, team):
    teams, games = d["teams"], d["games"]
    r = next(x for x in st["rows"] if x["team"] == team)
    ow, ol = overall(games, team)
    started = any(g["conf"] and g["final"] for g in games)
    mine = [g for g in games if team in (g["home"], g["away"])]
    info = teams[team]
    done = started and all(g["final"] for g in games if g["conf"])
    seed_txt = (f" · seeded {r['seed']}" if done else
                f" · seeded {r['seed']} today" if started else "")
    head = (f"<div class=card><h2>{tb.logo_img(team, 28)} {esc(team)}</h2>"
            f"<p class=bblead>{ow}\u2013{ol} overall · {r['w']}\u2013{r['l']} in the "
            f"Big 12{seed_txt}"
            f" · home floor {esc(info.get('currentVenue') or '')}, "
            f"{esc(info.get('currentCity') or '')}</p></div>")
    rows = []
    for g in mine:
        opp = g["away"] if g["home"] == team else g["home"]
        where_ = "vs" if g["neutral"] else ("" if g["home"] == team else "at")
        res = ""
        if g["final"]:
            won = (g["hp"] > g["ap"]) == (g["home"] == team)
            us, them = (g["hp"], g["ap"]) if g["home"] == team else (g["ap"], g["hp"])
            res = f"<b class={'bbw' if won else 'bbl'}>{'W' if won else 'L'}</b> {us}–{them}"
        else:
            tv = d["media"].get(g["id"]) or []
            res = " · ".join(b for b in (tip(g, teams), esc(" / ".join(tv[:2])),
                                         line_text(g, d["lines"])) if b)
        day = game_day(g)
        rows.append(
            f"<tr class='{'bbconf' if g['conf'] or g['tourney'] else ''}'>"
            f"<td class=dim>{day.strftime('%b')} {day.day}</td>"
            f"<td class=teamcell>{where_ + ' ' if where_ else ''}"
            f"{name(opp, teams, 18, '../')}</td>"
            f"<td><a class=bbmeta href='{game_href(g, '../')}'>{res}</a></td></tr>")
    return head + (f"<div class=card><h2>Schedule</h2><div class=tablewrap>"
                   f"<table class=stbl><tbody>{''.join(rows)}</tbody></table>"
                   f"</div><p class=note>Big 12 games are shaded. Times are the "
                   f"arena's.</p></div>")


# --- odds ------------------------------------------------------------------

def odds(p, certain_in=False, certain_out=False):
    """A probability as people read one: no 0% for a long shot that is
    alive and no 100% for a near-lock that is not yet certain. Certainty is
    said in words, and only when the arithmetic proves it."""
    if certain_in:
        return "<span class=bbsure>clinched</span>"
    if certain_out:
        return "<span class=dim>out</span>"
    if p >= 0.995:
        return "&gt;99%"
    if p <= 0:
        return "<span class=dim>—</span>"
    if p < 0.005:
        return "&lt;1%"
    return f"{p * 100:.0f}%"


def heat(p):
    """A cell's shade, from nothing to the accent at certainty."""
    return f"background:color-mix(in srgb, var(--accent) {round(p * 70)}%, transparent)"


def model_note(model, sim):
    fitted = (model.get("fitted") or "")[:10]
    return (f"From {sim['n']:,} simulations of the rest of the conference "
            f"season, each seeded with the full Big 12 procedure and then "
            f"played through the bracket. Games are drawn from this site's own "
            f"rating of every Division I team, fitted to every score this "
            f"season{f' (last fitted {fitted})' if fitted else ''}: a team's "
            f"edge in points, plus {model.get('hfa', 0):.1f} for the home floor, "
            f"against an {model.get('sigma', 11):.1f}-point spread of results. "
            f"Early in the season the rating leans on last season's, pulled "
            f"toward average, until a team has played about ten games.")


def seed_runs(seeds):
    """[1, 2, 3, 5] as "1–3, 5"."""
    out, run = [], []
    for k in seeds:
        if run and k == run[-1] + 1:
            run.append(k)
            continue
        if run:
            out.append(run)
        run = [k]
    if run:
        out.append(run)
    return ", ".join(f"{r[0]}–{r[-1]}" if len(r) > 1 else str(r[0])
                     for r in out) or "—"


def proven(cert, ex):
    """The clinched and out flags, from the exact proof when there is one:
    it is tight where the bounds are only safe."""
    if not ex:
        return cert
    out = {}
    for t, c in cert.items():
        e = ex["teams"][t]
        r = dict(c)
        for k in (1, 4, 8):
            r[f"in{k}"] = max(e["seeds"]) <= k
            r[f"out{k}"] = min(e["seeds"]) > k
        r["shareIn"] = e["share"] == "yes"
        r["shareOut"] = e["share"] == "no"
        out[t] = r
    return out


def exact_card(d, st, ex):
    teams = d["teams"]
    E = ex["teams"]
    rows = []
    for r in st["rows"]:
        t, e = r["team"], E[r["team"]]
        rows.append(
            f"<tr><td class=posc>{r['seed']}</td>"
            f"<td class=teamcell>{name(t, teams, 18)}</td>"
            f"<td>{r['w']}–{r['l']}</td><td class=dim>{e['games']}</td>"
            f"<td><b>{seed_runs(e['seeds'])}</b></td>"
            f"<td>{seed_runs(e['winOut']) if e['games'] else '—'}</td>"
            f"<td>{seed_runs(e['loseOut']) if e['games'] else '—'}</td></tr>")
    n = ex["left"]
    return f"""<div class=card><h2>Every seed still possible</h2>
  <div class=tablewrap><table class="stbl bbodds"><thead><tr><th>Now</th>
  <th>Team</th><th>Big 12</th><th>Left</th><th>Possible seeds</th>
  <th>If it wins out</th><th>If it loses out</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table></div>
  <p class=note>Proved, not simulated. With {n} conference game{'s' if n != 1 else ''}
  left there are {ex['seasons']:,} ways the rest of the season can go, and
  every one of them was played out and seeded with the full procedure. Where
  nothing separates two teams the conference holds a draw, so a team caught
  in one is counted at every seed the draw could give it. "If it wins out"
  is whatever everyone else does.</p>
</div>"""


def race_body(d, st, sim, cert, model, ex=None):
    teams = d["teams"]
    T = sim["teams"]
    cert = proven(cert, ex)
    order = sorted(teams, key=lambda t: (-T[t]["top1"], -T[t]["top4"],
                                         -T[t]["expW"], t))
    rec = {r["team"]: r for r in st["rows"]}
    rows = []
    for t in order:
        o, c, r = T[t], cert[t], rec[t]
        rows.append(
            f"<tr><td class=teamcell>{name(t, teams, 20)}</td>"
            f"<td>{r['w']}–{r['l']}</td><td class=dim>{c['left']}</td>"
            f"<td class=dim>{o['expW']:.1f}</td>"
            f"<td style='{heat(o['share'])}'>{odds(o['share'], c['shareIn'], c['shareOut'])}</td>"
            f"<td style='{heat(o['top1'])}'>{odds(o['top1'], c['in1'], c['out1'])}</td>"
            f"<td style='{heat(o['top4'])}'>{odds(o['top4'], c['in4'], c['out4'])}</td>"
            f"<td style='{heat(o['top8'])}'>{odds(o['top8'], c['in8'], c['out8'])}</td>"
            f"<td style='{heat(o['title'])}'>{odds(o['title'])}</td></tr>")
    grid_rows = []

    def cell(t, k, p):
        # With a proof in hand, an impossible seed is marked as one, and a
        # possible seed the simulations happened not to reach is not blank.
        if ex and k + 1 not in ex["teams"][t]["seeds"]:
            return (f"<td class='bbcell bbnope' title='{esc(t)}: seed {k + 1} "
                    f"is no longer possible'>·</td>")
        txt = '' if p < 0.005 else f'{p * 100:.0f}'
        if ex and not txt:
            txt = "&lt;1"
        return (f"<td class=bbcell style='{heat(p)}' title='{esc(t)}: seed "
                f"{k + 1}, {p * 100:.1f}%'>{txt}</td>")
    for t in sorted(teams, key=lambda t: sum((k + 1) * p for k, p in enumerate(T[t]["seed"]))):
        cells = "".join(cell(t, k, p) for k, p in enumerate(T[t]["seed"]))
        grid_rows.append(f"<tr><td class=teamcell>{name(t, teams, 18)}</td>{cells}</tr>")
    head = "".join(f"<th>{k}</th>" for k in range(1, 17))
    return f"""<div class=card><h2>The race to Kansas City</h2>
  <div class=tablewrap><table class="stbl bbodds"><thead><tr><th>Team</th>
  <th>Big 12</th><th>Left</th><th>Proj. wins</th><th>Share of title</th>
  <th>No. 1 seed</th><th>Double bye</th><th>Bye</th><th>Wins tournament</th>
  </tr></thead><tbody>{''.join(rows)}</tbody></table></div>
  <p class=note>A double bye is a top-four seed, a bye a top-eight seed.
  "Clinched" and "out" appear only when nothing left to play can change
  them, however every tie would break; everything else is a chance.
  {model_note(model, sim)}</p>
</div>
{exact_card(d, st, ex) if ex and ex["left"] else ""}
<div class=card><h2>Every seed, every team</h2>
  <div class=tablewrap><table class="stbl bbgrid"><thead><tr><th>Team</th>{head}</tr></thead>
  <tbody>{''.join(grid_rows)}</tbody></table></div>
  <p class=note>The chance, in percent, of each team finishing at each seed,
  ordered by the average seed. {"A dot is a seed the proof above rules out." if ex else "A blank cell is under half a percent."}</p>
</div>"""


# --- the ratings ---------------------------------------------------------------

def ratings_body(d, st, model):
    """Every Division I team on one scale, the Big 12 first: the numbers
    every chance on this section is drawn from, shown rather than implied."""
    teams = d["teams"]
    r = model.get("ratings", {})
    ranked = sorted(r, key=lambda t: (-r[t], t))
    rank = {t: i + 1 for i, t in enumerate(ranked)}
    rec = {x["team"]: x for x in st["rows"]}
    b12 = [t for t in ranked if t in teams]
    rows = []
    for t in b12:
        ow, ol = overall(d["games"], t)
        x = rec.get(t)
        rows.append(
            f"<tr><td class=posc>{rank[t]}</td>"
            f"<td class=teamcell>{name(t, teams, 18)}</td>"
            f"<td><b>{r[t]:+.1f}</b></td><td>{ow}–{ol}</td>"
            f"<td>{x['w']}–{x['l']}</td></tr>")
    nat = []
    for t in ranked:
        cls = " class=bbconf" if t in teams else ""
        nat.append(f"<tr{cls}><td class=posc>{rank[t]}</td>"
                   f"<td class=teamcell>{name(t, teams, 16)}</td>"
                   f"<td>{r[t]:+.1f}</td></tr>")
    fitted = (model.get("fitted") or "")[:10]
    if not r:
        return ("<div class=card><h2>The ratings</h2><p class=dim>No rating "
                "has been fitted for this season yet.</p></div>")
    a, b = b12[0], b12[-1]
    gap = r[a] - r[b]
    return f"""<div class=card><h2>The Big 12, rated</h2>
  <div class=tablewrap><table class="stbl bbodds"><thead><tr><th>Nation</th>
  <th>Team</th><th>Rating</th><th>Overall</th><th>Big 12</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table></div>
  <p class=note>A rating is points per game against an average Division I
  team on a neutral floor. Two ratings subtract: on a neutral floor {esc(a)}
  would be about a {gap:.0f}-point favorite over {esc(b)}, and the home side
  gets {model.get('hfa', 0):.1f} more. Results land around that prediction
  with a spread of {model.get('sigma', 11):.1f} points, which is what turns a
  gap into a chance.</p>
</div>
<div class=card><h2>All {len(ranked)} teams</h2>
  <details><summary>Show the national table <span class=dim>· Big 12 shaded</span></summary>
  <div class=tablewrap><table class="stbl bbodds"><thead><tr><th>Rank</th>
  <th>Team</th><th>Rating</th></tr></thead><tbody>{''.join(nat)}</tbody></table></div>
  </details>
  <p class=note>Fitted to {model.get('games', 0):,} games
  {f'as of {fitted}' if fitted else ''}, refitted every Monday morning. The
  method is the football section's: the ratings, and one home-court number,
  that best explain every margin played, by least squares. Teams with only a
  handful of Division I games are rated together as one, so a single result
  cannot carry a team. Until a team has played about ten games its rating is
  blended with last season's, pulled most of the way back to average.</p>
</div>"""


# --- the crowds ----------------------------------------------------------------

ARENAS = json.load(open(os.path.join(HERE, "data", "arenas.json")))["arenas"]


def home_floor(games, team):
    """The venue a team plays most of its true home games in. A home game
    somewhere else (a holiday game in a pro arena, a floor being renovated)
    is a home game, but not a crowd in the arena the capacity belongs to."""
    seen = {}
    for g in games:
        if g["home"] == team and not g["neutral"] and g.get("venue"):
            seen[g["venue"]] = seen.get(g["venue"], 0) + 1
    return max(seen, key=lambda v: (seen[v], v)) if seen else None


def attendance_body(d):
    teams, games = d["teams"], d["games"]
    rows, crowds = [], []
    for t in sorted(teams):
        a = ARENAS.get(t)
        floor = home_floor(games, t)
        mine = [g for g in games if g["home"] == t and not g["neutral"]
                and g["final"] and g.get("attendance") and g.get("venue") == floor]
        cap = a["capacity"] if a else None
        n = len(mine)
        tot = sum(g["attendance"] for g in mine)
        avg = tot / n if n else 0
        conf = [g["attendance"] for g in mine if g["conf"]]
        full = sum(1 for g in mine if cap and g["attendance"] >= cap)
        best = max(mine, key=lambda g: g["attendance"]) if mine else None
        biggest = f"{best['attendance']:,}" if best else "—"
        for g in mine:
            crowds.append((g["attendance"], g, cap))
        pct = avg / cap if cap and n else 0
        src = (f"<a href='{esc(a['source'])}' rel=noopener title='"
               f"{esc(a.get('note') or 'source')}'>{cap:,}</a>") if a else "—"
        rows.append((pct, f"<tr><td class=teamcell>{name(t, teams, 18)}</td>"
                    f"<td>{esc(a['arena'] if a else floor or '')}</td><td>{src}</td>"
                    f"<td class=dim>{n}</td>"
                    f"<td>{f'{avg:,.0f}' if n else '—'}</td>"
                    f"<td style='{heat(min(pct, 1))}'>{f'{pct * 100:.0f}%' if n else '—'}</td>"
                    f"<td>{f'{sum(conf) / len(conf):,.0f}' if conf else '—'}</td>"
                    f"<td>{full}</td>"
                    f"<td>{biggest}</td></tr>"))
    rows.sort(key=lambda r: -r[0])
    crowds.sort(key=lambda c: (-c[0], c[1]["start"] or ""))
    top = "".join(
        f"<tr><td class=dim>{long_date(game_day(g))}</td>"
        f"<td class=teamcell>{name(g['away'], teams, 16)} at "
        f"{name(g['home'], teams, 16)}</td><td><b>{n_:,}</b></td>"
        f"<td>{f'{n_ / cap * 100:.0f}%' if cap else '—'}</td></tr>"
        for n_, g, cap in crowds[:15])
    if not crowds:
        return ("<div class=card><h2>The crowds</h2><p class=dim>No home game "
                "this season has a crowd on record yet.</p></div>")
    return f"""<div class=card><h2>How full each arena is</h2>
  <div class=tablewrap><table class="stbl bbodds"><thead><tr><th>Team</th>
  <th>Arena</th><th>Seats</th><th>Games</th><th>Average crowd</th>
  <th>Full</th><th>Big 12 games</th><th>Sellouts</th><th>Biggest</th>
  </tr></thead><tbody>{''.join(r for _, r in rows)}</tbody></table></div>
  <p class=note>Every home game in the team's own arena with a crowd on
  record, non-conference games included; "Big 12 games" is the average of the
  conference ones alone. Full is the average crowd against the seats, and a
  sellout is a crowd at or above them, so standing room can take a team past
  100%. Seats are the basketball capacity each school lists, linked to where
  it says so; CollegeBasketballData, which supplies the crowds, carries no
  capacities.</p>
</div>
<div class=card><h2>The biggest crowds</h2>
  <div class=tablewrap><table class="stbl bbodds"><tbody>{top}</tbody></table></div>
</div>"""


# --- the lab -------------------------------------------------------------------

def lab_body(d, model):
    """The what-if: the page is a shell, site/lab.js does the work with
    site/engine.js, and the season rides along as JSON in the page itself."""
    teams = d["teams"]
    conf = [g for g in d["games"] if g["conf"]]
    logos = {}
    for t in teams:
        e = tb.MARKS.get(t)
        if e and e.get("usable") is not False:
            logos[t] = f"{tb.BASE}logos/{e['key']}.{(e.get('ext') or '.svg').lstrip('.')}"
    r = model.get("ratings", {})
    data = {
        "teams": sorted(teams),
        "games": [{"id": g["id"], "home": g["home"], "away": g["away"],
                   "neutral": g["neutral"], "final": g["final"],
                   "hp": g["hp"], "ap": g["ap"],
                   "day": long_date(game_day(g)) if game_day(g) else ""}
                  for g in conf],
        "model": {"ratings": {t: r[t] for t in teams if t in r},
                  "hfa": model.get("hfa", 0), "sigma": model.get("sigma", 11)},
        "logos": logos,
    }
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    n_left = sum(not g["final"] for g in conf)
    return f"""<div id=lab>
<div class=card><h2>Pick the rest of the season</h2>
  <p class=bblead>Click a team to give it the win; click it again to leave
  the game to chance. Every game you leave alone is played out by this site's
  ratings, and the table below re-seeds the conference with the full Big 12
  procedure each time. {n_left} conference game{'s' if n_left != 1 else ''} left.</p>
  <p class=labbar><button type=button class=labact data-act=favorites>Pick every favorite</button>
  <button type=button class=labact data-act=reset>Clear my picks</button></p>
  <div id=lab-games></div>
</div>
<div class=card><h2>Where that leaves everyone</h2>
  <div id=lab-out aria-live=polite><p class=dim>Working it out…</p></div>
  <p class=note>Your picks are kept in the address bar, so copying the link
  shares the whole scenario. Nothing is stored anywhere.</p>
</div>
<script type=application/json id=lab-data>{blob}</script>
<noscript><p class=dim>The Lab runs in your browser and needs JavaScript.</p></noscript>
</div>"""


# --- one game ------------------------------------------------------------------

def big12_record(st, team):
    r = next((x for x in st["rows"] if x["team"] == team), None)
    return r


def odds_row(label, dist):
    """Average seed, No. 1, double bye and bye from a seed distribution."""
    avg = sum((k + 1) * p for k, p in enumerate(dist))
    top = lambda k: sum(dist[:k])
    return (f"<tr><td>{label}</td><td>{avg:.1f}</td>"
            f"<td style='{heat(top(1))}'>{odds(top(1))}</td>"
            f"<td style='{heat(top(4))}'>{odds(top(4))}</td>"
            f"<td style='{heat(top(8))}'>{odds(top(8))}</td></tr>")


def game_body(d, st, sim, model, g):
    teams, up = d["teams"], "../"
    home, away = g["home"], g["away"]
    day = game_day(g)
    tv = d["media"].get(g["id"]) or []
    where_ = ", ".join(x for x in (g.get("venue"), g.get("city"), g.get("state")) if x)
    kind = ("Big 12 tournament" + (f": {esc(g['notes'])}" if g.get("notes") else "")
            if g["tourney"] else "Big 12" if g["conf"] else "Non-conference")
    facts = [f"{long_date(day)}" if day else "", tip(g, teams) if not g["final"] else "",
             esc(where_), esc(" / ".join(tv)), kind]
    head = (f"<div class=card><h2>{name(away, teams, 28, up)} {joiner(g)} "
            f"{name(home, teams, 28, up)}</h2>"
            f"<p class=bblead>{' · '.join(f for f in facts if f)}</p>")

    if g["final"]:
        win_home = g["hp"] > g["ap"]
        w, l = (home, away) if win_home else (away, home)
        src = (" The score is ESPN's; CollegeBasketballData's record of the "
               "game follows within a day." if g.get("src") == "espn" else "")
        head += (f"<p class=bbfinal>Final: <b>{esc(w)} {max(g['hp'], g['ap'])}</b>, "
                 f"{esc(l)} {min(g['hp'], g['ap'])}</p>"
                 f"{f'<p class=note>{src.strip()}</p>' if src else ''}")
    else:
        r = model.get("ratings", {})
        gap = r.get(home, 0) - r.get(away, 0) + (0 if g["neutral"] else model.get("hfa", 0))
        p = engine.win_prob(model, home, away, g["neutral"])
        fav, pf = (home, p) if p >= 0.5 else (away, 1 - p)
        bits = [f"This site's rating makes <b>{esc(fav)}</b> a {pf * 100:.0f}% "
                f"favorite, by about {abs(gap):.0f} point{'s' if round(abs(gap)) != 1 else ''}"]
        ln = line_text(g, d["lines"])
        if ln:
            books = len((d["lines"].get(g["id"]) or {}).get("books") or [])
            bits.append(f"the posted line is {ln}" +
                        (f", the average of {books} books" if books > 1 else ""))
        head += f"<p>{'; '.join(bits)}.</p>"
    head += "</div>"

    # The two teams as they stand.
    rows = []
    for t in (away, home):
        ow, ol = overall(d["games"], t)
        r = big12_record(st, t)
        b12 = f"{r['w']}–{r['l']}, seeded {r['seed']} today" if r else "—"
        rows.append(f"<tr><td class=teamcell>{name(t, teams, 18, up)}</td>"
                    f"<td>{ow}–{ol}</td><td>{b12}</td></tr>")
    teams_card = (f"<div class=card><h2>The two teams</h2><div class=tablewrap>"
                  f"<table class=stbl><thead><tr><th>Team</th><th>Overall</th>"
                  f"<th>Big 12</th></tr></thead><tbody>{''.join(rows)}</tbody>"
                  f"</table></div></div>")

    # What it is worth, for a conference game still to be played.
    worth = ""
    e = sim.get("games", {}).get(str(g["id"]))
    if e:
        T = sim["teams"]
        blocks = []
        for side, t in (("away", away), ("home", home)):
            blocks.append(
                f"<h3 class=bbday>{tb.logo_img(t, 18)} {esc(t)}</h3>"
                f"<div class=tablewrap><table class='stbl bbodds'><thead><tr>"
                f"<th></th><th>Avg. seed</th><th>No. 1 seed</th>"
                f"<th>Double bye</th><th>Bye</th></tr></thead><tbody>"
                f"{odds_row('Today', T[t]['seed'])}"
                f"{odds_row(f'If {esc(home)} wins', e[side][0])}"
                f"{odds_row(f'If {esc(away)} wins', e[side][1])}"
                f"</tbody></table></div>")
        worth = (f"<div class=card><h2>What it is worth</h2>{''.join(blocks)}"
                 f"<p class=note>From the same {sim['n']:,} simulations as The "
                 f"Race, split by who won this game in each: the two teams' "
                 f"chances, seeded with the full Big 12 procedure, either "
                 f"way it goes. A double bye is a top-four seed, a bye a "
                 f"top-eight seed.</p></div>")

    # The rest of the season between the two.
    others = [x for x in d["games"] if x is not g and x["id"] != g["id"]
              and {x["home"], x["away"]} == {home, away}]
    meet = ""
    if others:
        meet = (f"<div class=card><h2>The other meeting{'s' if len(others) > 1 else ''}</h2>"
                f"<ul class=bbgames>{''.join(game_row(x, d, up, dated=True) for x in others)}</ul></div>")
    return head + f"<div class=\"duo even\"><div class=stack>{teams_card}</div>" \
        f"<div class=stack>{meet}</div></div>" + worth


# --- the bracket ---------------------------------------------------------------

ROUND_NAMES = ["First round", "Second round", "Quarterfinals", "Semifinals",
               "Final"]
# Mirrors engine.js's BRACKET: seeds, or the index of an earlier game.
BRACKET = [(9, 16), (12, 13), (10, 15), (11, 14),
           (8, "g0"), (5, "g1"), (7, "g2"), (6, "g3"),
           (1, "g4"), (4, "g5"), (2, "g6"), (3, "g7"),
           ("g8", "g9"), ("g10", "g11"), ("g12", "g13")]
ROUND_OF = [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 4]


def official_seeds(d):
    """The seeds the conference announced, as CBBD carries them on the
    Kansas City games: {team: seed}, empty until the bracket is out."""
    out = {}
    for g in d["games"]:
        if g["tourney"]:
            for t, k in ((g["home"], g.get("hseed")), (g["away"], g.get("aseed"))):
                if isinstance(k, int) and 1 <= k <= 16:
                    out[t] = k
    return out


def bracket_order(d, st, sim, log=print):
    """The seeds the bracket is drawn from: the conference's own once it has
    announced them, today's standings once conference play has started, and
    before that the order the simulations expect, since a table of sixteen
    0-0 teams seeds nothing.

    The announced seeds outrank the procedure here, because they are what
    Kansas City plays. They should agree; when they do not, the log says so,
    since that is either a draw or a reading of the rules to look at."""
    started = any(g["conf"] and g["final"] for g in d["games"])
    if started:
        order = [r["team"] for r in st["rows"]]
        named = official_seeds(d)
        if named:
            for t, k in named.items():
                if order.index(t) + 1 != k:
                    log(f"WARNING: basketball seeds: the conference has {t} at "
                        f"{k}, the procedure at {order.index(t) + 1}")
            rest = [t for t in order if t not in named]
            by_seed = {k: t for t, k in named.items()}
            order = [by_seed[k] if k in by_seed else rest.pop(0)
                     for k in range(1, len(order) + 1)]
        return order, False
    T = sim["teams"]
    return sorted(d["teams"], key=lambda t: (sum((k + 1) * p for k, p in
                                                 enumerate(T[t]["seed"])), t)), True


def tournament_games(d):
    """The Kansas City games on file, by the pair of teams in them."""
    return {frozenset((g["home"], g["away"])): g
            for g in d["games"] if g["tourney"]}


def bracket_results(order, d):
    """{game index: winner} for the bracket games already played, walking
    the bracket so a later round is matched only once its teams are known."""
    games = tournament_games(d)
    slot, out = {}, {}
    for gi, (a, b) in enumerate(BRACKET):
        ta = order[a - 1] if isinstance(a, int) else slot.get(a)
        tb_ = order[b - 1] if isinstance(b, int) else slot.get(b)
        g = games.get(frozenset((ta, tb_))) if ta and tb_ else None
        if g and g["final"]:
            out[gi] = slot[f"g{gi}"] = g["home"] if g["hp"] > g["ap"] else g["away"]
    return out


def bracket_body(d, st, sim, model):
    teams = d["teams"]
    order, projected = bracket_order(d, st, sim)
    results = {} if projected else bracket_results(order, d)
    reach = engine.bracket_odds(order, model, results)
    seed_of = {t: i + 1 for i, t in enumerate(order)}
    games = tournament_games(d)

    # The favorite in each slot, carried forward, to draw the likeliest path.
    # A game already played carries its winner instead, and one on the
    # schedule says when it tips.
    fav = {}
    cols = [[] for _ in ROUND_NAMES]
    for gi, (a, b) in enumerate(BRACKET):
        ta = order[a - 1] if isinstance(a, int) else fav[a]
        tb_ = order[b - 1] if isinstance(b, int) else fav[b]
        g = games.get(frozenset((ta, tb_)))
        real = g if g and g["final"] and gi in results else None
        if real:
            w = results[gi]
            line = f"final {max(real['hp'], real['ap'])}–{min(real['hp'], real['ap'])}"
        else:
            p = engine.win_prob(model, ta, tb_, True)
            w = ta if p >= 0.5 else tb_
            line = f"favorite {max(p, 1 - p) * 100:.0f}%"
            if g:
                when = local_dt(g, teams)
                line += f" · {when.strftime('%a') + ' ' if when else ''}{tip(g, teams)}"
        fav[f"g{gi}"] = w
        # Only the slots both of whose teams are settled are certain; the
        # rest of the path is the favorites', and is drawn lighter.
        sure = all(not isinstance(x, str) or int(x[1:]) in results for x in (a, b))

        def side(t):
            mark = " bbwin" if t == w else ""
            return (f"<div class='bbslot{mark}'><span class=bbseed>{seed_of[t]}</span>"
                    f"{name(t, teams, 16)}</div>")
        cols[ROUND_OF[gi]].append(
            f"<div class='bbmatchup{'' if sure or projected else ' bbmaybe'}'>"
            f"{side(ta)}{side(tb_)}"
            f"<div class=bbprob>{line}</div></div>")
    grid = "".join(
        f"<div class=bbround><h3>{n}</h3><div class=bbcol>{''.join(c)}</div></div>"
        for n, c in zip(ROUND_NAMES, cols))

    rows = []
    for t in sorted(order, key=lambda t: -reach[t][5]):
        # A round a team is already in (a bye, or a game won) is a fact,
        # not a chance, and reads as one.
        cells = "".join(f"<td style='{heat(p)}'>"
                        f"{'<span class=bbsure>✓</span>' if p >= 1 - 1e-9 else odds(p)}</td>"
                        for p in reach[t][1:])
        rows.append(f"<tr><td class=posc>{seed_of[t]}</td>"
                    f"<td class=teamcell>{name(t, teams, 18)}</td>{cells}</tr>")
    over = len(results) == len(BRACKET)
    live = bool(results) and not over
    title = ("The bracket as the simulations expect it" if projected else
             "How the tournament went" if over else
             "The tournament so far" if live else
             "If the season ended today")
    lead = ("Conference play has not started, so these are the seeds the "
            "simulations expect, not anyone's position."
            if projected else
            "The seeds Kansas City played, and every result there."
            if over else
            "The seeds Kansas City is playing, every result so far, and the "
            "favorite in each game still to come. A pairing drawn lighter "
            "waits on a game not yet played."
            if live else
            "Today's standings, seeded with the Big 12's procedure, played "
            "through the bracket.")
    return f"""<div class=card><h2>{title}</h2>
  <p class=bblead>{lead}{"" if over or live else " Each pairing shows the favorite and its chance; the likelier winner moves on."}</p>
  <div class=bbbracket>{grid}</div>
</div>
<div class=card><h2>How far each team gets</h2>
  <div class=tablewrap><table class=stbl><thead><tr><th>Seed</th><th>Team</th>
  <th>Second round</th><th>Quarters</th><th>Semis</th><th>Final</th>
  <th>Champion</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
  <p class=note>Exact, not simulated: from these seeds, the chance of
  reaching each round, every game on a neutral floor in Kansas City.{
  " Games already played count as settled: a team that has won is through, and one that has lost goes no further." if live else
  " The tournament odds on The Race also carry the uncertainty of the seeds themselves, so the two differ until the season is done." if not over else ""}</p>
</div>"""


# --- the matrix ------------------------------------------------------------------

def matrix_body(d, model):
    teams = sorted(d["teams"])
    games = [g for g in d["games"] if g["conf"]]
    meet = {}
    for g in games:
        meet.setdefault((g["home"], g["away"]), []).append(g)

    def cell(t, o):
        if t == o:
            return "<td class=bbself></td>"
        bits = []
        for g in meet.get((t, o), []) + meet.get((o, t), []):
            home = g["home"] == t
            if g["final"]:
                won = (g["hp"] > g["ap"]) == home
                bits.append(f"<b class={'bbw' if won else 'bbl'}>"
                            f"{'W' if won else 'L'}</b>")
            else:
                bits.append("H" if home else "A")
        cls = " bbtwice" if len(bits) > 1 else ""
        return f"<td class='bbm{cls}' title='{esc(t)} v {esc(o)}'>{' '.join(bits)}</td>"

    head = "".join(f"<th title='{esc(o)}'>{tb.logo_img(o, 18) or esc(o[:4])}</th>"
                   for o in teams)
    body = "".join(
        f"<tr><td class=teamcell>{name(t, d['teams'], 18)}</td>"
        + "".join(cell(t, o) for o in teams) + "</tr>" for t in teams)

    # The draw, valued: how many conference wins an exactly average Big 12
    # team would expect against each schedule, home and road as scheduled.
    avg = sum(model.get("ratings", {}).get(t, 0) for t in teams) / len(teams)
    m = dict(model, ratings=dict(model.get("ratings", {}), **{"~avg~": avg}))
    rows = []
    for t in teams:
        mine = [g for g in games if t in (g["home"], g["away"])]
        exp = 0.0
        for g in mine:
            home = g["home"] == t
            opp = g["away"] if home else g["home"]
            exp += (engine.win_prob(m, "~avg~", opp, g["neutral"]) if home else
                    1 - engine.win_prob(m, opp, "~avg~", g["neutral"]))
        twice = sorted({(g["away"] if g["home"] == t else g["home"]) for g in mine
                        if sum(1 for x in mine if {x["home"], x["away"]} ==
                               {g["home"], g["away"]}) > 1})
        rows.append((exp, t, len(mine), twice))
    rows.sort()
    draw = "".join(
        f"<tr><td class=posc>{i}</td><td class=teamcell>{name(t, d['teams'], 18)}</td>"
        f"<td>{exp:.1f}</td><td class=dim>{', '.join(esc(x) for x in twice)}</td></tr>"
        for i, (exp, t, n, twice) in enumerate(rows, 1))
    return f"""<div class=card><h2>Who plays whom</h2>
  <div class=tablewrap><table class="stbl bbmatrix"><thead><tr><th></th>{head}</tr></thead>
  <tbody>{body}</tbody></table></div>
  <p class=note>Read across: H is a home game, A a road game, and a W or L
  once it is played. Eighteen games against fifteen opponents means three
  are played twice, shaded.</p>
</div>
<div class=card><h2>The hardest draw</h2>
  <div class=tablewrap><table class=stbl><thead><tr><th></th><th>Team</th>
  <th>Wins for an average team</th><th>Played twice</th></tr></thead>
  <tbody>{draw}</tbody></table></div>
  <p class=note>How many of its eighteen games an exactly average Big 12 team
  would expect to win against each schedule, with the home and road games
  as drawn. Fewer is harder. The ratings are the ones The Race uses.</p>
</div>"""


# --- the empire ------------------------------------------------------------------

def empire_body(d, football_teams):
    """The Big 12 imperialism map, on basketball results. The football
    page's code (tiebreaker/empire.py and build.empire_page) does the work;
    this hands it basketball's games in football's shape, and points its
    team links at the basketball pages.

    Territories are the football map's: every county to the nearest Big 12
    campus, measured to the football stadium, which sits on the same campus
    as the arena. Conference games and the Big 12 tournament both count,
    the tournament final standing in for football's championship game."""
    games = [g for g in d["games"] if g["conf"] or g["tourney"]]
    starts = sorted(game_day(g) for g in games if game_day(g))
    first = starts[0] if starts else None
    shaped = []
    for g in games:
        day = game_day(g)
        week = ((day - first).days // 7 + 1) if first and day else 0
        shaped.append({
            "id": g["id"], "week": week, "start": g["start"],
            "home": g["home"], "away": g["away"],
            "home_points": g["hp"], "away_points": g["ap"],
            "completed": g["final"], "neutral_site": g["neutral"],
            "venue_id": None,
            "ccg": bool(g["tourney"] and "final" in (g.get("notes") or "").lower()
                        and "semi" not in (g.get("notes") or "").lower()
                        and "quarter" not in (g.get("notes") or "").lower()),
        })
    href_was = tb.team_href
    tb.team_href = lambda t: (f"team/{tb.team_slug(t)}" if t in d["teams"] else None)
    try:
        # The football schedule supplies the stadiums the territories are
        # measured from.
        fb_games = json.load(open(os.path.join(ROOT, "tiebreaker", "data",
                                               f"games_{tb_season()}.json")))
        venues = json.load(open(os.path.join(ROOT, "tiebreaker", "data",
                                             "venues.json")))
        stadiums = empire_mod.home_stadiums(fb_games, set(football_teams), venues)
        key = tuple(sorted(stadiums.items()))
        if key not in tb._EMPIRE_MAP:
            tb._EMPIRE_MAP[key] = empire_mod.territories(stadiums)
        tb.empire_map = lambda games_, teams_: tb._EMPIRE_MAP[key]
        html = tb.empire_page(d["season"], shaped, football_teams)
    finally:
        tb.team_href = href_was
    return html.replace("championship game", "Big 12 tournament final")


def tb_season():
    """The football season whose schedule places the stadiums: the one in
    progress, or the latest on disk."""
    import glob
    ys = sorted(int(os.path.basename(p)[6:10]) for p in
                glob.glob(os.path.join(ROOT, "tiebreaker", "data", "games_20*.json")))
    return ys[-1]


# --- the rules --------------------------------------------------------------

def rules_body(checks):
    rows = "".join(
        f"<tr><td>{esc(c['label'])}</td><td>{c['matched']} of {c['of']}</td>"
        f"<td class=dim>{esc(c['note'])}</td></tr>" for c in checks)
    return f"""<div class=card><h2>How the Big 12 seeds its tournament</h2>
  <p>All sixteen teams play in the conference tournament in Kansas City, and
  every seed matters: 1 to 4 skip two rounds, 5 to 8 skip one. So the Big 12
  breaks every tie in the standings, in this order.</p>
  <h3>Two teams tied</h3>
  <ol class=steps>
    <li>Head-to-head.</li>
    <li>Record against the team in the highest position in the standings,
    then the next, and so on down, until one of the two does better.</li>
    <li>Road record in conference games.</li>
    <li>Road record against the highest-placed team, then down the
    standings.</li>
    <li>A draw, held in public.</li>
  </ol>
  <h3>Three or more tied</h3>
  <ol class=steps>
    <li>A mini round-robin: each team's record against the others in the tie.
    Teams still level after it go through it again among themselves, and
    two left go to the two-team steps.</li>
    <li>Record against the highest-placed team and down, dropping the teams
    that fall behind.</li>
    <li>Conference road record.</li>
    <li>Conference road record against the highest-placed team and down.</li>
    <li>A draw.</li>
  </ol>
  <p>Whenever a step is comparing against another tied group further down
  the standings, that group counts as one opponent: a team's record against
  all of them together. The one exception is a two-team group that
  head-to-head has already split, which counts seed by seed.</p>
  <p>The regular-season title is never tiebroken. Teams level at the top are
  co-champions.</p>
  <p class=note>Source: the Big 12's
  <a href="https://big12sports.com/sports/2011/6/22/1520896.aspx">Tournament
  Seeding and Tiebreaker Procedures</a>, as of the 2023-24 season, the most
  recent version the conference has published.</p>
</div>
<div class=card><h2>Three things the document leaves open</h2>
  <ul class=steps>
    <li><b>A team that has not played the opponent being compared against.</b>
    The procedure says 2&ndash;0 is no better than 1&ndash;0 or 0&ndash;0. This
    site reads that as: a comparison counts only when every team in it has
    played that opponent, and otherwise that step is skipped. Over a full
    season every pair has met, so it matters only while the season is under
    way.</li>
    <li><b>The tied teams' own group.</b> When walking down the standings,
    the group the tie came from is skipped; a team is not compared against
    itself.</li>
    <li><b>After one team comes out of a three-way tie.</b> It takes the top
    seed of the group, and the others start the procedure again among
    themselves, from the mini round-robin.</li>
  </ul>
</div>
<div class=card><h2>Checked against the record</h2>
  <div class=tablewrap><table class=stbl><thead><tr><th>Season</th>
  <th>Seeds matched</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>
  <p class=note>The site's engine run on each finished season's results,
  against the seeds the conference actually used. The same engine runs on
  this season's results as they come in.</p>
</div>"""


def history_checks():
    """This engine on the seasons it can be checked against: the official
    seeds, recorded in data/official_seeds.json with their source, against
    what the engine makes of the same results."""
    path = os.path.join(HERE, "data", "official_seeds.json")
    if not os.path.exists(path):
        return []
    official = json.load(open(path))
    out = []
    for s in sorted(official["seasons"], key=int):
        rec = official["seasons"][s]
        d = season_mod.load(int(s))
        st = engine.standings(d["games"], d["teams"])
        got = [r["team"] for r in st["rows"]]
        want = rec["seeds"]
        matched = sum(1 for a, b in zip(got, want) if a == b)
        ties = sum(1 for r in st["rows"] if r["tied"])
        out.append({"label": season_mod.label(int(s)), "matched": matched,
                    "of": len(want),
                    "note": f"{ties} of the sixteen were in ties"})
    return out


# --- writing ----------------------------------------------------------------

CSS = open(os.path.join(HERE, "section.css")).read() if os.path.exists(
    os.path.join(HERE, "section.css")) else ""


def page(title, active, body, d, fname, desc, up="", head="",
         section=SECTION):
    """One page. `body` is a function, called once the asset base is set:
    the marks and links inside it are written relative to where the page
    sits, and a body built before that points at /basketball/logos/."""
    base_was = tb.BASE
    tb.BASE = up + CTX["deep"] + "../season/"
    here = URL if not CTX["deep"] else f"{URL}{d['season']}/"
    try:
        html = tb.build_subpage(
            title, active, body(),
            d["season"], "", canon=here + fname.replace("index.html", ""),
            desc=desc, head=tb.LOCAL_TIME_JS + f"<style>{CSS}</style>" + head,
            section=section,
            page=fname, up=up)
    finally:
        tb.BASE = base_was
    return html


def write(path, html):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(html)


def write_sitemap(built):
    """The section's pages for crawlers, every season's, listed in
    robots.txt beside the others. lastmod is the build's date, as the
    football sitemaps have it."""
    today = datetime.date.today().isoformat()
    pages = []
    for s, d in built.items():
        root = "" if s == CTX["live"] else f"{s}/"
        pages += [root + p for p in
                  ["", "race.html", "bracket.html", "schedule.html",
                   "matrix.html", "teams.html", "ratings.html", "lab.html",
                   "empire.html", "rules.html", "attendance.html"]]
        pages += [f"{root}team/{tb.team_slug(t)}" for t in sorted(d["teams"])]
        pages += [f"{root}game/{game_slug(g)}" for g in d["games"]]
    urls = "".join(f"  <url><loc>{URL}{p}</loc><lastmod>{today}</lastmod>"
                   f"</url>\n" for p in pages)
    write(os.path.join(SITE, "sitemap.xml"),
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          f"{urls}</urlset>\n")


# The 16-team Big 12 began in 2024-25. Earlier seasons were played under
# other memberships and, before 2023-24, other seeding procedures than
# site/engine.js implements, so the archive starts here.
ARCHIVE_FROM = 2025


def archive_seasons(live):
    """Finished seasons from ARCHIVE_FROM on whose games are on disk. Never
    fetched: a finished season cannot change, and these are in the repo."""
    out = []
    for s in range(live - 1, ARCHIVE_FROM - 1, -1):
        p = season_mod.fetch.raw_path("games", {"conference": season_mod.CONF,
                                                "season": s})
        if os.path.exists(p):
            out.append(s)
    return out


def build_season(season, out, fetching=False, as_of=None, deep=""):
    """Every page of one season into `out`. `deep` is "../" for an archived
    season in its own directory under the section."""
    d = season_mod.load(season, refresh=fetching)
    # as_of builds a past season as it stood that morning (UTC): every game
    # from then on is unplayed. For previews of the late-season pages out of
    # season; never used by the deploy.
    if as_of:
        d["games"] = [dict(g, final=False, hp=None, ap=None, status="scheduled")
                      if (g["start"] or "") >= as_of else g for g in d["games"]]
    CTX["season"], CTX["deep"] = season, deep
    yr = season_mod.label(season)
    for k in (SECTION, SCHED, ATT):
        # The shell formats {year} into this; the season's label is wanted,
        # not CBBD's number for it.
        tb.SECTIONS[k]["past"] = tb.SECTIONS[k]["past_text"].replace("{year}", yr)
    st = engine.standings(d["games"], d["teams"])
    # A finished season's fit is read, never made: refitting one would cost
    # five calls to learn what cannot have changed.
    model = (ratings_mod.ratings(season, refresh=fetching) if not deep else
             ratings_mod.load(season) or {"ratings": {}, "hfa": 3.0, "sigma": 11.0})
    sim = engine.simulate(d["games"], d["teams"], model)
    cert = engine.certain(d["games"], d["teams"])
    # The last stretch, proved: None until few enough games are left.
    ex = engine.exact(d["games"], d["teams"])
    football_teams = json.load(open(os.path.join(ROOT, "tiebreaker", "data",
                                                 "teams.json")))
    write(os.path.join(out, "index.html"), page(
        "The Standings", "standings", lambda: standings_body(d, st), d, "index.html",
        f"Big 12 men's basketball standings for {yr}, seeded for the "
        f"conference tournament with the Big 12's own tiebreakers."))
    write(os.path.join(out, "race.html"), page(
        "The Race", "race", lambda: race_body(d, st, sim, cert, model, ex), d,
        "race.html",
        f"Every Big 12 team's chances in {yr}: a share of the title, the top "
        f"seed, a double bye, a bye and the tournament, simulated."))
    write(os.path.join(out, "bracket.html"), page(
        "The Bracket", "bracket", lambda: bracket_body(d, st, sim, model), d,
        "bracket.html",
        f"The {yr} Big 12 tournament bracket as it stands, with each team's "
        f"chance of reaching every round in Kansas City."))
    write(os.path.join(out, "matrix.html"), page(
        "The Matrix", "matrix", lambda: matrix_body(d, model), d,
        "matrix.html",
        f"Who plays whom in the {yr} Big 12, who meets twice, and which "
        f"eighteen-game draw is hardest.", section=SCHED))
    write(os.path.join(out, "empire.html"), page(
        "The Empire", "empire", lambda: empire_body(d, football_teams), d,
        "empire.html",
        f"The {yr} Big 12 basketball imperialism map: every county starts "
        f"with its nearest Big 12 school, and every conference win takes "
        f"everything the loser holds.",
        head=f'<script defer src="{deep}../season/{tb.asset_v("empire.js")}"></script>'))
    write(os.path.join(out, "schedule.html"), page(
        "The Schedule", "schedule", lambda: schedule_body(d), d, "schedule.html",
        f"Every {yr} Big 12 men's basketball game: tip times, TV and lines.",
        section=SCHED))
    write(os.path.join(out, "ratings.html"), page(
        "The Ratings", "ratings", lambda: ratings_body(d, st, model), d,
        "ratings.html",
        f"Every Division I men's basketball team rated in points for {yr}, "
        f"the Big 12 first: the numbers this section's odds come from."))
    write(os.path.join(out, "attendance.html"), page(
        "The Crowds", "attendance", lambda: attendance_body(d), d,
        "attendance.html",
        f"Big 12 men's basketball attendance for {yr}: every arena's average "
        f"crowd against its real capacity, sellouts and the biggest crowds.",
        section=ATT))
    site_dir = os.path.join(HERE, "site")
    write(os.path.join(out, "lab.html"), page(
        "The Lab", "lab", lambda: lab_body(d, model), d, "lab.html",
        f"Pick the rest of the {yr} Big 12 men's basketball season and see "
        f"every team's seed, bye and title chances recomputed with the "
        f"conference's own tiebreakers.",
        head=(f'<script defer src="{deep}{tb.asset_v("engine.js", site_dir)}"></script>'
              f'<script defer src="{deep}{tb.asset_v("lab.js", site_dir)}"></script>')))
    write(os.path.join(out, "teams.html"), page(
        "The Teams", "teams", lambda: teams_body(d, st), d, "teams.html",
        f"The sixteen Big 12 men's basketball teams in {yr}."))
    for t in d["teams"]:
        write(os.path.join(out, "team", tb.team_slug(t)), page(
            t, "teams", lambda t=t: team_body(d, st, t), d, f"team/{tb.team_slug(t)}",
            f"{t} men's basketball in {yr}: record, Big 12 seed, schedule, "
            f"tip times, TV and lines.", up="../"))
    # One page per game a Big 12 team plays. Rebuilt whole each time: a
    # game whose venue or sides change gets a new slug, and the old page
    # must not linger.
    shutil.rmtree(os.path.join(out, "game"), ignore_errors=True)
    for g in d["games"]:
        title = f"{g['away']} {joiner(g)} {g['home']}"
        day = game_day(g)
        when = f" on {long_date(day)}" if day else ""
        write(os.path.join(out, "game", game_slug(g)), page(
            title, "schedule", lambda g=g: game_body(d, st, sim, model, g), d,
            f"game/{game_slug(g)}",
            (f"{title}{when}: " + ("the final score and both teams' records."
                                   if g["final"] else
                                   "tip time, TV, the line, and what the result "
                                   "is worth to each team's Big 12 seed.")),
            up="../", section=SCHED))
    write(os.path.join(out, "rules.html"), page(
        "The Rules", "rules", lambda: rules_body(history_checks()), d, "rules.html",
        "How the Big 12 seeds its men's basketball tournament, step by step, "
        "and how this site applies it."))
    print(f"built basketball {yr}: {len(d['games'])} games, "
          f"{sum(g['final'] for g in d['games'])} final -> {out}")
    return d


def main():
    """The live season at the section root and every archived season in its
    own directory beside it, or with --season N --out DIR (and --as-of
    DATE) one season on its own, for previews. Only the live season fetches."""
    args = sys.argv
    opt = lambda k: args[args.index(k) + 1] if k in args else None
    live = season_mod.current_season()
    if opt("--season"):
        CTX.update(live=int(opt("--season")), archive=[])
        tb.LIVE_YEAR = CTX["live"]
        build_season(CTX["live"], opt("--out") or SITE, as_of=opt("--as-of"))
        return
    CTX.update(live=live, archive=archive_seasons(live))
    tb.LIVE_YEAR = live
    built = {live: build_season(live, SITE, fetching="--fetch" in args)}
    for s in CTX["archive"]:
        built[s] = build_season(s, os.path.join(SITE, str(s)), deep="../")
    write_sitemap(built)


if __name__ == "__main__":
    main()

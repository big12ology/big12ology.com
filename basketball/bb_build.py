#!/usr/bin/env python3
"""Big 12 men's basketball: the /basketball/ section.

    python3 basketball/bb_build.py            # build from what is on disk
    python3 basketball/bb_build.py --fetch    # refresh first, if anything is due

A sibling of the football sections, not a part of them. It borrows the page
shell (masthead, section nav, footer, stylesheet and marks) from
tiebreaker/build.py so the site still reads as one thing, and keeps its own
data, rules and pages. The rules are site/engine.js; the data is season.py.

Pages land in basketball/site/ and tools/assemble.sh serves them at
/basketball/. Shared assets are reached at ../tiebreaker/, the way the
/schedule/ section reaches them.
"""
import datetime
import json
import os
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

SITE = os.path.join(HERE, "site")
SECTION = "basketball"

# THE SECTION REGISTERS ITSELF with the shared shell, here and only in this
# process, rather than living in tiebreaker/build.py: the football build
# never draws a basketball page, and keeping the registration beside the
# pages it serves keeps the two sections' files apart.
tb.SECTIONS[SECTION] = {
    "title": "Big 12 Basketball",
    "live": ("Unofficial fan tool. The official Big 12 tournament seeding and "
             "tiebreaker procedure, applied to every result as it comes in."),
    "past": ("The {year} season as it finished, with the official seeding "
             "procedure applied."),
}
NAV = [("standings", "./", "The Standings"),
       ("schedule", "schedule.html", "The Schedule"),
       ("teams", "teams.html", "The Teams"),
       ("rules", "rules.html", "The Rules")]
_nav_for = tb.nav_for
tb.nav_for = lambda section: NAV if section == SECTION else _nav_for(section)
# No year pills yet: they link to football seasons' directories, and the
# basketball archive keeps its own (Phase 4).
_tracker_top = tb.tracker_top


def _tracker_top_bb(year, active, matchcard="", section="tiebreaker", page="",
                    up="", yearpills=True, subnavon=True):
    return _tracker_top(year, active, matchcard, section, page, up,
                        yearpills=yearpills and section != SECTION,
                        subnavon=subnavon)


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

def game_row(g, d, up=""):
    teams = d["teams"]
    tv = d["media"].get(g["id"]) or []
    bits = []
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
            f"<span class=bbmeta>{' · '.join(b for b in bits if b)}</span></li>")


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
  or regional window CollegeBasketballData lists.</p>
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
            f"{name(opp, teams, 18, '../')}</td><td>{res}</td></tr>")
    return head + (f"<div class=card><h2>Schedule</h2><div class=tablewrap>"
                   f"<table class=stbl><tbody>{''.join(rows)}</tbody></table>"
                   f"</div><p class=note>Big 12 games are shaded. Times are the "
                   f"arena's.</p></div>")


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


def page(title, active, body, d, fname, desc, up="", head=""):
    """One page. `body` is a function, called once the asset base is set:
    the marks and links inside it are written relative to where the page
    sits, and a body built before that points at /basketball/logos/."""
    base_was = tb.BASE
    tb.BASE = up + "../tiebreaker/"
    try:
        html = tb.build_subpage(
            title, active, body(),
            d["season"], "", canon=URL + fname.replace("index.html", ""),
            desc=desc, head=tb.LOCAL_TIME_JS + f"<style>{CSS}</style>" + head,
            section=SECTION,
            page=fname, up=up)
    finally:
        tb.BASE = base_was
    return html


def write(path, html):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(html)


def write_sitemap(d):
    """The section's pages for crawlers, listed in robots.txt beside the
    others. lastmod is the build's date, as the football sitemaps have it."""
    today = datetime.date.today().isoformat()
    pages = ["", "schedule.html", "teams.html", "rules.html"] + [
        f"team/{tb.team_slug(t)}" for t in sorted(d["teams"])]
    urls = "".join(f"  <url><loc>{URL}{p}</loc><lastmod>{today}</lastmod>"
                   f"</url>\n" for p in pages)
    write(os.path.join(SITE, "sitemap.xml"),
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          f"{urls}</urlset>\n")


def main():
    fetching = "--fetch" in sys.argv
    # --season N builds a finished season into --out, for previews and, in
    # time, the archive. It never fetches.
    season = season_mod.current_season()
    out = SITE
    for i, a in enumerate(sys.argv):
        if a == "--season":
            season, fetching = int(sys.argv[i + 1]), False
        if a == "--out":
            out = sys.argv[i + 1]
    d = season_mod.load(season, refresh=fetching)
    tb.LIVE_YEAR = season
    st = engine.standings(d["games"], d["teams"])
    yr = season_mod.label(season)
    write(os.path.join(out, "index.html"), page(
        "The Standings", "standings", lambda: standings_body(d, st), d, "index.html",
        f"Big 12 men's basketball standings for {yr}, seeded for the "
        f"conference tournament with the Big 12's own tiebreakers."))
    write(os.path.join(out, "schedule.html"), page(
        "The Schedule", "schedule", lambda: schedule_body(d), d, "schedule.html",
        f"Every {yr} Big 12 men's basketball game: tip times, TV and lines."))
    write(os.path.join(out, "teams.html"), page(
        "The Teams", "teams", lambda: teams_body(d, st), d, "teams.html",
        f"The sixteen Big 12 men's basketball teams in {yr}."))
    for t in d["teams"]:
        write(os.path.join(out, "team", tb.team_slug(t)), page(
            t, "teams", lambda t=t: team_body(d, st, t), d, f"team/{tb.team_slug(t)}",
            f"{t} men's basketball in {yr}: record, Big 12 seed, schedule, "
            f"tip times, TV and lines.", up="../"))
    write(os.path.join(out, "rules.html"), page(
        "The Rules", "rules", lambda: rules_body(history_checks()), d, "rules.html",
        "How the Big 12 seeds its men's basketball tournament, step by step, "
        "and how this site applies it."))
    if out == SITE:
        write_sitemap(d)
    print(f"built basketball {yr}: {len(d['games'])} games, "
          f"{sum(g['final'] for g in d['games'])} final -> {out}")


if __name__ == "__main__":
    main()

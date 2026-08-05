#!/usr/bin/env python3
"""Build the Big 12 tiebreaker site.

    python3 build.py            # current season, uses cached data
    python3 build.py 2024       # specific season
    python3 build.py --fetch    # refetch results first (one API call)

Writes site/index.html — fully self-contained, no external requests.
"""
import datetime
import html
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
    p = os.path.join(HERE, "data", f"ratings_{year}.json")
    return json.load(open(p)) if os.path.exists(p) else {"systems": {}}


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


def logo_img(team, size=20):
    k = TEAM_KEY.get(team)
    if not k:
        return ""
    ext = "png" if k == "byu" else "svg"
    return (f"<img class=mark src='logos/{k}.{ext}' alt='' "
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
    return f"""<header class=b12-head>
  <div class=hwrap>
    <img src=logos/big12.svg alt="">
    <div>
      <h1>Big 12 Tiebreaker Tracker <span class=yr>· {year}</span></h1>
      <p>Unofficial fan tool. Applies the official Big 12 tiebreaking
      procedures to live results after every game.</p>
    </div>
  </div>
</header>
{subnav(active)}
<main>
{matchcard}"""


SUBNAV_LINKS = [("tracker", "./", "Tracker"), ("race", "race.html", "The Race"),
                ("schedule", "schedule.html", "Schedule"),
                ("brief", "brief.html", "The Brief"),
                ("history", "history.html", "Tie history"),
                ("how", "how.html", "How it works")]


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
        date = (g["start"] or "")[5:10].replace("-", "/")
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

    def abbr(t):
        return (teams.get(t) or {}).get("abbr") or t[:4].upper()

    head = "".join(
        f"<th title='{esc(t)}'>{esc(abbr(t))}</th>" for t in order)
    body = []
    for a in order:
        cells = []
        for b in order:
            if a == b:
                cells.append("<td class=selfcell>—</td>")
                continue
            g = meet.get(frozenset((a, b)))
            if g is None:
                cells.append("<td class=nomeet>·</td>")
                continue
            date = (g["start"] or "")[5:10].replace("-", "/")
            if g["completed"] and g["home_points"] is not None:
                mine = g["home_points"] if g["home"] == a else g["away_points"]
                theirs = g["away_points"] if g["home"] == a else g["home_points"]
                won = mine > theirs
                color = winpct_color(1.0 if won else 0.0)
                cells.append(
                    f"<td style='color:{color}' title='{esc(a)} "
                    f"{'def.' if won else 'lost to'} {esc(b)} "
                    f"{mine}–{theirs} ({date})'>"
                    f"{'W' if won else 'L'} {mine}–{theirs}</td>")
            else:
                at = "vs" if g["home"] == a else "at"
                cells.append(
                    f"<td class=dim title='{esc(a)} {at} {esc(b)}, "
                    f"{date}'>wk {g['week']}</td>")
        body.append(f"<tr><td class=teamcell>{logo_img(a, 14)}"
                    f"{esc(a)}</td>{''.join(cells)}</tr>")
    return ("<div class=card id=h2hcard><h2>Head-to-head grid</h2>"
            "<div class=tablescroll><table class=h2h><thead><tr><th></th>"
            + head + "</tr></thead><tbody>" + "".join(body)
            + "</tbody></table></div>"
            "<p class=note>Every conference meeting this season, read "
            "across: the row team's result or the scheduled week. A dot "
            "means the schedule never pairs them — in a nine-game draw "
            "that's more than a third of the grid, which is why the "
            "tiebreakers exist.</p></div>")


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
.badge.ok { background:#15803d26; color:#15803d }
.badge.warn { background:#b4530926; color:var(--warn) }
:root { --bg:#f6f4ef; --panel:#fff; --ink:#1a1c20; --dim:#6b7280;
  --line:#e2ddd2; --accent:#c8102e; --accent2:#003087; --warn:#b45309;
  --pctl:32%; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14161a; --panel:#1d2026; --ink:#e8e6e1; --dim:#9aa0aa;
    --line:#2e323a; --accent:#ff5a6e; --accent2:#7aa2ff; --warn:#fbbf24;
    --pctl:63%; } }
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
.tag.live { background:#15803d26; color:#15803d }
.tag.destiny { background:#b4530926; color:var(--warn) }
.scen { margin:5px 0 2px; padding-left:20px; font-size:13px; color:var(--dim) }
.elim { font-size:13px } ul.games { list-style:none; padding:0; margin:0 }
ul.games li { padding:5px 0; border-bottom:1px solid var(--line);
  font-size:14px } .ccgtag { color:var(--accent); font-weight:700;
  font-size:11px; text-transform:uppercase }
@media (prefers-color-scheme: dark) { .tag.live { color:#4ade80;
  background:#4ade8024 } }
"""


def build_brief(year, games, overrides, systems, sims, matchcard):
    """The Brief: auto-written weekly summary on the standard tracker top."""
    stand_rows = tb.standings(games, overrides)
    race = clinch_card(games, overrides, systems, stand_rows, sims)
    lev = leverage_card(games, sims) if sims else ""
    done = sorted((g for g in games if g["completed"]
                   and g["home_points"] is not None),
                  key=lambda g: g["start"] or "")
    finals = ""
    if done:
        latest = done[-1]["start"][:10]
        cutoff = (datetime.date.fromisoformat(latest)
                  - datetime.timedelta(days=7)).isoformat()
        recent = [g for g in done if (g["start"] or "")[:10] > cutoff]
        finals = ("<div class=card><h2>Finals, last seven days</h2>"
                  "<ul class=games>"
                  + "".join(game_row(g) for g in recent[::-1][:20])
                  + "</ul></div>")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%B %d, %Y")
    body = (f"<p style='color:var(--dim);font-size:13px;text-align:center;"
            f"margin:-4px 0 0'>The Brief · auto-written {esc(stamp)}</p>"
            + race + lev + finals
            + "<div class=card><h2>Take it with you</h2>"
              "<p style='font-size:14px'><a href=feed.xml>RSS feed</a> · "
              "<a href=data.json>data.json</a> · "
              "<a href=standings.csv>standings.csv</a> · "
              "<a href=how.html>how the tiebreakers work</a></p></div>")
    return build_subpage("The Brief", "brief", body, year, matchcard)


SUBPAGE_EXTRA_CSS = """
table { border-collapse:collapse; width:100%; font-size:14px }
th, td { text-align:left; padding:6px 9px; border-bottom:1px solid
  var(--line); font-variant-numeric:tabular-nums }
th { font-size:11px; text-transform:uppercase; letter-spacing:.05em;
  color:var(--dim) }
thead tr th { border-bottom:2px solid var(--line) }
.teamcell { white-space:nowrap }
h3.wkhead { font-size:13px; text-transform:uppercase; letter-spacing:.05em;
  color:var(--dim); margin:16px 0 4px }
.tablescroll { overflow-x:auto }
table.h2h { width:auto }
.h2h th, .h2h td { padding:3px 6px; font-size:11.5px; white-space:nowrap }
.selfcell { color:var(--line) }
.nomeet { color:var(--line); text-align:center }
"""


def build_subpage(title, active, body, year, matchcard):
    return f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{esc(title)} — Big 12 Tiebreaker Tracker</title>
<link rel=icon type=image/svg+xml href=favicon.svg>
<link rel=stylesheet href=brand.css>
<link rel=alternate type=application/rss+xml href=feed.xml>
<style>{BRIEF_CSS}{SUBPAGE_EXTRA_CSS}</style></head><body>
<nav class=b12-topbar><a class=b12-brand href="https://big12ology.com/">Big12<span>ology</span></a>
<a class=on href="https://big12ology.com/tiebreaker/">Tiebreaker</a><a href="https://big12ology.com/attendance/">Attendance</a></nav>
{tracker_top(year, active, matchcard)}
{body}
</main>
<footer class=b12-footer>A Big12ology project · not affiliated with the
Big 12 Conference · <a href=data.json>data.json</a> ·
<a href=standings.csv>standings.csv</a> ·
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
    return ctx["clinchcard"] + ctx["levcard"] + ctx["modelcard"]


def default_season(today=None):
    today = today or datetime.date.today()
    return today.year if today.month >= 6 else today.year - 1


def esc(s):
    return html.escape(str(s))


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
        when = (g["start"] or "")[:10]
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
    for r in rows:
        tg = r["tie_group"]
        if tg and tg not in tie_colors:
            tie_colors[tg] = len(tie_colors) % 4
        cls = f"tie{tie_colors[tg]}" if tg else ""
        mark = f"<sup>{list(tie_colors).index(tg) + 1}</sup>" if tg else ""
        p = tb.pct(r["conf_w"], r["conf_l"])
        c = team_color(teams, r["team"])
        body.append(
            f"<tr class='{cls}' data-rank={r['rank']} data-w={r['conf_w']} "
            f"data-l={r['conf_l']}>"
            f"<td>{r['rank']}</td>"
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
    table = (f"<div id=tablewrap{'' if rows else ' hidden'}>" + sorter +
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
            "logo": f"logos/{k}.{'png' if k == 'byu' else 'svg'}",
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
    }).replace("</", "<\\/")

    n_remaining = len([g for g in games
                       if not g["completed"] and not g.get("ccg")])
    model_opts = "".join(
        f"<option value='{esc(m['name'])}'>{esc(m['name'])}"
        f" ({esc(m['year'])})</option>" for m in models)
    whatif = "" if not n_remaining else WHATIF_CARD.format(
        n=n_remaining, model_opts=model_opts)

    standcard = STAND_CARD.format(
        played=len(reg_played), total=len(reg), table=table, stories=stories)

    page = TEMPLATE.format(
        year=year,
        top=tracker_top(year, "tracker", card),
        whatif=whatif,
        standcard=standcard,
        payload=payload,
        updated=now.strftime("%Y-%m-%d %H:%M UTC"),
    )
    ctx = {
        "clinchcard": clinch_card(games, overrides, systems, rows, sims),
        "levcard": leverage_card(games, sims) if sims else "",
        "soscard": sos_card(games, systems),
        "modelcard": scorecard_card(games, systems, closing_lines),
        "h2hcard": h2h_card(games, teams, rows),
        "matchcard": card,
        "sims": sims,
    }
    return page, ctx


STAND_CARD = """<div class="card standcard">
  <h2>Conference standings · {played} of {total} games played
  <span id=w-chip class=wchip hidden>what-if</span></h2>
  <progress max={total} value={played}></progress>
  {table}
  <div style="margin-top:14px" id=stories>{stories}</div>
</div>"""


WHATIF_CARD = """<div class=card id=whatif>
  <h2>What if&hellip; <span class=dim style="text-transform:none">pick winners
  for the {n} remaining games, conference and non-conference</span></h2>
  <div class=wcontrols>
    <label class=dim for=w-model>Model</label>
    <select id=w-model class=wbtn>{model_opts}</select>
    <button id=w-fav class=wbtn>Use favorites for all</button>
    <button id=w-clear class=wbtn>Clear picks</button>
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
<title>Big 12 Tiebreaker Tracker — {year}</title>
<meta name=description content="The official Big 12 tiebreaking procedures applied to live results after every game — projected championship matchup, tie narratives, and a what-if simulator.">
<link rel=canonical href="https://big12ology.com/tiebreaker/">
<link rel=icon type=image/svg+xml href=favicon.svg>
<meta property=og:type content=website>
<meta property=og:site_name content=Big12ology>
<meta property=og:title content="Big 12 Tiebreaker Tracker — {year}">
<meta property=og:description content="The official Big 12 tiebreaking procedures applied to live results after every game — plus a what-if simulator with five rating models.">
<meta property=og:url content="https://big12ology.com/tiebreaker/">
<meta property=og:image content="https://big12ology.com/tiebreaker/og.png">
<meta property=og:image:width content=1200>
<meta property=og:image:height content=630>
<meta name=twitter:card content=summary_large_image>
<link rel=stylesheet href=brand.css>
<link rel=alternate type=application/rss+xml title="Big 12 Tiebreaker Tracker" href=feed.xml>
<style>
:root {{
  --bg: #f6f4ef; --panel: #ffffff; --ink: #1a1c20; --dim: #6b7280;
  --line: #e2ddd2; --accent: #c8102e; --accent2: #003087;
  --ok: #15803d; --warn: #b45309;
  --tie0: #fff3f4; --tie1: #eef4ff; --tie2: #f0fdf4; --tie3: #fefce8;
  --pctl: 32%;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #14161a; --panel: #1d2026; --ink: #e8e6e1; --dim: #9aa0aa;
    --line: #2e323a; --accent: #ff5a6e; --accent2: #7aa2ff;
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
.lchip.win {{ background: color-mix(in srgb, var(--ok, #15803d) 15%,
  transparent); color: #15803d; }}
.lchip.lose {{ background: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--accent); }}
.lchip.none {{ background: color-mix(in srgb, var(--dim) 14%, transparent);
  color: var(--dim); }}
.lchip.skip {{ background: none; border: 1px solid var(--line);
  color: var(--dim); font-weight: 500; }}
@media (prefers-color-scheme: dark) {{
  .lchip.win {{ color: #4ade80; background: color-mix(in srgb, #4ade80 14%,
    transparent); }}
}}
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
  .duo {{ grid-template-columns: 1fr; }}
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
.wgame {{ display: flex; align-items: center; gap: 8px; padding: 5px 0;
  border-bottom: 1px solid var(--line); flex-wrap: wrap; }}
.wgame:last-child {{ border-bottom: none; }}
.wgame .at {{ color: var(--dim); font-size: 12px; }}
.wgame .wdate {{ color: var(--dim); font-size: 12px; margin-left: auto; }}
.nctag {{ color: var(--dim); font-size: 10.5px; border: 1px solid var(--line);
  border-radius: 20px; padding: 1px 7px; text-transform: uppercase;
  letter-spacing: .04em; }}
.tag {{ font-size: 11px; border-radius: 20px; padding: 2px 9px;
  font-weight: 700; letter-spacing: .03em; white-space: nowrap; }}
.tag.live {{ background: color-mix(in srgb, var(--ok, #15803d) 15%,
  transparent); color: #15803d; }}
.tag.out {{ background: color-mix(in srgb, var(--dim) 14%, transparent);
  color: var(--dim); }}
.tag.alive {{ background: color-mix(in srgb, var(--accent2) 14%,
  transparent); color: var(--accent2); }}
.tag.destiny {{ background: color-mix(in srgb, var(--warn) 15%, transparent);
  color: var(--warn); }}
@media (prefers-color-scheme: dark) {{
  .tag.live {{ color: #4ade80; background: color-mix(in srgb, #4ade80 14%,
    transparent); }}
}}
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
@media (max-width: 700px) {{ .pick {{ min-width: 120px; }} }}
</style>
</head>
<body>
<nav class=b12-topbar>
  <a class=b12-brand href="https://big12ology.com/">Big12<span>ology</span></a>
  <a class=on href="https://big12ology.com/tiebreaker/">Tiebreaker</a>
  <a href="https://big12ology.com/attendance/">Attendance</a>
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
  <a href=how.html>how the tiebreakers work</a>.</p>
</div>

<div class="card rules">
  <h2>The official tiebreaking procedure</h2>
  <p style="font-size:14px">Two teams tied for first who met during the season
  both make the championship game — the head-to-head winner is the #1 seed.
  Otherwise ties for the two berths break in order:</p>
  <ol>
    <li><b>Head-to-head</b> among the tied teams (for 3+ teams: win percentage
    in games among the tied group; a team that beat every other tied team is
    seeded even if the group didn't all play each other).</li>
    <li><b>Common opponents:</b> win percentage against all conference
    opponents common to the tied teams.</li>
    <li><b>Next highest placed common opponent</b>, proceeding down the
    standings — tied placement groups are compared as a collective group.</li>
    <li><b>Strength of conference schedule:</b> combined conference win
    percentage of each team's conference opponents.</li>
    <li><b>Total wins</b> in a 12-game season (at most one win over an FCS or
    lower-division team counts; NCAA-exempt extra games excluded).</li>
    <li><b>SportSource Analytics team Rating Score</b> after the final weekend
    of the regular season.</li>
    <li><b>Coin toss.</b></li>
  </ol>
  <p style="font-size:14px">In multi-team ties, once one team is seeded the
  remaining teams restart the procedure from the top; at two teams the
  two-team rules apply. Steps 6–7 use non-public inputs, so when a projection
  reaches them it is flagged until the conference's values are known.</p>
</div>

</div>
</div>
</main>
<script id=payload type=application/json>{payload}</script>
<script src=engine.js></script>
<script src=app.js></script>
<footer class=b12-footer>
  Results from <a href="https://collegefootballdata.com">collegefootballdata.com</a> ·
  procedure per the <a
  href="https://s3.amazonaws.com/big12sports.com/documents/2025/11/4/Big_12_Football_2024_Tiebreaker_Policy.pdf">official
  Big 12 tiebreaker policy</a> · marks via Wikimedia Commons (provenance in
  <a href="logos/SOURCES.json">SOURCES.json</a>) · last updated {updated}.<br>
  A Big12ology project · not affiliated with the Big 12 Conference; conference
  and team marks belong to their owners and appear for identification only.<br>
  <a href="https://github.com/big12ology">GitHub</a> ·
  <a href=feed.xml>RSS</a> ·
  <a href=brief.html>The Brief</a> ·
  <a href=history.html>Tie history</a> ·
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
<link rel=icon type=image/svg+xml href=favicon.svg>
<link rel=stylesheet href=brand.css>
<meta property=og:type content=article>
<meta property=og:site_name content=Big12ology>
<meta property=og:title content="How the Big 12 tiebreakers actually work">
<meta property=og:description content="The official procedure in plain English, plus the 2024 four-way tie worked step by step by the tracker's rules engine.">
<meta property=og:url content="https://big12ology.com/tiebreaker/how.html">
<meta property=og:image content="https://big12ology.com/tiebreaker/og.png">
<meta name=twitter:card content=summary_large_image>
<style>
:root {{
  --bg: #f6f4ef; --panel: #ffffff; --ink: #1a1c20; --dim: #6b7280;
  --line: #e2ddd2; --accent: #c8102e; --accent2: #003087;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #14161a; --panel: #1d2026; --ink: #e8e6e1; --dim: #9aa0aa;
    --line: #2e323a; --accent: #ff5a6e; --accent2: #7aa2ff;
  }}
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
.badge.ok {{ background: #15803d26; color: #15803d; }}
.badge.warn {{ background: #b4530926; color: #b45309; }}
.mark {{ vertical-align: -3px; margin-right: 7px; object-fit: contain; }}
.dim {{ color: var(--dim); }}
.note {{ color: var(--dim); font-size: 13px; }}


</style>
</head>
<body>
<nav class=b12-topbar>
  <a class=b12-brand href="https://big12ology.com/">Big12<span>ology</span></a>
  <a class=on href="https://big12ology.com/tiebreaker/">Tiebreaker</a>
  <a href="https://big12ology.com/attendance/">Attendance</a>
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
  <a href=feed.xml>RSS</a> ·
  <a href=brief.html>The Brief</a> ·
  <a href=history.html>Tie history</a> ·
  <a href=data.json>Data</a> ·
  <a href="https://big12ology.com/privacy">Privacy</a> ·
  <a href="mailto:dept@big12ology.com">dept@big12ology.com</a>
</footer>
<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "355e765d921e4b36ad2bf78d509eae6c"}}'></script>
</body>
</html>
"""


def build_explainer(year, matchcard):
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
    out = os.path.join(SITE, "how.html")
    with open(out, "w") as f:
        f.write(EXPLAINER.format(worked_2024=worked,
                                 top=tracker_top(year, "how", matchcard)))
    print(f"built {out}")


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    year = int(argv[0]) if argv else default_season()
    if "--fetch" in sys.argv:
        games = fetcher.fetch_season(year)
        fetcher.fetch_ratings(year)
        fetcher.fetch_lines(year)
        if not os.path.exists(os.path.join(HERE, "data", "teams.json")):
            fetcher.fetch_teams()
    else:
        path = os.path.join(HERE, "data", f"games_{year}.json")
        if not os.path.exists(path):
            games = fetcher.fetch_season(year)
        else:
            games = json.load(open(path))
    os.makedirs(SITE, exist_ok=True)
    out = os.path.join(SITE, "index.html")
    page, ctx = render(year, games)
    with open(out, "w") as f:
        f.write(page)
    print(f"built {out} for {year}")
    with open(os.path.join(SITE, "race.html"), "w") as f:
        f.write(build_subpage("The Race", "race", build_race_page(ctx),
                              year, ctx["matchcard"]))
    with open(os.path.join(SITE, "schedule.html"), "w") as f:
        f.write(build_subpage("Schedule", "schedule",
                              build_schedule_page(games, ctx),
                              year, ctx["matchcard"]))
    # the Brief and Tie history share the same standard top
    hist_frag = os.path.join(HERE, "history", "history_body.html")
    if os.path.exists(hist_frag):
        with open(os.path.join(SITE, "history.html"), "w") as f:
            f.write(build_subpage("Tie history", "history",
                                  open(hist_frag).read(),
                                  year, ctx["matchcard"]))
    print("built race.html, schedule.html, history.html")
    build_explainer(year, ctx["matchcard"])
    overrides = tb.load_overrides()
    systems = load_ratings(year).get("systems", {})
    fp = os.path.join(SITE, "feed.xml")
    with open(fp, "w") as f:
        f.write(feed_mod.build_feed(games, year, systems, overrides))
    print(f"built {fp}")

    # downloads + the Brief (same deterministic sims as the page)
    track, _wk = next_conf_week_ids(games)
    sims = (odds_mod.simulate(games, systems, overrides, track=track)
            if systems else {})
    rows = tb.standings(games, overrides)
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
    with open(os.path.join(SITE, "data.json"), "w") as f:
        json.dump(data, f, indent=1)
    with open(os.path.join(SITE, "standings.csv"), "w") as f:
        f.write("rank,team,conf_w,conf_l,nonconf_w,nonconf_l,"
                "overall_w,overall_l,p_ccg\n")
        for r in rows:
            p = (sims.get(r["team"], {}) or {}).get("p_ccg", "")
            f.write(f"{r['rank']},{r['team']},{r['conf_w']},{r['conf_l']},"
                    f"{r['nonconf_w']},{r['nonconf_l']},{r['overall_w']},"
                    f"{r['overall_l']},{p}\n")
    with open(os.path.join(SITE, "brief.html"), "w") as f:
        f.write(build_brief(year, games, overrides, systems, sims,
                            ctx["matchcard"]))
    print("built data.json, standings.csv, brief.html")


if __name__ == "__main__":
    main()

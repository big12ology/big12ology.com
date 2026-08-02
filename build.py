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

import fetch as fetcher
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


MODEL_ORDER = ["SP+", "Chain", "FPI", "Elo", "SRS"]


def favorites_for(games, systems):
    """{system: {game_id: {team, margin}}} for every unplayed conference game.
    Margin is converted to scoring points via the system's per_pt scale and
    includes its home-field bump."""
    out = {}
    for name, s in systems.items():
        r, hfa, per = s["ratings"], s["hfa"], s.get("per_pt", 1.0) or 1.0
        m = {}
        for g in games:
            if not g["conference_game"] or g.get("ccg") or g["completed"]:
                continue
            if g["home"] in r and g["away"] in r:
                d = r[g["home"]] - r[g["away"]] + hfa
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
    favorites = favorites_for(games, systems)
    models = [{"name": n, "year": systems[n].get("year")}
              for n in MODEL_ORDER if n in favorites]
    rows = tb.standings(games, overrides)
    ccg = tb.championship(games, overrides)
    conf = [g for g in games if (g["conference_game"] or g.get("ccg"))]
    reg = [g for g in conf if not g.get("ccg")]
    played = [g for g in conf if g["completed"] and g["home_points"] is not None]
    remaining = [g for g in conf if not g["completed"]]
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
            f"<td>{'—' if p is None else f'{p:.3f}'}</td>"
            f"<td>{r['overall_w']}–{r['overall_l']}</td></tr>")
    sorter = ("<div class=sorter>Sort: "
              "<button class='on' id=sort-pct>Win % (official)</button>"
              "<button id=sort-raw>Raw wins</button></div>")
    table = (f"<div id=tablewrap{'' if rows else ' hidden'}>" + sorter +
             "<table><thead><tr><th></th><th>Team</th><th>Conf</th>"
             "<th>Pct</th><th>Overall</th></tr></thead><tbody id=stand>"
             + "".join(body) + "</tbody></table></div>")

    # -- tiebreaker narratives ---------------------------------------------
    stories = []
    n = 0
    for r in rows:
        if r["log"] is not None:
            n += 1
            tie_names = r["tie_group"].replace("+", ", ")
            lines = "".join(f"<li>{esc(x)}</li>" for x in r["log"])
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

    n_remaining = len([g for g in reg if not g["completed"]])
    model_opts = "".join(
        f"<option value='{esc(m['name'])}'>{esc(m['name'])}"
        f" ({esc(m['year'])})</option>" for m in models)
    whatif = "" if not n_remaining else WHATIF_CARD.format(
        n=n_remaining, model_opts=model_opts)

    return TEMPLATE.format(
        year=year,
        card=card,
        whatif=whatif,
        table=table,
        stories=stories,
        results=results,
        upcoming=upcoming,
        played=len(reg_played),
        total=len(reg),
        payload=payload,
        updated=now.strftime("%Y-%m-%d %H:%M UTC"),
    )


WHATIF_CARD = """<div class=card id=whatif>
  <h2>What if&hellip; <span class=dim style="text-transform:none">pick winners
  for the {n} remaining conference games</span></h2>
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
<style>
:root {{
  --bg: #f6f4ef; --panel: #ffffff; --ink: #1a1c20; --dim: #6b7280;
  --line: #e2ddd2; --accent: #c8102e; --accent2: #003087;
  --ok: #15803d; --warn: #b45309;
  --tie0: #fff3f4; --tie1: #eef4ff; --tie2: #f0fdf4; --tie3: #fefce8;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #14161a; --panel: #1d2026; --ink: #e8e6e1; --dim: #9aa0aa;
    --line: #2e323a; --accent: #ff5a6e; --accent2: #7aa2ff;
    --ok: #4ade80; --warn: #fbbf24;
    --tie0: #2a1d20; --tie1: #1d2330; --tie2: #1d2a22; --tie3: #2a281d;
  }}
  .mark {{ background: #f0ede6; border-radius: 4px; padding: 2px; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
header {{ border-bottom: 4px solid var(--accent); padding: 28px 20px 18px;
  background: var(--panel); }}
header h1 {{ margin: 0; font-size: 26px; letter-spacing: -.02em; }}
header p {{ margin: 4px 0 0; color: var(--dim); font-size: 14px; }}
main {{ max-width: 880px; margin: 0 auto; padding: 20px; display: grid; gap: 20px; }}
.card {{ background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; padding: 18px 20px; }}
.card h2 {{ margin: 0 0 10px; font-size: 15px; text-transform: uppercase;
  letter-spacing: .06em; color: var(--dim); font-weight: 600; }}
.hwrap {{ display: flex; align-items: center; gap: 16px; max-width: 880px;
  margin: 0 auto; }}
.conflogo {{ height: 54px; width: auto; }}
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
.cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
@media (max-width: 700px) {{ .cols {{ grid-template-columns: 1fr; }} }}
ul.games {{ list-style: none; padding: 0; margin: 0; }}
ul.games li {{ padding: 6px 0; border-bottom: 1px solid var(--line); font-size: 14px; }}
.dim {{ color: var(--dim); }}
.ccgtag {{ color: var(--accent); font-weight: 700; font-size: 12px;
  text-transform: uppercase; }}
.rules ol {{ padding-left: 22px; }} .rules li {{ margin: 7px 0; font-size: 14px; }}
footer {{ max-width: 880px; margin: 0 auto; padding: 10px 20px 40px;
  color: var(--dim); font-size: 13px; }}
footer a {{ color: var(--accent2); }}
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
<header>
  <div class=hwrap>
    <img src=logos/big12.svg alt="Big 12" class=conflogo>
    <div>
      <h1>Big 12 Tiebreaker Tracker <span class=dim>· {year}</span></h1>
      <p>Unofficial fan tool. Applies the official Big 12 tiebreaking
      procedures to live results after every game.</p>
    </div>
  </div>
</header>
<main>
{card}

{whatif}

<div class=card>
  <h2>Conference standings · {played} of {total} games played
  <span id=w-chip class=wchip hidden>what-if</span></h2>
  <progress max={total} value={played}></progress>
  {table}
  <div style="margin-top:14px" id=stories>{stories}</div>
</div>

<div class=cols>
  <div class=card>
    <h2>Latest results</h2>
    <ul class=games>{results}</ul>
  </div>
  <div class=card>
    <h2>Up next</h2>
    <ul class=games>{upcoming}</ul>
  </div>
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
</main>
<script id=payload type=application/json>{payload}</script>
<script src=engine.js></script>
<script src=app.js></script>
<footer>
  Results from <a href="https://collegefootballdata.com">collegefootballdata.com</a>.
  Procedure per the <a
  href="https://s3.amazonaws.com/big12sports.com/documents/2025/11/4/Big_12_Football_2024_Tiebreaker_Policy.pdf">official
  Big 12 tiebreaker policy</a> · not affiliated with the Big 12 Conference.
  Conference and team marks belong to their institutions (via Wikimedia
  Commons; provenance in <a href="logos/SOURCES.json">SOURCES.json</a>) and are
  used here for identification only. Last updated {updated}.
</footer>
</body>
</html>
"""


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    year = int(argv[0]) if argv else default_season()
    if "--fetch" in sys.argv:
        games = fetcher.fetch_season(year)
        fetcher.fetch_ratings(year)
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
    with open(out, "w") as f:
        f.write(render(year, games))
    print(f"built {out} for {year}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate site/history.html — Big 12 tie archaeology, 2011-2025.

Every final-standings tie in the modern (post-division) Big 12, broken by
this site's engine. Honest framing: the engine implements the CURRENT
(2024, 16-team) tiebreaking policy; for older seasons the page shows what
that policy would have said, which is not always what the conference's
then-current policy decided. It's archaeology through today's lens.

Static page: run this locally (needs CFBD_API_KEY) and commit the output;
the hourly CI build does not regenerate it.

    python3 gen_history.py          # fetch missing seasons, build page
"""
import json
import os

import fetch as fetcher
import tiebreaker as tb
from build import esc, logo_img

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "history")
FIRST, LAST = 2011, 2025


def season_games(year):
    p = os.path.join(HIST, f"games_{year}.json")
    if not os.path.exists(p):
        cached = os.path.join(HERE, "data", f"games_{year}.json")
        if os.path.exists(cached):
            games = json.load(open(cached))
        else:
            games = fetcher.fetch_season(year)
        json.dump(games, open(p, "w"), indent=1)
    return json.load(open(p))


def season_section(year, games):
    rows = tb.standings(games)
    if not rows:
        return "", []
    groups = {}
    for r in rows:
        if r["tie_group"]:
            groups.setdefault(r["tie_group"], []).append(r)
    n_ties = len(groups)
    top2 = [r["team"] for r in rows if r["rank"] <= 2]
    parts = [f"<details class=season><summary><b>{year}</b> "
             f"<span class=dim>· top two: {esc(top2[0])}, {esc(top2[1])} · "
             f"{n_ties} tie group{'s' if n_ties != 1 else ''}</span>"
             f"</summary>"]
    parts.append("<table class=mini><thead><tr><th></th><th>Team</th>"
                 "<th>Conf</th></tr></thead><tbody>")
    for r in rows:
        mark = "*" if r["tie_group"] else ""
        parts.append(f"<tr><td>{r['rank']}</td>"
                     f"<td>{esc(r['team'])}{mark}</td>"
                     f"<td>{r['conf_w']}–{r['conf_l']}</td></tr>")
    parts.append("</tbody></table>")
    for tg, members in groups.items():
        first = next(r for r in members if r["log"] is not None)
        names = tg.replace("+", ", ")
        parts.append(f"<details><summary>How the {esc(names)} tie breaks"
                     f"</summary><ol class=steps>")
        for line in first["log"]:
            parts.append(f"<li>{esc(line)}</li>")
        parts.append("</ol></details>")
    parts.append("</details>")
    stats = [{"year": year, "group": tg,
              "size": len(tg.split("+")),
              "steps": sorted({e["step"] for r in groups[tg]
                               for e in (r["events"] or [])
                               if r["events"]})}
             for tg in groups]
    return "".join(parts), stats


def h2h_grid(all_games):
    teams = sorted({g["home"] for gs in all_games.values() for g in gs
                    if g["conference_game"]}
                   & set(tb.conf_records(
                       list(all_games[LAST])).keys()) | set())
    # current 16 only, all-time records among them
    current = sorted(tb.conf_records(all_games[LAST]).keys())
    wl = {a: {b: [0, 0] for b in current} for a in current}
    for year, gs in all_games.items():
        for g in gs:
            if not g["conference_game"] or g.get("ccg") or not g["completed"]:
                continue
            w = tb.winner(g)
            if not w:
                continue
            l = g["away"] if w == g["home"] else g["home"]
            if w in wl and l in wl:
                wl[w][l][0] += 1
                wl[l][w][1] += 1
    head = "".join(f"<th title='{esc(t)}'>{esc(t[:3].upper())}</th>"
                   for t in current)
    rows = []
    for a in current:
        cells = []
        for b in current:
            if a == b:
                cells.append("<td class=selfcell>—</td>")
                continue
            w, l = wl[a][b]
            if w == l == 0:
                cells.append("<td class=dim>·</td>")
            else:
                p = w / (w + l)
                cells.append(f"<td style='color:hsl({round(p * 130)} 60% "
                             f"var(--pctl))' title='{esc(a)} {w}–{l} vs "
                             f"{esc(b)}'>{w}–{l}</td>")
        rows.append(f"<tr><td class=teamcell>{logo_img(a, 14)}"
                    f"{esc(a[:12])}</td>{''.join(cells)}</tr>")
    return ("<div class='table-scroll'><table class='mini h2h'><thead>"
            f"<tr><th></th>{head}</tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def main():
    os.makedirs(HIST, exist_ok=True)
    all_games = {}
    sections = []
    tie_count = 0
    step_hist = {}
    for year in range(LAST, FIRST - 1, -1):
        games = season_games(year)
        all_games[year] = games
        html, stats = season_section(year, games)
        sections.append(html)
        for s in stats:
            tie_count += 1
            for st in s["steps"]:
                step_hist[st] = step_hist.get(st, 0) + 1
    step_names = {"a": "head-to-head / mini round-robin",
                  "b": "common opponents", "c": "the standings walk",
                  "d": "strength of schedule", "e": "total wins",
                  "f": "SportSource rating", "g": "coin toss"}
    hist_rows = "".join(
        f"<tr><td>({k})</td><td>{esc(step_names[k])}</td><td>{v}</td></tr>"
        for k, v in sorted(step_hist.items()))

    page = f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Big 12 tie archaeology, {FIRST}–{LAST} — Big12ology</title>
<meta name=description content="Every final-standings tie in the modern Big 12, broken step by step by the tiebreaker engine, plus the all-time head-to-head grid.">
<link rel=icon type=image/svg+xml href=favicon.svg>
<link rel=stylesheet href=/brand.css>
<style>
:root {{ --bg:#f6f4ef; --panel:#fff; --ink:#1a1c20; --dim:#6b7280;
  --line:#e2ddd2; --accent:#c8102e; --accent2:#003087; --pctl:32%; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#14161a; --panel:#1d2026; --ink:#e8e6e1; --dim:#9aa0aa;
    --line:#2e323a; --accent:#ff5a6e; --accent2:#7aa2ff; --pctl:63%; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.6 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif }}
header {{ border-bottom:4px solid var(--accent); padding:24px 20px;
  background:var(--panel) }}
header h1 {{ margin:0; font-size:23px }} header p {{ margin:4px 0 0;
  color:var(--dim); font-size:14px }}
main {{ max-width:820px; margin:0 auto; padding:24px 20px 48px }}
a {{ color:var(--accent2) }}
.dim {{ color:var(--dim) }}
details.season {{ background:var(--panel); border:1px solid var(--line);
  border-radius:10px; padding:12px 16px; margin:10px 0 }}
details.season > summary {{ cursor:pointer; font-size:17px }}
details details {{ border:1px solid var(--line); border-radius:8px;
  padding:8px 12px; margin:8px 0 }}
details details summary {{ cursor:pointer; font-size:14px; font-weight:600 }}
.steps {{ margin:8px 0 4px; padding-left:20px }}
.steps li {{ margin:5px 0; font-size:13.5px }}
table.mini {{ border-collapse:collapse; margin:10px 0; font-size:13.5px }}
table.mini th, table.mini td {{ padding:4px 8px; border-bottom:1px solid
  var(--line); text-align:left; font-variant-numeric:tabular-nums }}
table.mini th {{ font-size:11px; text-transform:uppercase;
  letter-spacing:.05em; color:var(--dim) }}
.mark {{ vertical-align:-3px; margin-right:6px }}
.teamcell {{ white-space:nowrap }}
.table-scroll {{ overflow-x:auto }}
.h2h td, .h2h th {{ padding:3px 5px; font-size:11.5px }}
.selfcell {{ color:var(--line) }}
.note {{ color:var(--dim); font-size:13.5px }}
h2 {{ font-size:20px; margin:30px 0 8px }}
</style></head><body>
<nav class=b12-topbar><a class=b12-brand href=/>Big12<span>ology</span></a>
<a class=on href=/tiebreaker/>Tiebreaker</a><a href=/attendance/>Attendance</a>
<a class=b12-right href=/privacy>Privacy</a></nav>
<header><h1>Tie archaeology <span class=dim>· {FIRST}–{LAST}</span></h1>
<p>Every final-standings tie in the modern Big 12, broken step by step.
<a href="./">Back to the tracker</a></p></header>
<main>
<p>The Big 12 has played round-robin-or-close schedules without divisions
since {FIRST}. Across those {LAST - FIRST + 1} seasons the engine finds
<b>{tie_count} final-standings tie groups</b>. Below, each season's table
(* marks tied teams) with the full resolution narrative for every tie —
as decided by the <a href=how.html>current 16-team policy</a>.
<b>Caveat honestly stated:</b> earlier seasons were governed by earlier
policies (and 2011–2023 had true round robins, where common-opponent logic
degenerates gracefully); this page shows what today's rules would have
said, which occasionally differs from what the conference ruled at the
time.</p>

<h2>Which step settles ties?</h2>
<table class=mini><thead><tr><th>Step</th><th>Name</th>
<th>Ties it helped settle</th></tr></thead><tbody>{hist_rows}</tbody></table>
<p class=note>Counts the steps that seeded at least one team in each tie
group's resolution.</p>

<h2>Season by season</h2>
{"".join(sections)}

<h2>All-time head-to-head, current sixteen</h2>
<p class=note>Conference games only, {FIRST}–{LAST}, read across: row team's
record against column team. Many pairs first met in 2023–24 — that's
realignment for you.</p>
{h2h_grid(all_games)}
</main>
<footer class=b12-footer>A Big12ology project · not affiliated with the
Big 12 Conference · data from collegefootballdata.com ·
<a href="https://big12ology.com/privacy">Privacy</a></footer>
<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{{"token": "355e765d921e4b36ad2bf78d509eae6c"}}'></script>
</body></html>"""
    out = os.path.join(HERE, "site", "history.html")
    with open(out, "w") as f:
        f.write(page)
    print(f"built {out}: {tie_count} tie groups across "
          f"{LAST - FIRST + 1} seasons")


if __name__ == "__main__":
    main()

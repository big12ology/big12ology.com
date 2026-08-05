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

# What actually happened. 2011-2016: no championship game — the conference
# awarded shared titles on record alone (tiebreakers only picked BCS/CFP
# representatives). Declared champions per Big 12 records / contemporaneous
# reporting. 2017+: the championship-game pairing comes straight from the
# season data (the CCG row), so nothing is hand-typed for those years.
DECLARED_CHAMPS = {
    2011: ["Oklahoma State"],
    2012: ["Kansas State", "Oklahoma"],
    2013: ["Baylor"],
    2014: ["Baylor", "TCU"],
    2015: ["Oklahoma"],
    2016: ["Oklahoma"],
}


def fetch_history_season(year):
    """Fetch by conference, not by current membership — historical seasons
    must include departed members (Oklahoma, Texas, and 2011's Texas A&M
    and Missouri) and every game between them."""
    raw = fetcher.get(f"games?year={year}&seasonType=regular",
                      fetcher.key())
    games = []
    for g in raw:
        hc, ac = g.get("homeConference"), g.get("awayConference")
        if hc != "Big 12" and ac != "Big 12":
            continue
        notes = g.get("notes") or ""
        games.append({
            "id": g.get("id"), "week": g.get("week"),
            "start": g.get("startDate"), "notes": notes,
            "ccg": "championship" in notes.lower(),
            "completed": bool(g.get("completed")),
            "conference_game": bool(g.get("conferenceGame"))
            and hc == "Big 12" and ac == "Big 12",
            "home": g.get("homeTeam"), "away": g.get("awayTeam"),
            "home_conf": hc, "away_conf": ac,
            "home_class": g.get("homeClassification"),
            "away_class": g.get("awayClassification"),
            "home_points": g.get("homePoints"),
            "away_points": g.get("awayPoints"),
        })
    games.sort(key=lambda x: (x["week"], x["start"] or ""))
    return games


def season_games(year):
    p = os.path.join(HIST, f"games_{year}.json")
    if not os.path.exists(p):
        games = fetch_history_season(year)
        json.dump(games, open(p, "w"), indent=1)
    return mark_ccg(json.load(open(p)))


def mark_ccg(games):
    """Repair championship-game flags for historical seasons.

    1. A 'championship' note only counts as THE Big 12 CCG when both sides
       are Big 12 — future Big 12 members drag their old conferences' title
       games (Pac-12, AAC) into the data.
    2. CFBD's 2017-2021 feeds don't tag the Big 12 CCG at all. In a strict
       round-robin, no pair meets twice — so the season's rematch, by date,
       is the championship game.
    """
    for g in games:
        if g.get("ccg") and not (g.get("home_conf") == "Big 12"
                                 and g.get("away_conf") == "Big 12"):
            g["ccg"] = False
    if not any(g.get("ccg") for g in games):
        seen = {}
        conf = sorted((g for g in games if g["conference_game"]
                       and g["completed"]),
                      key=lambda g: g["start"] or "")
        for g in conf:
            pair = frozenset((g["home"], g["away"]))
            if pair in seen:
                g["ccg"] = True  # the rematch — round robins have none
            else:
                seen[pair] = g
    return games


def actual_outcome(year, games):
    """(description_html, engine_agrees, diff_note) for the season."""
    ccg = next((g for g in games if g.get("ccg") and g["completed"]), None)
    rows = tb.standings(games)
    eng_top2 = [r["team"] for r in rows if r["rank"] <= 2]
    if ccg:
        pair = {ccg["home"], ccg["away"]}
        w = tb.winner(ccg)
        desc = (f"Actual championship game: {esc(ccg['away'])} at "
                f"{esc(ccg['home'])} — {esc(w)} won the title.")
        if set(eng_top2) == pair:
            return desc, True, None
        note = (f"The conference sent <b>{esc(ccg['away'])}</b> and "
                f"<b>{esc(ccg['home'])}</b>; today's policy applied to the "
                f"same results selects <b>{esc(eng_top2[0])}</b> and "
                f"<b>{esc(eng_top2[1])}</b>.")
        return desc, False, note
    champs = DECLARED_CHAMPS.get(year, [])
    desc = ("Actual outcome: " +
            (" and ".join(esc(c) for c in champs)
             + (" declared co-champions" if len(champs) > 1
                else " won the title outright")) +
            " — no championship game existed; shared titles were awarded "
            "on record alone.")
    if len(champs) == 1 and champs[0] == eng_top2[0]:
        return desc, True, None
    if len(champs) > 1:
        note = (f"The conference hung {len(champs)} banners; today's "
                f"one-true-champion procedure picks "
                f"<b>{esc(eng_top2[0])}</b> alone.")
        agrees = eng_top2[0] in champs
        return desc, agrees and False, note
    if not champs:
        raise ValueError(f"{year}: no championship game found in the data "
                         f"and no declared champion on record")
    note = (f"Declared champion {esc(champs[0])}; today's policy says "
            f"<b>{esc(eng_top2[0])}</b>.")
    return desc, False, note


def season_section(year, games):
    rows = tb.standings(games)
    if not rows:
        return "", [], None
    groups = {}
    for r in rows:
        if r["tie_group"]:
            groups.setdefault(r["tie_group"], []).append(r)
    n_ties = len(groups)
    top2 = [r["team"] for r in rows if r["rank"] <= 2]
    desc, agrees, diff_note = actual_outcome(year, games)
    flag = "" if agrees else " <span class=diffflag>≠</span>"
    parts = [f"<details class=season><summary><b>{year}</b> "
             f"<span class=dim>· engine top two: {esc(top2[0])}, "
             f"{esc(top2[1])} · {n_ties} tie group"
             f"{'s' if n_ties != 1 else ''}</span>{flag}</summary>"]
    parts.append(f"<p class=actual>{desc}</p>")
    if diff_note:
        parts.append(f"<p class=diffbox><b>Where history and the current "
                     f"policy part ways:</b> {diff_note}</p>")
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
    return "".join(parts), stats, (None if agrees else
                                   {"year": year, "note": diff_note})


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
    diffs = []
    for year in range(LAST, FIRST - 1, -1):
        games = season_games(year)
        all_games[year] = games
        html, stats, diff = season_section(year, games)
        sections.append(html)
        if diff:
            diffs.append(diff)
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

    body = f"""<style>
details.season {{ background:var(--panel); border:1px solid var(--line);
  border-radius:10px; padding:12px 16px; margin:10px 0 }}
details.season > summary {{ cursor:pointer; font-size:17px }}
details details {{ border:1px solid var(--line); border-radius:8px;
  padding:8px 12px; margin:8px 0 }}
details details summary {{ cursor:pointer; font-size:14px; font-weight:600 }}
.steps {{ margin:8px 0 4px; padding-left:20px }}
.steps li {{ margin:5px 0; font-size:13.5px }}
table.mini {{ border-collapse:collapse; margin:10px 0; font-size:13.5px;
  width:auto }}
table.mini th, table.mini td {{ padding:4px 8px; border-bottom:1px solid
  var(--line); text-align:left; font-variant-numeric:tabular-nums }}
table.mini th {{ font-size:11px; text-transform:uppercase;
  letter-spacing:.05em; color:var(--dim) }}
.table-scroll {{ overflow-x:auto }}
.h2h td, .h2h th {{ padding:3px 5px; font-size:11.5px }}
.selfcell {{ color:var(--line) }}
.actual {{ font-size:14px; margin:10px 0 6px }}
.diffbox {{ font-size:13.5px; background:var(--bg); border-left:3px solid
  var(--accent); border-radius:4px; padding:8px 12px; margin:8px 0 }}
.diffflag {{ color:var(--accent); font-weight:800 }}
.histwrap h2 {{ font-size:20px; margin:30px 0 8px }}
.histwrap p {{ font-size:15px }}
</style>
<div class=histwrap>
<p><b>Tie archaeology, {FIRST}–{LAST}.</b> The Big 12 has played
round-robin-or-close schedules without divisions since {FIRST}. Across those
{LAST - FIRST + 1} seasons the engine finds <b>{tie_count} final-standings
tie groups</b>. Below, each season's table (* marks tied teams) with the
full resolution narrative for every tie — as decided by the
<a href=how.html>current 16-team policy</a>. <b>Caveat honestly stated:</b>
earlier seasons were governed by earlier policies; this page shows what
today's rules would have said, which occasionally differs from what the
conference ruled at the time.</p>

<h2>Where today's rules would have changed history</h2>
{"".join(f"<p class=diffbox><b>{d['year']}:</b> {d['note']}</p>" for d in diffs)
 if diffs else "<p class=note>None — every actual outcome matches what the current policy produces.</p>"}

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
</div>"""
    out = os.path.join(HIST, "history_body.html")
    with open(out, "w") as f:
        f.write(body)
    print(f"built fragment {out}: {tie_count} tie groups across "
          f"{LAST - FIRST + 1} seasons — run build.py to wrap it")


if __name__ == "__main__":
    main()

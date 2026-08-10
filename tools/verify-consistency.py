#!/usr/bin/env python3
"""Make the site agree with itself.

    tools/verify-consistency.py dist

Every number on every page comes out of build.py, and only build.py's inputs
are tested. That leaves a whole class of failure uncovered: the engine is
right, the data is right, and the page puts the right number in the wrong row.
Nothing downstream would notice, because nothing downstream reads the page.

So this reads the page. Each finished season publishes the same standings
three times, by three different code paths — a JSON payload for the charts, a
CSV for download, and an HTML table for people — and they have to say the same
thing. Where the site states a conclusion twice (who is in the championship
game, on the brief and in the payload) that has to match too, and a percentage
rendered beside a record has to be that record's percentage.

Three representations agreeing is worth more than any one of them being
checked against a fixture: a fixture goes stale, and these are generated from
the same source by code that could each be wrong in a different way.

Exit 1 on any disagreement. Pure stdlib.
"""
import csv
import io
import json
import os
import re
import sys
from html.parser import HTMLParser

DASH = "–"          # EN DASH, which is what the records are rendered with


class Tables(HTMLParser):
    """Every table on the page, as lists of cell text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._t = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._t = []
        elif tag == "tr" and self._t is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._t is not None:
            self.tables.append(self._t)
            self._t = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._t.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def text_of(path):
    return open(path, encoding="utf-8").read()


def standings_rows(html, teams):
    """The standings table, as (rank, team, record, pct) — however it is laid
    out. Matched by shape rather than by class, so a restyle does not silently
    turn this check off."""
    p = Tables()
    p.feed(html)
    out = []
    for table in p.tables:
        rows = []
        for cells in table:
            hit = [c for c in cells if c in teams]
            rec = [c for c in cells if re.fullmatch(rf"\d+{DASH}\d+", c)]
            if not hit or not rec:
                continue
            rank = next((c for c in cells if re.fullmatch(r"\d+", c)), None)
            pct = next((c for c in cells if re.fullmatch(r"[01]\.\d+", c)), None)
            rows.append((rank, hit[0], rec[0], pct))
        if len(rows) > len(out):
            out = rows
    return out


def check_season(dist, year, bad):
    base = os.path.join(dist, "tiebreaker", year) if year else \
        os.path.join(dist, "tiebreaker")
    where = f"tiebreaker/{year}" if year else "tiebreaker"

    dj = os.path.join(base, "data.json")
    if not os.path.exists(dj):
        return
    data = json.load(open(dj))
    payload = {r["team"]: r for r in data.get("standings", [])}
    if not payload:
        return                      # a season that has not kicked off yet

    # --- the CSV says what the payload says
    cpath = os.path.join(base, "standings.csv")
    if os.path.exists(cpath):
        for row in csv.DictReader(io.StringIO(text_of(cpath))):
            t = row["team"]
            if t not in payload:
                bad.append(f"{where}: standings.csv has {t}, the payload does not")
                continue
            p = payload[t]
            for k in ("rank", "conf_w", "conf_l", "nonconf_w", "nonconf_l",
                      "overall_w", "overall_l"):
                if int(row[k]) != int(p[k]):
                    bad.append(f"{where}: {t} {k} is {row[k]} in the CSV and "
                               f"{p[k]} in the payload")
        n_csv = sum(1 for _ in csv.DictReader(io.StringIO(text_of(cpath))))
        if n_csv != len(payload):
            bad.append(f"{where}: standings.csv has {n_csv} teams, "
                       f"the payload has {len(payload)}")

    # --- the table people read says it too
    spath = os.path.join(base, "standings.html")
    if os.path.exists(spath):
        rows = standings_rows(text_of(spath), set(payload))
        if not rows:
            bad.append(f"{where}: standings.html has no table this can read — "
                       f"either the page changed shape or it is empty")
        seen = set()
        for rank, team, record, pct in rows:
            if team in seen:
                continue            # the page may repeat a team in another view
            seen.add(team)
            p = payload[team]
            want = f"{p['conf_w']}{DASH}{p['conf_l']}"
            if record != want:
                bad.append(f"{where}: standings.html shows {team} at {record}, "
                           f"the payload says {want}")
            if rank is not None and int(rank) != int(p["rank"]):
                bad.append(f"{where}: standings.html ranks {team} {rank}, "
                           f"the payload says {p['rank']}")
            # A percentage printed beside a record has to be that record's.
            if pct is not None:
                played = p["conf_w"] + p["conf_l"]
                if played:
                    want_pct = round(p["conf_w"] / played, 3)
                    if abs(float(pct) - want_pct) > 0.0006:
                        bad.append(f"{where}: {team} is {record} but shows "
                                   f"{pct}, which is not {want_pct:.3f}")
        missing = set(payload) - seen
        if missing:
            bad.append(f"{where}: standings.html is missing "
                       f"{', '.join(sorted(missing))}")

    # --- the conclusion on the brief matches the one in the payload
    champ = data.get("championship") or {}
    brief = os.path.join(base, "index.html")
    if champ.get("resolved") and os.path.exists(brief):
        page = text_of(brief)
        for slot in ("seed1", "seed2"):
            team = champ.get(slot)
            if team and team not in page:
                bad.append(f"{where}: the payload puts {team} in the "
                           f"championship game and the brief never says so")


def main(dist):
    bad = []
    years = [""]
    tb = os.path.join(dist, "tiebreaker")
    if os.path.isdir(tb):
        years += sorted(d for d in os.listdir(tb)
                        if re.fullmatch(r"\d{4}", d)
                        and os.path.isdir(os.path.join(tb, d)))
    checked = 0
    for y in years:
        before = len(bad)
        check_season(dist, y, bad)
        checked += 1
        del before
    if bad:
        print(f"verify-consistency: {len(bad)} disagreement(s)\n")
        for b in bad:
            print(f"  {b}")
        return 1
    print(f"verify-consistency: {checked} season(s) — the table, the CSV and "
          f"the payload agree, and the brief names the same finalists")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify-consistency.py <dist>")
    sys.exit(main(sys.argv[1]))

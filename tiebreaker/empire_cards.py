#!/usr/bin/env python3
"""Shareable images of The Empire, one per week, frozen once written.

The attendance section's cards, applied to the map. Every week of a season
gets its own pair of files and keeps them:

    cards/<year>/empire-week-NN.svg / .png   the map after week NN
    cards/<year>/empire-start.svg / .png     the map before any Big 12 game
    cards/empire.svg / .png                  a copy of the newest live one

The versioned name is the one to share, because it still shows week 3 in
November. The unversioned pair is for anybody who wants "whatever is
current", and it is what the Empire page offers social sites as its preview.

SELF-CONTAINED, for the reason the attendance cards are: an SVG loaded
through an <img> tag gets no stylesheet, no webfont and no external file, so
every color is inline, the type is a generic stack, the background is
painted, and the sixteen logos are embedded as data. The PNG exists because
link previews and most social sites will not show an SVG.

COMMITTED, NOT BUILT AT DEPLOY. A week's card is written once and changes
only if a result is corrected, so the archive is a handful of new files a
week, and it survives the generator breaking. A card whose SVG comes out
byte-identical to the one on disk is skipped, PNG included, so the hourly
deploy spends nothing on weeks that have not moved.

Chrome draws the PNGs. It is on the GitHub runner and on a Mac, and nothing
else that renders SVG text well is on both.

Usage: python3 empire_cards.py [YEAR ...] [--png]
"""
import base64
import html
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build                                             # noqa: E402
import empire                                            # noqa: E402

CARDS = os.path.join(HERE, "cards")
LOGOS = os.path.join(HERE, "site", "logos")
PAGE_URL = "big12ology.com/tiebreaker/empire.html"

W, H = 1200, 630
INK, DIM, LINE, BG = "#16181A", "#5b6169", "#e2ddd3", "#f6f4ef"
TEAL = "#0B6E77"
FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "Helvetica, Arial, sans-serif")


def _esc(s):
    return html.escape(str(s), quote=True)


_LOGO = {}


def _use(team, x, y, size):
    """A logo placed by reference to its one embedded copy (see _defs)."""
    k = build.TEAM_KEY[team]
    return (f'<use href="#lg-{k}" x="{x}" y="{y}" width="{size}" '
            f'height="{size}" transform="translate({x},{y}) scale({size / 20}) '
            f'translate({-x},{-y})"/>' if size != 20 else
            f'<use href="#lg-{k}" x="{x}" y="{y}"/>')


def _defs(teams):
    """Every logo the card shows, embedded once. A card names a team up to
    six times (a badge per territory it holds and a row in the list), and
    embedding the mark at every one of them was most of the file."""
    return "<defs>" + "".join(
        f'<image id="lg-{build.TEAM_KEY[t]}" href="{_logo(t)}" width="20" '
        f'height="20"/>' for t in sorted(teams)) + "</defs>"


def _logo(team):
    """A team's mark as a data URI, read once."""
    if team not in _LOGO:
        k = build.TEAM_KEY[team]
        ext = "png" if k == "byu" else "svg"
        mime = "image/png" if ext == "png" else "image/svg+xml"
        with open(os.path.join(LOGOS, f"{k}.{ext}"), "rb") as f:
            _LOGO[team] = (f"data:{mime};base64,"
                           + base64.b64encode(f.read()).decode())
    return _LOGO[team]


def _label(frame, games, teams):
    """"Through Week 7 · Sun, Oct 18", or the starting map's name."""
    if frame["week"] is None:
        return "Before the first conference game"
    when = max((g.get("start") or "" for g in games
                if g["week"] == frame["week"] and g.get("completed")
                and g["home"] in teams and g["away"] in teams), default="")
    return (f"Through Week {frame['week']}"
            + (f" · {build.pretty_date(when, 'short')}" if when else ""))


def card(year, frame, terr, games, teams, color):
    """One card, as an SVG string: the map on the left, who holds it on the
    right, and the season, the week and the page's address."""
    blocks = terr["blocks"]
    x0, y0, vw, vh = (float(v) for v in terr["viewBox"].split())
    # The map gets the left of the card; the holdings list the right.
    MX, MY, MW, MH = 28, 118, 790, 474
    s = min(MW / vw, MH / vh)
    ox = MX + (MW - vw * s) / 2 - x0 * s
    oy = MY + (MH - vh * s) / 2 - y0 * s
    took = {b for m in frame["moves"] for b in m["took"]}

    paths = "".join(
        f'<path d="{blk["d"]}" fill="{color[frame["owner"][b]]}" '
        f'stroke="{INK if b in took else "#ffffff"}" '
        f'stroke-width="{2.4 if b in took else 1.1}" stroke-linejoin="round"/>'
        for b, blk in sorted(blocks.items(), key=lambda kv: kv[0] in took))
    S = 20
    marks = "".join(
        f'<circle cx="{blk["home"][0]}" cy="{blk["home"][1]}" r="2.6" '
        f'fill="#fff" stroke="#000" stroke-width="0.8"/>'
        f'<circle cx="{blk["label"][0]}" cy="{blk["label"][1]}" r="{S / 2 + 3}" '
        f'fill="#fff" stroke="#000" stroke-opacity="0.35" stroke-width="0.8"/>'
        + _use(frame["owner"][b], blk["label"][0] - S / 2,
               blk["label"][1] - S / 2, S)
        for b, blk in sorted(blocks.items()))
    states = (f'<path d="{terr["states"]}" fill="none" stroke="#000" '
              f'stroke-opacity="0.18" stroke-width="0.5"/>')
    themap = (f'<g transform="translate({ox:.2f},{oy:.2f}) scale({s:.5f})">'
              f'{paths}{states}{marks}</g>')

    # Who holds what: everyone with land, biggest first.
    rows = [r for r in empire.standings(frame, blocks) if r["blocks"]]
    LX, LY, ROW = 850, 150, 30
    gone = 16 - len(rows)
    lines = [f'<text x="{LX}" y="{LY - 14}" font-size="15" font-weight="700" '
             f'letter-spacing="1.2" fill="{DIM}">WHO HOLDS THE MAP</text>']
    for i, r in enumerate(rows):
        y = LY + i * ROW
        share = r["area"]
        lines.append(
            _use(r["team"], LX, y, 20) +
            f'<text x="{LX + 30}" y="{y + 15}" font-size="17" fill="{INK}">'
            f'{_esc(r["team"])}</text>'
            f'<rect x="{LX + 180}" y="{y + 6}" width="90" height="8" rx="4" '
            f'fill="{LINE}"/>'
            f'<rect x="{LX + 180}" y="{y + 6}" width="{max(90 * share, 2):.1f}" '
            f'height="8" rx="4" fill="{TEAL}"/>'
            f'<text x="{LX + 322}" y="{y + 15}" font-size="16" '
            f'text-anchor="end" fill="{INK}">{share * 100:.1f}%</text>')
    y = LY + len(rows) * ROW + 18
    if gone:
        lines.append(f'<text x="{LX}" y="{y}" font-size="14" fill="{DIM}">'
                     f'{gone} of the sixteen hold nothing</text>')
        y += 22
    if took:
        lines.append(
            f'<rect x="{LX}" y="{y - 11}" width="22" height="13" rx="2" '
            f'fill="{BG}" stroke="{INK}" stroke-width="2"/>'
            f'<text x="{LX + 30}" y="{y}" font-size="14" fill="{DIM}">'
            f'Outlined: changed hands this week</text>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{_esc(FONT)}">
{_defs(set(frame["owner"].values()))}
<rect width="{W}" height="{H}" fill="{BG}"/>
<text x="36" y="62" font-size="38" font-weight="700" fill="{INK}">The Big 12 Empire</text>
<text x="36" y="98" font-size="21" fill="{DIM}">{year} season · {_esc(_label(frame, games, teams))}</text>
{themap}
{''.join(lines)}
<text x="{W - 36}" y="{H - 26}" font-size="16" text-anchor="end" fill="{DIM}">{PAGE_URL}</text>
</svg>"""


def name(frame):
    return ("empire-start" if frame["week"] is None
            else f"empire-week-{frame['week']:02d}")


def _chrome():
    named = os.environ.get("CHROME_BIN") or os.environ.get("CHROME_PATH")
    for p in (named,
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/usr/bin/google-chrome", "/usr/bin/chromium-browser",
              "/usr/bin/chromium"):
        if p and os.path.exists(p):
            return p
    return None


def png(svg_path, png_path):
    """Rasterize at twice the card's size, so it stays sharp on a retina
    screen or when a site scales it up. Loud when Chrome is missing: a
    missing PNG is a 404 on a URL somebody else put in their post."""
    chrome = _chrome()
    if not chrome:
        sys.exit("no Chrome found for --png; set CHROME_BIN")
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--hide-scrollbars", "--force-device-scale-factor=2",
                    f"--window-size={W},{H}",
                    "--default-background-color=FFFFFFFF",
                    f"--screenshot={png_path}", f"file://{svg_path}"],
                   check=True, capture_output=True)


def emit(path_stem, svg, want_png):
    """Write only on change, and draw the PNG only when the SVG moved or
    the PNG is missing. Returns True when anything was written."""
    os.makedirs(os.path.dirname(path_stem), exist_ok=True)
    sp, pp = path_stem + ".svg", path_stem + ".png"
    had = open(sp, encoding="utf-8").read() if os.path.exists(sp) else None
    moved = had != svg
    if moved:
        with open(sp, "w", encoding="utf-8") as f:
            f.write(svg)
    if want_png and (moved or not os.path.exists(pp)):
        png(sp, pp)
        return True
    return moved


def build_year(year, want_png):
    """Every week of one season. Returns the newest frame's stem."""
    games = build.fetcher.mark_ccg(json.load(open(
        os.path.join(HERE, "data", f"games_{year}.json"))))
    teams = build.load_teams()
    terr = build.empire_map(games, set(teams))
    blocks = terr["blocks"]
    frames = empire.replay(games, set(blocks))
    color = {t: build.team_color(teams, t) for t in blocks}
    newest = None
    for f in frames:
        stem = os.path.join(CARDS, str(year), name(f))
        if emit(stem, card(year, f, terr, games, set(blocks), color), want_png):
            print(f"  {year}/{name(f)}")
        newest = stem
    return newest


def main(argv):
    want_png = "--png" in argv
    # The build's own answer to "which season is live", not LIVE_YEAR, which
    # is only set once build.main() has run.
    live = build.default_season()
    years = [int(a) for a in argv if not a.startswith("-")] or [live]
    for year in years:
        newest = build_year(year, want_png)
        # The unversioned pair follows the live season only: a backfill of an
        # old year must not move "the current map" backwards.
        if year == live and newest:
            for ext in ("svg", "png"):
                src, dst = f"{newest}.{ext}", os.path.join(CARDS, f"empire.{ext}")
                if os.path.exists(src) and (
                        not os.path.exists(dst)
                        or open(src, "rb").read() != open(dst, "rb").read()):
                    shutil.copyfile(src, dst)
                    print(f"  empire.{ext} <- {os.path.basename(newest)}")


if __name__ == "__main__":
    main(sys.argv[1:])

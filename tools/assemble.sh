#!/usr/bin/env bash
#
# Assemble the three sites into one directory laid out exactly the way
# big12ology.com is served.
#
#     tools/assemble.sh [outdir]        # default: <repo>/dist, wherever you run it
#
# The tiebreaker must already have been built — this only copies. Run
# `cd tiebreaker && python3 build.py --fetch` first, or use the workflow.
#
#     /                 the hub          (repo root, minus repo furniture)
#     /tiebreaker/      tiebreaker/site/ (generated)
#     /attendance/      attendance/      (served as-is, data included)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The default output belongs to the repo, not to wherever you happen to be
# standing: run this from tiebreaker/ and a relative "dist" built a whole
# second copy of the site at tiebreaker/dist while the real one went stale —
# and the stale one is what the preview keeps serving. A path you pass in is
# still yours, and still relative to your shell.
if [ "$#" -gt 0 ]; then
  DIST="$1"
  case "$DIST" in /*) ;; *) DIST="$PWD/$DIST" ;; esac
else
  DIST="$ROOT/dist"
fi

# Everything that is repo, not site. The subtree directories are excluded at
# the root because each one is placed under its own path below.
#
# The second group is unanchored on purpose. /.env only excludes a file sitting
# at the transfer root, so the same file one directory down ships. Anything
# that names a credential or a backend gets excluded wherever it appears, in
# every one of the copies below — and the gate at the bottom checks that it
# worked, because an exclude that silently stops matching looks exactly like an
# exclude that is doing its job.
COMMON=(--exclude=.DS_Store --exclude=__pycache__ --exclude=node_modules
        --exclude=/.git --exclude=/.github --exclude=/.claude --exclude=/.idea
        --exclude=/.gitignore --exclude=/.env
        --exclude=.env --exclude=.env.* --exclude=.dev.vars
        --exclude=wrangler.toml --exclude=.wrangler --exclude='*.sql'
        --exclude='client_secret*.json' --exclude='*credentials*.json')

# Empty it, do not remove it. `rm -rf "$DIST"` deletes the directory itself,
# which pulls the ground out from under anything serving it — a local preview
# pointed at dist/ dies mid-session and looks like the site crashed. Emptying
# leaves the inode alone, so a server holding it open simply sees the files
# come back a second later.
mkdir -p "$DIST"
find "$DIST" -mindepth 1 -delete

echo "assembling into $DIST"

# This copies every top-level path that is not named here. That default is the
# reason /worker is on the list: the pick'em backend is repo, not site, and
# nothing below would have noticed it arriving.
rsync -a "${COMMON[@]}" \
      --exclude=/tiebreaker --exclude=/attendance --exclude=/tools \
      --exclude=/dist --exclude=/HANDOFF.md --exclude=/worker \
      "$ROOT/" "$DIST/"

# Two of this directory's files belong to the pick'em and are tracked here
# only because this is where build.py writes them: pickcon.js, which asks
# /api/consensus, and pickem-scores.json, which the grader reads back. With
# the section off nothing references either, so they would ship as dead
# weight pointing at an API that does not exist.
# Seeded rather than empty: bash 3.2 — which is what macOS ships — treats
# "${arr[@]}" on an empty array as an unbound variable under set -u.
PICKEM_EX=(--exclude=.keep-pickem-none)
[ "${B12_PICKEM:-}" = "1" ] || PICKEM_EX+=(--exclude=pickcon.js
                                           --exclude=pickem-scores.json)
rsync -a "${COMMON[@]}" "${PICKEM_EX[@]}" \
      "$ROOT/tiebreaker/site/" "$DIST/tiebreaker/"

# The schedule section is generated beside the tiebreaker and served as a
# sibling path; its pages reach back to ../tiebreaker/ for the shared marks
# and stylesheet rather than carrying a second copy of them.
rsync -a "${COMMON[@]}" "$ROOT/tiebreaker/site_schedule/" "$DIST/schedule/"

# The attendance repo is served as-is by Pages today, README and scripts and
# all. Copying it wholesale is what makes the output comparable byte for byte
# against what is live; see tools/compare-live.sh.
rsync -a "${COMMON[@]}" "$ROOT/attendance/" "$DIST/attendance/"

# The published pick'em slates, under /pickem/data/ rather than /pickem/ so
# they cannot collide with the section's own pages. These are the frozen
# lines: the record of what each week was played on, which the grader reads
# back and which is the only durable copy — data/lines_<year>.json is
# overwritten every refresh. Public for the same reason data.json and
# attendance.csv are, and because a scoreboard nobody can check is a claim.
# Absent until the first weekly refresh runs, so its absence is not an error.
#
# mkdir first: rsync creates the last missing directory of a destination but
# not two, and /pickem/data/ is two while /pickem/ holds no pages yet.

# The pick'em section itself, generated beside the tiebreaker like the
# schedule and reaching back to ../tiebreaker/ for brand.css, theme.js and
# pct.js rather than carrying a second copy of them. Its pages are shells:
# everything a reader sees arrives from /api/* at runtime.
#
# Gated on B12_PICKEM, the same flag build.py reads. pages.yml sets it from
# the date — the section opens on 2026-08-20 and every deploy after that
# ships it — so the flag is a schedule rather than something anybody has to
# remember to push. Off by default, which is what a local build and CI get;
# nothing is deleted, it is simply not published.
if [ "${B12_PICKEM:-}" = "1" ]; then
  # Both games and the account they share. soon.html and _moved/ are build
  # scaffolding and have no business in the published tree.
  rsync -a "${COMMON[@]}" --exclude=soon.html --exclude=/_moved \
        "$ROOT/tiebreaker/site_pools/" "$DIST/pools/"
else
  # Dark, /pools/ is the Coming Soon page and its screenshots — and nothing
  # else. Not app.js, not styles.css, not a slate: the teaser is one
  # self-contained page, so there is no second thing to keep in step and
  # nothing that can start talking to an API that does not exist.
  mkdir -p "$DIST/pools"
  cp "$ROOT/tiebreaker/site_pools/soon.html" "$DIST/pools/index.html"
  cp "$ROOT/tiebreaker/site_pools/sitemap.xml" "$DIST/pools/sitemap.xml"
  rsync -a "${COMMON[@]}" "$ROOT/tiebreaker/site_pools/shots/" \
        "$DIST/pools/shots/"
fi

# /pickem/ shipped before the games were grouped. It stays, as a redirect, for
# as long as anything out there still links to it.
mkdir -p "$DIST/pickem"
cp "$ROOT/tiebreaker/site_pools/_moved/pickem.html" "$DIST/pickem/index.html"

# The published slates, under /pickem/data/ so they cannot collide with the
# section's pages. These are the frozen lines — the record of what each week
# was played on, which the grader reads back and which is the only durable
# copy, since data/lines_<year>.json is overwritten on every refresh. Public
# for the same reason data.json and attendance.csv are.
#
# mkdir first: rsync creates the last missing directory of a destination but
# not two, and /pickem/data/ was two before the section above existed.
if [ "${B12_PICKEM:-}" = "1" ] && [ -d "$ROOT/tiebreaker/pickem" ]; then
  mkdir -p "$DIST/pools/data"
  rsync -a "${COMMON[@]}" "$ROOT/tiebreaker/pickem/" "$DIST/pools/data/"
fi

# --- checks ------------------------------------------------------------
# --- the pick'em switch, in hand-written files -----------------------------
# B12_PICKEM already decides what build.py generates and what gets copied. It
# could not reach the four hand-written pages, the root sitemap or robots.txt,
# because nothing generates those — so the last version of this left the nav
# link deleted outright and a comment saying where to put it back. That made
# launch day a manual edit in several files, which is precisely the "one
# chrome" duplication hazard this project has already been bitten by.
#
# So those files carry BOTH versions, and this picks one:
#
#   <!-- PICKEM-ONLY -->  ...  <!-- /PICKEM-ONLY -->   kept only when on
#   <!-- PICKEM-OFF -->   ...  <!-- /PICKEM-OFF -->    kept only when off
#
# and `# PICKEM-ONLY` / `# /PICKEM-ONLY` for robots.txt, which has no comment
# syntax a browser would hide. Markers are always removed; only the content
# between them varies. Turning the section on is now the flag and nothing else.
echo "pick'em content: $([ "${B12_PICKEM:-}" = "1" ] && echo on || echo off)"
while IFS= read -r page; do
  grep -q 'PICKEM-ONLY\|PICKEM-OFF' "$page" || continue
  B12_PICKEM="${B12_PICKEM:-}" python3 - "$page" <<'PY'
import os, re, sys
p = sys.argv[1]
on = os.environ.get("B12_PICKEM") == "1"
s = open(p, encoding="utf-8").read()

def block(tag, keep):
    """Drop or unwrap one marked region, in either comment syntax."""
    global s
    for open_, close in ((f"<!-- {tag} -->", f"<!-- /{tag} -->"),
                         (f"# {tag}", f"# /{tag}")):
        pat = re.compile(
            r"[ \t]*" + re.escape(open_) + r"[ \t]*\n?(.*?)"
            r"[ \t]*" + re.escape(close) + r"[ \t]*\n?", re.S)
        s = pat.sub((lambda m: m.group(1)) if keep else "", s)

block("PICKEM-ONLY", on)
block("PICKEM-OFF", not on)
open(p, "w", encoding="utf-8").write(s)
PY
done < <(find "$DIST" -type f \( -name '*.html' -o -name '*.xml' -o -name '*.txt' \))

# --- footer stamp ---------------------------------------------------------
# The four hand-written pages (hub, attendance, privacy, 404) carry the same
# footer as the generated ones, and the footer names when the site was last
# updated. Static HTML cannot know that, so they ship a {{BUILD_STAMP}} token
# and it is filled here, at the moment the deploy is assembled. The format
# matches build.py's build_stamp() exactly — the whole point is that every page
# on the domain reads the same.
# Overridable so a build can be reproduced. Two assembles a minute apart
# differ only here, and a determinism check that could not pin it would have
# to diff around the one line that is supposed to change.
STAMP="${B12_BUILD_STAMP:-$(date -u '+%B %-d, %H:%M UTC')}"
echo "stamping footers: $STAMP"
while IFS= read -r page; do
  grep -q '{{BUILD_STAMP}}' "$page" || continue
  # A literal replacement, so nothing in $STAMP is read as sed syntax.
  python3 - "$page" "$STAMP" <<'PY'
import sys
p, stamp = sys.argv[1], sys.argv[2]
with open(p, encoding="utf-8") as f:
    s = f.read()
with open(p, "w", encoding="utf-8") as f:
    f.write(s.replace("{{BUILD_STAMP}}", stamp))
PY
done < <(find "$DIST" -name '*.html')

# --- the hub's numbers ----------------------------------------------------
# index.html is hand-written and is the only page on the domain that is. It
# now shows the next kickoff and the projected title game, which are facts
# about a season rather than things a person types, so build.py writes them
# to tiebreaker/site/hub.json and they are filled in here — the same
# arrangement as {{BUILD_STAMP}} directly above, for the same reason.
#
# None of it depends on the clock: "the next game" means the first one with no
# result. Two assembles a minute apart produce identical bytes, which
# tools/verify-deterministic.sh checks and pages.yml relies on.
HUB_JSON="$DIST/tiebreaker/hub.json"
if [ -f "$HUB_JSON" ] && grep -q '{{HUB_' "$DIST/index.html" 2>/dev/null; then
  python3 - "$DIST/index.html" "$HUB_JSON" <<'PY'
import datetime
import html, json, sys

page, data = sys.argv[1], sys.argv[2]
hub = json.load(open(data, encoding="utf-8"))
e = html.escape

nxt = hub.get("next") or {}
ccg = hub.get("ccg") or []
counts = hub.get("counts") or {}
att = hub.get("attendance") or {}
spot = hub.get("spotlight")


def millions(n):
    return f"{n / 1_000_000:.1f}M" if n else ""


def line(sp):
    """The home number, written the way the pick'em writes it."""
    if sp is None:
        return "no line yet"
    if sp == 0:
        return "pick'em"
    n = f"{abs(sp):g}"
    # U+2212, and the favorite named rather than a bare sign, because the
    # hero has room to say it and "-7" alone means nothing at a glance.
    return f"{nxt.get('home') if sp < 0 else nxt.get('away')} −{n}"


def when(iso):
    """A readable UTC instant, for before the script runs and instead of it.

    The page rewrites this to the reader's own zone on load, which is the only
    correct answer — a kickoff has one instant and sixteen local times. Until
    then it says UTC and says so, because a deadline printed without a zone is
    a bug this project has already decided about.
    """
    if not iso:
        return ""
    try:
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return t.strftime("%a %-d %b, %H:%M UTC")


tok = {
    "HUB_NEXT_WHEN": e(when(nxt.get("kickoff"))),
    "HUB_NEXT_AWAY": e(nxt.get("away") or ""),
    "HUB_NEXT_HOME": e(nxt.get("home") or ""),
    "HUB_NEXT_JOIN": "vs" if nxt.get("neutral") else "at",
    "HUB_NEXT_LINE": e(line(nxt.get("spread"))),
    "HUB_NEXT_ISO": e(nxt.get("kickoff") or ""),
    "HUB_CCG_A": e(ccg[0]["team"]) if len(ccg) > 0 else "",
    "HUB_CCG_B": e(ccg[1]["team"]) if len(ccg) > 1 else "",
    "HUB_CCG_A_PCT": f"{round(ccg[0]['p'] * 100)}%" if len(ccg) > 0 else "",
    "HUB_CCG_B_PCT": f"{round(ccg[1]['p'] * 100)}%" if len(ccg) > 1 else "",
    # The week's biggest conference game, as ONE LINE, or nothing at all.
    #
    # It used to be the line plus spot["html"] — the fork, both branches, four
    # percentages and their arrows — plus a note saying where the arrows were
    # measured from and whose opinion they are. All of that is right, and all
    # of it is about 200px of card: on a phone it is the entire bottom row of
    # a front page whose one job is to show all four sections at once. The
    # fork is on /tiebreaker/ where it has the room. This is the sentence that
    # makes somebody go there.
    #
    # Nothing at all, rather than a heading over a blank: before the season
    # and once every conference game is played there is no next game to point
    # at, and "Biggest swing this week:" followed by nothing is worse than a
    # card that simply does not have the line.
    "HUB_SPOTLIGHT": (
        f"<p class=live-line>Biggest swing this week: <b>{e(spot['away'])}</b>"
        f" at <b>{e(spot['home'])}</b> &middot; {e(spot['when'])} &middot; "
        f"{spot['total']} points of a title-game berth change hands</p>"
        if spot else ""),
    "HUB_SEASON": str(hub.get("season") or ""),
    "HUB_TEAMS": str(counts.get("teams") or ""),
    "HUB_GAMES": str(counts.get("games") or ""),
    "HUB_ATT_SEASONS": str(att.get("seasons") or ""),
    "HUB_ATT_GAMES": f"{att['games']:,}" if att.get("games") else "",
    "HUB_ATT_FANS": millions(att.get("fans")),
}

s = open(page, encoding="utf-8").read()
for k, v in tok.items():
    s = s.replace("{{" + k + "}}", v)
open(page, "w", encoding="utf-8").write(s)
PY
  echo "hub numbers: next kickoff and the projected title game"
fi

# The facts were once also dumped out as a static "in figures" list at the
# foot of three section pages, filled here from facts.json. They are gone:
# 345 one-line sentences stacked in two columns buried the page they were
# meant to support, and the archived-season copies of those pages were served
# the live season's list because facts.json has one set of sections and no
# year. The facts remain where they read as facts — one at a time, in the hub
# rotator — and the generator that produces them is untouched.
#
# There is no metadata substitute worth adding: keywords meta is ignored,
# description is one truncated sentence, and hiding the list behind CSS is a
# spam signal rather than a workaround. The pages carry real prose and real
# JSON-LD, which is the part that was ever doing the work.

# --- content security policy ----------------------------------------------
# A budget on what a page is allowed to execute, so that a string which should
# have been escaped and was not is a broken tooltip instead of a session.
#
# WHY HERE AND NOT IN THE PAGES. GitHub Pages serves whatever bytes it is
# given and cannot set a header, so the only CSP this repo can deliver is a
# <meta>. That is a real limit rather than a preference: a <meta> policy cannot
# carry frame-ancestors and cannot carry HSTS, so clickjacking and the first
# unencrypted request are still the zone's problem and belong in a Cloudflare
# Transform Rule. What a <meta> DOES cover is script execution, which is the
# part that matters here and the part this repo can test.
#
# LAST, AFTER EVERY OTHER REWRITE. The policy names the inline scripts by the
# hash of their contents, so it has to be computed from the bytes that ship.
# The pick'em switch, the footer stamp and the hub numbers all edit these files
# above; computing this before any of them would name a script that no longer
# exists and take the theme bootstrap down with it.
#
# FIRST IN THE HEAD, once written. A meta policy governs what comes after it,
# so a policy placed below the theme bootstrap would let the very script it is
# supposed to authorise run unchecked and then start enforcing. That also means
# a wrong hash is a VISIBLE failure — the theme flashes and cards do not
# pre-collapse — rather than a silent one, which is the right way round.
#
# THE HASHES ARE COMPUTED, NOT LISTED. A hand-maintained list of sha256- values
# in this file would be wrong the first time somebody edited a bootstrap by a
# character, and wrong in a way that only shows on the deployed site. So each
# page is scanned for its own inline scripts and gets its own policy.
echo "content-security-policy: hashing inline scripts"
while IFS= read -r page; do
  python3 - "$page" <<'PY'
import base64, hashlib, re, sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
# No <head> means one of the redirect stubs — a doctype, a meta refresh and a
# link, with no script of any kind on it. There is nothing for a policy to
# govern and nowhere guaranteed to put it that precedes the content. The gate
# below the loop is what makes that judgement safe rather than assumed: a
# page carrying an inline script and no policy fails the build.
if "<head" not in s or "Content-Security-Policy" in s:
    sys.exit(0)

# Executable inline scripts only. A <script type="application/ld+json"> block
# is data — browsers do not execute it and CSP does not govern it — and
# hashing those would add noise to every policy on the site for nothing.
inline = re.compile(
    r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script\s*>", re.S | re.I)
hashes = []
for attrs, bodytext in inline.findall(s):
    t = re.search(r'type\s*=\s*["\']?([^"\'\s>]+)', attrs, re.I)
    if t and t.group(1).lower() not in ("text/javascript", "module",
                                        "application/javascript"):
        continue
    # The hash is over the element's exact contents, byte for byte — no
    # trimming. A stripped copy is a different script to the browser.
    d = base64.b64encode(
        hashlib.sha256(bodytext.encode("utf-8")).digest()).decode()
    h = f"'sha256-{d}'"
    if h not in hashes:
        hashes.append(h)

# style-src keeps 'unsafe-inline' and that is not an oversight to clean up
# later. The charts set `style="--tc:…"` on generated rows and the
# pre-collapse bootstrap builds a <style> element, both of which a strict
# style-src blocks; hashing generated CSS is not possible and a nonce needs a
# server. Inline STYLE is not how a page gets taken over — inline SCRIPT is,
# and that is the one being locked down.
#
# connect-src names both Cloudflare hosts because the beacon posts to
# /cdn-cgi/rum on this origin in some configurations and to
# cloudflareinsights.com in others, and a policy that breaks analytics on a
# config change would get deleted rather than fixed.
#
# NO frame-ancestors HERE. It is one of the directives a <meta> policy cannot
# deliver — the browser parses it, ignores it, and logs an error on every page
# for its trouble, which is how a policy starts looking broken to anybody who
# opens the console. Clickjacking is real and unaddressed by this file; it is
# the Transform Rule's job, along with HSTS, and saying so here beats shipping
# a directive that does nothing.
policy = "; ".join([
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "form-action 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "connect-src 'self' https://cloudflareinsights.com "
    "https://static.cloudflareinsights.com",
    " ".join(["script-src 'self'", "https://static.cloudflareinsights.com",
              *hashes]),
])
meta = f'<meta http-equiv="Content-Security-Policy" content="{policy}">\n'

# Immediately after <head…>, so it precedes every script on the page.
s = re.sub(r"(<head\b[^>]*>)", lambda m: m.group(1) + "\n" + meta, s, count=1)
open(p, "w", encoding="utf-8").write(s)
PY
done < <(find "$DIST" -name '*.html')

# --- cache-bust by content, not by hand ------------------------------------
# Every ?v= in the assembled site is rewritten here to a hash of the bytes it
# points at, so a file that changed always changes its own URL and a file that
# did not never does.
#
# build.py already did this for the pages it generates, through asset_v(). The
# four hand-written pages could not, because their versions were integers
# typed by hand, and they had drifted exactly as you would expect: the same
# brand.css was ?v=8 on 404.html, ?v=9 on index.html and ?v=48 under
# attendance/, and a stylesheet fix landed without any of the three moving.
# The check below only ever asked whether a query string was PRESENT, never
# whether it was current, so nothing caught it.
#
# The module graph settles first, and leaves first, because a module's own
# bytes contain its import specifiers: change html.js and gametip.js's text
# changes with it, so gametip.js needs a new hash too, and so does app.js
# above it. Walking bottom up is what carries a leaf edit up to the tag in
# the page.
#
# That ordering fixes a live bug, not just a stale one. The attendance modules
# disagreed about each other's versions: app.js imported html.js?v=45 while
# charts.js and gametip.js imported html.js?v=44. Two URLs for one file is two
# modules to a browser, so html.js, stats.js and gametip.js were each fetched,
# parsed and instantiated twice on every page load. One hash per file collapses
# each pair back into one.
python3 - "$DIST" <<'PY'
import hashlib, os, re, sys

DIST = os.path.abspath(sys.argv[1])

# The shared, mutable assets: the same list the NO CACHE-BUST check uses, plus
# the attendance modules that are only ever reached from inside other modules.
BUSTED = {"brand.css", "tokens.css", "theme.js", "cards.js", "state.js",
          "metrics.js", "app.js", "engine.js", "pct.js", "replay.js",
          "scrollcue.js", "styles.css", "charts.js", "gametip.js", "stats.js",
          "html.js"}

def digest(path):
    return hashlib.sha1(open(path, "rb").read()).hexdigest()[:8]

def resolve(ref, source):
    """A reference as written, to a real file inside DIST, or None."""
    bare = ref.split("?", 1)[0].split("#", 1)[0]
    if not bare or bare.startswith(("http:", "https:", "//", "data:", "mailto:")):
        return None
    base = DIST if bare.startswith("/") else os.path.dirname(source)
    p = os.path.normpath(os.path.join(base, bare.lstrip("/")))
    return p if p.startswith(DIST) and os.path.isfile(p) else None

IMPORT = re.compile(r"((?:from|import)\s*\(?\s*)(['\"])(\.[^'\"]*?\.js)(\?[^'\"]*)?\2")

hashes = {}

def settle(path, stack=()):
    """Hash a module once its own imports carry their final versions."""
    if path in hashes:
        return hashes[path]
    if path in stack:
        return None          # an import cycle: leave those specifiers alone
    text = open(path, encoding="utf-8").read()
    for m in IMPORT.finditer(text):
        target = resolve(m.group(3), path)
        if target:
            settle(target, stack + (path,))
    def stamp(m):
        target = resolve(m.group(3), path)
        h = hashes.get(target) if target else None
        if not h:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{m.group(3)}?v={h}{m.group(2)}"
    out = IMPORT.sub(stamp, text)
    if out != text:
        open(path, "w", encoding="utf-8").write(out)
    hashes[path] = digest(path)
    return hashes[path]

for root, _, files in os.walk(DIST):
    for f in sorted(files):
        if f.endswith(".js"):
            settle(os.path.join(root, f))

# Then the pages, which point at modules whose bytes have stopped moving.
REF = re.compile(r"""\b(src|href)=(["']?)([^"'\s>]+)\2""")

for root, _, files in os.walk(DIST):
    for f in sorted(files):
        if not f.endswith(".html"):
            continue
        page = os.path.join(root, f)
        text = open(page, encoding="utf-8").read()
        def stamp(m, page=page):
            attr, quote, ref = m.group(1), m.group(2), m.group(3)
            bare = ref.split("?", 1)[0]
            if os.path.basename(bare) not in BUSTED:
                return m.group(0)
            target = resolve(ref, page)
            if not target:
                return m.group(0)
            return f"{attr}={quote}{bare}?v={hashes.get(target) or digest(target)}{quote}"
        out = REF.sub(stamp, text)
        if out != text:
            open(page, "w", encoding="utf-8").write(out)
PY

# A missing page or a stale asset has shipped silently in this project
# before. Both are cheap to catch here and expensive to notice in the wild.

fail=0
note() { echo "  MISSING  $1"; fail=1; }

# An unfilled token would ship the literal braces into the page. Both kinds:
# the footer stamp, and the hub's numbers — whose fill is conditional on
# hub.json existing, so a missing file would otherwise put "{{HUB_NEXT_HOME}}"
# in the hero of the front page and fail nothing.
while IFS= read -r page; do
  echo "  UNSTAMPED  ${page#"$DIST"/}"; fail=1
done < <(grep -rlE '\{\{(BUILD_STAMP|HUB_[A-Z_]+|FACTS:[a-z]+)\}\}' "$DIST" \
           --include='*.html' || true)
# FACTS: stays in that pattern deliberately. Nothing fills it any more, so a
# token left behind in a hand-written page is now a build failure rather than
# a pair of braces in the middle of the attendance page.

for f in index.html privacy.html 404.html CNAME robots.txt sitemap.xml \
         brand.css tokens.css theme.js cards.js state.js metrics.js \
         tiebreaker/index.html tiebreaker/how.html tiebreaker/history.html \
         tiebreaker/draw.html tiebreaker/rotation.html tiebreaker/cutline.html tiebreaker/ladder.html \
         tiebreaker/app.js tiebreaker/engine.js tiebreaker/feed.xml \
         tiebreaker/hub.json tiebreaker/facts.json \
         schedule/index.html schedule/draw.html schedule/rotation.html \
         schedule/sitemap.xml \
         attendance/index.html attendance/site/app.js \
         attendance/data/teams.json attendance/data/seasons/index.json \
         ; do
  [ -e "$DIST/$f" ] || note "$f"
done

# The pick'em's own manifest, checked only when it was asked for. Listed
# unconditionally it would report nine missing files on every ordinary build,
# which is how a required-file check stops being read.
if [ "${B12_PICKEM:-}" = "1" ]; then
  for f in pools/index.html pools/account.html pools/app.js \
           pools/styles.css pools/sitemap.xml \
           pools/pickem/index.html pools/pickem/card.html \
           pools/pickem/board.html pools/pickem/rules.html \
           pools/survivor/index.html pools/survivor/pool.html \
           pools/survivor/rules.html; do
    [ -e "$DIST/$f" ] || note "$f"
  done
else
  # And the reverse. Dark, exactly two things may be under /pickem/: the
  # teaser and its pictures. Anything else means the section leaked.
  [ -e "$DIST/pools/index.html" ] || note "pools/index.html (the teaser)"
  while IFS= read -r stray; do
    echo "  MUST NOT SHIP  ${stray#"$DIST"/} with B12_PICKEM unset"
    fail=1
  done < <(find "$DIST/pools" -type f \
                ! -name index.html ! -name sitemap.xml \
                ! -path "*/shots/*" 2>/dev/null)
fi

# --- the privacy page names every key we store ----------------------------
# privacy.html enumerates local storage by name, which is only a promise
# worth making if it stays true. A feature that starts storing something is
# one line of JavaScript and no reason to remember a legal page exists, so
# this is the reminder: every b12- key the shipped site reads or writes must
# appear on the privacy page, or the build fails and says which one does not.
# `|| true` on both: grep exits 1 when it matches nothing, and under
# `set -euo pipefail` that failure propagates out of the command
# substitution and kills the build — a gate that finds nothing to complain
# about must not be the thing that fails.
KEYS=$( { grep -rhoE 'localStorage\.(get|set|remove)Item\(\s*"b12-[a-z-]+"' \
            "$DIST" --include='*.js' --include='*.html' 2>/dev/null \
          | grep -oE '"b12-[a-z-]+"' | tr -d '"' | sort -u; } || true)
# state.js addresses its namespaces through a prefix, so the literal keys
# never appear beside localStorage. Collect those too.
KEYS="$KEYS
$( { grep -rhoE 'B12State\.(get|set)Page?\(\s*"[a-z-]+"' "$DIST" \
       --include='*.js' 2>/dev/null | grep -oE '"[a-z-]+"' | tr -d '"' \
     | sed 's/^/b12-/' | sort -u; } || true)"
for k in $(echo "$KEYS" | sort -u); do
  [ -n "$k" ] || continue
  grep -q "$k" "$DIST/privacy.html" || {
    echo "  UNDISCLOSED  $k is stored but is not named on privacy.html"
    fail=1; }
done

[ -s "$DIST/CNAME" ] && grep -qx "big12ology.com" "$DIST/CNAME" || {
  echo "  CNAME does not say big12ology.com"; fail=1; }

# One chrome, three copies of it. brand.css, tokens.css and theme.js each exist
# at the root and again inside attendance/ and tiebreaker/site/, because each
# subtree is served from its own path and was once its own repo. Nothing keeps
# them in step, and they are byte-identical only because everyone has so far
# remembered — a fix applied to one copy simply does not reach the other two,
# and the symptom is a section that looks subtly wrong on one path only.
#
# Found the hard way: a masthead fix landed in the root copy and changed
# nothing on /tiebreaker/, because that page reads its own.
for shared in brand.css tokens.css theme.js cards.js state.js metrics.js; do
  a="$ROOT/$shared"
  for b in "$ROOT/attendance/$shared" "$ROOT/tiebreaker/site/$shared"; do
    [ -f "$a" ] && [ -f "$b" ] || continue
    cmp -s "$a" "$b" || {
      echo "  OUT OF SYNC  ${b#"$ROOT"/} differs from $shared at the root"
      fail=1; }
  done
done

# Cache-busting is not optional (HANDOFF.md). Every reference to one of the
# shared, mutable assets must carry a query string.
BUSTED='brand\.css|tokens\.css|theme\.js|cards\.js|state\.js|metrics\.js|app\.js|engine\.js|pct\.js|replay\.js|scrollcue\.js|styles\.css|charts\.js|gametip\.js|stats\.js'

while IFS= read -r page; do
  # Both quoting styles: the hub writes href="…", build.py emits some
  # attributes bare.
  refs=$(grep -oE "(src|href)=\"[^\"]*(${BUSTED})\"|(src|href)=[^\"' >]*(${BUSTED})[ >]" \
         "$page" || true)
  [ -n "$refs" ] || continue
  echo "  NO CACHE-BUST  ${page#"$DIST"/}"
  echo "$refs" | sed 's/^/    /'
  fail=1
done < <(find "$DIST" -name '*.html')

# A page that runs script must have a policy governing it.
#
# The step that writes them skips anything with no <head>, which today is the
# redirect stubs and nothing else. That is a judgement about the current
# output, not a property of it — add a <script> to one of those stubs, or a
# page shaped in some new way, and it would ship unprotected and pass every
# other check on this list. So the exemption is stated as a rule and enforced:
# no inline script without a policy naming its hash.
# The same regex as the writer above, deliberately: a check that detects
# inline script differently from the step that protects it will eventually
# disagree with it, and the disagreement is silent in both directions. The
# first draft of this used `grep -E '<script(?![^>]*src=)'`, which is a PCRE
# lookahead that -E cannot parse — so it errored on every page, fell through
# to a weaker pattern, and looked exactly like a check that was passing.
while IFS= read -r page; do
  python3 - "$page" <<'PY' || { echo "  NO CSP  ${page#"$DIST"/} runs inline script with no policy"; fail=1; }
import re, sys
s = open(sys.argv[1], encoding="utf-8").read()
if "Content-Security-Policy" in s:
    sys.exit(0)
for attrs, body in re.findall(
        r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script\s*>", s, re.S | re.I):
    t = re.search(r'type\s*=\s*["\']?([^"\'\s>]+)', attrs, re.I)
    if t and t.group(1).lower() not in ("text/javascript", "module",
                                        "application/javascript"):
        continue
    if body.strip():
        sys.exit(1)
sys.exit(0)
PY
done < <(find "$DIST" -name '*.html')

# Every check above asks whether something is missing. None of them asks
# whether something arrived that must never be served, and that asymmetry is
# how a credential ships: the root rsync copies each top-level path that is not
# on an explicit exclude list, so a new directory of backend tooling is public
# the moment it exists. A missing exclude fails nothing here and is invisible
# in the wild — the file is simply served, correctly, to everyone.
#
# So the excludes are a fence and this is the alarm. Adding a backend without
# reading this script is now a failed build rather than a leaked client secret.
while IFS= read -r bad; do
  echo "  MUST NOT SHIP  ${bad#"$DIST"/}"; fail=1
done < <(find "$DIST" \( -name '.env' -o -name '.env.*' -o -name '.dev.vars' \
                      -o -name 'wrangler.toml' -o -name '.wrangler' \
                      -o -name '*.sql' -o -name 'package.json' \
                      -o -name 'package-lock.json' \
                      -o -name 'client_secret*.json' \
                      -o -name '*credentials*.json' \
                      -o -name '*service_account*.json' \) 2>/dev/null || true)

# Under /pickem/ the rule is stricter than the named list above, and it has to
# be. That list is a denylist: it catches app.js and styles.css because those
# names happen to appear in it, and would wave through a pickem/countdown.js
# added next year. A gate that silently stops covering new files is worse than
# no gate, because it still reads as protection.
while IFS= read -r page; do
  bad=$(grep -oE '(src|href)="(\./)?[A-Za-z0-9_./-]+\.(js|css)"' "$page" \
        | grep -v '://' || true)
  [ -n "$bad" ] || continue
  echo "  NO CACHE-BUST  ${page#"$DIST"/}"
  echo "$bad" | sed 's/^/    /'
  fail=1
done < <(find "$DIST/pickem" -name '*.html' 2>/dev/null)

if [ "$fail" -ne 0 ]; then
  echo "assemble: FAILED"
  exit 1
fi

echo "assembled $(find "$DIST" -type f | wc -l | tr -d ' ') files, checks passed"

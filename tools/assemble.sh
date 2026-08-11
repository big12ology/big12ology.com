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

rm -rf "$DIST"
mkdir -p "$DIST"

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
# Gated on B12_PICKEM, the same flag build.py reads. The Worker behind
# /api/* does not exist yet, so every page of this section renders an error
# and privacy.html still promises "no accounts, no cookies". Off by default,
# which is what CI gets; nothing is deleted, it is simply not published.
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


def millions(n):
    return f"{n / 1_000_000:.1f}M" if n else ""


def line(sp):
    """The home number, written the way the pick'em writes it."""
    if sp is None:
        return "no line yet"
    if sp == 0:
        return "pick'em"
    n = f"{abs(sp):g}"
    # U+2212, and the favourite named rather than a bare sign, because the
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

# --- the facts, as crawlable text -----------------------------------------
# The hub rotates one fact per card from JavaScript, which is right for the
# hub and useless as content: a crawler sees an empty <p>, and even a renderer
# that runs the script sees one of three hundred sentences chosen at random.
#
# So every fact is also written out statically, on the section page it belongs
# to. A page carrying {{FACTS:attendance}} gets the attendance list. That is
# the whole mechanism, and it works the same for the pages build.py generates
# and the hand-written one under /attendance/, which is why it lives here
# rather than in either.
#
# Dated facts are included with their date spelled out. On the hub a dated
# fact is only true on its day; in a list headed "in figures" it is a dated
# record, and reads as one.
FACTS_JSON="$DIST/tiebreaker/facts.json"
if [ -f "$FACTS_JSON" ]; then
  while IFS= read -r page; do
    python3 - "$page" "$FACTS_JSON" <<'PY'
import html, json, re, sys

page, data = sys.argv[1], sys.argv[2]
facts = json.load(open(data, encoding="utf-8"))["sections"]
MONTH = ["", "January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"]


def when(on):
    m, d = int(on[:2]), int(on[3:])
    return f"{MONTH[m]} {d}: "


def block(name):
    rows = facts.get(name) or []
    if not rows:
        return ""
    items = "".join(
        "<li>" + (when(f["on"]) if f.get("on") else "")
        + html.escape(f["t"]) + "</li>"
        for f in sorted(rows, key=lambda f: (f.get("on") or "", f["t"])))
    return f'<ul class=figures>{items}</ul>'


s = open(page, encoding="utf-8").read()
s = re.sub(r"\{\{FACTS:([a-z]+)\}\}", lambda m: block(m.group(1)), s)
open(page, "w", encoding="utf-8").write(s)
PY
  done < <(grep -rl '{{FACTS:' "$DIST" --include='*.html' || true)
  n=$(grep -rl 'class=figures' "$DIST" --include='*.html' 2>/dev/null | wc -l)
  echo "facts rendered into ${n// /} page(s)"
fi

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

for f in index.html privacy.html 404.html CNAME robots.txt sitemap.xml \
         brand.css tokens.css theme.js \
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
for shared in brand.css tokens.css theme.js; do
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
BUSTED='brand\.css|tokens\.css|theme\.js|app\.js|engine\.js|pct\.js|replay\.js|scrollcue\.js|styles\.css|charts\.js|gametip\.js|stats\.js'

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

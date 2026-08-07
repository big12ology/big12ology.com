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
        --exclude=/.git --exclude=/.github --exclude=/.claude
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

rsync -a "${COMMON[@]}" "$ROOT/tiebreaker/site/" "$DIST/tiebreaker/"

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
if [ -d "$ROOT/tiebreaker/pickem" ]; then
  mkdir -p "$DIST/pickem/data"
  rsync -a "${COMMON[@]}" "$ROOT/tiebreaker/pickem/" "$DIST/pickem/data/"
fi

# --- checks ------------------------------------------------------------
# --- footer stamp ---------------------------------------------------------
# The four hand-written pages (hub, attendance, privacy, 404) carry the same
# footer as the generated ones, and the footer names when the site was last
# updated. Static HTML cannot know that, so they ship a {{BUILD_STAMP}} token
# and it is filled here, at the moment the deploy is assembled. The format
# matches build.py's build_stamp() exactly — the whole point is that every page
# on the domain reads the same.
STAMP="$(date -u '+%B %-d, %H:%M UTC')"
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

# A missing page or a stale asset has shipped silently in this project
# before. Both are cheap to catch here and expensive to notice in the wild.

fail=0
note() { echo "  MISSING  $1"; fail=1; }

# An unfilled token would ship the literal braces into the footer.
while IFS= read -r page; do
  echo "  UNSTAMPED  ${page#"$DIST"/}"; fail=1
done < <(grep -rl '{{BUILD_STAMP}}' "$DIST" --include='*.html' || true)

for f in index.html privacy.html 404.html CNAME robots.txt sitemap.xml \
         brand.css tokens.css theme.js \
         tiebreaker/index.html tiebreaker/how.html tiebreaker/history.html \
         tiebreaker/draw.html tiebreaker/rotation.html tiebreaker/cutline.html tiebreaker/ladder.html \
         tiebreaker/app.js tiebreaker/engine.js tiebreaker/feed.xml \
         schedule/index.html schedule/draw.html schedule/rotation.html \
         schedule/sitemap.xml \
         attendance/index.html attendance/site/app.js \
         attendance/data/teams.json attendance/data/seasons/index.json; do
  [ -e "$DIST/$f" ] || note "$f"
done

[ -s "$DIST/CNAME" ] && grep -qx "big12ology.com" "$DIST/CNAME" || {
  echo "  CNAME does not say big12ology.com"; fail=1; }

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

if [ "$fail" -ne 0 ]; then
  echo "assemble: FAILED"
  exit 1
fi

echo "assembled $(find "$DIST" -type f | wc -l | tr -d ' ') files, checks passed"

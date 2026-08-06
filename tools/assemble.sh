#!/usr/bin/env bash
#
# Assemble the three sites into one directory laid out exactly the way
# big12ology.com is served.
#
#     tools/assemble.sh [outdir]        # default: dist/
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
DIST="${1:-dist}"
case "$DIST" in /*) ;; *) DIST="$PWD/$DIST" ;; esac

# Everything that is repo, not site. The subtree directories are excluded at
# the root because each one is placed under its own path below.
COMMON=(--exclude=.DS_Store --exclude=__pycache__ --exclude=node_modules
        --exclude=/.git --exclude=/.github --exclude=/.claude
        --exclude=/.gitignore --exclude=/.env)

rm -rf "$DIST"
mkdir -p "$DIST"

echo "assembling into $DIST"

rsync -a "${COMMON[@]}" \
      --exclude=/tiebreaker --exclude=/attendance --exclude=/tools \
      --exclude=/dist --exclude=/HANDOFF.md \
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

# --- checks ------------------------------------------------------------
# A missing page or a stale asset has shipped silently in this project
# before. Both are cheap to catch here and expensive to notice in the wild.

fail=0
note() { echo "  MISSING  $1"; fail=1; }

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

if [ "$fail" -ne 0 ]; then
  echo "assemble: FAILED"
  exit 1
fi

echo "assembled $(find "$DIST" -type f | wc -l | tr -d ' ') files, checks passed"

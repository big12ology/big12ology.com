#!/usr/bin/env bash
# Build it twice and prove the bytes are the same.
#
#     tools/verify-deterministic.sh
#
# pages.yml deploys on a cron roughly three hundred times a season, and its
# opening comment says the output is deterministic so the unchanged parts
# redeploy identical bytes. Nothing has ever checked that. It is not a small
# claim to leave unchecked: a dict that iterates in a different order, a float
# that formats differently, a set that serialises by hash, and every deploy
# starts rewriting pages nobody edited — which buries a real change in noise
# and makes compare-live.sh useless for spotting one.
#
# Two properties, separately, and both are the deploy-time claim rather than
# byte-determinism from nothing:
#
#   A REBUILD OVER AN EXISTING TREE CHANGES NOTHING. That is what pages.yml
#   depends on and what the committed site/ trees rely on. build.py leaves
#   {{BUILD_STAMP}} as a token for the footer, and the three files that do
#   carry a real clock — data.json, feed.xml, the forecasts — compare their
#   content without it and leave the file alone when only the clock moved.
#
#   assemble.sh is deterministic given a stamp. It fills that token at deploy
#   time, which is the one thing that legitimately differs between two runs a
#   minute apart, so it is pinned rather than diffed around.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { printf '\n\033[31m%s\033[0m\n' "$*"; exit 1; }

# --- build.py -------------------------------------------------------------
# Run it twice into copies of the tree, so the working copy is untouched
# either way.
cp -R "$ROOT/tiebreaker" "$TMP/tb"
( cd "$TMP/tb" && B12_PICKEM=1 python3 build.py >/dev/null )
cp -R "$TMP/tb" "$TMP/tb-before"
( cd "$TMP/tb" && B12_PICKEM=1 python3 build.py >/dev/null )

for tree in site site_schedule site_pools; do
  if ! diff -r "$TMP/tb-before/$tree" "$TMP/tb/$tree" >"$TMP/diff-$tree" 2>&1; then
    echo "a rebuild changed $tree with no input change:"
    head -40 "$TMP/diff-$tree"
    fail "build.py rewrites files it did not need to"
  fi
done
echo "  build.py: a rebuild over an existing tree changes nothing"

# --- assemble.sh ----------------------------------------------------------
export B12_BUILD_STAMP="January 1, 00:00 UTC"
for pass in 1 2; do
  B12_PICKEM=1 "$ROOT/tools/assemble.sh" "$TMP/dist$pass" >/dev/null
done
if ! diff -r "$TMP/dist1" "$TMP/dist2" >"$TMP/diff-dist" 2>&1; then
  echo "assemble.sh is not deterministic:"
  head -40 "$TMP/diff-dist"
  fail "two identical assembles produced different output"
fi
echo "  assemble.sh: two runs at a fixed stamp, identical dist"

# --- and the stamp really is the only thing that moves ---------------------
# If this ever fails it means something else picked up a clock, which is the
# failure this whole script exists to name.
unset B12_BUILD_STAMP
B12_PICKEM=1 "$ROOT/tools/assemble.sh" "$TMP/dist3" >/dev/null
if diff -r "$TMP/dist1" "$TMP/dist3" >"$TMP/diff-stamp" 2>&1; then
  echo "  (the stamp happened to match; nothing to check)"
else
  if grep -qvE '^(diff |[<>] .*last updated|---|[0-9]+c[0-9]+)' "$TMP/diff-stamp"; then
    echo "something other than the build stamp changed between runs:"
    grep -vE '^(diff |[<>] .*last updated|---|[0-9]+c[0-9]+)' "$TMP/diff-stamp" \
      | head -20
    fail "a second clock leaked into the build"
  fi
  echo "  the build stamp is the only thing that differs between deploys"
fi

echo "deterministic: yes"

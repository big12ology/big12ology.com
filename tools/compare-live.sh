#!/usr/bin/env bash
#
# Compare an assembled dist/ against what big12ology.com serves right now.
# This is the pre-cutover check: the monorepo must produce the site that is
# already live before Pages is pointed at it.
#
#     tools/assemble.sh dist && tools/compare-live.sh dist
#     tools/compare-live.sh dist https://big12ology.com
#
# Files fall into three buckets:
#
#   static      non-HTML assets: must match byte for byte. Any difference is a
#               real finding.
#   stamped     every HTML page. All of them now carry "last updated <time>"
#               in the shared footer, so the live copy and a fresh assemble
#               never match byte for byte — they were stamped at different
#               minutes, and against a site deployed yesterday they differ by
#               a day. These are compared again with the stamp, dates, times
#               and ?v= hashes normalised away; if they match after that, the
#               difference is only the stamp.
#   missing     not served live. Expect a short list, and know each entry.
#
# Every page used to be byte-comparable because only lab.html carried a time.
# Tolerating the stamp on all HTML is the cost of one consistent footer; note
# that it is *only* the stamp being ignored — every other byte still has to
# match, so a real content change still shows up as DIFFERS.
#
# Exits non-zero if any static file differs or is missing.
#
set -euo pipefail

DIST="${1:-dist}"
BASE="${2:-https://big12ology.com}"
[ -d "$DIST" ] || { echo "no such directory: $DIST" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

norm() {
  # The footer stamp is matched as a whole clause, up to the sentence period,
  # so it does not matter what format it is in: "August 7, 00:59 UTC" and a
  # stamp from three days ago both collapse to the same token. Matching the
  # clause rather than allowing a ±1 minute window is deliberate — a dry run
  # compares against whatever is live, which is routinely hours or days old,
  # and a one-minute tolerance would fail every time.
  sed -E -e 's/last updated [^.<]*/last updated STAMP/g' \
         -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}/DATE/g' \
         -e 's/[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?/TIME/g' \
         -e 's/\?v=[0-9A-Za-z]+/?v=HASH/g' "$1"
}

same=0; stamped=0; differ=0; missing=0
differ_list=(); missing_list=(); stamped_list=()

while IFS= read -r file; do
  rel="${file#"$DIST"/}"
  # CNAME is a Pages control file: it configures the custom domain and is
  # never served. Nothing to compare.
  if [ "$rel" = "CNAME" ]; then continue; fi
  code=$(curl -sS -o "$TMP/live" -w '%{http_code}' "$BASE/$rel" || echo 000)

  if [ "$code" != "200" ]; then
    missing=$((missing + 1)); missing_list+=("$rel  (HTTP $code)")
    continue
  fi

  if cmp -s "$file" "$TMP/live"; then
    same=$((same + 1))
    continue
  fi

  # Every HTML page carries the footer's build stamp, and the tiebreaker's
  # data exports carry their own; compare all of them normalised.
  case "$rel" in
    *.html|tiebreaker/*.json|tiebreaker/*.xml|tiebreaker/*.csv)
      if diff -q <(norm "$file") <(norm "$TMP/live") >/dev/null; then
        stamped=$((stamped + 1)); stamped_list+=("$rel")
        continue
      fi
      ;;
  esac

  differ=$((differ + 1))
  differ_list+=("$rel")
  {
    echo "--- live/$rel"
    echo "+++ dist/$rel"
    # diff exits 1 on difference and pipefail would take that as fatal.
    diff <(norm "$TMP/live") <(norm "$file") | head -20 || true
  } >> "$TMP/diffs"
done < <(find "$DIST" -type f | sort)

echo
echo "identical .......... $same"
echo "stamp-only ......... $stamped   (generated, normalises equal)"
echo "differ ............. $differ"
echo "not served live .... $missing"

for r in "${stamped_list[@]:-}"; do [ -n "$r" ] && echo "  stamp-only  $r"; done
for r in "${missing_list[@]:-}"; do [ -n "$r" ] && echo "  missing     $r"; done
for r in "${differ_list[@]:-}"; do [ -n "$r" ] && echo "  DIFFERS     $r"; done

if [ -s "$TMP/diffs" ]; then
  echo
  echo "=== differences (normalised, first 20 lines each) ==="
  cat "$TMP/diffs"
fi

[ "$differ" -eq 0 ] && [ "$missing" -eq 0 ]

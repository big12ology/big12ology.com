#!/usr/bin/env bash
# Put the generated trees back the way HEAD has them.
#
#     tools/clean-build.sh            # show what would go, change nothing
#     tools/clean-build.sh --yes      # do it
#
# A local `python3 build.py` rewrites about 190 tracked files under site/,
# site_schedule/ and site_pools/, and none of them belong in a commit: code
# commits in this repo carry no built output, pages.yml rebuilds from source,
# and the only thing the committed trees are read by is the node suite. So
# after any local build the tree is loudly dirty with a diff that is not
# yours, and the useful state is the one HEAD already has.
#
# THE .inputs STAMPS ARE THE REASON THIS IS A SCRIPT. build.py writes a digest
# of every input that can change what a finished season renders to, and skips
# 2024 and 2025 on the next run when the digest still matches. They are
# gitignored, so `git checkout -- tiebreaker/site` puts the PAGES back and
# leaves the STAMPS saying those seasons are current. The next build believes
# the stamp, skips both seasons, and assemble.sh fails on footer drift —
# several steps later, in a different tool, with a message about footers.
# Reverting without clearing them is the trap; doing it in one place is the
# fix.
#
# Untracked output goes too, and only the kinds build.py writes: the Model
# pages and the pick'em shells are generated but have never been committed,
# so a checkout leaves them behind and the tree is half restored.
#
# What it will not touch: anything outside those four paths. Source, tests,
# workflows, worker/ and data/ are not this script's business, and a cleanup
# that can reach them is one you cannot run without reading it first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# forecasts/ rides along because the same local build rewrites it and the
# scheduled deploy owns it. Committing ours pushes a staler "generated"
# timestamp over the cron's fresher one, which is a tug of war with a robot.
PATHS=(tiebreaker/site tiebreaker/site_schedule tiebreaker/site_pools
       tiebreaker/forecasts)

GO=""
[ "${1:-}" = "--yes" ] && GO=1
[ "${1:-}" = "-y" ] && GO=1

# Captured once, and counted with grep's empty case handled. Under `set -e`
# with pipefail a `grep` that matches nothing fails the whole pipeline, so
# the first draft of this exited silently on an already-clean tree: no
# output, status 1, and nothing to say which of the checks had "failed".
status="$(git status --porcelain -- "${PATHS[@]}" || true)"
count() { printf '%s' "$status" | grep -c "$1" || true; }
tracked="$(printf '%s' "$status" | grep -vc '^??' || true)"
untracked="$(count '^??')"
stamps="$(find tiebreaker -name .inputs -not -path '*/.git/*' | wc -l | tr -d ' ')"

echo "modified and tracked : $tracked"
echo "untracked output     : $untracked"
echo ".inputs stamps       : $stamps"

# Named, not counted. A file about to be deleted with no way back is worth
# printing even when there are eight of them, because the one time this list
# holds something unexpected is the one time it matters.
if [ "$untracked" -gt 0 ]; then
  echo
  echo "these are untracked and will be DELETED:"
  git status --porcelain -- "${PATHS[@]}" | sed -n 's/^?? /  /p'
fi

if [ -z "$GO" ]; then
  echo
  echo "dry run. nothing changed. pass --yes to do it."
  exit 0
fi

echo
git checkout -- "${PATHS[@]}" 2>/dev/null || true
echo "reverted tracked output"

find tiebreaker -name .inputs -not -path '*/.git/*' -delete
echo "removed $stamps .inputs stamp(s), so the next build rebuilds every season"

git clean -q -f -- "${PATHS[@]}"
echo "removed untracked output"

echo
left="$(git status --porcelain -- "${PATHS[@]}" | wc -l | tr -d ' ')"
if [ "$left" = "0" ]; then
  echo "clean: the generated trees match HEAD"
else
  echo "STILL DIRTY in the generated trees, which this script cannot explain:"
  git status --porcelain -- "${PATHS[@]}" | sed 's/^/  /'
  exit 1
fi

#!/usr/bin/env python3
"""Did this change move the site? Builds HEAD and the working tree, and diffs.

    tools/golden.py

WHAT IT IS FOR. The rules engine is being moved out of Python and into
site/engine.js one module at a time. Every step of that touches code deciding
what the pages say, and the existing nets do not cover it: test_parity.py
proves the two engines agree with each other, which stops meaning anything the
moment there is one of them, and verify-deterministic.sh proves a build repeats
itself, which is true of a wrong build too. What is missing is "the pages did
not change", and that is a comparison against the code as it was.

So it builds twice — once from HEAD, once from the working tree — and diffs
every generated file. Both builds run at the same moment from the same data,
so anything that differs is the change under review and nothing else.

WHY IT IS NOT A TEST. It was, briefly, and that was wrong twice over.

  * On a clean checkout there is nothing to compare: HEAD and the working tree
    are the same source, so in CI it either says nothing or it has to reach
    back a commit and start failing every time the data legitimately moves —
    which for this repo is several times a day, because pages.yml commits its
    own output back.
  * Comparing a fresh build against the committed pages fails on any day but
    the day they were committed. The build reads the calendar in seven places:
    sitemap lastmod is today's date, and odds.regress_stale ages the ratings,
    which moved a published margin from 11.0 to 10.9 overnight. That is the
    build being correct, not the port being wrong.

Building both sides here removes both problems. It also stops depending on
what happens to be committed: site/index.html and site/history.html are
gitignored on purpose (tiebreaker/.gitignore), so they never appear in a fresh
checkout at all, and a check anchored on the committed tree reported the front
page of the tiebreaker as "newly built" every time it ran in CI.

READ THE OUTPUT, DO NOT JUST TRUST THE EXIT CODE. During the engine port:

  * moving the rules (tiebreaker.py -> engine.js): NOTHING may differ. Those
    functions have no RNG. A difference is a port bug.
  * moving odds.py: the odds files differ exactly once, because mulberry32
    replaces random.gauss and A&S 7.1.26 replaces math.erf. Convince yourself
    it is only the numbers, and say so in the commit.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TB = os.path.join(ROOT, "tiebreaker")
TREES = ("site", "site_pools", "site_schedule")

# The committed pages are built with the pools section on — pages.yml decides
# from the date, and what deploys today is the lit assembly.
ENV = dict(os.environ, B12_PICKEM="1")

# Blanked before comparing: the three clocks build.py itself excludes when it
# decides whether a file needs rewriting at all (build.py:4647, :4678, and
# assemble.sh's stamp). Two builds a few seconds apart will differ here and it
# means nothing. Everything else the calendar touches is handled by both sides
# being built at the same moment.
CLOCKS = re.compile(
    r'\{\{BUILD_STAMP\}\}'
    r'|updated [A-Z][a-z]+ \d+, \d\d:\d\d UTC'
    r'|<lastBuildDate>[^<]*</lastBuildDate>'
    r'|"generated": "[^"]*"')

# Everything build.py emits is markup or data. The one exception is the logo
# registry, a source file that happens to be JSON (build.py:442 reads it and
# nothing writes it).
GENERATED_EXT = (".html", ".xml", ".csv", ".json")
NOT_GENERATED = {"site/logos/SOURCES.json"}


def is_generated(rel):
    return rel.endswith(GENERATED_EXT) and rel not in NOT_GENERATED


def build(src_desc, dst, from_head):
    """Put a tiebreaker/ at `dst` and build it. Returns the built path."""
    tb = os.path.join(dst, "tiebreaker")
    if from_head:
        os.makedirs(tb)
        # HEAD's tiebreaker/, without touching the working tree or needing a
        # second checkout. `git archive` respects the commit exactly.
        ar = subprocess.run(["git", "-C", ROOT, "archive", "HEAD", "tiebreaker"],
                            capture_output=True)
        if ar.returncode:
            sys.exit(f"git archive failed: {ar.stderr.decode()[:300]}")
        subprocess.run(["tar", "-x", "-C", dst], input=ar.stdout, check=True)
    else:
        shutil.copytree(TB, tb, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", ".env"))
    # facts.py reads ../attendance/data/attendance.csv (facts.py:125), the one
    # file the build opens outside tiebreaker/. Without a sibling it still
    # builds, just smaller — facts.json loses its attendance section and
    # hub.json its numbers, with no error.
    os.symlink(os.path.join(ROOT, "attendance"), os.path.join(dst, "attendance"))
    r = subprocess.run([sys.executable, "build.py"], cwd=tb, env=ENV,
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"the {src_desc} build failed, so there is nothing to "
                 f"compare:\n{r.stdout[-1500:]}\n{r.stderr[-2000:]}")
    return tb


def show(a_path, b_path, label, window=90):
    """The first place two generated files disagree, with context.

    Not `diff -u`: these pages are close to single-line — the whole Brief is
    one <p> and a run of <div>s — so a unified diff is kilobytes of a single
    -/+ pair with the change buried in the middle.
    """
    a = CLOCKS.sub("", open(a_path, encoding="utf-8", errors="replace").read())
    b = CLOCKS.sub("", open(b_path, encoding="utf-8", errors="replace").read())
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]),
             min(len(a), len(b)))
    lo = max(0, i - window)
    cut = lambda s: (("..." if lo else "")
                     + s[lo:i + window].replace("\n", "\\n")
                     + ("..." if i + window < len(s) else ""))
    print(f"  {label}, first difference at byte {i}:")
    print(f"    HEAD:    {cut(a)}")
    print(f"    working: {cut(b)}")
    print()


def walk(root):
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = os.path.join(dirpath, n)
            yield os.path.relpath(p, root), p


def main():
    if not subprocess.run(["git", "-C", ROOT, "diff", "--quiet", "HEAD"]).returncode:
        print("the working tree is HEAD, so there is nothing to compare.\n"
              "Make a change first — this answers 'did what I just did move "
              "the site', not 'is the site correct'.")
        return 0

    tmp = tempfile.mkdtemp(prefix="golden-")
    try:
        old = build("HEAD", os.path.join(tmp, "head"), from_head=True)
        new = build("working tree", os.path.join(tmp, "work"), from_head=False)

        moved, missing, added, n = [], [], [], 0
        for tree in TREES:
            a_root, b_root = os.path.join(old, tree), os.path.join(new, tree)
            have = dict(walk(a_root)) if os.path.isdir(a_root) else {}
            got = dict(walk(b_root)) if os.path.isdir(b_root) else {}
            for rel in sorted(have):
                name = f"{tree}/{rel}"
                if not is_generated(name):
                    continue
                if rel not in got:
                    missing.append(name)
                    continue
                n += 1
                a = open(have[rel], "rb").read()
                b = open(got[rel], "rb").read()
                if a == b:
                    continue
                if (CLOCKS.sub("", a.decode("utf-8", "replace"))
                        != CLOCKS.sub("", b.decode("utf-8", "replace"))):
                    moved.append(name)
            added += [f"{tree}/{rel}" for rel in sorted(got)
                      if rel not in have and is_generated(f"{tree}/{rel}")]

        if not (moved or missing or added):
            print(f"golden: {n} generated files, HEAD and the working tree "
                  f"build identically")
            return 0

        print("this change moves the site.\n")
        for label, items in (("changed", moved), ("no longer built", missing),
                             ("newly built", added)):
            if items:
                print(f"  {label} ({len(items)}):")
                for f in items[:25]:
                    print(f"    {f}")
                if len(items) > 25:
                    print(f"    ... and {len(items) - 25} more")
                print()
        for f in moved[:3]:
            show(os.path.join(old, f), os.path.join(new, f), f)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

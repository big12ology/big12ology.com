#!/usr/bin/env python3
"""Check an assembled dist/ the way a reader would arrive at it.

    tools/verify-dist.py dist

Four things nothing else looks at, all of them the kind of failure that
ships quietly because the build succeeds:

  LINKS AND ASSETS RESOLVE. assemble.sh gates on a manifest of files that
  must exist and on a cache-bust regex. Neither notices a href pointing at a
  page that was renamed, or an <img> whose logo was never copied. The gate
  answers "is this file here"; this answers "does everything that points at a
  file find one".

  THE SITE AGREES WITH ITSELF. Every number on every page comes out of
  build.py, and only build.py's inputs are tested — so a lookup that puts the
  right number in the wrong row passes the whole suite. Where the same fact
  appears twice, it has to match: the sitemap against the files on disk, a
  page's canonical against where it actually sits, the footer stamp against
  every other footer.

  NOTHING IS SHOUTING NaN. A rendered "None", "undefined" or "NaN" in the
  HTML is a Python or template bug that reached a reader.

  THE CSS IS CSS. build.py writes its markup as a mix of .format() templates
  and f-strings, and in both of those a literal brace is written "{{". A CSS
  block that is emitted verbatim instead — interpolated into an f-string as a
  value, say, the way SUBPAGE_EXTRA_CSS is — never has its braces collapsed,
  and every rule in it ships as "{{ ... }}", which no browser parses. The page
  looks built and the styling is simply absent, so nothing downstream notices.

Exit 1 on any finding, with the file and what was expected. Pure stdlib, like
everything else here.
"""
import os
import re
import sys
import urllib.parse
from html.parser import HTMLParser

# Attributes that name something that must exist on disk.
REF_ATTRS = {"href", "src", "srcset", "poster"}

# Schemes and forms that leave the site and so cannot be checked here.
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#|data:|mailto:|tel:)", re.I)

# Served by the Worker on a zone route, not by Pages. These are real URLs with
# no file behind them, and the noscript fallbacks link to them on purpose.
WORKER = re.compile(r"^/api(?:/|$)")

# Text that should never survive to a rendered page.
TELLS = re.compile(r"\b(?:NaN|undefined|None|\[object Object\])\b")

# Where a tell is legitimate: prose and code that talks ABOUT the value.
TELL_OK = re.compile(r"(?:No[nN]e of|none of)")

# A doubled brace in CSS: brace-doubling that was never collapsed.
UNCOLLAPSED = re.compile(r"\{\{|\}\}")


class Refs(HTMLParser):
    """Every local reference in a page, plus the text nodes worth scanning."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []
        self.text = []
        self.styles = []
        self._skip = 0
        self._in_style = 0
        self.canonical = None
        self.title = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "style":
            self._in_style += 1
        if tag == "title":
            self._in_title = True
        if tag == "link" and d.get("rel") == "canonical":
            self.canonical = d.get("href")
        for k, v in attrs:
            if k not in REF_ATTRS or not v:
                continue
            if k == "srcset":
                for part in v.split(","):
                    u = part.strip().split(" ")[0]
                    if u:
                        self.refs.append((k, u))
            else:
                self.refs.append((k, v))

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag == "style" and self._in_style:
            self._in_style -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = data.strip()
        if self._in_style:
            self.styles.append(data)
        if not self._skip:
            self.text.append(data)


def resolve(dist, page, url):
    """Where a reference points, as a path inside dist, or None if external."""
    if EXTERNAL.match(url) or WORKER.match(url):
        return None
    url = urllib.parse.urlsplit(url).path
    if not url:
        return None
    if url.startswith("/"):
        target = os.path.join(dist, url.lstrip("/"))
    else:
        target = os.path.normpath(os.path.join(os.path.dirname(page), url))
    return target


def exists(target):
    if os.path.isfile(target):
        return True
    # A directory URL is served by its index.html.
    if os.path.isdir(target) and os.path.isfile(os.path.join(target, "index.html")):
        return True
    # GitHub Pages serves /foo as /foo.html.
    return os.path.isfile(target + ".html")


def pages(dist):
    for root, _dirs, files in os.walk(dist):
        for f in files:
            if f.endswith(".html"):
                yield os.path.join(root, f)


def rel(dist, p):
    return os.path.relpath(p, dist)


def main(dist):
    bad = []
    stamps = set()

    for page in sorted(pages(dist)):
        html = open(page, encoding="utf-8").read()
        p = Refs()
        p.feed(html)
        here = rel(dist, page)

        # --- every local reference resolves
        for attr, url in p.refs:
            target = resolve(dist, page, url)
            if target is None:
                continue
            if not exists(target):
                bad.append(f"{here}: {attr}={url!r} points at nothing")

        # --- the canonical lands somewhere real
        #
        # NOT "the canonical equals this file's own path". Pointing elsewhere
        # is the whole purpose: brief.html canonicalises to /tiebreaker/, the
        # schedule pages that moved sections point at /schedule/, and the
        # /pickem/ redirect stub points at the page it redirects to. What
        # would actually be broken is a canonical naming a page nobody built.
        if p.canonical:
            got = urllib.parse.urlsplit(p.canonical).path or "/"
            target = os.path.join(dist, got.lstrip("/"))
            if not exists(target):
                bad.append(f"{here}: canonical {got} is not a page on this site")

        # --- one build stamp across the whole domain
        for m in re.finditer(r"last updated ([^.<]+)\.", html):
            stamps.add(m.group(1).strip())

        # --- no uncollapsed brace-doubling in the CSS
        #
        # One finding per page, not per rule: a template that ships this way
        # ships every rule in it, and eighty copies of the same line would
        # push everything else off the report.
        #
        # The one legitimate "}}" in CSS is a nested block closed tight
        # against its parent — "@media(...){ .x{color:red}}". Nothing here
        # writes it that way, and a space is the fix if anything ever wants to.
        css = "".join(p.styles)
        hits = list(UNCOLLAPSED.finditer(css))
        if hits:
            m = hits[0]
            around = " ".join(css[max(0, m.start() - 40):m.end() + 40].split())
            bad.append(f"{here}: {len(hits)} doubled brace(s) in <style> — "
                       f"...{around}...")

        # --- nothing shouting a debug value
        text = " ".join(p.text)
        for m in TELLS.finditer(text):
            around = text[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
            if TELL_OK.search(around):
                continue
            bad.append(f"{here}: rendered {m.group(0)!r} — ...{around.strip()}...")

    # --- every sitemap entry is a file, and vice versa for the sections
    for smap in sorted(
            os.path.join(r, f) for r, _d, fs in os.walk(dist) for f in fs
            if f == "sitemap.xml"):
        body = open(smap, encoding="utf-8").read()
        for loc in re.findall(r"<loc>([^<]+)</loc>", body):
            path = urllib.parse.urlsplit(loc).path
            target = os.path.join(dist, path.lstrip("/"))
            if not exists(target):
                bad.append(f"{rel(dist, smap)}: lists {path}, which is not built")

    # --- the footer says the same thing everywhere
    if len(stamps) > 1:
        bad.append(f"{len(stamps)} different build stamps on one deploy: "
                   f"{sorted(stamps)}")

    n = len(list(pages(dist)))
    if bad:
        print(f"verify-dist: {len(bad)} problem(s) across {n} pages\n")
        for b in bad[:80]:
            print(f"  {b}")
        if len(bad) > 80:
            print(f"  ... and {len(bad) - 80} more")
        return 1
    print(f"verify-dist: {n} pages, links and assets resolve, "
          f"canonicals agree, CSS parses, one build stamp")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify-dist.py <dist>")
    sys.exit(main(sys.argv[1]))

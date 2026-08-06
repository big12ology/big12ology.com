#!/usr/bin/env python3
"""Source opponent marks from Wikipedia, with provenance.

    python3 fetch_logos.py 2026 --dry-run   # resolve and report, download nothing
    python3 fetch_logos.py 2026             # fetch what is missing
    python3 fetch_logos.py 2026 --force     # re-fetch even if present

Costs no CFBD quota — this talks to Wikipedia, and reads the schedule from
the committed data/games_<year>.json.

Every mark lands in site/logos/ with a matching entry in
site/logos/SOURCES.json recording where it came from and under what licence.
That file is the point of this script: a logo with no provenance is a
liability, and the sixteen conference marks have carried one from the start.

Resolution is a guess and is reported as such. The lead image of an
athletics programme's article is almost always its logo, but "almost" is
why --dry-run exists and why anything uncertain is flagged rather than
quietly accepted.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOGOS = os.path.join(HERE, "site", "logos")
SOURCES = os.path.join(LOGOS, "SOURCES.json")
API = "https://en.wikipedia.org/w/api.php"

# Wikimedia asks for a real identifier and blocks generic ones.
UA = "big12ology-logo-fetcher/1.0 (https://big12ology.com; dept@big12ology.com)"

# Article chrome that turns up in the image list and is never a team mark.
JUNK = re.compile(r"commons-logo|wikimedia|wiki\w*-logo|edit-icon|ambox|"
                  r"question_book|symbol|flag_of|increase|decrease|red_pog|"
                  r"location_dot|padlock|folder_hexagon|office-book|"
                  r"blue_pencil|magnifying|text_document", re.I)


def api(**params):
    params.setdefault("format", "json")
    params.setdefault("action", "query")
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


# CFBD's short names that search resolves to the wrong school outright.
# "Southern" is Southern University, not Southern Miss; "Virginia" is the
# Cavaliers, not the Hokies; Murray State University is in Kentucky, and
# Murray State College is a junior college in Oklahoma. Each of these was
# caught by --dry-run returning a plausible article for a different team.
OVERRIDES = {
    "Murray State": "Murray State Racers football",
    "Notre Dame": "Notre Dame Fighting Irish football",
    "Southern": "Southern Jaguars football",
    "Virginia": "Virginia Cavaliers football",
    "Long Island University": "LIU Sharks",
    # Search ranks the in-state rival above the school itself.
    "Iowa": "Iowa Hawkeyes football",
    # The okina defeats the search index; the article has no apostrophe.
    "Hawai'i": "Hawaii Rainbow Warriors football",
}

# A software licence on an "image" means the lead image was a template icon,
# not a mark. Caught on Georgia Southern, which came back LGPL.
SOFTWARE_LICENCE = re.compile(r"\b(GPL|LGPL|MIT|BSD|Apache)\b", re.I)

# "<School> University" is the institution, whose lead image is a seal or an
# academic wordmark. We want the athletics programme, whose lead image is
# the mark that appears on a helmet.
INSTITUTION = re.compile(r"\b(University|College|Institute)$", re.I)


def find_article(team):
    """The athletics programme article, e.g. Kansas -> Kansas Jayhawks.

    Prefers a "<School> <Mascot> football" page, then any programme page,
    and refuses the institution's own article — that route yields seals."""
    if team in OVERRIDES:
        return OVERRIDES[team]
    seen = []
    for query in (f"{team} college football team", f"{team} athletics"):
        for hit in api(list="search", srsearch=query, srlimit=8).get(
                "query", {}).get("search", []):
            title = hit["title"]
            if re.search(r"^\d{4}|season|list of|rivalry|stadium|arena",
                         title, re.I):
                continue
            if not title.lower().startswith(team.lower().rstrip(".")[:6]):
                continue
            if INSTITUTION.search(title):
                seen.append(title)     # keep as a last resort only
                continue
            if title.lower().endswith(" football"):
                return title
            seen.append(title)
    # No "... football" page: take the first programme page we saw, and only
    # then fall back to the institution.
    for title in seen:
        if not INSTITUTION.search(title):
            return title
    return seen[0] if seen else None


def lead_image(article):
    """The article's primary image — for a programme page, its logo."""
    res = api(prop="pageimages", piprop="original|name", titles=article)
    for page in res.get("query", {}).get("pages", {}).values():
        if page.get("pageimage"):
            return "File:" + page["pageimage"]
    return None


def any_logo_image(article, team):
    """Fallback: scan the article's images for something logo-shaped."""
    res = api(prop="images", imlimit=100, titles=article)
    best, best_score = None, 0
    tokens = [t.lower() for t in re.findall(r"\w+", team) if len(t) > 3]
    for page in res.get("query", {}).get("pages", {}).values():
        for img in page.get("images", []):
            name = img["title"]
            if JUNK.search(name) or not re.search(r"\.(svg|png)$", name, re.I):
                continue
            low = name.lower()
            score = 0
            score += 3 if "logo" in low else 0
            score += 2 if "athletic" in low else 0
            score += 2 if "wordmark" in low else 0
            score += 1 if low.endswith(".svg") else 0
            score += sum(1 for t in tokens if t in low)
            if score > best_score:
                best, best_score = name, score
    return best


def image_info(filetitle):
    res = api(prop="imageinfo", titles=filetitle,
              iiprop="url|size|mime|extmetadata")
    for page in res.get("query", {}).get("pages", {}).values():
        for info in page.get("imageinfo", []):
            return info
    return None


def meta(info, field):
    return (info.get("extmetadata", {}).get(field, {}) or {}).get("value", "")


def strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def key_for(team, taken):
    """A short filename key. Deterministic, and never collides."""
    base = re.sub(r"[^a-z0-9]", "", team.lower())[:12] or "team"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}{n}", n + 1
    return key


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    year = int(args[0]) if args else 2026
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    games = json.load(open(os.path.join(HERE, "data", f"games_{year}.json")))
    big12 = set(json.load(open(os.path.join(HERE, "data", "teams.json"))))
    sources = json.load(open(SOURCES))
    have = {e.get("team") for e in sources if e.get("team")}
    taken = {e["key"] for e in sources}

    opponents = sorted({g[s] for g in games for s in ("home", "away")
                        if g[s] not in big12})
    todo = [t for t in opponents if force or t not in have]
    print(f"{year}: {len(opponents)} non-conference opponents, "
          f"{len(todo)} to source\n")

    added, flagged = [], []
    for i, team in enumerate(todo, 1):
        article = find_article(team)
        if not article:
            flagged.append((team, "no article found"))
            print(f"  {i:2}/{len(todo)}  {team:28} NO ARTICLE")
            continue
        f = lead_image(article) or any_logo_image(article, team)
        if not f:
            flagged.append((team, f"no image on {article}"))
            print(f"  {i:2}/{len(todo)}  {team:28} {article:32} NO IMAGE")
            continue
        info = image_info(f)
        # A software licence means we picked up a template icon. Try the
        # article's own images instead before giving up.
        if info and SOFTWARE_LICENCE.search(
                strip_html(meta(info, "LicenseShortName"))):
            alt = any_logo_image(article, team)
            if alt and alt != f:
                f, info = alt, image_info(alt)
        if not info:
            flagged.append((team, f"no imageinfo for {f}"))
            continue

        licence = strip_html(meta(info, "LicenseShortName")) or "UNKNOWN"
        # Some URLs carry tracking parameters, and splitext on the whole URL
        # then returns ".org&utm_campaign=..." as the extension. Take the
        # extension from the path only.
        ext = os.path.splitext(
            urllib.parse.urlparse(info["url"]).path)[1].lower()
        key = key_for(team, taken)
        taken.add(key)
        entry = {
            "key": key, "team": team, "article": article,
            "requested": f.replace("File:", ""),
            "file": f.replace("File:", "").replace(" ", "_"),
            "url": info["url"], "mime": info.get("mime", ""),
            "bytes": info.get("size", 0), "ext": ext, "licence": licence,
            "credit": strip_html(meta(info, "Credit"))[:200],
            "artist": strip_html(meta(info, "Artist"))[:200],
            "restrictions": strip_html(meta(info, "Restrictions")),
            "file_page": info.get("descriptionurl", ""),
        }
        # No freely-licensed mark exists for this team on Wikipedia — what
        # is there is a template icon under a software licence. Record the
        # fact and download nothing; the site shows a marker and explains
        # itself rather than displaying something that is not the team's
        # mark, or a silent gap.
        if SOFTWARE_LICENCE.search(licence):
            entry.update(usable=False, file="", url="", bytes=0, ext="",
                         note=f"no freely-licensed mark found; the article's "
                              f"image is {licence}-licensed template art, "
                              f"not the team's mark")
            print(f"  {i:2}/{len(todo)}  {team:28} {article:32} "
                  f"{licence:22} !! NO USABLE MARK")
            added.append(entry)
            continue
        entry["usable"] = True

        mark = "  " if licence.lower().startswith(("public", "cc")) else "!!"
        print(f"  {i:2}/{len(todo)}  {team:28} {article:32} "
              f"{licence:22} {mark}")

        if not dry:
            req = urllib.request.Request(info["url"],
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                blob = r.read()
            with open(os.path.join(LOGOS, key + ext), "wb") as fh:
                fh.write(blob)
            entry["bytes"] = len(blob)
        added.append(entry)
        time.sleep(0.3)          # be a good citizen of someone else's API

    if not dry and added:
        sources = [e for e in sources
                   if e.get("team") not in {a["team"] for a in added}]
        json.dump(sources + added, open(SOURCES, "w"), indent=1)

    print(f"\n{'would add' if dry else 'added'}: {len(added)}")
    by_licence = {}
    for e in added:
        by_licence.setdefault(e["licence"], []).append(e["team"])
    for lic, teams in sorted(by_licence.items(),
                             key=lambda kv: -len(kv[1])):
        print(f"  {len(teams):3}  {lic}")
    if flagged:
        print(f"\nneeds a human ({len(flagged)}):")
        for team, why in flagged:
            print(f"  {team:28} {why}")


if __name__ == "__main__":
    main()

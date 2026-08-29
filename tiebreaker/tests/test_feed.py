#!/usr/bin/env python3
"""Feed invariants: valid XML, unique stable guids, correct items for a
finished season, sensible preseason feed, rebuild determinism."""
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import feed
from test_clinch import load, truncate

import build
import json


def check(cond, msg):
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def parse(xml):
    root = ET.fromstring(xml)
    items = root.findall("./channel/item")
    guids = [i.findtext("guid") for i in items]
    titles = [i.findtext("title") for i in items]
    return items, guids, titles


def main():
    ok = True
    systems = build.load_ratings(2026)["systems"]

    games = load()
    xml = feed.build_feed(games, 2025, systems)
    items, guids, titles = parse(xml)
    ok &= check(len(items) > 0, f"2025 feed has items ({len(items)})")
    ok &= check(len(items) <= feed.MAX_ITEMS, "item cap respected")
    ok &= check(len(guids) == len(set(guids)), "guids unique")
    ok &= check(any("clinches" in t for t in titles)
                or any(g.startswith("clinched-") for g in guids),
                "clinch items present for finished 2025")
    ok &= check(any(g.startswith("chaos-2025-week") for g in guids),
                "weekly chaos wraps present")
    ok &= check(any(t.startswith("Championship Final") for t in titles),
                "championship game item present")

    # mid-season snapshot: eliminated items exist, all dates parse
    snap = truncate(games, "2025-11-24")
    xml2 = feed.build_feed(snap, 2025, systems)
    items2, guids2, _ = parse(xml2)
    import email.utils
    dates = [email.utils.parsedate_to_datetime(i.findtext("pubDate"))
             for i in items2]
    ok &= check(all(d is not None for d in dates), "all pubDates parse")
    ok &= check(dates == sorted(dates, reverse=True), "items newest-first")
    ok &= check(any(g.startswith("eliminated-") for g in guids2),
                "elimination items present mid-season")
    # stability: mid-season guids are a subset of full-season game/wrap
    # guids (compare uncapped — the item cap evicts old finals by design)
    cap = feed.MAX_ITEMS
    feed.MAX_ITEMS = 10000
    _, guids_all, _ = parse(feed.build_feed(games, 2025, systems))
    feed.MAX_ITEMS = cap
    stable = {g for g in guids2 if g.startswith(("game-", "chaos-"))}
    full = {g for g in guids_all if g.startswith(("game-", "chaos-"))}
    ok &= check(stable <= full, "mid-season guids stable into full season")

    # determinism
    ok &= check(
        [i.findtext("guid") for i in parse(feed.build_feed(snap, 2025, systems))[0]]
        == guids2, "rebuild produces identical item set")

    # preseason 2026, TRUNCATED TO ONE. This read the live file and assumed
    # it was still preseason, which stopped being true the moment 2026 opened
    # in Dublin on August 29 -- the test then failed on the calendar rather
    # than on anything feed.py did. Cut back to before the opener, so what is
    # asserted is the preseason SHAPE and not the date the suite is run.
    g26 = truncate(
        json.load(open(os.path.join(ROOT, "data", "games_2026.json"))),
        "2026-08-01")
    xml3 = feed.build_feed(g26, 2026, systems)
    items3, guids3, _ = parse(xml3)
    ok &= check(len(items3) == 1 and guids3[0] == "preseason-2026",
                "preseason feed has the single welcome item")

    print("OK" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

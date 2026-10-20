"""The Big 12 imperialism map: who holds the country, game by game.

THE RULES, which are the r/CFB imperialism map's, narrowed to one conference.
Every county in the lower 48 starts with its nearest Big 12 school, measured
from the county's center to the school's home stadium. When two Big 12 teams
play, the winner takes everything the loser holds at that moment, not only
the loser's own land. A team that has lost all of it is still on the
schedule, and gets it all back the moment it beats whoever is holding it.

Only Big 12 against Big 12 moves territory. A loss to Notre Dame costs a team
nothing here, because nobody outside the conference holds land to win.

WHY THE COUNTRY MOVES IN SIXTEEN PIECES. A team's starting land is the only
land that is ever "its own"; everything else it holds, it won from someone
who held it as a block. So ownership changes only ever move whole starting
territories, and the map is sixteen fixed shapes with a color each. The
shapes are computed once from the county map and the replay is a list of
which school holds each one, week by week.

THE COUNTY MAP is us-atlas 3.0.1 (ISC, built from the US Census Bureau's
cartographic boundaries), committed in data/geo/ so a build never fetches it:

    counties-10m.json         longitude and latitude, for measuring distance
    counties-albers-10m.json  the same counties projected to Albers USA, for
                              drawing; equal-area, so pixel area is land area

The two share county ids (FIPS), which is the whole join. Alaska, Hawaii and
the territories are left out: the Albers file shrinks Alaska to fit an inset,
so its area would be a lie, and the classic map is the lower 48 anyway.
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GEO = os.path.join(HERE, "data", "geo")

# State FIPS prefixes that are not in the lower 48.
OUTSIDE = {"02", "15", "60", "66", "69", "72", "78"}


# --- TopoJSON -----------------------------------------------------------------

def _arcs(topo):
    """Every arc as absolute coordinates, undoing the delta quantization."""
    t = topo.get("transform")
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for p in arc:
            if t:
                x += p[0]
                y += p[1]
                pts.append((x * t["scale"][0] + t["translate"][0],
                            y * t["scale"][1] + t["translate"][1]))
            else:
                pts.append((p[0], p[1]))
        out.append(pts)
    return out


def _polygons(geom):
    """A geometry's polygons, each a list of rings, each a list of arc refs."""
    if geom["type"] == "Polygon":
        return [geom["arcs"]]
    if geom["type"] == "MultiPolygon":
        return geom["arcs"]
    return []


def _ring(refs, arcs):
    """One ring's points from its arc references (~i means arc i reversed)."""
    pts = []
    for r in refs:
        a = arcs[r] if r >= 0 else arcs[~r][::-1]
        pts.extend(a if not pts else a[1:])
    return pts


def _signed_area(pts):
    s = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        s += x0 * y1 - x1 * y0
    return s / 2


def _centroid(pts):
    """The area centroid of one ring, falling back to the mean of its points
    for a ring too thin to have an area worth dividing by."""
    a = _signed_area(pts)
    if abs(a) < 1e-12:
        n = len(pts)
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        f = x0 * y1 - x1 * y0
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    return (cx / (6 * a), cy / (6 * a))


def _county_shapes(topo):
    """{fips: (area, centroid)} for the lower-48 counties of one file.

    Area is the sum over the county's polygons of outer ring minus holes,
    whichever way round the file winds them; the centroid is the largest
    ring's, which keeps it inside counties that come in several pieces."""
    arcs = _arcs(topo)
    out = {}
    for g in topo["objects"]["counties"]["geometries"]:
        fips = str(g["id"]).zfill(5)
        if fips[:2] in OUTSIDE:
            continue
        area, best = 0.0, None
        for poly in _polygons(g):
            rings = [_ring(r, arcs) for r in poly]
            if not rings:
                continue
            signs = [_signed_area(r) for r in rings]
            area += abs(sum(signs))
            big = max(range(len(rings)), key=lambda i: abs(signs[i]))
            if best is None or abs(signs[big]) > best[0]:
                best = (abs(signs[big]), _centroid(rings[big]))
        if best:
            out[fips] = (area, best[1])
    return out


def _haversine(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _dissolve(polys, arcs):
    """The outline of a set of polygons, with every shared border removed.

    An arc used twice inside the set is a border between two of its counties
    and drops out; an arc used once is on the edge. The survivors are then
    walked end to start back into closed rings. This is topojson.merge's
    method, and it is why the data is TopoJSON: shared borders are the same
    arc, so finding them is counting, not geometry."""
    used = {}
    for poly in polys:
        for ring in poly:
            for r in ring:
                k = r if r >= 0 else ~r
                used[k] = used.get(k, 0) + 1
    edges = []
    for poly in polys:
        for ring in poly:
            for r in ring:
                if used[r if r >= 0 else ~r] == 1:
                    edges.append(arcs[r] if r >= 0 else arcs[~r][::-1])

    def key(p):
        return (round(p[0], 6), round(p[1], 6))

    starts = {}
    for i, e in enumerate(edges):
        starts.setdefault(key(e[0]), []).append(i)
    seen = [False] * len(edges)
    rings = []
    for i in range(len(edges)):
        if seen[i]:
            continue
        seen[i] = True
        ring = list(edges[i])
        while key(ring[-1]) != key(ring[0]):
            nxt = next((j for j in starts.get(key(ring[-1]), ())
                        if not seen[j]), None)
            if nxt is None:
                break
            seen[nxt] = True
            ring.extend(edges[nxt][1:])
        rings.append(ring)
    return rings


def _path(rings, nd=0):
    """SVG path data for closed rings, at `nd` decimal places of a pixel.

    Whole pixels by default. The map is drawn about as wide as its viewBox,
    so a unit is a pixel, and the tenths were half the page's weight."""
    f = f"{{:.{nd}f}}"
    out = []
    for ring in rings:
        if len(ring) < 3:
            continue
        pts = [(f.format(x), f.format(y)) for x, y in ring]
        # Consecutive points that round to the same pixel are dropped: at this
        # scale most of a county line is sub-pixel detail nobody can see.
        dedup = [pts[0]]
        for p in pts[1:]:
            if p != dedup[-1]:
                dedup.append(p)
        out.append("M" + "L".join(f"{x},{y}" for x, y in dedup) + "Z")
    return "".join(out)


# --- the sixteen territories ---------------------------------------------------

def home_stadiums(games, teams, venues):
    """{team: (lon, lat)}: each school's most-used home venue this season.

    From the schedule rather than a hand list, so a school that moves
    buildings moves its claim with it. Games the feed flags as neutral-site
    are a fallback, not ignored: in 2024 Kansas played every home game at
    Arrowhead while its own stadium was rebuilt, all of them flagged neutral,
    and the 2024 map had no Kansas at all. Kansas City was its home that
    year, so that is where its claim starts."""
    def tally(neutral):
        counts = {}
        for g in games:
            if (g["home"] in teams and bool(g.get("neutral_site")) == neutral
                    and g.get("venue_id")):
                c = counts.setdefault(g["home"], {})
                c[g["venue_id"]] = c.get(g["venue_id"], 0) + 1
        return counts

    counts = tally(False)
    for team, c in tally(True).items():
        counts.setdefault(team, c)
    out = {}
    for team, c in counts.items():
        vid = max(sorted(c), key=lambda k: c[k])
        v = venues.get(str(vid)) or {}
        if v.get("lat") is not None and v.get("lon") is not None:
            out[team] = (v["lon"], v["lat"])
    return out


def territories(stadiums):
    """The sixteen starting territories, ready to draw.

    {"viewBox": "x y w h",
     "states": svg path of state lines,
     "blocks": {team: {"d": svg path, "area": share of the lower 48 (0-1),
                       "counties": n, "label": [x, y], "home": [x, y]}}}
    """
    geo = json.load(open(os.path.join(GEO, "counties-10m.json")))
    alb = json.load(open(os.path.join(GEO, "counties-albers-10m.json")))
    geo_shapes = _county_shapes(geo)
    alb_shapes = _county_shapes(alb)

    teams = sorted(stadiums)
    owner = {}
    for fips, (_area, c) in geo_shapes.items():
        if fips in alb_shapes:
            owner[fips] = min(teams, key=lambda t: (_haversine(c, stadiums[t]), t))

    arcs = _arcs(alb)
    polys = {t: [] for t in teams}
    for g in alb["objects"]["counties"]["geometries"]:
        fips = str(g["id"]).zfill(5)
        if fips in owner:
            polys[owner[fips]].extend(_polygons(g))

    total = sum(alb_shapes[f][0] for f in owner)
    blocks = {}
    for t in teams:
        mine = [f for f in owner if owner[f] == t]
        area = sum(alb_shapes[f][0] for f in mine)
        # The label sits in the member county nearest the territory's
        # area-weighted middle, which keeps it inside even a crescent.
        mx = sum(alb_shapes[f][0] * alb_shapes[f][1][0] for f in mine) / area
        my = sum(alb_shapes[f][0] * alb_shapes[f][1][1] for f in mine) / area
        lab = min(mine, key=lambda f: (alb_shapes[f][1][0] - mx) ** 2
                  + (alb_shapes[f][1][1] - my) ** 2)
        # Home is the county the stadium stands in, as near as a county
        # center can say: the closest one, by the same measure as the claim.
        home = min(mine, key=lambda f: _haversine(geo_shapes[f][1], stadiums[t]))
        blocks[t] = {
            "d": _path(_dissolve(polys[t], arcs)),
            "area": round(area / total, 4),
            "counties": len(mine),
            "label": [round(v, 1) for v in alb_shapes[lab][1]],
            "home": [round(v, 1) for v in alb_shapes[home][1]],
        }

    # State lines, once each, for the lower 48 only.
    lines = set()
    for g in alb["objects"]["states"]["geometries"]:
        if str(g["id"]).zfill(2) in OUTSIDE:
            continue
        for poly in _polygons(g):
            for ring in poly:
                for r in ring:
                    lines.add(r if r >= 0 else ~r)
    def line(pts):
        out = []
        for x, y in pts:
            p = f"{x:.0f},{y:.0f}"
            if not out or out[-1] != p:
                out.append(p)
        return "M" + "L".join(out) if len(out) > 1 else ""

    states = "".join(line(arcs[i]) for i in sorted(lines))

    xs = [x for b in polys.values() for poly in b for ring in poly
          for x, _ in _ring(ring, arcs)]
    ys = [y for b in polys.values() for poly in b for ring in poly
          for _, y in _ring(ring, arcs)]
    pad = 6
    box = (math.floor(min(xs)) - pad, math.floor(min(ys)) - pad,
           math.ceil(max(xs) - min(xs)) + 2 * pad,
           math.ceil(max(ys) - min(ys)) + 2 * pad)
    return {"viewBox": " ".join(str(v) for v in box), "states": states,
            "blocks": blocks}


# --- the season ----------------------------------------------------------------

def _winner(g):
    hp, ap = g.get("home_points"), g.get("away_points")
    if hp is None or ap is None or hp == ap:
        return None
    return (g["home"], g["away"]) if hp > ap else (g["away"], g["home"])


def replay(games, teams):
    """The season, game by game: who held what after each week.

    Returns a list of frames, the first being the starting map:

        {"week": n or None, "owner": {block: team},
         "moves": [{"winner", "loser", "took": [blocks], "game": id}]}

    A game moves territory only when both sides are Big 12 teams and it has
    a final score. Games are applied in kickoff order inside a week, which
    matters: a team can win land on Thursday and lose all of it on Saturday.
    """
    # Sorted: callers pass a set, and a set of strings iterates in a
    # different order in every process (hash randomization), which made the
    # embedded map data differ between two builds of the same input.
    owner = {t: t for t in sorted(teams)}
    frames = [{"week": None, "owner": dict(owner), "moves": []}]
    played = sorted(
        (g for g in games
         if g["home"] in teams and g["away"] in teams and g.get("completed")
         and _winner(g)),
        key=lambda g: (g["week"], g.get("start") or "", g.get("id") or 0))
    by_week = {}
    for g in played:
        by_week.setdefault(g["week"], []).append(g)
    for week in sorted(by_week):
        moves = []
        for g in by_week[week]:
            win, lose = _winner(g)
            took = sorted(b for b, t in owner.items() if t == lose)
            for b in took:
                owner[b] = win
            moves.append({"winner": win, "loser": lose, "took": took,
                          "game": g.get("id"), "ccg": bool(g.get("ccg"))})
        frames.append({"week": week, "owner": dict(owner), "moves": moves})
    return frames


def standings(frame, blocks):
    """Each team's holdings in one frame, biggest empire first.

    [{"team", "blocks": [..], "area": share, "counties": n}], with teams that
    hold nothing at the end."""
    hold = {}
    for b, t in frame["owner"].items():
        hold.setdefault(t, []).append(b)
    rows = []
    for t in blocks:
        mine = sorted(hold.get(t, []))
        rows.append({"team": t, "blocks": mine,
                     "area": round(sum(blocks[b]["area"] for b in mine), 4),
                     "counties": sum(blocks[b]["counties"] for b in mine)})
    rows.sort(key=lambda r: (-r["area"], r["team"]))
    return rows

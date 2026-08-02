#!/usr/bin/env python3
"""Big 12 football tiebreaker engine.

Implements the official Big 12 tiebreaking procedures (2024 policy, 16 teams,
no round robin) over a season's game list as produced by fetch.py.

Every decision is returned with a human-readable log so the site can show
exactly which rule broke each tie.

Steps (identical lettering to the policy):
  Two-team:  a h2h, b common opponents, c next-highest-placed common opponent,
             d opponents' conference win% (SOS), e total wins (FCS cap),
             f SportSource Analytics rating, g coin toss.
  Multi-team: same ladder, except (a) is win% in games among the tied teams
             with the "defeated all others without playing all" removal rule,
             and after any team is seeded the survivors restart the procedure.

Steps f and g cannot be computed from public data; they read overrides.json
({"sportsource": {"Team": rank}, "coin_toss": ["WinnerA", ...]}). Without an
override the tie is reported unresolved at that step.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- basic records

def conf_games(games):
    return [g for g in games if g["conference_game"] and g["completed"]
            and g["home_points"] is not None and not g.get("ccg")]


def winner(g):
    if g["home_points"] == g["away_points"]:
        return None
    return g["home"] if g["home_points"] > g["away_points"] else g["away"]


def conf_records(games):
    """{team: [wins, losses]} over completed conference games."""
    rec = {}
    for g in conf_games(games):
        w = winner(g)
        for t in (g["home"], g["away"]):
            rec.setdefault(t, [0, 0])
        if w:
            rec[w][0] += 1
            loser = g["away"] if w == g["home"] else g["home"]
            rec[loser][1] += 1
    return rec


def pct(w, l):
    return w / (w + l) if (w + l) else None


def team_pct(rec, t):
    w, l = rec.get(t, [0, 0])
    return pct(w, l)


def opponents(team, games):
    """Conference opponents this team has played (completed games)."""
    out = set()
    for g in conf_games(games):
        if g["home"] == team:
            out.add(g["away"])
        elif g["away"] == team:
            out.add(g["home"])
    return out


def record_vs(team, others, games):
    """(wins, losses) for team against a set of opponents, conference games."""
    w = l = 0
    for g in conf_games(games):
        if g["home"] == team and g["away"] in others:
            other = g["away"]
        elif g["away"] == team and g["home"] in others:
            other = g["home"]
        else:
            continue
        if winner(g) == team:
            w += 1
        elif winner(g) == other:
            l += 1
    return w, l


# ------------------------------------------------------------- standings groups

def placement_groups(games):
    """Standings as a list of groups, each a list of teams sharing a conference
    win%. Groups ordered best to worst. Used for step (c)'s 'proceeding through
    the standings' and its collective-group rule."""
    rec = conf_records(games)
    by_pct = {}
    for t, (w, l) in rec.items():
        by_pct.setdefault(pct(w, l) if (w + l) else -1, []).append(t)
    return [sorted(by_pct[p]) for p in sorted(by_pct, reverse=True)]


# ------------------------------------------------------------- individual steps

def fmt_pct(p):
    return "—" if p is None else f"{p:.3f}"


def best_unique(vals, higher_better=True):
    """vals: {team: value or None}. Returns team if exactly one team has the
    strictly best non-None value AND every team has a value, else None.
    (A metric with missing entries can't fairly separate anyone.)"""
    if any(v is None for v in vals.values()):
        return None
    best = max(vals.values()) if higher_better else min(vals.values())
    leaders = [t for t, v in vals.items() if v == best]
    return leaders[0] if len(leaders) == 1 else None


def step_h2h(tied, games, log):
    """Two-team step (a)."""
    a, b = tied
    w, l = record_vs(a, {b}, games)
    if w > l:
        log.append(f"(a) Head-to-head: {a} defeated {b}.")
        return a
    if l > w:
        log.append(f"(a) Head-to-head: {b} defeated {a}.")
        return b
    log.append(f"(a) Head-to-head: {a} and {b} did not play.")
    return None


def step_among_tied(tied, games, log):
    """Multi-team step (a): win% in games among the tied teams, with the
    incomplete-round-robin sub-rules."""
    pairs_played = {t: set() for t in tied}
    for g in conf_games(games):
        if g["home"] in tied and g["away"] in tied:
            pairs_played[g["home"]].add(g["away"])
            pairs_played[g["away"]].add(g["home"])
    full_round_robin = all(pairs_played[t] == set(tied) - {t} for t in tied)

    if not full_round_robin:
        # sub-rule 1: a team that defeated every other tied team is seeded
        for t in tied:
            others = set(tied) - {t}
            w, l = record_vs(t, others, games)
            if w == len(others) and l == 0:
                log.append(f"(a) Not all tied teams played each other, but "
                           f"{t} defeated every other tied team — seeded.")
                return t
        log.append("(a) Not all tied teams played each other and no team "
                   "defeated all others — proceed to next step.")
        return None

    vals = {}
    for t in tied:
        w, l = record_vs(t, set(tied) - {t}, games)
        vals[t] = pct(w, l)
    detail = ", ".join(f"{t} {fmt_pct(vals[t])}" for t in sorted(tied))
    win = best_unique(vals)
    if win:
        log.append(f"(a) Record among tied teams: {detail} — {win} seeded.")
    else:
        log.append(f"(a) Record among tied teams: {detail} — no single leader.")
    return win


def step_common_opponents(tied, games, log):
    """Step (b): win% vs conference opponents common to all tied teams."""
    common = None
    for t in tied:
        opp = opponents(t, games) - set(tied)
        common = opp if common is None else common & opp
    if not common:
        log.append("(b) Common conference opponents: none — proceed.")
        return None
    vals = {}
    for t in tied:
        w, l = record_vs(t, common, games)
        vals[t] = pct(w, l)
    detail = ", ".join(f"{t} {fmt_pct(vals[t])}" for t in sorted(tied))
    win = best_unique(vals)
    names = ", ".join(sorted(common))
    if win:
        log.append(f"(b) vs common opponents ({names}): {detail} — {win} seeded.")
    else:
        log.append(f"(b) vs common opponents ({names}): {detail} — still tied.")
    return win


def step_next_highest_common(tied, games, log):
    """Step (c): walk the standings top to bottom; at each placement group that
    every tied team has played (at least one game against, for tied groups —
    exactly the common opponent, for solo slots), compare win% vs that
    group collectively. Skip the tied teams' own group."""
    groups = placement_groups(games)
    for grp in groups:
        grp_set = set(grp) - set(tied)
        if not grp_set:
            continue
        # every tied team must have at least one game vs the group ("common")
        recs = {t: record_vs(t, grp_set, games) for t in tied}
        if any(w + l == 0 for w, l in recs.values()):
            continue
        vals = {t: pct(*recs[t]) for t in tied}
        if len(set(vals.values())) == 1:
            continue  # identical — keep walking down the standings
        detail = ", ".join(
            f"{t} {recs[t][0]}-{recs[t][1]}" for t in sorted(tied))
        win = best_unique(vals)
        names = "/".join(sorted(grp_set))
        if win:
            log.append(f"(c) vs next-highest-placed common opponent group "
                       f"[{names}]: {detail} — {win} seeded.")
            return win
        log.append(f"(c) vs [{names}]: {detail} — separates some but no "
                   f"single leader; continuing down the standings.")
    log.append("(c) Walked full standings without a single leader — proceed.")
    return None


def step_sos(tied, games, log):
    """Step (d): combined conference win% of each team's conference opponents
    (schedule-weighted: an opponent played counts its full conference record;
    repeat opponents would count twice, which cannot happen in Big 12 play)."""
    rec = conf_records(games)
    vals, detail_parts = {}, []
    for t in sorted(tied):
        w = l = 0
        for opp in opponents(t, games):
            ow, ol = rec.get(opp, [0, 0])
            w, l = w + ow, l + ol
        vals[t] = pct(w, l)
        detail_parts.append(f"{t} {w}-{l} ({fmt_pct(vals[t])})")
    detail = ", ".join(detail_parts)
    win = best_unique(vals)
    if win:
        log.append(f"(d) Opponents' combined conference record: {detail} — "
                   f"{win} seeded.")
    else:
        log.append(f"(d) Opponents' combined conference record: {detail} — "
                   f"still tied.")
    return win


def step_total_wins(tied, games, log):
    """Step (e): total wins in the 12-game season; at most one win over an
    FCS/lower-division team counts."""
    vals = {}
    for t in tied:
        wins = fcs_wins = 0
        for g in games:
            if not g["completed"] or g.get("ccg") or winner(g) != t:
                continue
            other_class = g["away_class"] if g["home"] == t else g["home_class"]
            if g["home"] != t and g["away"] != t:
                continue
            if other_class and other_class != "fbs":
                fcs_wins += 1
            else:
                wins += 1
        vals[t] = wins + min(fcs_wins, 1)
    detail = ", ".join(f"{t} {vals[t]}" for t in sorted(tied))
    win = best_unique(vals)
    if win:
        log.append(f"(e) Total wins (max one FCS win): {detail} — {win} seeded.")
    else:
        log.append(f"(e) Total wins (max one FCS win): {detail} — still tied.")
    return win


def step_sportsource(tied, overrides, log):
    ranks = (overrides or {}).get("sportsource", {})
    vals = {t: ranks.get(t) for t in tied}
    if any(v is None for v in vals.values()):
        log.append("(f) SportSource Analytics rating: not available — "
                   "cannot be resolved automatically.")
        return None
    win = best_unique(vals, higher_better=False)
    detail = ", ".join(f"{t} #{vals[t]}" for t in sorted(tied))
    if win:
        log.append(f"(f) SportSource Analytics ranking: {detail} — {win} seeded.")
    else:
        log.append(f"(f) SportSource Analytics ranking: {detail} — still tied.")
    return win


def step_coin_toss(tied, overrides, log):
    order = (overrides or {}).get("coin_toss", [])
    for t in order:
        if t in tied:
            log.append(f"(g) Coin toss: {t} won the toss.")
            return t
    log.append("(g) Coin toss required — awaiting result.")
    return None


# ------------------------------------------------------------------ tie breaking

def break_tie(tied, games, overrides=None):
    """Order a group of tied teams per the official procedure.

    Returns (ordered_teams, log, resolved). If a tie bottoms out at an
    unavailable step (f/g without overrides) the remaining teams are appended
    in alphabetical order and resolved=False.
    """
    tied = sorted(tied)
    log = []
    order = []
    remaining = list(tied)

    while len(remaining) > 1:
        n0 = len(remaining)
        if len(remaining) == 2:
            seeded = _run_two_team(remaining, games, overrides, log)
        else:
            seeded = _run_multi_team(remaining, games, overrides, log)
        if seeded is None:
            log.append(f"UNRESOLVED: {', '.join(remaining)} cannot be "
                       f"separated with available data.")
            order.extend(remaining)
            return order, log, False
        order.append(seeded)
        remaining.remove(seeded)
        if len(remaining) > 1 and n0 > 2:
            log.append(f"Restarting procedure for remaining tied teams: "
                       f"{', '.join(remaining)}.")
    order.extend(remaining)
    return order, log, True


def _run_two_team(tied, games, overrides, log):
    for step in (
        lambda: step_h2h(tied, games, log),
        lambda: step_common_opponents(tied, games, log),
        lambda: step_next_highest_common(tied, games, log),
        lambda: step_sos(tied, games, log),
        lambda: step_total_wins(tied, games, log),
        lambda: step_sportsource(tied, overrides, log),
        lambda: step_coin_toss(tied, overrides, log),
    ):
        win = step()
        if win:
            return win
    return None


def _run_multi_team(tied, games, overrides, log):
    for step in (
        lambda: step_among_tied(tied, games, log),
        lambda: step_common_opponents(tied, games, log),
        lambda: step_next_highest_common(tied, games, log),
        lambda: step_sos(tied, games, log),
        lambda: step_total_wins(tied, games, log),
        lambda: step_sportsource(tied, overrides, log),
        lambda: step_coin_toss(tied, overrides, log),
    ):
        win = step()
        if win:
            return win
    return None


# --------------------------------------------------------------------- standings

def standings(games, overrides=None):
    """Full tiebroken standings. Returns list of dicts:
    {rank, team, conf_w, conf_l, overall_w, overall_l, tie_group, log,
     resolved}. Official policy governs seeds 1-2; lower placements use the
    same procedure for display purposes."""
    rec = conf_records(games)
    if not rec:
        return []
    overall = {}
    for g in games:
        if not g["completed"]:
            continue
        w = winner(g)
        for t in (g["home"], g["away"]):
            if t in rec:
                overall.setdefault(t, [0, 0])
        if w and w in rec:
            overall[w][0] += 1
        if w:
            loser = g["away"] if w == g["home"] else g["home"]
            if loser in rec:
                overall[loser][1] += 1

    rows = []
    rank = 1
    for grp in placement_groups(games):
        if len(grp) == 1:
            ordered, log, resolved, tie_id = grp, None, True, None
        else:
            ordered, log, resolved = break_tie(grp, games, overrides)
            tie_id = "+".join(sorted(grp))
        for i, t in enumerate(ordered):
            w, l = rec[t]
            ow, ol = overall.get(t, [0, 0])
            rows.append({
                "rank": rank + i, "team": t,
                "conf_w": w, "conf_l": l,
                "overall_w": ow, "overall_l": ol,
                "tie_group": tie_id,
                "log": log if i == 0 else None,  # attach log once per group
                "resolved": resolved,
            })
        rank += len(ordered)
    return rows


def championship(games, overrides=None):
    """The projected/final CCG matchup with special first-place handling:
    if exactly two teams tie for first and they played, both are in and the
    h2h winner is the #1 seed (no further steps needed)."""
    rows = standings(games, overrides)
    if len(rows) < 2:
        return None
    note = None
    groups = placement_groups(games)
    top = groups[0]
    if len(top) == 2:
        a, b = top
        w, _ = record_vs(a, {b}, games)
        l, _ = record_vs(b, {a}, games)
        if w or l:
            one = a if w else b
            two = b if w else a
            note = (f"Two teams tied for first: both play in the championship "
                    f"game; {one} is the #1 seed by head-to-head win.")
            return {"seed1": one, "seed2": two, "note": note,
                    "resolved": True}
    return {"seed1": rows[0]["team"], "seed2": rows[1]["team"], "note": note,
            "resolved": rows[0]["resolved"] and rows[1]["resolved"]}


def load_overrides():
    p = os.path.join(HERE, "overrides.json")
    if os.path.exists(p):
        return json.load(open(p))
    return {}

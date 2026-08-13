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

A group only ever shrinks from the TOP. A step that separates the group
without producing a single leader does NOT drop the trailing teams: the whole
group carries to the next step. The policy supports this literally ("After one
team has an advantage and is 'seeded', all remaining teams ... repeat the
tie-breaking procedure" — no elimination-from-below clause), and 2024 proves
it empirically. Arizona State, BYU, Colorado and Iowa State tied at 7-2; at
step (b) BYU and Iowa State led at .800 with Colorado at .600. Bouncing
Colorado there would send BYU to the championship game on step (c). The
conference sent Iowa State, which is what this ladder produces by carrying all
three to step (d). Do not "fix" this without re-reading that season.

Steps f and g cannot be computed from public data; they read overrides.json
({"sportsource": {"Team": rank}, "coin_toss": ["WinnerA", ...]}). Without an
override the tie is reported unresolved at that step.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- basic records

def has_score(g):
    """Both scores are present, which is not the same thing as the game being
    over.

    The obvious check is `g["home_points"] is not None`, and it is the one
    this build used nearly everywhere. It is wrong in a way that only shows up
    on a Saturday afternoon. The feed does not fill the two score fields in a
    single write: a row can arrive carrying an int in home_points and a null
    in away_points, for the seconds between the provider posting one side and
    the other, and for as long as a stalled or reverted update leaves it that
    way. Every caller of this idea compares the two numbers on the very next
    line, so the half-filled row does not quietly produce a wrong winner —
    it raises TypeError and takes the whole build down with it.

    Checking both fields is therefore not defensive padding. `completed` is
    the provider's opinion about the game; these two fields are the data the
    arithmetic actually needs, and only the second thing can decide whether
    the arithmetic is safe to run at all. Callers still test `completed`
    separately where they care about the distinction, because a game can carry
    scores it is not finished with.

    `.get()` rather than subscripting, because the history files under
    data/ predate some of the keys fetch.py writes today, and a caller reading
    an old season should get "no result here" rather than a KeyError.
    """
    return (g.get("home_points") is not None
            and g.get("away_points") is not None)


def conf_games(games):
    """The completed conference games, which is nearly every question here.

    THREADED RATHER THAN MEMOISED, and the `cg` argument that now runs
    through this module is why. This filter is O(the whole season) and it was
    being run from the bottom of a very deep loop: clinch.exact enumerates
    every completion of the remaining schedule, each one calls cut_membership,
    and each of those walks a tie procedure whose every step re-derived this
    list from scratch. A profile of one build counted 2.1 million calls
    costing 15 seconds of a 46-second run — more than every Monte Carlo
    simulation on the site put together.

    A cache is the obvious fix and the wrong one: clinch._apply and
    odds.simulate both mutate the games in place and restore them, so a memo
    keyed on the list would answer with the previous combination's results
    and there is no cheap key that notices. So the callers that know the
    games are not changing under them filter once and pass `cg` down. Every
    signature keeps its old shape with cg=None meaning "derive it", so
    nothing outside this module has to know.
    """
    return [g for g in games if g["conference_game"] and g["completed"]
            and has_score(g) and not g.get("ccg")]


def winner(g):
    if g["home_points"] == g["away_points"]:
        return None
    return g["home"] if g["home_points"] > g["away_points"] else g["away"]


def conf_records(games, cg=None):
    """{team: [wins, losses]} over completed conference games."""
    rec = {}
    for g in (conf_games(games) if cg is None else cg):
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


def opponents(team, games, cg=None):
    """Conference opponents this team has played (completed games)."""
    out = set()
    for g in (conf_games(games) if cg is None else cg):
        if g["home"] == team:
            out.add(g["away"])
        elif g["away"] == team:
            out.add(g["home"])
    return out


def record_vs(team, others, games, cg=None):
    """(wins, losses) for team against a set of opponents, conference games."""
    w = l = 0
    for g in (conf_games(games) if cg is None else cg):
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

def placement_groups(games, cg=None):
    """Standings as a list of groups, each a list of teams sharing a conference
    win%. Groups ordered best to worst. Used for step (c)'s 'proceeding through
    the standings' and its collective-group rule."""
    rec = conf_records(games, cg)
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


def step_h2h(tied, games, log, cg=None):
    """Two-team step (a)."""
    a, b = tied
    w, l = record_vs(a, {b}, games, cg)
    if w > l:
        log.append(f"(a) Head-to-head: {a} defeated {b}.")
        return a
    if l > w:
        log.append(f"(a) Head-to-head: {b} defeated {a}.")
        return b
    log.append(f"(a) Head-to-head: {a} and {b} did not play.")
    return None


def step_among_tied(tied, games, log, cg=None):
    """Multi-team step (a): win% in games among the tied teams, with the
    incomplete-round-robin sub-rules."""
    pairs_played = {t: set() for t in tied}
    for g in (conf_games(games) if cg is None else cg):
        if g["home"] in tied and g["away"] in tied:
            pairs_played[g["home"]].add(g["away"])
            pairs_played[g["away"]].add(g["home"])
    full_round_robin = all(pairs_played[t] == set(tied) - {t} for t in tied)

    if not full_round_robin:
        # sub-rule 1: a team that defeated every other tied team is seeded
        for t in tied:
            others = set(tied) - {t}
            w, l = record_vs(t, others, games, cg)
            if w == len(others) and l == 0:
                log.append(f"(a) Not all tied teams played each other, but "
                           f"{t} defeated every other tied team — seeded.")
                return t
        log.append("(a) Not all tied teams played each other and no team "
                   "defeated all others — proceed to next step.")
        return None

    vals = {}
    for t in tied:
        w, l = record_vs(t, set(tied) - {t}, games, cg)
        vals[t] = pct(w, l)
    detail = ", ".join(f"{t} {fmt_pct(vals[t])}" for t in sorted(tied))
    win = best_unique(vals)
    if win:
        log.append(f"(a) Record among tied teams: {detail} — {win} seeded.")
    else:
        log.append(f"(a) Record among tied teams: {detail} — no single leader.")
    return win


def step_common_opponents(tied, games, log, cg=None):
    """Step (b): win% vs conference opponents common to all tied teams."""
    common = None
    for t in tied:
        opp = opponents(t, games, cg) - set(tied)
        common = opp if common is None else common & opp
    if not common:
        log.append("(b) Common conference opponents: none — proceed.")
        return None
    vals = {}
    for t in tied:
        w, l = record_vs(t, common, games, cg)
        vals[t] = pct(w, l)
    detail = ", ".join(f"{t} {fmt_pct(vals[t])}" for t in sorted(tied))
    win = best_unique(vals)
    names = ", ".join(sorted(common))
    if win:
        log.append(f"(b) vs common opponents ({names}): {detail} — {win} seeded.")
    else:
        log.append(f"(b) vs common opponents ({names}): {detail} — still tied.")
    return win


def step_next_highest_common(tied, games, log, cg=None):
    """Step (c): walk the standings top to bottom; at each placement group that
    every tied team has played (at least one game against, for tied groups —
    exactly the common opponent, for solo slots), compare win% vs that
    group collectively. Skip the tied teams' own group."""
    groups = placement_groups(games, cg)
    for grp in groups:
        grp_set = set(grp) - set(tied)
        if not grp_set:
            continue
        # every tied team must have at least one game vs the group ("common")
        recs = {t: record_vs(t, grp_set, games, cg) for t in tied}
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


def step_sos(tied, games, log, cg=None):
    """Step (d): combined conference win% of each team's conference opponents
    (schedule-weighted: an opponent played counts its full conference record;
    repeat opponents would count twice, which cannot happen in Big 12 play)."""
    rec = conf_records(games, cg)
    vals, detail_parts = {}, []
    for t in sorted(tied):
        w = l = 0
        for opp in opponents(t, games, cg):
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

def break_tie(tied, games, overrides=None, cg=None):
    """Order a group of tied teams per the official procedure.

    Returns (ordered_teams, log, resolved, events). events records which step
    seeded each team: [{"team", "step", "line"}] — teams seeded by
    elimination (the last one standing) have no event. If a tie bottoms out
    at an unavailable step (f/g without overrides) the remaining teams are
    appended in alphabetical order and resolved=False.
    """
    tied = sorted(tied)
    # Once, here, rather than once per step per elimination round. Callers
    # already holding the filtered list pass it in; clinch.cut_membership is
    # the one that matters, being the floor of the enumeration loop.
    if cg is None:
        cg = conf_games(games)
    log = []
    order = []
    events = []
    remaining = list(tied)

    while len(remaining) > 1:
        n0 = len(remaining)
        if len(remaining) == 2:
            seeded = _run_two_team(remaining, games, overrides, log, cg)
        else:
            seeded = _run_multi_team(remaining, games, overrides, log, cg)
        if seeded is None:
            log.append(f"UNRESOLVED: {', '.join(remaining)} cannot be "
                       f"separated with available data.")
            order.extend(remaining)
            return order, log, False, events
        line = log[-1]
        step = line[1] if line.startswith("(") else None
        events.append({"team": seeded, "step": step, "line": line})
        order.append(seeded)
        remaining.remove(seeded)
        if len(remaining) > 1 and n0 > 2:
            log.append(f"Restarting procedure for remaining tied teams: "
                       f"{', '.join(remaining)}.")
    order.extend(remaining)
    return order, log, True, events


def _run_two_team(tied, games, overrides, log, cg=None):
    for step in (
        lambda: step_h2h(tied, games, log, cg),
        lambda: step_common_opponents(tied, games, log, cg),
        lambda: step_next_highest_common(tied, games, log, cg),
        lambda: step_sos(tied, games, log, cg),
        # Step (e) counts the whole twelve-game season, non-conference and
        # all, so it is the one step that must have the unfiltered list.
        lambda: step_total_wins(tied, games, log),
        lambda: step_sportsource(tied, overrides, log),
        lambda: step_coin_toss(tied, overrides, log),
    ):
        win = step()
        if win:
            return win
    return None


def _run_multi_team(tied, games, overrides, log, cg=None):
    for step in (
        lambda: step_among_tied(tied, games, log, cg),
        lambda: step_common_opponents(tied, games, log, cg),
        lambda: step_next_highest_common(tied, games, log, cg),
        lambda: step_sos(tied, games, log, cg),
        # See _run_two_team: (e) is a whole-season step.
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
    cg = conf_games(games)
    rec = conf_records(games, cg)
    if not rec:
        return []
    overall = {}
    nonconf = {}
    for g in games:
        if not g["completed"]:
            continue
        w = winner(g)
        for t in (g["home"], g["away"]):
            if t in rec:
                overall.setdefault(t, [0, 0])
                nonconf.setdefault(t, [0, 0])
        if w and w in rec:
            overall[w][0] += 1
            if not g["conference_game"] and not g.get("ccg"):
                nonconf[w][0] += 1
        if w:
            loser = g["away"] if w == g["home"] else g["home"]
            if loser in rec:
                overall[loser][1] += 1
                if not g["conference_game"] and not g.get("ccg"):
                    nonconf[loser][1] += 1

    rows = []
    rank = 1
    for grp in placement_groups(games, cg):
        if len(grp) == 1:
            ordered, log, resolved, tie_id, events = grp, None, True, None, None
        else:
            ordered, log, resolved, events = break_tie(grp, games, overrides, cg)
            tie_id = "+".join(sorted(grp))
        for i, t in enumerate(ordered):
            w, l = rec[t]
            ow, ol = overall.get(t, [0, 0])
            nw, nl = nonconf.get(t, [0, 0])
            rows.append({
                "rank": rank + i, "team": t,
                "conf_w": w, "conf_l": l,
                "nonconf_w": nw, "nonconf_l": nl,
                "overall_w": ow, "overall_l": ol,
                "tie_group": tie_id,
                "log": log if i == 0 else None,  # attach log once per group
                "events": events if i == 0 else None,
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
    cg = conf_games(games)
    groups = placement_groups(games, cg)
    top = groups[0]
    if len(top) == 2:
        a, b = top
        w, _ = record_vs(a, {b}, games, cg)
        l, _ = record_vs(b, {a}, games, cg)
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

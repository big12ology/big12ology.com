#!/usr/bin/env python3
"""Clinching and elimination analysis for the Big 12 championship game.

Every status this module reports is a *proof*, not an estimate:

- Bounds phase (any point in the season, O(n)): win-count arithmetic with
  strict inequalities, so no tiebreaker can invalidate a claim.
    clinched    at most one other team can even reach this team's current
                win total -> strictly ahead of >= 14 teams in all outcomes.
    eliminated  at least two teams are already past this team's ceiling.
    destiny     winning out puts the team strictly ahead of all but at most
                one team -> in the championship game with no tiebreaker help.

- Exact phase (late season): when the number of remaining conference games
  r satisfies 2^r <= budget, enumerate every completion and evaluate top-2
  membership with the real tiebreaker engine. A team is clinched iff it is
  *surely* in under every completion, eliminated iff in none. Uncertainty
  counts against certainty: outcomes whose cut membership depends on
  unresolved steps (SportSource / coin toss) or on the total-wins step while
  the tied teams still have unplayed non-conference games are treated as
  "maybe" — they block clinch proofs and block elimination proofs alike.

The exact phase also emits this-week clinch scenarios ("clinches with a win
plus a Utah loss") by grouping completions by the upcoming week's outcomes.
"""
import itertools

import tiebreaker as tb

EXACT_BUDGET = 1 << 18   # max completions to enumerate exhaustively (~80s;
                         # engages with ~18 conf games left, mid-November)
SCENARIO_MAX_TERMS = 3   # longest "A over B + C over D + ..." conjunction


# ------------------------------------------------------------------ inventory

def conf_teams(games):
    """All 16 teams, from the schedule (records may not exist yet)."""
    out = set()
    for g in games:
        if g["conference_game"] and not g.get("ccg"):
            out.add(g["home"])
            out.add(g["away"])
    return sorted(out)


def remaining_conf(games):
    return [g for g in games if g["conference_game"] and not g.get("ccg")
            and not g["completed"]]


def unplayed_nonconf_teams(games):
    """Teams that still have unplayed non-conference games (step-e hazard)."""
    out = set()
    for g in games:
        if g["completed"] or g.get("ccg") or g["conference_game"]:
            continue
        out.add(g["home"])
        out.add(g["away"])
    return out


# ----------------------------------------------------------------- membership

def cut_membership(games, overrides, ncf_teams):
    """(sure_in, maybe_in) team sets for the top-2 cut of a *finished*
    (or hypothetically finished) conference season.

    Only the placement group that straddles the cut needs tie-breaking;
    groups fully above the cut are in, fully below are out."""
    groups = tb.placement_groups(games)
    sure, maybe = set(), set()
    seats = 2
    for grp in groups:
        if seats <= 0:
            break
        if len(grp) <= seats:
            sure.update(grp)
            seats -= len(grp)
            continue
        # straddling group: tie order decides which members cross
        order, _log, resolved, events = tb.break_tie(grp, games, overrides)
        risky = (not resolved) or any(
            e["step"] in ("e", "f", "g") and
            any(t in ncf_teams for t in grp)
            for e in events[:seats])
        if risky:
            maybe.update(grp)
        else:
            sure.update(order[:seats])
        seats = 0
    return sure, maybe


# --------------------------------------------------------------------- bounds

def bounds(games):
    """{team: {"w", "r", "max_w", "clinched", "eliminated", "destiny"}}"""
    teams = conf_teams(games)
    rec = tb.conf_records(games)
    rem = {t: 0 for t in teams}
    for g in remaining_conf(games):
        rem[g["home"]] += 1
        rem[g["away"]] += 1
    out = {}
    for t in teams:
        w = rec.get(t, [0, 0])[0]
        out[t] = {"w": w, "r": rem[t], "max_w": w + rem[t]}
    for t in teams:
        me = out[t]
        can_reach_now = sum(1 for x in teams if x != t
                            and out[x]["max_w"] >= me["w"])
        ahead_for_good = sum(1 for x in teams if x != t
                             and out[x]["w"] > me["max_w"])
        can_reach_ceiling = sum(1 for x in teams if x != t
                                and out[x]["max_w"] >= me["max_w"])
        me["clinched"] = can_reach_now <= 1
        me["eliminated"] = ahead_for_good >= 2
        me["destiny"] = (not me["clinched"] and me["r"] > 0
                         and can_reach_ceiling <= 1)
    return out


# ---------------------------------------------------------------- exact phase

def _apply(combo, rem_games):
    """Set winners on the shared remaining-game dicts (mutation is reverted
    by the caller via _clear)."""
    for g, home_wins in zip(rem_games, combo):
        g["completed"] = True
        if home_wins:
            g["home_points"], g["away_points"] = 28, 17
        else:
            g["home_points"], g["away_points"] = 17, 28


def _clear(rem_games):
    for g in rem_games:
        g["completed"] = False
        g["home_points"] = g["away_points"] = None


def exact(games, overrides=None, budget=EXACT_BUDGET):
    """Enumerate every completion of the remaining conference schedule.

    Returns None when over budget, else:
      {"teams": {team: {"always_in", "ever_in"}},
       "week": upcoming week number or None,
       "week_games": [(home, away), ...],
       "clinch_combos": {team: set of week-combo tuples},
       "n_outcomes": int}
    """
    rem = remaining_conf(games)
    if len(rem) > 30 or (1 << len(rem)) > budget:
        return None
    ncf = unplayed_nonconf_teams(games)
    teams = conf_teams(games)
    # work on copies so callers' data is never mutated
    base = [dict(g) for g in games]
    rem_work = [g for g in base
                if g["conference_game"] and not g.get("ccg")
                and not g["completed"]]
    week = min((g["week"] for g in rem_work), default=None)
    wk_idx = [i for i, g in enumerate(rem_work) if g["week"] == week]

    always_in = {t: True for t in teams}
    ever_in = {t: False for t in teams}
    # per this-week combo: does team survive as "sure in" in ALL completions?
    week_always = {}

    for combo in itertools.product((1, 0), repeat=len(rem_work)):
        _apply(combo, rem_work)
        sure, maybe = cut_membership(base, overrides, ncf)
        _clear(rem_work)
        wkey = tuple(combo[i] for i in wk_idx)
        cell = week_always.setdefault(wkey, {t: True for t in teams})
        for t in teams:
            s = t in sure
            m = s or t in maybe
            always_in[t] = always_in[t] and s
            ever_in[t] = ever_in[t] or m
            cell[t] = cell[t] and s

    clinch_combos = {
        t: {k for k, cell in week_always.items() if cell[t]}
        for t in teams}
    return {"teams": {t: {"always_in": always_in[t], "ever_in": ever_in[t]}
                      for t in teams},
            "week": week,
            "week_games": [(g["home"], g["away"]) for g in rem_work
                           if g["week"] == week],
            "clinch_combos": clinch_combos,
            "n_outcomes": 1 << len(rem_work)}


# ----------------------------------------------------------------- scenarios

def scenario_texts(team, week_games, combos, max_terms=SCENARIO_MAX_TERMS):
    """Minimal sufficient this-week conditions for a clinch, as prose.

    combos: set of full week-outcome tuples (1 = home wins) under which the
    team is clinched regardless of everything after this week."""
    if not combos:
        return []
    n = len(week_games)
    all_combos = set(itertools.product((1, 0), repeat=n))
    if combos == all_combos:
        return ["has already clinched (this week can't change it)"]

    def covers(cond):
        # cond: {game_index: required_outcome}; sufficient iff every full
        # combo extending it clinches
        for c in all_combos:
            if all(c[i] == v for i, v in cond.items()) and c not in combos:
                return False
        return True

    def phrase(i, v):
        home, away = week_games[i]
        winner, loser = (home, away) if v else (away, home)
        art = "an" if team[0] in "AEIOU" else "a"
        if team == winner:
            return f"{art} {team} win"
        if team == loser:
            return f"{art} {team} loss"
        return f"{winner} over {loser}"

    found = []
    for size in range(1, max_terms + 1):
        for idxs in itertools.combinations(range(n), size):
            for vals in itertools.product((1, 0), repeat=size):
                cond = dict(zip(idxs, vals))
                if any(set(f).issubset(set(cond.items())) for f in found):
                    continue  # a smaller sufficient condition covers this
                if covers(cond):
                    found.append(tuple(sorted(cond.items())))
        if found:
            break  # report only the minimal size tier
    texts = []
    for cond in found:
        parts = [phrase(i, v) for i, v in cond]
        texts.append(" + ".join(parts))
    return texts


# -------------------------------------------------------------------- analyze

def analyze(games, overrides=None, budget=EXACT_BUDGET):
    """Merge bounds and (when affordable) exact enumeration.

    Returns {"mode": "bounds"|"exact", "teams": {team: {...}},
             "week": int|None, "n_outcomes": int|None}
    Per team: status in {"clinched","eliminated","alive"},
              destiny bool, method str, scenarios [str, ...]."""
    b = bounds(games)
    ex = exact(games, overrides, budget)
    out = {}
    for t in sorted(b):
        info = {"w": b[t]["w"], "r": b[t]["r"],
                "destiny": b[t]["destiny"], "scenarios": []}
        if ex:
            e = ex["teams"][t]
            if e["always_in"]:
                info["status"], info["method"] = "clinched", "exact"
            elif not e["ever_in"]:
                info["status"], info["method"] = "eliminated", "exact"
            else:
                info["status"], info["method"] = "alive", "exact"
                combos = ex["clinch_combos"][t]
                info["scenarios"] = scenario_texts(
                    t, ex["week_games"], combos)
            # bounds must never contradict the enumeration
            if b[t]["clinched"]:
                assert e["always_in"], f"bound/exact clinch mismatch: {t}"
            if b[t]["eliminated"]:
                assert not e["ever_in"], f"bound/exact elim mismatch: {t}"
        else:
            if b[t]["clinched"]:
                info["status"], info["method"] = "clinched", "bounds"
            elif b[t]["eliminated"]:
                info["status"], info["method"] = "eliminated", "bounds"
            else:
                info["status"], info["method"] = "alive", "bounds"
        out[t] = info
    return {"mode": "exact" if ex else "bounds",
            "teams": out,
            "week": ex["week"] if ex else None,
            "n_outcomes": ex["n_outcomes"] if ex else None}

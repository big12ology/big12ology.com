#!/usr/bin/env python3
"""What the unbalanced schedule is worth.

Sixteen teams play nine conference games. Nobody plays everybody, so two
teams finishing 7-2 did not attempt the same thing, and the standings say
nothing about the difference. This measures it: every team's expected
conference wins on every other team's slate.

The one rule that has to be stated, because the number is meaningless
without it: **a team cannot play itself.** Where a borrowed slate contains
the borrower, it faces the team it borrowed from instead. Utah playing
Texas Tech's schedule still plays Texas Tech — otherwise the exercise
quietly deletes the best team in the league from Utah's season and calls the
result "an easier schedule".

Probabilities come from odds.p_from_margin and strengths from
odds.team_strength, so this page and the championship odds cannot disagree
about who is better than whom.
"""
import odds


def slates(games):
    """{team: [(opponent, is_home), ...]} over the conference schedule."""
    out = {}
    for g in games:
        if not g.get("conference_game") or g.get("ccg"):
            continue
        out.setdefault(g["home"], []).append((g["away"], True))
        out.setdefault(g["away"], []).append((g["home"], False))
    return out


def expected_wins(team, slate, strength, hfa, owner=None):
    """Expected wins for `team` playing `slate`.

    `owner` is whose slate it is. Where the slate faces `team`, it faces
    `owner` instead — the substitution described in the module docstring.
    """
    st = strength.get(team)
    if st is None:
        return None
    total = 0.0
    for opp, home in slate:
        if opp == team:
            opp = owner
        os_ = strength.get(opp)
        if os_ is None:
            continue
        total += odds.p_from_margin(st - os_ + (hfa if home else -hfa))
    return total


def matrix(games, systems, teams=None):
    """{team: {slate_owner: expected wins}} plus each team's own slate.

    Returns (matrix, rows) where rows is a per-team summary sorted by the
    schedule a team actually drew, hardest first.
    """
    sl = slates(games)
    if not systems or not sl:
        return {}, []
    strength = odds.team_strength(systems)
    hfa = odds.hfa_points(systems)
    names = sorted(t for t in (teams or sl) if t in sl and t in strength)

    m = {t: {} for t in names}
    for t in names:
        for owner in names:
            m[t][owner] = expected_wins(t, sl[owner], strength, hfa, owner)

    rows = []
    for t in names:
        others = [(m[t][o], o) for o in names if o != t and m[t][o] is not None]
        if not others:
            continue
        others.sort()
        own = m[t][t]
        rows.append({
            "team": t,
            "own": own,
            "hardest": others[0],          # (wins, whose slate)
            "easiest": others[-1],
            "spread": others[-1][0] - others[0][0],
            # How the draw treated them: own minus the average of all
            # sixteen slates. Negative means a harder draw than typical.
            "vs_average": own - sum(w for w, _ in others + [(own, t)])
                                / (len(others) + 1),
        })
    rows.sort(key=lambda r: r["vs_average"])
    return m, rows

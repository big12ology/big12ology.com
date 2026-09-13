#!/usr/bin/env python3
"""A board with unplayed teams on it, and a card that will not guess.

Both halves of one bug, found on the Sunday after the first Big 12 game of
2026. standings() ranks only the teams it has conference evidence for, so the
board was two rows long: BYU at 1-0 and Arizona at 0-1. Everything that read
those two rows read them as the whole league.

  * The fourteen teams with no conference game were appended UNDER the one
    team that had lost one, so 0-1 outranked 0-0 on a board whose first
    column is winning percentage.

  * championship() took rows[0] and rows[1] off that board, which named the
    team that had just lost as the #2 seed, with a green "resolved" badge on
    it. The same shortcut, later in the year, hands back the alphabetical
    head of a tie the ladder could not break: eight teams level at 1-0 came
    out of it as "Arizona vs Baylor".

The seasons in data/ cannot catch either one. They are finished, every team
has nine conference games, and the shortcut is only wrong before that is
true. So these run on hand-built weeks.
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import engine                                            # noqa: E402
import build                                             # noqa: E402

TEAMS = ["Arizona", "Arizona State", "Baylor", "BYU", "Cincinnati",
         "Colorado", "Houston", "Iowa State", "Kansas", "Kansas State",
         "Oklahoma State", "TCU", "Texas Tech", "UCF", "Utah",
         "West Virginia"]

FAIL = []
_id = [0]


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    print(f"  [{'ok' if cond else 'FAIL'}] {msg}")


def game(home, away, hp=None, ap=None, conf=True, week=1):
    _id[0] += 1
    return {"id": _id[0], "week": week, "home": home, "away": away,
            "home_points": hp, "away_points": ap, "completed": hp is not None,
            "conference_game": conf, "home_class": "fbs", "away_class": "fbs"}


def schedule():
    """Every team paired off once, unplayed. Enough for confTeams to know all
    sixteen, which is what tells the board who is missing."""
    return [game(TEAMS[i], TEAMS[i + 1]) for i in range(0, len(TEAMS), 2)]


def board(games):
    rows = engine.standings(games, {})
    padded = engine.pad(rows, games)
    return padded, engine.display_ranks(padded)


# --- one conference game played ---------------------------------------------
print("one game played:")
games = [game("BYU", "Arizona", 27, 17)] + schedule()
rows, ranks = board(games)
order = [r["team"] for r in rows]

check(len(rows) == 16, f"the board carries all sixteen teams (got {len(rows)})")
check(order[0] == "BYU", f"the team that won is first (got {order[0]})")
check(order[-1] == "Arizona",
      f"the team that lost is last, under fourteen teams at 0-0 "
      f"(got {order[-1]})")
check(ranks["BYU"] == "1", f"BYU is 1 (got {ranks['BYU']})")
check(ranks["Arizona"] == "16", f"Arizona is 16 (got {ranks['Arizona']})")
check(ranks["Baylor"] == "T2",
      f"the unplayed share second, they have not separated themselves "
      f"(got {ranks['Baylor']})")
check(all(r["rank"] == 2 for r in rows if r.get("unplayed")),
      "every unplayed team carries the position it shares")

ccg = engine.championship(games, {})
check(ccg["seed1"] == "BYU", f"seed 1 is the only team placed (got {ccg['seed1']})")
check(ccg["seed2"] is None,
      f"seed 2 is nobody: the team that lost is sixteenth "
      f"(got {ccg['seed2']})")
check(ccg["resolved"] is False, "and the card does not claim to be resolved")
check(ccg["reason"] == "unplayed",
      f"the reason is the fourteen who have not played, not a coin toss "
      f"(got {ccg['reason']})")
check(len(ccg["pending"]) == 14 and "Arizona" not in ccg["pending"],
      "the open seat's candidates are the fourteen, and not the team a "
      "loss behind them")

left = build.official_board(games, {}, rows)
flat = [t for b in left for t in b["teams"]]
check(flat[0] == "BYU" and flat[-1] == "Arizona",
      f"the conference's own board agrees end to end (got {flat[0]} first, "
      f"{flat[-1]} last)")
check([b["pos"] for b in left] == ["1", "T2", "16"],
      f"and reads 1, T2, 16 (got {[b['pos'] for b in left]})")

# --- a full first week, eight teams level at 1-0 ----------------------------
print("eight teams at 1-0, none of whom have met:")
games = [game(TEAMS[i], TEAMS[i + 1], 30, 20) for i in range(0, 16, 2)]
ccg = engine.championship(games, {})
check(ccg["seed1"] is None and ccg["seed2"] is None,
      f"neither seat is filled (got {ccg['seed1']} and {ccg['seed2']})")
check(ccg["reason"] == "unresolved",
      f"because the ladder ran and could not separate them "
      f"(got {ccg['reason']})")
check(len(ccg["pending"]) == 8,
      f"all eight are candidates (got {len(ccg['pending'])})")
check("Arizona" not in (ccg["seed1"], ccg["seed2"]),
      "and nobody is seeded for sorting first alphabetically")

# --- the finished seasons still answer --------------------------------------
print("a finished season is unchanged:")
import json                                              # noqa: E402
games = json.load(open(os.path.join(HERE, "data", "games_2024.json")))
rows, ranks = board(games)
ccg = engine.championship(games, {})
check(ccg["seed1"] == "Arizona State" and ccg["seed2"] == "Iowa State",
      f"2024 still names both seeds (got {ccg['seed1']}, {ccg['seed2']})")
check(ccg["pending"] is None and ccg["reason"] is None,
      "with nothing pending")
check(sorted(ranks.values(), key=lambda s: int(s.lstrip("T"))) ==
      [str(i) for i in range(1, 17)],
      "and every team has a position of its own")

print("OK" if not FAIL else "FAILURES")
for f in FAIL:
    print(f"  {f}")
sys.exit(1 if FAIL else 0)

"""The cross-check's handling of a crowd ESPN has and we do not, yet.

The fetch and the verifier ask ESPN the same question seconds apart, so ESPN
attaching an attendance figure between the two reads as a disagreement if the
only categories are agree and MISMATCH. On 2026-09-06 that is exactly what
happened: Kansas State 71-3 over Nicholls, no crowd at 06:36:07, 51,719 at
06:36:16, a red run, and the next run quietly filling the number in from ESPN
with nothing else changed. Nobody had disagreed with anybody, and the failure
message sent whoever read it after a school box score to arbitrate it.

So the shape is tested rather than the wording: one number is a race, the same
game still ahead of us on the next run is a broken fill, and two numbers that
differ is still the thing worth stopping the world for.

Everything is stubbed. A test that called ESPN would be flaky, and this is the
script that decides whether the pipeline goes red.

    python3 -m unittest discover -s tests
"""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "verify_attendance.py"

KSU, TTU = 401856771, 401856770


def load():
    """Import the verifier with its ESPN call already stubbed.

    fetch_attendance is imported at module scope for espn_game, and importing
    it for real is what a test must not do here, so it goes in as a stub module
    before the loader can reach for the real one.
    """
    stub = type(sys)("fetch_attendance")
    stub.espn_game = lambda espn_id: None
    sys.modules["fetch_attendance"] = stub
    spec = importlib.util.spec_from_file_location("verify_attendance", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.time.sleep = lambda *a, **k: None
    return mod


class Race(unittest.TestCase):
    def run_check(self, games, espn, previous=None):
        """Returns (exit_code, printed). exit_code 0 means the run stays green."""
        mod = load()
        mod.espn_game = lambda i: {"attendance": espn.get(int(i)), "source": "stub"}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "data" / "seasons").mkdir(parents=True)
            (root / "data" / "verification").mkdir(parents=True)
            (root / "data" / "seasons" / "2026.json").write_text(
                json.dumps({"games": games}))
            if previous is not None:
                (root / "data" / "verification" / "2026.json").write_text(
                    json.dumps({"games": previous}))
            mod.ROOT = root
            buf = io.StringIO()
            code = 0
            with redirect_stdout(buf):
                try:
                    mod.main(2026)
                except SystemExit as e:
                    code = 1 if e.code else 0
                    if e.code:
                        buf.write(str(e.code))
            return code, buf.getvalue()

    # The Kansas State game as it stood at 06:36 on 2026-09-06: played, scored,
    # no crowd on our side, a crowd on ESPN's.
    KSU_GAME = {"team": "Kansas State", "week": 1, "opponent": "Nicholls",
                "espnId": KSU, "attendance": None,
                "pointsFor": 71, "pointsAgainst": 3}
    TTU_GAME = {"team": "Texas Tech", "week": 1, "opponent": "Abilene Christian",
                "espnId": TTU, "attendance": 60229,
                "pointsFor": 45, "pointsAgainst": 3}

    def test_first_sighting_is_a_race_and_stays_green(self):
        code, out = self.run_check([self.KSU_GAME, self.TTU_GAME],
                                   {KSU: 51719, TTU: 60229})
        self.assertEqual(code, 0, "the race failed the run")
        self.assertIn("espn_ahead", out)
        self.assertNotIn("MISMATCH", out)

    def test_a_game_that_agreed_last_run_is_still_a_first_sighting(self):
        # Not every prior verdict is a strike. Only the same one twice.
        code, _ = self.run_check(
            [self.KSU_GAME], {KSU: 51719},
            previous=[{"espnId": KSU, "status": "agree"}])
        self.assertEqual(code, 0)

    def test_ahead_of_us_twice_running_is_a_broken_fill(self):
        code, out = self.run_check(
            [self.KSU_GAME], {KSU: 51719},
            previous=[{"espnId": KSU, "status": "espn_ahead"}])
        self.assertEqual(code, 1, "a stuck fill passed")
        self.assertIn("Kansas State wk1", out)
        # The message has to point at the fetch. Sending someone to a box score
        # to arbitrate a single number is the bug this whole file is about.
        self.assertIn("fill is not working", out)

    def test_no_memory_reads_as_a_first_sighting(self):
        # A missing verification file is the first run of a season, not a
        # reason to fail.
        code, _ = self.run_check([self.KSU_GAME], {KSU: 51719}, previous=None)
        self.assertEqual(code, 0)

    def test_two_numbers_that_differ_still_stop_the_run(self):
        code, out = self.run_check(
            [{"team": "Baylor", "week": 1, "opponent": "Auburn", "espnId": 9,
              "attendance": 51000, "pointsFor": 17, "pointsAgainst": 16}],
            {9: 40000})
        self.assertEqual(code, 1, "a real disagreement was let through")
        self.assertIn("MISMATCH", out)

    def test_neither_source_has_it_is_not_a_race(self):
        # missing_both keeps its own meaning: a manual box-score entry is the
        # answer there, and it must not be swallowed by the new status.
        code, out = self.run_check([self.KSU_GAME], {KSU: None})
        self.assertEqual(code, 0)
        self.assertIn("missing_both", out)
        self.assertNotIn("espn_ahead", out)


if __name__ == "__main__":
    unittest.main()

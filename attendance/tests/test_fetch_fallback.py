"""The fetch's behavior when CFBD is not there.

This path only runs during an outage or a spent monthly quota, which is
exactly where a regression sits unnoticed until the day it costs a game.
Everything here is stubbed: a test that called ESPN would be flaky, and a
test that called CFBD would spend from a 1,000-call month.

The ESPN payloads below are trimmed captures of real responses (events
401628582, 401856766 and 401856767), kept because the parsing depends on
shape details that are easy to get wrong from memory — the CDN wraps the
gamepackage a level down, attendance lives outside the competition object,
and a game that has not kicked off carries no score field at all rather
than a zero. The core-API capture is a different service with a different
shape, and it is the backstop when the gamepackage host stops answering.

    python3 -m unittest discover -s tests
"""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "fetch_attendance.py"

spec = importlib.util.spec_from_file_location("fetch_attendance", SCRIPT)
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)


@contextlib.contextmanager
def quiet():
    """Capture what the fetch prints, and yield it for assertions.

    Not only for a tidy log. Under Actions the fetch reports a dead CFBD as
    `::warning::`, which the runner turns into an annotation on the run — so
    a test exercising that path hangs a warning about season 1999 on a run
    that fetched 2026, in the same list a real problem would appear in. The
    output is worth asserting on and not worth publishing."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


FINAL = {  # event 401628582 — UCF 57, New Hampshire 3
    "gamepackageJSON": {
        "gameInfo": {"attendance": 44206},
        "header": {"competitions": [{
            "status": {"type": {"completed": True}},
            "competitors": [
                {"homeAway": "home", "score": "57",
                 "team": {"location": "UCF"}},
                {"homeAway": "away", "score": "3",
                 "team": {"location": "New Hampshire"}},
            ],
        }]},
    },
}
SCHEDULED = {  # event 401856766 — TCU vs North Carolina, Dublin, not yet played
    "gamepackageJSON": {
        "gameInfo": {"venue": {"fullName": "Aviva Stadium"}},
        "header": {"competitions": [{
            "status": {"type": {"completed": False}},
            "competitors": [
                {"homeAway": "home", "team": {"location": "TCU"}},
                {"homeAway": "away", "team": {"location": "North Carolina"}},
            ],
        }]},
    },
}
CORE = {  # event 401856767 — UCF vs Bethune-Cookman, core API competition
    "attendance": 43127,
    "venue": {"fullName": "Acrisure Bounce House"},
    # Everything the score would come from is a $ref, which is why this
    # endpoint answers for attendance and nothing else.
    "status": {"$ref": "http://sports.core.api.espn.com/..."},
    "competitors": [
        {"homeAway": "home", "score": {"$ref": "http://.../score"}},
        {"homeAway": "away", "score": {"$ref": "http://.../score"}},
    ],
}


class ReadsEspnPayloads(unittest.TestCase):
    def parse(self, payload):
        with mock.patch.object(fa, "get_json", return_value=payload):
            return fa.espn_game(1)

    def test_final_game(self):
        g = self.parse(FINAL)
        self.assertEqual(g["attendance"], 44206)
        self.assertTrue(g["completed"])
        self.assertEqual(g["home"], {"points": 57, "team": "UCF"})
        self.assertEqual(g["away"], {"points": 3, "team": "New Hampshire"})

    def test_unplayed_game_has_no_points(self):
        g = self.parse(SCHEDULED)
        self.assertIsNone(g["attendance"])
        self.assertFalse(g["completed"])
        # None, never 0 — a phantom 0-0 would read as a played game.
        self.assertIsNone(g["home"]["points"])
        self.assertIsNone(g["away"]["points"])
        self.assertEqual(g["home"]["team"], "TCU")

    def test_zero_attendance_is_missing(self):
        payload = json.loads(json.dumps(FINAL))
        payload["gamepackageJSON"]["gameInfo"]["attendance"] = 0
        self.assertIsNone(self.parse(payload)["attendance"])

    def test_source_is_named(self):
        self.assertEqual(self.parse(FINAL)["source"], "ESPN gamepackage")

    def test_both_endpoints_down_is_not_fatal(self):
        with mock.patch.object(fa, "get_json", side_effect=OSError("boom")), \
                quiet():
            self.assertIsNone(fa.espn_game(1))

    def test_core_api_covers_a_dead_gamepackage_host(self):
        # The September 2026 failure exactly: site.api answered 403 to
        # everything, the fill loop swallowed it, and two played games sat
        # with attendance null. The backstop has to carry the crowd through.
        blocked = urllib.error.HTTPError(
            "https://cdn.espn.com/core/college-football/game", 403,
            "Forbidden", {}, None)

        def answers(url, headers=None):
            if url.startswith(fa.ESPN_GAME):
                raise blocked
            return CORE

        with mock.patch.object(fa, "get_json", side_effect=answers), quiet():
            g = fa.espn_game(401856767)
        self.assertEqual(g["attendance"], 43127)
        self.assertEqual(g["source"], "ESPN core API")
        # No score is better than a guessed one: the caller skips the score
        # fill when the sides come back empty.
        self.assertFalse(g["completed"])
        self.assertEqual(g["home"], {})

    def test_core_api_zero_attendance_is_missing(self):
        with mock.patch.object(fa, "get_json",
                               return_value={**CORE, "attendance": 0}):
            self.assertIsNone(fa.espn_core_attendance(1)["attendance"])


class MatchesTeamNames(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(fa.same_team("Kansas State", "Kansas State"))

    def test_punctuation_and_case_ignored(self):
        self.assertTrue(fa.same_team("TCU", "tcu"))

    def test_substring_is_not_a_match(self):
        # The one that matters: a loose match here writes Arizona State's
        # score against Arizona.
        self.assertFalse(fa.same_team("Arizona", "Arizona State"))

    def test_missing_name_is_not_a_match(self):
        self.assertFalse(fa.same_team(None, None))


# One summary per game id, shaped the way espn_game returns them.
SUMMARIES = {
    1: {"attendance": 48000, "completed": True,
        "home": {"points": 24, "team": "TCU"},
        "away": {"points": 17, "team": "North Carolina"}},
    2: {"attendance": 51000, "completed": True,
        "home": {"points": 35, "team": "Kansas"},
        "away": {"points": 14, "team": "Tulsa"}},
    3: {"attendance": 60000, "completed": True,
        "home": {"points": 21, "team": "Georgia Tech"},
        "away": {"points": 28, "team": "Colorado"}},
    4: {"attendance": None, "completed": False,
        "home": {"points": None, "team": "Baylor"},
        "away": {"points": None, "team": "Auburn"}},
    5: {"attendance": 40000, "completed": True,
        "home": {"points": 10, "team": "Someone Else"},
        "away": {"points": 7, "team": "Nobody"}},
    6: {"attendance": 55000, "completed": True,
        "home": {"points": 3, "team": "Utah"},
        "away": {"points": 45, "team": "Idaho"}},
}
for _s in SUMMARIES.values():
    _s["source"] = "ESPN gamepackage"


def season_fixture():
    return {
        "season": 2026,
        "source": "CollegeFootballData API ...",
        "weekLabels": ["Week 0", "Week 1"],
        "games": [
            # Neutral site: role on both rows, so only the name says which
            # side of the box score this team is on. TCU is ESPN's home.
            {"team": "TCU", "week": 0, "date": "2026-08-01", "espnId": 1,
             "opponent": "North Carolina", "attendance": None,
             "role": "neutral", "venue": "Aviva Stadium"},
            {"team": "Kansas", "week": 1, "date": "2026-08-02", "espnId": 2,
             "opponent": "Tulsa", "attendance": None},
            {"team": "Colorado", "week": 1, "date": "2026-08-02", "espnId": 3,
             "opponent": "Georgia Tech", "attendance": None, "role": "away"},
            {"team": "Baylor", "week": 1, "date": "2026-08-02", "espnId": 4,
             "opponent": "Auburn", "attendance": None},
            # Neutral row whose team ESPN does not name: score must be left
            # alone rather than guessed from position.
            {"team": "Houston", "week": 1, "date": "2026-08-02", "espnId": 5,
             "opponent": "Rice", "attendance": None, "role": "neutral"},
            # Second row for a game already in the file, fully populated.
            {"team": "Iowa State", "week": 1, "date": "2026-08-02",
             "espnId": 2, "opponent": "Kansas", "attendance": 44000,
             "pointsFor": 7, "pointsAgainst": 3, "weather": {"tempF": 80}},
            # Not played yet: must not be fetched at all.
            {"team": "Utah", "week": 1, "date": "2099-09-30", "espnId": 6,
             "opponent": "Idaho", "attendance": None},
        ],
        "conferences": {"TCU": "Big 12"},
        "big12Era": True,
    }


class RefreshesFromEspnAlone(unittest.TestCase):
    # One refresh for the whole class: the assertions all read the same run,
    # and repeating it per test would say nothing extra thirty times over.
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False)
        json.dump(season_fixture(), tmp)
        tmp.close()
        cls.path = Path(tmp.name)
        cls.addClassCleanup(cls.path.unlink)

        def fake_espn(espn_id):
            cls.calls.append(espn_id)
            return SUMMARIES[espn_id]

        with mock.patch.object(fa, "espn_game", fake_espn), \
                mock.patch.object(fa.time, "sleep"), quiet():
            fa.refresh_from_espn(cls.path, 2026)
        cls.season = json.loads(cls.path.read_text())
        cls.rows = {(g["team"], g["espnId"]): g for g in cls.season["games"]}

    def test_neutral_row_resolves_by_name(self):
        tcu = self.rows[("TCU", 1)]
        self.assertEqual((tcu["pointsFor"], tcu["pointsAgainst"]), (24, 17))

    def test_home_row_takes_the_home_score(self):
        ku = self.rows[("Kansas", 2)]
        self.assertEqual((ku["pointsFor"], ku["pointsAgainst"]), (35, 14))

    def test_away_row_takes_the_away_score(self):
        cu = self.rows[("Colorado", 3)]
        self.assertEqual((cu["pointsFor"], cu["pointsAgainst"]), (28, 21))

    def test_unresolvable_neutral_row_keeps_no_score(self):
        uh = self.rows[("Houston", 5)]
        self.assertNotIn("pointsFor", uh)
        # Attendance is the same number whichever side you are on, so it
        # still lands even when the score cannot.
        self.assertEqual(uh["attendance"], 40000)

    def test_attendance_is_marked_as_espn_sourced(self):
        tcu = self.rows[("TCU", 1)]
        self.assertEqual(tcu["attendance"], 48000)
        self.assertEqual(tcu["attendanceSource"],
                         "ESPN gamepackage (CFBD unavailable)")

    def test_game_in_progress_gets_nothing(self):
        bu = self.rows[("Baylor", 4)]
        self.assertNotIn("pointsFor", bu)
        self.assertIsNone(bu["attendance"])

    def test_populated_row_is_untouched(self):
        isu = self.rows[("Iowa State", 2)]
        self.assertEqual(isu["attendance"], 44000)
        self.assertEqual(isu["pointsFor"], 7)
        self.assertEqual(isu["weather"], {"tempF": 80})

    def test_unplayed_game_is_never_fetched(self):
        self.assertNotIn(6, self.calls)
        self.assertIsNone(self.rows[("Utah", 6)]["attendance"])

    def test_one_call_per_game_not_per_row(self):
        self.assertEqual(sorted(self.calls), [1, 2, 3, 4, 5])

    def test_top_level_keys_survive(self):
        self.assertEqual(self.season["conferences"], {"TCU": "Big 12"})
        self.assertIs(self.season["big12Era"], True)


class LeavesTheFileAloneWhenNothingMoved(unittest.TestCase):
    """A season whose games are all ahead of it — every run between now and
    the opener. The file must come back byte-identical: this writer indents
    at 2 and add_conferences.py at 1, so a no-op rewrite would land as a
    whole-season diff that reads like new data."""

    def test_untouched(self):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False)
        season = season_fixture()
        for g in season["games"]:
            g["date"] = "2099-09-30"
        tmp.write(json.dumps(season, indent=1) + "\n")
        tmp.close()
        path = Path(tmp.name)
        self.addCleanup(path.unlink)
        before = path.read_bytes()

        with mock.patch.object(fa, "espn_game") as espn, \
                mock.patch.object(fa.time, "sleep"), quiet():
            fa.refresh_from_espn(path, 2026)
        espn.assert_not_called()
        self.assertEqual(path.read_bytes(), before)


class RoutesAroundCfbd(unittest.TestCase):
    """The trigger. CFBD answers a run over its monthly limit with 429 and
    {"message": "Monthly call quota exceeded."} — that must reach the ESPN
    path instead of ending the run."""

    def setUp(self):
        self.quota = urllib.error.HTTPError(
            "https://api.collegefootballdata.com/games", 429,
            "Too Many Requests", {}, None)
        self.refreshed = []

    def run_main(self, year):
        with mock.patch.object(fa, "cfbd", side_effect=self.quota), \
                mock.patch.object(fa, "refresh_from_espn",
                                  side_effect=lambda out, y:
                                  self.refreshed.append((out.name, y))), \
                mock.patch.dict("os.environ", {"CFBD_API_KEY": "test-key",
                                               "GITHUB_ACTIONS": "true"}), \
                quiet() as out:
            try:
                fa.main(year)
            finally:
                self.printed = out.getvalue()

    def test_spent_quota_falls_through_to_espn(self):
        self.run_main(2026)
        self.assertEqual(self.refreshed, [("2026.json", 2026)])

    def test_the_wall_is_announced_not_swallowed(self):
        # GITHUB_ACTIONS is set above so this covers the annotation form too:
        # falling back quietly would be its own bug, and captured output is
        # the only place to check it without publishing a warning to the run.
        self.run_main(2026)
        self.assertIn("::warning::", self.printed)
        self.assertIn("CFBD unavailable", self.printed)
        self.assertIn("429", self.printed)

    def test_no_committed_season_re_raises(self):
        # Nothing to refresh: a silent success would write an empty season.
        with self.assertRaises(urllib.error.HTTPError):
            self.run_main(1999)
        self.assertEqual(self.refreshed, [])


class RefusesAnEmptySeason(unittest.TestCase):
    """The other way CFBD fails: HTTP 200 with no games in it, which raises
    nothing on its own. Before the guard that response flowed all the way to
    write_text and replaced the committed season with an empty one; it must
    route to the same ESPN fallback a 429 does, and fail loudly for a season
    with nothing committed to fall back on."""

    def run_main(self, year, payload):
        self.refreshed = []
        with mock.patch.object(fa, "cfbd", return_value=payload), \
                mock.patch.object(fa, "refresh_from_espn",
                                  side_effect=lambda out, y:
                                  self.refreshed.append((out.name, y))), \
                mock.patch.dict("os.environ", {"CFBD_API_KEY": "test-key"}), \
                quiet() as out:
            try:
                fa.main(year)
            finally:
                self.printed = out.getvalue()

    def test_empty_response_routes_to_espn(self):
        self.run_main(2026, [])
        self.assertEqual(self.refreshed, [("2026.json", 2026)])
        self.assertIn("no games for 2026", self.printed)

    def test_no_big12_rows_is_empty_too(self):
        # A payload that answers but holds nobody we track: same glitch,
        # filtered one line later.
        self.run_main(2026, [{"homeTeam": "Alabama", "awayTeam": "Auburn"}])
        self.assertEqual(self.refreshed, [("2026.json", 2026)])

    def test_no_committed_season_re_raises(self):
        with self.assertRaises(RuntimeError):
            self.run_main(1999, [])
        self.assertEqual(self.refreshed, [])


class VenueCatalog(unittest.TestCase):
    """The committed catalog stands in for a per-run /venues call, so its
    shape contract is load-bearing: the catalog stores lat/lon/tz and the
    fetch reads latitude/longitude/timezone. A rename on either side would
    not crash anything; it would just quietly drop weather and shift late
    kickoffs to the wrong day."""

    def test_normalizes_to_the_cfbd_field_names(self):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False)
        tmp.write(json.dumps({"3616": {
            "name": "Amon G. Carter Stadium", "city": "Fort Worth",
            "state": "TX", "lat": 32.7097, "lon": -97.3681,
            "tz": "America/Chicago"}}))
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        with mock.patch.object(fa, "VENUE_CATALOG", Path(tmp.name)):
            cat = fa.load_venue_catalog()
        self.assertEqual(cat[3616], {
            "timezone": "America/Chicago", "city": "Fort Worth",
            "state": "TX", "latitude": 32.7097, "longitude": -97.3681})

    def test_missing_file_says_so_rather_than_raising(self):
        # None is main()'s cue to spend the one live /venues call.
        with mock.patch.object(fa, "VENUE_CATALOG", Path("/nonexistent")):
            self.assertIsNone(fa.load_venue_catalog())

    def test_the_real_committed_catalog_loads(self):
        # Against the actual file, not a fixture: this is the pair of
        # artifacts that must agree, and the fixture above cannot notice
        # the tiebreaker side changing its mind about the shape.
        cat = fa.load_venue_catalog()
        self.assertIsNotNone(cat, f"{fa.VENUE_CATALOG} is gone")
        self.assertGreater(len(cat), 500)
        self.assertEqual(cat[3616]["timezone"], "America/Chicago")
        self.assertIsNotNone(cat[3616]["latitude"])


if __name__ == "__main__":
    unittest.main()

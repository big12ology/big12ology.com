"""The date a game is filed under, when its kickoff has not been announced.

CFBD's placeholder for an unannounced window is midnight Eastern on game day.
Converted to a Central or Mountain stadium's own time, as every other kickoff
is, that is the evening before, so 66 of the 80 unannounced 2026 games read
Friday on the attendance page while the schedule had them on Saturday
(2026-10-18). kickoff_local(..., tbd=True) reads the placeholder's date in
Eastern, where it means the day the game is on.
"""
import importlib.util
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "fetch_attendance", ROOT / "scripts" / "fetch_attendance.py")
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)

MOUNTAIN = ZoneInfo("America/Denver")
CENTRAL = ZoneInfo("America/Chicago")
PHOENIX = ZoneInfo("America/Phoenix")
EASTERN = ZoneInfo("America/New_York")


class UnannouncedKickoff(unittest.TestCase):
    def test_placeholder_is_game_day_in_every_zone(self):
        # Saturday 2026-10-24, midnight Eastern daylight time.
        for tz in (MOUNTAIN, CENTRAL, PHOENIX, EASTERN):
            k = fa.kickoff_local("2026-10-24T04:00:00.000Z", tz, tbd=True)
            self.assertEqual(k.date().isoformat(), "2026-10-24", str(tz))

    def test_after_daylight_time_ends(self):
        # Saturday 2026-11-07: the placeholder moves to 05:00Z once Eastern
        # is back on standard time, and still means that Saturday.
        k = fa.kickoff_local("2026-11-07T05:00:00.000Z", MOUNTAIN, tbd=True)
        self.assertEqual(k.date().isoformat(), "2026-11-07")

    def test_the_week_follows_the_date(self):
        k = fa.kickoff_local("2026-10-24T04:00:00.000Z", MOUNTAIN, tbd=True)
        self.assertEqual(fa.display_week(k.date(), 2026),
                         fa.display_week(
                             fa.kickoff_local("2026-10-24T20:00:00Z",
                                              MOUNTAIN).date(), 2026))


class AnnouncedKickoff(unittest.TestCase):
    def test_a_set_time_is_still_the_stadiums_own_day(self):
        # Utah at Colorado, 02:15Z on the 18th, is 8:15 PM on the 17th in
        # Boulder. That conversion is right and must not change.
        k = fa.kickoff_local("2026-10-18T02:15:00.000Z", MOUNTAIN)
        self.assertEqual(k.date().isoformat(), "2026-10-17")
        self.assertEqual(k.strftime("%H:%M"), "20:15")

    def test_no_start_is_no_kickoff(self):
        self.assertIsNone(fa.kickoff_local("", MOUNTAIN, tbd=True))


if __name__ == "__main__":
    unittest.main()

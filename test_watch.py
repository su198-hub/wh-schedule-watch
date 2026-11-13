"""Offline tests for watch.py's windows and parsing (standard library only):  python -m unittest test_watch"""
import datetime as dt, unittest
from zoneinfo import ZoneInfo
import watch as W

ET = ZoneInfo("America/New_York")


def at(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=ET)


class Decide(unittest.TestCase):
    def test_evening_weekday_later_day(self):
        self.assertEqual(W.decide(at("2026-11-12 22:10"), "schedule", [dt.date(2026, 11, 13)])[0], "sched")      # Thu night, Friday
        self.assertEqual(W.decide(at("2026-11-12 17:00"), "schedule", [dt.date(2026, 11, 13)])[0], "sched")
        self.assertEqual(W.decide(at("2026-11-12 23:59"), "schedule", [dt.date(2026, 11, 13)])[0], "sched")
        self.assertIsNone(W.decide(at("2026-11-12 16:59"), "schedule", [dt.date(2026, 11, 13)])[0])               # before the window
        self.assertIsNone(W.decide(at("2026-11-14 20:00"), "schedule", [dt.date(2026, 11, 15)])[0])               # Saturday evening
        self.assertIsNone(W.decide(at("2026-11-12 22:10"), "schedule", [dt.date(2026, 11, 12)])[0])               # covers no later day

    def test_sunday_evening_for_monday(self):
        self.assertEqual(W.decide(at("2026-11-15 20:30"), "schedule", [dt.date(2026, 11, 16)])[0], "sched")
        self.assertIsNone(W.decide(at("2026-11-15 12:30"), "schedule", [dt.date(2026, 11, 16)])[0])

    def test_morning_same_day_release(self):
        j, why = W.decide(at("2026-11-12 07:10"), "schedule", [dt.date(2026, 11, 12)])
        self.assertEqual(j, "sched_day"); self.assertIn("morning", why)
        self.assertEqual(W.decide(at("2026-11-12 06:00"), "schedule", [dt.date(2026, 11, 12)])[0], "sched_day")
        self.assertIsNone(W.decide(at("2026-11-12 05:59"), "schedule", [dt.date(2026, 11, 12)])[0])
        self.assertIsNone(W.decide(at("2026-11-14 07:10"), "schedule", [dt.date(2026, 11, 14)])[0])               # Saturday
        self.assertIsNone(W.decide(at("2026-11-12 07:10"), "schedule", [dt.date(2026, 11, 13)])[0])               # covers tomorrow only

    def test_daytime_updates(self):
        self.assertEqual(W.decide(at("2026-03-31 11:44"), "update", [dt.date(2026, 3, 31)])[0], "sched_day")      # the Mar 31 example
        self.assertEqual(W.decide(at("2026-03-31 09:00"), "schedule", [dt.date(2026, 3, 31)])[0], "sched_day")
        self.assertIsNone(W.decide(at("2026-03-31 17:00"), "update", [dt.date(2026, 3, 31)])[0])                  # after the window
        self.assertIsNone(W.decide(at("2026-03-31 11:44"), "schedule", [dt.date(2026, 4, 1)])[0])                 # tomorrow only: log
        self.assertIsNone(W.decide(at("2026-03-29 11:44"), "update", [dt.date(2026, 3, 29)])[0])                  # Sunday


class Parse(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(W.kind_of("President Trump's Daily Schedule\nFriday, Nov. 13, 2026"), "schedule")
        self.assertEqual(W.kind_of("The White House has just added an executive order signing at 5PM to the daily guidance"), "update")
        self.assertEqual(W.kind_of("UPDATED daily guidance: the signing moved to 6 PM"), "update")
        self.assertIsNone(W.kind_of("In-town pool report 4: the White House has called a lid"))

    def test_days(self):
        self.assertEqual(W.days_covered("📅 FRIDAY, NOVEMBER 13\n...\n📅 SATURDAY, NOVEMBER 14", 2026), [dt.date(2026, 11, 13), dt.date(2026, 11, 14)])
        self.assertEqual(W.days_covered("Daily Schedule\nThursday, Nov. 12, 2026", 2026), [dt.date(2026, 11, 12)])

    def test_date_range_without_headers_warns_not_first_date(self):
        t = "President Trump's Daily Schedule\nTuesday-Thursday, Oct. 13-15, 2026\n9:00 AM Executive Time"
        d = W.days_covered(t, 2026)
        self.assertEqual(d, [])                                  # not [Oct 13]
        self.assertTrue(W.range_without_days(t, d))
        t2 = t + "\nTUESDAY, OCT. 13\n9:00 AM x\nWEDNESDAY, OCTOBER 14\n"
        d2 = W.days_covered(t2, 2026)
        self.assertEqual(d2, [dt.date(2026, 10, 13), dt.date(2026, 10, 14)]); self.assertFalse(W.range_without_days(t2, d2))


class Dispatch(unittest.TestCase):
    def test_no_target(self):
        import os
        for k in ("DISPATCH_TOKEN", "TARGET_REPO"): os.environ.pop(k, None)
        self.assertEqual(W.dispatch("sched_day"), "no target configured")


if __name__ == "__main__":
    unittest.main()

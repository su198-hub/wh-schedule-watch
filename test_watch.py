"""Offline tests for watch.py's windows and parsing (standard library only):  python -m unittest test_watch"""
import csv, datetime as dt, json, os, shutil, tempfile, unittest
from zoneinfo import ZoneInfo
import watch as W

ET = ZoneInfo("America/New_York")
REAL_WEEKEND = ("President Trump's Weekend Schedule\nFriday, Nov. 13 - Sunday, Nov. 15, 2026\n"
                "9:00 AM THE PRESIDENT participates in Executive Time\n")


def at(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=ET)


class Decide(unittest.TestCase):
    def test_evening_weekday_later_day_dispatches(self):
        self.assertEqual(W.decide(at("2026-11-12 22:10"), "schedule", [dt.date(2026, 11, 13)])[0], "sched")      # Thu night, Friday
        self.assertEqual(W.decide(at("2026-11-12 17:00"), "schedule", [dt.date(2026, 11, 13)])[0], "sched")
        self.assertEqual(W.decide(at("2026-11-12 23:59"), "schedule", [dt.date(2026, 11, 13)])[0], "sched")
        self.assertIsNone(W.decide(at("2026-11-12 16:59"), "schedule", [dt.date(2026, 11, 13)])[0])               # before the window
        self.assertIsNone(W.decide(at("2026-11-14 20:00"), "schedule", [dt.date(2026, 11, 15)])[0])               # Saturday evening
        self.assertIsNone(W.decide(at("2026-11-12 22:10"), "schedule", [dt.date(2026, 11, 12)])[0])               # covers no later day

    def test_sunday_evening_is_log_only(self):
        j, why = W.decide(at("2026-11-15 20:30"), "schedule", [dt.date(2026, 11, 16)])
        self.assertIsNone(j); self.assertIn("log only", why)

    def test_morning_and_daytime_are_log_only_never_dispatched(self):
        for when, kind in (("2026-11-12 07:10", "schedule"), ("2026-03-31 11:44", "update"), ("2026-03-31 09:00", "schedule"),
                           ("2026-11-12 06:00", "schedule")):
            d = dt.datetime.fromisoformat(when).date()
            j, why = W.decide(at(when), kind, [d])
            self.assertIsNone(j, when); self.assertIn("log only", why)
        self.assertNotIn("sched_day", repr(W.decide(at("2026-03-31 11:44"), "update", [dt.date(2026, 3, 31)])))


class Parse(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(W.kind_of("President Trump's Daily Schedule\nFriday, Nov. 13, 2026"), "schedule")
        self.assertEqual(W.kind_of("The White House has just added an executive order signing at 5PM to the daily guidance"), "update")
        self.assertEqual(W.kind_of("UPDATED daily guidance: the signing moved to 6 PM"), "update")
        self.assertIsNone(W.kind_of("In-town pool report 4: the White House has called a lid"))

    def test_days(self):
        self.assertEqual(W.days_covered("📅 FRIDAY, NOVEMBER 13\n...\n📅 SATURDAY, NOVEMBER 14", 2026), [dt.date(2026, 11, 13), dt.date(2026, 11, 14)])
        self.assertEqual(W.days_covered("Daily Schedule\nThursday, Nov. 12, 2026", 2026), [dt.date(2026, 11, 12)])

    def test_real_weekend_title_range_reads_no_day(self):
        """the relay's real title: 'Friday, Nov. 13 - Sunday, Nov. 15, 2026'. Neither end of the range is a day."""
        d = W.days_covered(REAL_WEEKEND, 2026)
        self.assertEqual(d, [])
        self.assertTrue(W.range_without_days(REAL_WEEKEND, d))
        with_headers = REAL_WEEKEND + "📅 FRIDAY, NOVEMBER 13\n9:00 AM x\n📅 SATURDAY, NOVEMBER 14\n10:00 AM y\n📅 SUNDAY, NOVEMBER 15\n"
        d2 = W.days_covered(with_headers, 2026)
        self.assertEqual(d2, [dt.date(2026, 11, 13), dt.date(2026, 11, 14), dt.date(2026, 11, 15)])
        self.assertFalse(W.range_without_days(with_headers, d2))

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
        for k in ("DISPATCH_TOKEN", "TARGET_REPO"): os.environ.pop(k, None)
        self.assertEqual(W.dispatch("sched"), "no target configured")

    def test_failed_dispatch_is_retried_not_marked_seen(self):
        """a failed dispatch leaves the post unseen (retried next pass); five failures end it"""
        tmp = tempfile.mkdtemp()
        old = (W.SEEN, W.LOG, W.posts, W.dispatch, W.get, W.dt)
        try:
            W.SEEN, W.LOG = os.path.join(tmp, "seen.json"), os.path.join(tmp, "log.csv")
            W.posts = lambda: [("at://x", "2026-11-12T22:10:00.000Z", REAL_WEEKEND + "📅 FRIDAY, NOVEMBER 13\n9:00 AM x\n", "schedule")]
            W.get = lambda *a, **k: []
            calls = []
            W.dispatch = lambda job=None: calls.append(job) or "dispatch failed: HTTP 502"

            class FakeDT(old[5].datetime):
                @classmethod
                def now(cls, tz=None):
                    return old[5].datetime(2026, 11, 13, 3, 20, tzinfo=old[5].timezone.utc)      # Thu 22:20 ET
            W.dt = type("m", (), {"datetime": FakeDT, "timezone": old[5].timezone, "date": old[5].date, "timedelta": old[5].timedelta})
            W.main()
            self.assertEqual(json.load(open(W.SEEN))["uris"], [])                  # not seen: retried
            self.assertFalse(os.path.exists(W.LOG) and list(csv.DictReader(open(W.LOG))))      # and not logged as done
            for _ in range(4): W.main()
            self.assertEqual(len(calls), 5)
            self.assertEqual(json.load(open(W.SEEN))["uris"], ["at://x"])          # the fifth failure ends it
            self.assertIn("gave up", list(csv.DictReader(open(W.LOG)))[0]["dispatched"])
            W.dispatch = lambda job=None: "dispatched sched (HTTP 204)"
            W.main(); self.assertEqual(len(calls), 5)                              # seen now: no more calls
        finally:
            W.SEEN, W.LOG, W.posts, W.dispatch, W.get, W.dt = old
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

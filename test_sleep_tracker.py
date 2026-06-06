#!/usr/bin/env python3
"""Unit tests for sleep_tracker. Run: python3 -m unittest -v"""

import os
import tempfile
import unittest
from datetime import time

import sleep_tracker as st


def night(date_s, bedtime, wake, total, restless=10, awakenings=1,
          resting_hr=55, notes=None):
    """Build an entry dict with derived fields, mirroring all_entries()."""
    bt, wt = st.parse_time(bedtime), st.parse_time(wake)
    tib = st.time_in_bed_min(bt, wt)
    return {
        "date": date_s, "bedtime": bedtime, "wake_time": wake,
        "total_sleep_min": total, "restless_moments": restless,
        "awakenings": awakenings, "resting_hr": resting_hr, "notes": notes,
        "_date": st.parse_date(date_s), "tib_min": tib,
        "efficiency": st.sleep_efficiency(total, tib),
    }


def week(eff_pct, start_day=1, restless=10, bedtime="23:00", wake="07:00"):
    """7 nights all at a target efficiency (tib derived from times)."""
    tib = st.time_in_bed_min(st.parse_time(bedtime), st.parse_time(wake))
    total = int(round(tib * eff_pct / 100))
    return [night(f"2026-01-{start_day + i:02d}", bedtime, wake, total, restless)
            for i in range(7)]


class TestTimeMath(unittest.TestCase):
    def test_time_in_bed_crosses_midnight(self):
        self.assertEqual(st.time_in_bed_min(time(22, 58), time(8, 27)), 569)

    def test_time_in_bed_same_evening_to_morning(self):
        self.assertEqual(st.time_in_bed_min(time(23, 0), time(7, 0)), 480)

    def test_efficiency(self):
        self.assertAlmostEqual(st.sleep_efficiency(440, 569), 77.33, places=1)

    def test_efficiency_zero_tib(self):
        self.assertEqual(st.sleep_efficiency(100, 0), 0.0)

    def test_subtract_minutes_wraps_past_midnight(self):
        self.assertEqual(st.subtract_minutes_from_time(time(8, 0), 540), time(23, 0))

    def test_parse_time_formats(self):
        self.assertEqual(st.parse_time("8:05"), time(8, 5))
        self.assertEqual(st.parse_time("22:58"), time(22, 58))

    def test_bedtime_drift_is_minimal_wrap(self):
        # 23:30 vs 00:30 should be 60 min, not 1380
        self.assertEqual(st._bedtime_drift_min(time(23, 30), time(0, 30)), 60)


class TestPrescribedWindow(unittest.TestCase):
    def test_floor_applies(self):
        # avg sleep tiny -> window floored at 330
        entries = [night("2026-01-01", "23:00", "07:00", 100)]
        self.assertEqual(st.prescribed_window_min(entries), st.WINDOW_FLOOR_MIN)

    def test_avg_plus_padding(self):
        entries = [night(f"2026-01-0{i+1}", "23:00", "07:00", 400) for i in range(7)]
        self.assertEqual(st.prescribed_window_min(entries), 430)  # 400 + 30

    def test_bedtime_from_window(self):
        self.assertEqual(st.prescribed_bedtime(480, time(7, 0)), time(23, 0))


class TestWeeklyAdjustment(unittest.TestCase):
    def test_expand_above_90(self):
        msg = st.weekly_adjustment(week(92))[0]
        self.assertIn("expand", msg.lower())

    def test_hold_85_to_90(self):
        msg = st.weekly_adjustment(week(88))[0]
        self.assertIn("hold", msg.lower())

    def test_trim_below_85(self):
        msg = st.weekly_adjustment(week(80))[0]
        self.assertIn("trim", msg.lower())

    def test_none_without_full_week(self):
        self.assertIsNone(st.weekly_adjustment(week(90)[:5]))


class TestTrends(unittest.TestCase):
    def test_restless_improving(self):
        entries = [night(f"2026-01-{i+1:02d}", "23:00", "07:00", 400,
                         restless=100 - i * 5) for i in range(14)]
        rt = st.restless_trend(entries)
        self.assertTrue(rt["improving"])
        self.assertLess(rt["slope"], 0)

    def test_restless_not_improving_when_flat(self):
        entries = [night(f"2026-01-{i+1:02d}", "23:00", "07:00", 400, restless=50)
                   for i in range(14)]
        rt = st.restless_trend(entries)
        self.assertFalse(rt["improving"])

    def test_trend_none_with_too_few(self):
        self.assertIsNone(st.restless_trend(week(90)[:3]))


class TestNudges(unittest.TestCase):
    def test_caffeine_after_noon_detected(self):
        self.assertTrue(st._caffeine_after_noon("coffee in the afternoon"))
        self.assertTrue(st._caffeine_after_noon("espresso at 3pm"))

    def test_caffeine_morning_not_flagged(self):
        self.assertFalse(st._caffeine_after_noon("coffee with breakfast"))

    def test_low_efficiency_nudge(self):
        e = [night("2026-01-01", "23:00", "07:00", 360)]  # 75%
        nudges = st.daily_nudges(e, 480, time(7, 0))
        self.assertTrue(any("stimulus-control" in n.lower() for n in nudges))

    def test_alcohol_flag(self):
        e = [night("2026-01-01", "23:00", "07:00", 460, notes="had wine")]
        nudges = st.daily_nudges(e, 480, time(7, 0))
        self.assertTrue(any("alcohol" in n.lower() for n in nudges))


class TestGuardrails(unittest.TestCase):
    def _three_low_adherent_weeks(self):
        # Low total sleep (250m) floors the window at 330m. Bedtime 01:30 with a
        # 07:00 wake gives tib == window (330m) => perfect adherence, yet
        # efficiency = 250/330 = 75.8% (<85). This is the realistic case the
        # doctor flag is meant to catch.
        entries = []
        for i in range(21):
            entries.append(night(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                                  "01:30", "07:00", 250, restless=20))
        return entries

    def test_doctor_flag_fires_with_good_adherence(self):
        entries = self._three_low_adherent_weeks()
        # sanity: adherence really is good
        wk = st.weekly_blocks(entries)[-1]
        self.assertLessEqual(st.weekly_adherence(wk), st.CONSISTENCY_DRIFT_MIN)
        flags = st.doctor_flags(entries)
        self.assertTrue(any("doctor" in f.lower() for f in flags))

    def test_no_doctor_flag_when_adherence_drifts(self):
        # Same low efficiency but bedtimes far from the prescribed window:
        # should NOT escalate to a doctor (tighten consistency first).
        entries = [night(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                         "22:00", "07:00", 380, restless=20) for i in range(21)]
        flags = st.doctor_flags(entries)
        self.assertFalse(any("doctor" in f.lower() and "adherence" in f.lower()
                             for f in flags))

    def test_fatigue_note_triggers_doctor_flag(self):
        e = week(90)
        e[-1]["notes"] = "felt exhausted, unrefreshing sleep"
        flags = st.doctor_flags(e)
        self.assertTrue(any("doctor" in f.lower() for f in flags))

    def test_no_doctor_flag_when_efficiency_ok(self):
        self.assertEqual(st.doctor_flags(week(92) * 3), [])


class TestSparkline(unittest.TestCase):
    def test_sparkline_length(self):
        s = st.sparkline([1, 2, 3, 4, 5])
        self.assertEqual(len(s), 5)

    def test_sparkline_flat(self):
        self.assertEqual(st.sparkline([5, 5, 5]), st.SPARK_CHARS[0] * 3)

    def test_sparkline_empty(self):
        self.assertEqual(st.sparkline([]), "")


class TestDBRoundTrip(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = st.connect(self.path)
        st.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_upsert_and_read(self):
        st.upsert_entry(self.conn, {
            "date": "2026-01-01", "bedtime": "23:00", "wake_time": "07:00",
            "total_sleep_min": 420, "restless_moments": 12, "awakenings": 2,
            "resting_hr": 55, "notes": "x",
        })
        rows = st.all_entries(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tib_min"], 480)
        self.assertAlmostEqual(rows[0]["efficiency"], 87.5, places=1)

    def test_upsert_replaces(self):
        base = {"date": "2026-01-01", "bedtime": "23:00", "wake_time": "07:00",
                "total_sleep_min": 400, "restless_moments": 1, "awakenings": None,
                "resting_hr": None, "notes": None}
        st.upsert_entry(self.conn, base)
        base["total_sleep_min"] = 450
        st.upsert_entry(self.conn, base)
        rows = st.all_entries(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_sleep_min"], 450)

    def test_settings(self):
        st.set_setting(self.conn, "wake_time", "06:30")
        self.assertEqual(st.get_setting(self.conn, "wake_time"), "06:30")

    def test_logging_streak(self):
        for d in ("2026-01-01", "2026-01-02", "2026-01-03"):
            st.upsert_entry(self.conn, {
                "date": d, "bedtime": "23:00", "wake_time": "07:00",
                "total_sleep_min": 400, "restless_moments": 1,
                "awakenings": None, "resting_hr": None, "notes": None})
        self.assertEqual(st.logging_streak(st.all_entries(self.conn)), 3)

    def test_metric_columns_persist_and_coalesce(self):
        # write a night with metrics
        st.upsert_entry(self.conn, {
            "date": "2026-01-01", "bedtime": "23:00", "wake_time": "07:00",
            "total_sleep_min": 400, "restless_moments": 1, "awakenings": None,
            "resting_hr": None, "notes": None,
            "steps": 8000, "stress_avg": 35, "hrv_overnight": 60.5})
        row = st.all_entries(self.conn)[0]
        self.assertEqual(row["steps"], 8000)
        self.assertEqual(row["stress_avg"], 35)
        self.assertAlmostEqual(row["hrv_overnight"], 60.5)
        # a later write WITHOUT metrics must not wipe them (COALESCE)
        st.upsert_entry(self.conn, {
            "date": "2026-01-01", "bedtime": "22:30", "wake_time": "07:00",
            "total_sleep_min": 420, "restless_moments": 2, "awakenings": None,
            "resting_hr": None, "notes": "edited"})
        row = st.all_entries(self.conn)[0]
        self.assertEqual(row["total_sleep_min"], 420)   # core overwritten
        self.assertEqual(row["steps"], 8000)            # metric preserved


class TestCorrelation(unittest.TestCase):
    def test_pearson_perfect(self):
        self.assertAlmostEqual(st._pearson([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)
        self.assertAlmostEqual(st._pearson([1, 2, 3, 4], [8, 6, 4, 2]), -1.0)

    def test_pearson_undefined_constant(self):
        self.assertIsNone(st._pearson([5, 5, 5], [1, 2, 3]))

    def test_correlate_finds_relationship(self):
        # stress goes up as efficiency goes down -> strong negative r
        entries = []
        for i in range(8):
            entries.append({"steps": None, "stress_avg": 20 + i * 5,
                            "body_battery_high": None, "body_battery_low": None,
                            "hrv_overnight": None, "respiration_avg": None,
                            "resting_hr": None,
                            "efficiency": 95 - i * 3, "restless_moments": i})
        rows = st.correlate(entries, "efficiency", min_n=5)
        top = next(r for r in rows if r["column"] == "stress_avg")
        self.assertLess(top["r"], -0.9)
        self.assertEqual(top["strength"], "strong")

    def test_correlate_skips_sparse_factors(self):
        entries = [{"steps": 100 * i, "stress_avg": None,
                    "body_battery_high": None, "body_battery_low": None,
                    "hrv_overnight": None, "respiration_avg": None,
                    "resting_hr": None, "efficiency": 80 + i,
                    "restless_moments": i} for i in range(3)]
        # only 3 nights -> below min_n=5, nothing reported
        self.assertEqual(st.correlate(entries, "efficiency", min_n=5), [])


if __name__ == "__main__":
    unittest.main()

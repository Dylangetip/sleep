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


class TestHoursBeforeBed(unittest.TestCase):
    def test_afternoon_caffeine(self):
        self.assertEqual(st._hours_before_bed("15:00", time(23, 0)), 8.0)

    def test_after_bedtime_clamps_to_zero(self):
        self.assertEqual(st._hours_before_bed("23:30", time(23, 0)), 0.0)

    def test_missing_inputs(self):
        self.assertIsNone(st._hours_before_bed(None, time(23, 0)))
        self.assertIsNone(st._hours_before_bed("15:00", None))


class TestSchemaAndMerge(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    def tearDown(self):
        os.remove(self.path)

    def _legacy_db(self):
        """A pre-change DB: NOT NULL sleep cols, no optional columns."""
        import sqlite3
        c = sqlite3.connect(self.path)
        c.executescript(
            "CREATE TABLE entries (date TEXT PRIMARY KEY, bedtime TEXT NOT NULL,"
            " wake_time TEXT NOT NULL, total_sleep_min INTEGER NOT NULL,"
            " restless_moments INTEGER NOT NULL DEFAULT 0, awakenings INTEGER,"
            " resting_hr INTEGER, notes TEXT);"
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        c.execute("INSERT INTO entries VALUES ('2026-01-01','23:00','07:00',420,5,2,55,'x')")
        c.commit()
        c.close()

    def test_migration_relaxes_notnull_and_preserves_rows(self):
        self._legacy_db()
        conn = st.connect(self.path)
        st.init_db(conn)
        info = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(entries)")}
        # NOT NULL relaxed on sleep columns
        self.assertEqual(info["bedtime"], 0)
        self.assertEqual(info["total_sleep_min"], 0)
        # optional columns added
        for c in st.METRIC_COLUMN_NAMES + st.SUBJECTIVE_COLUMN_NAMES:
            self.assertIn(c, info)
        # original row intact
        rows = st.all_entries(conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_sleep_min"], 420)
        # idempotent: second init is a no-op
        st.init_db(conn)
        self.assertEqual(len(st.all_entries(conn)), 1)
        conn.close()

    def test_journal_only_insert(self):
        conn = st.connect(self.path)
        st.init_db(conn)
        st.upsert_entry(conn, {"date": "2026-02-01", "caffeine_mg": 150,
                               "caffeine_last_time": "16:00", "rested": 4})
        row = st.all_entries(conn)[0]
        self.assertIsNone(row["bedtime"])
        self.assertIsNone(row["efficiency"])      # no sleep yet
        self.assertEqual(row["caffeine_mg"], 150)
        self.assertEqual(st.scored_entries(st.all_entries(conn)), [])
        conn.close()

    def test_merge_journal_then_sleep(self):
        conn = st.connect(self.path)
        st.init_db(conn)
        st.upsert_entry(conn, {"date": "2026-02-02", "caffeine_mg": 200})
        st.upsert_entry(conn, {"date": "2026-02-02", "bedtime": "23:00",
                               "wake_time": "07:00", "total_sleep_min": 400,
                               "restless_moments": 3})
        row = st.all_entries(conn)[0]
        self.assertEqual(row["caffeine_mg"], 200)     # check-in preserved
        self.assertEqual(row["total_sleep_min"], 400)  # sleep filled in
        self.assertIsNotNone(row["efficiency"])
        conn.close()

    def test_merge_sleep_then_journal(self):
        conn = st.connect(self.path)
        st.init_db(conn)
        st.upsert_entry(conn, {"date": "2026-02-03", "bedtime": "23:00",
                               "wake_time": "07:00", "total_sleep_min": 400,
                               "restless_moments": 3, "steps": 8000})
        st.upsert_entry(conn, {"date": "2026-02-03", "rested": 5, "nap_min": 20})
        row = st.all_entries(conn)[0]
        self.assertEqual(row["total_sleep_min"], 400)  # sleep preserved
        self.assertEqual(row["steps"], 8000)           # metric preserved
        self.assertEqual(row["rested"], 5)             # check-in added
        conn.close()


class TestNullSafeReports(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = st.connect(self.path)
        st.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_reports_run_with_journal_only_night(self):
        # several scored nights + one journal-only latest day
        for i in range(8):
            st.upsert_entry(self.conn, {
                "date": f"2026-03-0{i+1}", "bedtime": "23:00", "wake_time": "07:00",
                "total_sleep_min": 400 + i, "restless_moments": 5})
        st.upsert_entry(self.conn, {"date": "2026-03-09", "caffeine_mg": 120})
        # none of these should raise
        rep = st.report_data(self.conn)
        self.assertIsNotNone(rep["last_night"])        # uses last scored night
        self.assertEqual(rep["last_night"]["date"], "2026-03-08")
        st.stats_data(self.conn)
        st.render_report(self.conn)
        st.render_trend(self.conn)

    def test_reports_run_with_only_journal_nights(self):
        st.upsert_entry(self.conn, {"date": "2026-04-01", "caffeine_mg": 100})
        rep = st.report_data(self.conn)
        self.assertIsNone(rep["last_night"])
        self.assertEqual(rep["series_14"], [])
        self.assertEqual(st.stats_data(self.conn)["best_efficiency"], None)
        st.render_report(self.conn)   # should not raise


class _FakeGarmin:
    """Minimal stand-in for garminconnect.Garmin: a night queried under
    `wake_date` whose bedtime is the previous evening (Garmin's convention)."""
    def __init__(self, bed_dt, wake_dt):
        from datetime import timezone
        self._bed = int(bed_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        self._wake = int(wake_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)

    def get_sleep_data(self, ds):
        return {"dailySleepDTO": {"calendarDate": ds, "sleepTimeSeconds": 7 * 3600,
                                  "sleepStartTimestampLocal": self._bed,
                                  "sleepEndTimestampLocal": self._wake, "awakeCount": 2},
                "restlessMomentsCount": 20, "restingHeartRate": 55}

    def get_user_summary(self, ds):
        return {"totalSteps": 8000, "averageStressLevel": 30}

    def get_hrv_data(self, ds):
        return {"hrvSummary": {"lastNightAvg": 60}}

    def get_respiration_data(self, ds):
        return {"avgSleepRespirationValue": 14}


class TestGarminDateOffset(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = st.connect(self.path)
        st.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_offset_detected_and_checkin_merges(self):
        from datetime import datetime
        api = _FakeGarmin(datetime(2026, 6, 6, 23, 10), datetime(2026, 6, 7, 7, 0))
        # a "tonight" check-in on the evening of Jun 6 targets Jun 6 + offset
        st.upsert_entry(self.conn, {"date": "2026-06-07", "caffeine_mg": 200,
                                    "caffeine_last_time": "14:30", "rested": 4})
        # next-morning sync pulls the night Garmin filed under Jun 7
        entry = st._sync_one(self.conn, api, "2026-06-07", "08:00")
        self.assertEqual(entry["date"], "2026-06-07")
        self.assertEqual(entry["bedtime"], "23:10")
        self.assertEqual(st.get_setting(self.conn, "garmin_date_offset"), "1")
        # the check-in and the sleep are now ONE merged row
        rows = st.all_entries(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["caffeine_mg"], 200)        # check-in kept
        self.assertEqual(rows[0]["total_sleep_min"], 420)    # sleep merged in
        self.assertIsNotNone(rows[0]["efficiency"])


class TestHealthData(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = st.connect(self.path)
        st.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_activities_upsert_and_query(self):
        st.upsert_activity(self.conn, {"activity_id": "A1", "date": "2026-06-07",
                                       "type": "running", "calories": 400,
                                       "training_load": 100})
        st.upsert_activity(self.conn, {"activity_id": "A1", "date": "2026-06-07",
                                       "type": "running", "calories": 450})  # update
        rows = st.all_activities(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["calories"], 450)

    def test_meals_crud(self):
        mid = st.insert_meal(self.conn, {"date": "2026-06-07", "calories": 600,
                                         "protein_g": 30, "status": "analyzed"})
        st.update_meal(self.conn, mid, {"calories": 650})
        self.assertEqual(st.get_meal(self.conn, mid)["calories"], 650)
        self.assertEqual(len(st.all_meals(self.conn, "2026-06-07")), 1)
        self.assertEqual(st.delete_meal(self.conn, mid), 1)

    def test_daily_features_merges_domains(self):
        st.upsert_entry(self.conn, {"date": "2026-06-07", "bedtime": "23:00",
                                    "wake_time": "07:00", "total_sleep_min": 420,
                                    "restless_moments": 5, "weight_kg": 80.0,
                                    "active_calories": 600})
        st.upsert_activity(self.conn, {"activity_id": "A1", "date": "2026-06-07",
                                       "calories": 400, "training_load": 90})
        st.insert_meal(self.conn, {"date": "2026-06-07", "calories": 700, "protein_g": 40})
        st.insert_meal(self.conn, {"date": "2026-06-07", "calories": 800, "protein_g": 50})
        f = next(x for x in st.daily_features(self.conn) if x["date"] == "2026-06-07")
        self.assertEqual(f["calories_in"], 1500)
        self.assertEqual(f["protein_g"], 90)
        self.assertEqual(f["activity_load"], 90)
        self.assertEqual(f["weight_kg"], 80.0)
        self.assertIsNotNone(f["efficiency"])

    def test_correlate_picks_up_diet_factor(self):
        # calories_in inversely related to efficiency across 8 days
        feats = []
        for i in range(8):
            feats.append({"date": f"2026-06-0{i+1}", "efficiency": 95 - i * 3,
                          "calories_in": 1800 + i * 150, "restless_per_hr": None})
        rows = st.correlate(feats, "efficiency", min_n=5)
        cal = next(r for r in rows if r["column"] == "calories_in")
        self.assertLess(cal["r"], -0.9)

    def test_correlate_lagged_next_day(self):
        # higher efficiency on day D -> lower resting HR on D+1
        feats = []
        for i in range(10):
            feats.append({"date": f"2026-06-{i+1:02d}", "efficiency": 80 + i,
                          "resting_hr": 60 - i})
        res = st.correlate_lagged(feats, "efficiency", "resting_hr", lag=1, min_n=5)
        self.assertIsNotNone(res)
        self.assertLess(res["r"], -0.9)


class TestMealAI(unittest.TestCase):
    def test_analyze_meal_photo_with_fake_client(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        import ai

        # a tiny temp image
        fd, img_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        Image.new("RGB", (32, 32), (120, 80, 60)).save(img_path, "JPEG")

        class _Parsed:
            calories, protein_g, carbs_g, fat_g = 540, 35.0, 50.0, 18.0
            description = "Grilled chicken with rice and veg"
            items = ["chicken", "rice", "broccoli"]

        class _Resp:
            parsed_output = _Parsed()

        class _Msgs:
            def parse(self, **kw):
                # assert the image + text were passed
                content = kw["messages"][0]["content"]
                assert any(b["type"] == "image" for b in content)
                return _Resp()

        class _FakeClient:
            messages = _Msgs()

        orig = ai._client
        ai._client = lambda api_key: _FakeClient()
        try:
            out = ai.analyze_meal_photo(img_path, notes="big portion", api_key="x")
        finally:
            ai._client = orig
            os.remove(img_path)
        self.assertEqual(out["calories"], 540)
        self.assertEqual(out["protein_g"], 35.0)
        self.assertIn("chicken", out["ai_items_json"])

    def test_analyze_meal_text_with_fake_client(self):
        import ai

        class _Parsed:
            calories, protein_g, carbs_g, fat_g = 480, 32.0, 55.0, 14.0
            description = "Chicken burrito bowl"
            items = ["chicken", "rice", "beans"]

        class _Resp:
            parsed_output = _Parsed()

        class _Msgs:
            def parse(self, **kw):
                # text-only: the single content string carries the description
                assert "burrito" in kw["messages"][0]["content"]
                return _Resp()

        class _FakeClient:
            messages = _Msgs()

        orig = ai._client
        ai._client = lambda api_key: _FakeClient()
        try:
            out = ai.analyze_meal_text("chicken burrito bowl, no sour cream", api_key="x")
        finally:
            ai._client = orig
        self.assertEqual(out["calories"], 480)
        self.assertIn("beans", out["ai_items_json"])


class TestReminders(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = st.connect(self.path)
        st.init_db(self.conn)
        # 7 scored nights at ~88%: window = avg sleep + 30
        for i in range(7):
            st.upsert_entry(self.conn, {
                "date": f"2026-06-0{i+1}", "bedtime": "23:30", "wake_time": "07:30",
                "total_sleep_min": 420, "restless_moments": 5})
        st.set_setting(self.conn, "wake_time", "07:30")
        # window = 450 -> prescribed bedtime 23:59 (before midnight)

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_winddown_fires_inside_window_once(self):
        from datetime import datetime
        bed = st.prescribed_bedtime(
            st.prescribed_window_min(st.all_entries(self.conn)),
            st.parse_time("07:30"))
        bed_dt = st._bedtime_dt_for(datetime(2026, 6, 8, 12, 0), bed,
                                    st.parse_time("07:30"))
        inside = bed_dt - __import__("datetime").timedelta(minutes=40)
        due = st.due_reminders(self.conn, inside)
        self.assertTrue(any(k == "winddown" for k, _, _ in due))
        # marking it fired suppresses a second one the same day
        st.set_setting(self.conn, "reminder_last_winddown",
                       inside.date().isoformat())
        due2 = st.due_reminders(self.conn, inside)
        self.assertFalse(any(k == "winddown" for k, _, _ in due2))

    def test_winddown_not_outside_window(self):
        from datetime import datetime
        early = datetime(2026, 6, 8, 18, 0)   # hours before bedtime
        due = st.due_reminders(self.conn, early)
        self.assertFalse(any(k == "winddown" for k, _, _ in due))

    def test_checkin_fires_when_empty_and_not_after_checkin(self):
        from datetime import datetime
        evening = datetime(2026, 6, 8, 21, 30)
        due = st.due_reminders(self.conn, evening)
        self.assertTrue(any(k == "checkin" for k, _, _ in due))
        # doing tonight's check-in (today + offset 1 => 2026-06-09) suppresses it
        st.upsert_entry(self.conn, {"date": "2026-06-09", "rested": 4})
        due2 = st.due_reminders(self.conn, evening)
        self.assertFalse(any(k == "checkin" for k, _, _ in due2))

    def test_bedtime_past_midnight_rolls_to_tomorrow(self):
        from datetime import datetime, time as _t
        wake = st.parse_time("08:00")
        bd = st._bedtime_dt_for(datetime(2026, 6, 8, 23, 0), _t(0, 30), wake)
        self.assertEqual(bd.day, 9)   # 00:30 belongs to tomorrow
        bd2 = st._bedtime_dt_for(datetime(2026, 6, 8, 23, 0), _t(23, 30), wake)
        self.assertEqual(bd2.day, 8)

    def test_reminders_plist(self):
        p = st.build_reminders_plist()
        self.assertEqual(p["Label"], "com.sleeptracker.reminders")
        self.assertIn("remind", p["ProgramArguments"])
        self.assertEqual(p["StartInterval"], 600)
        self.assertIn("SLEEP_DB", p["EnvironmentVariables"])


if __name__ == "__main__":
    unittest.main()

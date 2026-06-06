#!/usr/bin/env python3
"""
backfill.py — bulk-import a date range of sleep + health metrics from Garmin.

Logs in to Garmin Connect ONCE, then walks day by day pulling each night's
sleep plus the daytime/recovery metrics (stress, Body Battery, overnight HRV,
breathing rate, steps) and upserts them into the same SQLite database the CLI
and web app use. Re-runnable and idempotent — existing nights are updated in
place, never duplicated.

Usage:
    export GARMIN_EMAIL="you@example.com"
    export GARMIN_PASSWORD="..."
    python3 backfill.py 2025-12-06            # start date -> today
    python3 backfill.py 2025-12-06 2026-03-01 # explicit start and end

Credentials are read from the environment only and never stored. This uses the
unofficial `garminconnect` library: pip install garminconnect
"""

import os
import sys
import time
from datetime import date, datetime, timedelta

import sleep_tracker as st

try:
    from garminconnect import Garmin
except ImportError:
    sys.exit("The garminconnect library is missing. Run: pip install garminconnect")


def main():
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        sys.exit("Set GARMIN_EMAIL and GARMIN_PASSWORD first (the two export lines).")

    if len(sys.argv) < 2:
        sys.exit("Usage: python3 backfill.py START [END]   dates as YYYY-MM-DD")
    start = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    end = (datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
           if len(sys.argv) > 2 else date.today())

    conn = st.connect(st.DEFAULT_DB)
    st.init_db(conn)
    wake_default = st.get_setting(conn, "wake_time", st.DEFAULT_WAKE_TIME)

    print(f"Logging in to Garmin as {email} ...")
    api = Garmin(email, password)
    api.login()
    print(f"Pulling sleep + metrics from {start} to {end} (database: {st.DEFAULT_DB}) ...")

    saved = skipped = failed = 0
    d = start
    while d <= end:
        ds = d.isoformat()
        try:
            entry = st.fetch_garmin_night(api, ds, wake_default)
            if not entry:
                print(f"  {ds}: no data")
                skipped += 1
            else:
                st.upsert_entry(conn, entry)
                print(f"  {ds}: {entry['bedtime']} -> {entry['wake_time']}  "
                      f"{st.minutes_to_hm(entry['total_sleep_min'])}  "
                      f"| stress {entry.get('stress_avg')}  steps {entry.get('steps')}")
                saved += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  {ds}: error: {exc}")
            failed += 1
        time.sleep(0.7)  # be polite to Garmin's servers
        d += timedelta(days=1)

    conn.close()
    print(f"\nDone. Saved {saved} | no-data {skipped} | errors {failed}")


if __name__ == "__main__":
    main()

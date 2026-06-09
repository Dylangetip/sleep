#!/usr/bin/env python3
"""
sleep_tracker.py — a personal sleep tracker + CBT-I suggestion engine.

A single-file Python CLI backed by SQLite. It logs nightly sleep metrics,
computes sleep efficiency and trailing averages, and produces evidence-based
suggestions grounded in CBT-I (sleep restriction + stimulus control).

This tool provides general, evidence-based guidance only. It is NOT a medical
device and does NOT diagnose anything. See the disclaimer printed with every
report.

Usage examples:
    python sleep_tracker.py log --date 2026-06-05 --bedtime 22:58 \
        --wake 08:27 --total-sleep 440 --restless 80 --resting-hr 56
    python sleep_tracker.py log            # interactive prompts
    python sleep_tracker.py report
    python sleep_tracker.py trend
    python sleep_tracker.py set-wake 08:30
    python sleep_tracker.py seed           # insert the example night
    python sleep_tracker.py sync-garmin --date 2026-06-05   # optional
"""

import argparse
import csv
import os
import shutil
import sqlite3
import sys
from datetime import datetime, date, time, timedelta

# --------------------------------------------------------------------------- #
# Constants / configuration
# --------------------------------------------------------------------------- #


def app_data_dir():
    """Stable per-user data directory (DB, Garmin tokens, sync log) so the data
    survives re-downloading or moving the project folder."""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/SleepTracker")
    else:
        base = os.path.join(
            os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
            "SleepTracker",
        )
    os.makedirs(base, exist_ok=True)
    return base


def default_db_path():
    """SLEEP_DB env override, else <app-data>/sleep.db."""
    env = os.environ.get("SLEEP_DB")
    if env:
        return env
    return os.path.join(app_data_dir(), "sleep.db")


DEFAULT_DB = default_db_path()
GARMIN_TOKEN_DIR = os.path.join(app_data_dir(), "garth")
DAILY_SYNC_LOG = os.path.join(app_data_dir(), "daily-sync.log")
LAUNCHD_LABEL = "com.sleeptracker.dailysync"

# CBT-I sleep-restriction parameters
WINDOW_PADDING_MIN = 30          # avg total sleep + this = prescribed window
WINDOW_FLOOR_MIN = 330           # hard floor of 5.5 hours
DEFAULT_WAKE_TIME = "08:00"      # used until the user sets their own fixed wake

# Efficiency thresholds (percent)
EFF_HIGH = 90.0                  # > 90 -> expand window
EFF_OK_LOW = 85.0                # 85-90 -> hold; < 85 -> hold/trim + nudges

CONSISTENCY_DRIFT_MIN = 60       # bedtime drift that triggers a consistency nudge

# Core night columns. Sleep fields are nullable so a subjective "check-in" can be
# logged before that night's Garmin sleep is synced. (name -> column DDL type.)
CORE_COLUMN_TYPES = [
    ("date", "TEXT PRIMARY KEY"),
    ("bedtime", "TEXT"),
    ("wake_time", "TEXT"),
    ("total_sleep_min", "INTEGER"),
    ("restless_moments", "INTEGER DEFAULT 0"),
    ("awakenings", "INTEGER"),
    ("resting_hr", "INTEGER"),
    ("notes", "TEXT"),
]
CORE_COLUMNS = [c for c, _ in CORE_COLUMN_TYPES]

# Optional Garmin health-metric columns (added by migration; nullable).
METRIC_COLUMNS = [
    ("steps", "INTEGER"),
    ("stress_avg", "INTEGER"),
    ("body_battery_high", "INTEGER"),
    ("body_battery_low", "INTEGER"),
    ("hrv_overnight", "REAL"),
    ("respiration_avg", "REAL"),
]
METRIC_COLUMN_NAMES = [c for c, _ in METRIC_COLUMNS]

# Optional subjective daily check-in columns (things Garmin can't see).
SUBJECTIVE_COLUMNS = [
    ("caffeine_mg", "INTEGER"),
    ("caffeine_last_time", "TEXT"),
    ("nap_min", "INTEGER"),
    ("last_meal_time", "TEXT"),
    ("wind_down", "INTEGER"),
    ("screens_before_bed", "INTEGER"),
    ("mood", "INTEGER"),
    ("rested", "INTEGER"),
    ("daytime_sleepiness", "INTEGER"),
]
SUBJECTIVE_COLUMN_NAMES = [c for c, _ in SUBJECTIVE_COLUMNS]

# Optional body / daily-summary columns from the wider Garmin pull (nullable).
# Weight is stored canonically in kilograms; convert on display per weight_unit.
BODY_COLUMNS = [
    ("weight_kg", "REAL"),
    ("body_fat_pct", "REAL"),
    ("vo2max", "REAL"),
    ("training_readiness", "INTEGER"),
    ("hydration_ml", "INTEGER"),
    ("intensity_moderate_min", "INTEGER"),
    ("intensity_vigorous_min", "INTEGER"),
    ("active_calories", "INTEGER"),
    ("resting_calories", "INTEGER"),
    ("floors_climbed", "INTEGER"),
    ("daily_distance_m", "REAL"),
]
BODY_COLUMN_NAMES = [c for c, _ in BODY_COLUMNS]

# Every optional/nullable column, and the full ordered column list.
OPTIONAL_COLUMNS = METRIC_COLUMNS + SUBJECTIVE_COLUMNS + BODY_COLUMNS
ALL_COLUMNS = (CORE_COLUMNS + METRIC_COLUMN_NAMES + SUBJECTIVE_COLUMN_NAMES
               + BODY_COLUMN_NAMES)

# Factors correlated against sleep (column -> human label). resting_hr is a core
# column but is a useful factor too; the *_hours_before_bed are derived.
CORRELATION_FACTORS = [
    ("steps", "daily steps"),
    ("stress_avg", "average daytime stress"),
    ("body_battery_high", "Body Battery peak"),
    ("body_battery_low", "Body Battery low point"),
    ("hrv_overnight", "overnight HRV"),
    ("respiration_avg", "overnight breathing rate"),
    ("resting_hr", "resting heart rate"),
    ("caffeine_mg", "caffeine intake"),
    ("caffeine_hours_before_bed", "caffeine timing before bed"),
    ("last_meal_hours_before_bed", "last meal timing before bed"),
    ("nap_min", "daytime napping"),
    ("wind_down", "wind-down routine"),
    ("screens_before_bed", "screens before bed"),
    ("mood", "daytime mood"),
    ("rested", "how rested you felt"),
    ("daytime_sleepiness", "daytime sleepiness"),
    # body / activity / diet (daily) factors
    ("active_calories", "active calories burned"),
    ("intensity_vigorous_min", "vigorous-intensity minutes"),
    ("training_readiness", "training readiness"),
    ("hydration_ml", "hydration"),
    ("weight_kg", "body weight"),
    ("calories_in", "calories eaten"),
    ("protein_g", "protein eaten"),
    ("carbs_g", "carbs eaten"),
    ("fat_g", "fat eaten"),
    ("activity_load", "training load"),
    ("activity_count", "number of workouts"),
]

# Practical, do-this-tonight takeaways per factor (shown next to each insight).
FACTOR_ACTIONS = {
    "hrv_overnight": "Usually the strongest signal. Check HRV each morning as a "
        "read on how recovered you are, and protect it: steady wind-down, and "
        "go easy on hard late workouts.",
    "resting_hr": "A higher-than-usual resting HR (often late meals, illness or a "
        "stressful day) is an early warning — on those days wind down earlier.",
    "body_battery_high": "A higher daytime Body Battery peak means more in the "
        "recovery tank. Defend it with real breaks during the day, not just at night.",
    "body_battery_low": "Running the tank to empty by evening tracks with rougher "
        "nights — build in recovery breaks so you don't bottom out.",
    "stress_avg": "On high-stress days, schedule a genuine buffer before bed — a "
        "walk, breathing, and no work or screens in the last hour.",
    "respiration_avg": "Elevated overnight breathing often follows late meals or "
        "stress — watch it alongside those habits.",
    "steps": "Step count barely moves your sleep, so don't chase a step goal for "
        "sleep's sake — put that energy into the recovery signals instead.",
    "caffeine_mg": "Cut total caffeine or shift it earlier — it has a ~5-6h "
        "half-life, so afternoon cups still circulate at bedtime.",
    "caffeine_hours_before_bed": "Aim to finish caffeine ~8-10h before bed; the "
        "more hours of buffer, the cleaner your sleep tends to be.",
    "last_meal_hours_before_bed": "Try to finish eating ~3h before bed — late "
        "meals can fragment sleep and raise overnight heart rate.",
    "nap_min": "Long or late naps bleed off sleep pressure; if you nap, keep it "
        "under ~20 min and before mid-afternoon.",
    "wind_down": "Your wind-down routine is paying off — keep protecting that "
        "last hour before bed.",
    "screens_before_bed": "Screens in the last hour track with worse nights — try "
        "dimming and switching to something offline before bed.",
    "mood": "Lower-mood days tend to sleep worse — worth a decompression buffer "
        "in the evening.",
    "rested": "How rested you feel is your own readout — use it to gauge whether "
        "the window and habits are working.",
    "daytime_sleepiness": "Persistent daytime sleepiness is the symptom that "
        "matters most — if it stays high, revisit the window and see a clinician.",
    "active_calories": "More active calories often deepen sleep — but very hard "
        "or very late sessions can backfire; watch the timing.",
    "intensity_vigorous_min": "Vigorous exercise generally helps sleep when it's "
        "not too close to bedtime — aim to finish 3+ hours before bed.",
    "training_readiness": "Readiness blends sleep, HRV and load — when it's low, "
        "favor an easier day and protect tonight's wind-down.",
    "hydration_ml": "Stay hydrated through the day, but taper fluids near bedtime "
        "to cut nighttime awakenings.",
    "weight_kg": "Weight trends move slowly; line it up against sleep and intake "
        "over weeks, not single days.",
    "calories_in": "Big calorie days — especially late — can disrupt sleep; keep "
        "dinners earlier and lighter.",
    "protein_g": "Protein supports recovery; spread it across the day rather than "
        "a heavy late load.",
    "carbs_g": "Late, heavy carbs can spike then crash overnight — watch evening "
        "portions.",
    "fat_g": "High-fat late meals digest slowly and can fragment sleep — keep them "
        "earlier.",
    "activity_load": "Training load drives fitness but also fatigue — balance hard "
        "days with recovery and watch HRV.",
    "activity_count": "More sessions isn't always better — recovery is where the "
        "adaptation happens.",
}

DISCLAIMER = (
    "This tool offers general, evidence-based guidance only and does NOT "
    "provide a diagnosis."
)

DROWSINESS_NOTE = (
    "Heads-up: sleep restriction can cause daytime drowsiness during the first "
    "1-2 weeks. Be cautious about driving or operating vehicles/machinery while "
    "sleepy."
)

# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _entries_ddl(table="entries"):
    """Full CREATE TABLE for entries with nullable sleep columns + all optional
    columns, built from the column constants (one source of truth)."""
    cols = [f"{name} {ddl}" for name, ddl in CORE_COLUMN_TYPES]
    cols += [f"{name} {ddl}" for name, ddl in OPTIONAL_COLUMNS]
    return f"CREATE TABLE {table} (\n  " + ",\n  ".join(cols) + "\n);"


def init_db(conn):
    conn.executescript(
        _entries_ddl().replace("CREATE TABLE entries",
                               "CREATE TABLE IF NOT EXISTS entries")
        + """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activities (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id   TEXT UNIQUE,
            date          TEXT,
            type          TEXT,
            name          TEXT,
            start_time    TEXT,
            duration_min  REAL,
            distance_m    REAL,
            calories      INTEGER,
            avg_hr        INTEGER,
            max_hr        INTEGER,
            training_load REAL,
            notes         TEXT
        );
        CREATE TABLE IF NOT EXISTS meals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT,
            time          TEXT,
            photo_path    TEXT,
            notes         TEXT,
            calories      INTEGER,
            protein_g     REAL,
            carbs_g       REAL,
            fat_g         REAL,
            ai_description TEXT,
            ai_items_json TEXT,
            status        TEXT,
            created_at    TEXT,
            analyzed_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_summaries (
            date         TEXT PRIMARY KEY,
            summary      TEXT,
            model        TEXT,
            generated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date);
        CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);
        """
    )

    info = list(conn.execute("PRAGMA table_info(entries)"))
    existing = {row[1] for row in info}
    notnull = {row[1]: row[3] for row in info}  # column -> NOT NULL flag

    # Add any missing optional columns (idempotent, non-destructive).
    for col, coltype in OPTIONAL_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE entries ADD COLUMN {col} {coltype}")

    # Relax NOT NULL on the sleep columns (legacy DBs) so a check-in can be
    # logged before that night's sleep is synced. SQLite can't ALTER a column's
    # NOT NULL, so rebuild the table — but ONLY when the constraint is still
    # present, making this a cheap no-op on every subsequent init.
    legacy_notnull = any(notnull.get(c) for c in
                         ("bedtime", "wake_time", "total_sleep_min"))
    if legacy_notnull:
        _rebuild_entries_relaxed(conn, existing)

    conn.commit()


def _rebuild_entries_relaxed(conn, existing_cols):
    """Rebuild `entries` with the relaxed (nullable) schema, preserving all rows.
    Runs inside a transaction; safe to crash mid-way (original table is intact)."""
    carry = [c for c in ALL_COLUMNS if c in existing_cols]
    collist = ", ".join(carry)
    conn.executescript("DROP TABLE IF EXISTS entries_new;")
    conn.execute(_entries_ddl("entries_new"))
    conn.execute(f"INSERT INTO entries_new ({collist}) SELECT {collist} FROM entries")
    conn.executescript(
        "DROP TABLE entries;\n"
        "ALTER TABLE entries_new RENAME TO entries;"
    )


def ensure_db_ready(db_path=None):
    """Make sure the app-data dir exists and, on first run, copy a legacy
    ./sleep.db into the stable location so data isn't stranded in a download
    folder. Honors an explicit SLEEP_DB (then does nothing clever)."""
    if os.environ.get("SLEEP_DB"):
        return
    target = db_path or DEFAULT_DB
    os.makedirs(os.path.dirname(target), exist_ok=True)
    legacy = os.path.join(os.getcwd(), "sleep.db")
    if not os.path.exists(target) and os.path.exists(legacy) \
            and os.path.abspath(legacy) != os.path.abspath(target):
        shutil.copy2(legacy, target)
        print(f"Copied your existing sleep.db into {target}\n"
              f"so your data persists across re-downloads.")


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Time / calculation helpers
# --------------------------------------------------------------------------- #


def parse_time(s):
    """Parse 'HH:MM' (or 'H:MM') into a datetime.time."""
    s = s.strip()
    for fmt in ("%H:%M", "%H.%M", "%I:%M%p", "%I:%M %p"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Could not parse time: {s!r} (expected HH:MM)")


def parse_date(s):
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def fmt_time(t):
    return t.strftime("%H:%M")


def time_in_bed_min(bedtime, wake_time):
    """Minutes between bedtime and wake_time, handling crossing midnight."""
    base = date(2000, 1, 1)
    bed_dt = datetime.combine(base, bedtime)
    wake_dt = datetime.combine(base, wake_time)
    if wake_dt <= bed_dt:
        wake_dt += timedelta(days=1)  # crossed midnight
    return int((wake_dt - bed_dt).total_seconds() // 60)


def sleep_efficiency(total_sleep_min, tib_min):
    if tib_min <= 0:
        return 0.0
    return total_sleep_min / tib_min * 100.0


def minutes_to_hm(mins):
    mins = int(round(mins))
    return f"{mins // 60}h {mins % 60:02d}m"


def subtract_minutes_from_time(wake_time, minutes):
    """Return the clock time that is `minutes` before wake_time."""
    base = date(2000, 1, 1)
    dt = datetime.combine(base, wake_time) - timedelta(minutes=minutes)
    return dt.time()


# --------------------------------------------------------------------------- #
# Data access + derived metrics
# --------------------------------------------------------------------------- #


def all_entries(conn):
    """Return entries oldest -> newest as a list of dicts with derived fields.
    Sleep-derived fields are None for 'check-in only' nights (no Garmin sleep
    yet) so every consumer must use .get / guard for None."""
    rows = conn.execute("SELECT * FROM entries ORDER BY date ASC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["_date"] = parse_date(d["date"])
        bt = parse_time(d["bedtime"]) if d.get("bedtime") else None
        wt = parse_time(d["wake_time"]) if d.get("wake_time") else None
        d["tib_min"] = time_in_bed_min(bt, wt) if (bt and wt) else None
        d["efficiency"] = (sleep_efficiency(d["total_sleep_min"], d["tib_min"])
                           if (d["tib_min"] and d.get("total_sleep_min") is not None)
                           else None)
        # restless moments is a raw count that grows with time in bed; the
        # per-hour rate is what's comparable across nights of different length.
        d["restless_per_hr"] = (round(d["restless_moments"] / (d["tib_min"] / 60), 2)
                                if (d["tib_min"] and d.get("restless_moments") is not None)
                                else None)
        # derived "hours before bed" for caffeine / last meal (needs bedtime)
        d["caffeine_hours_before_bed"] = _hours_before_bed(d.get("caffeine_last_time"), bt)
        d["last_meal_hours_before_bed"] = _hours_before_bed(d.get("last_meal_time"), bt)
        out.append(d)
    return out


def _hours_before_bed(event_time_str, bedtime):
    """Hours between an event clock-time (e.g. last caffeine) and bedtime, where
    the event is assumed to occur earlier the same day. Returns None if inputs
    missing; clamps to 0 if the event reads as after bedtime."""
    if not event_time_str or bedtime is None:
        return None
    try:
        ev = parse_time(event_time_str)
    except (ValueError, TypeError):
        return None
    mins = time_in_bed_min(ev, bedtime)  # ev -> bedtime, handles wrap
    if mins > 18 * 60:  # event reads as just after bedtime -> treat as ~0
        return 0.0
    return round(mins / 60, 1)


def scored_entries(entries):
    """Nights that have objective sleep data (efficiency computed)."""
    return [e for e in entries if e.get("efficiency") is not None]


# --------------------------------------------------------------------------- #
# Activities + meals (separate tables, many rows per day)
# --------------------------------------------------------------------------- #

ACTIVITY_COLUMNS = ["activity_id", "date", "type", "name", "start_time",
                    "duration_min", "distance_m", "calories", "avg_hr", "max_hr",
                    "training_load", "notes"]


def upsert_activity(conn, a):
    """Insert or update one Garmin activity, keyed by its activity_id."""
    params = {c: a.get(c) for c in ACTIVITY_COLUMNS}
    cols = ", ".join(ACTIVITY_COLUMNS)
    vals = ", ".join(f":{c}" for c in ACTIVITY_COLUMNS)
    setc = ", ".join(f"{c}=excluded.{c}" for c in ACTIVITY_COLUMNS if c != "activity_id")
    conn.execute(
        f"INSERT INTO activities ({cols}) VALUES ({vals}) "
        f"ON CONFLICT(activity_id) DO UPDATE SET {setc}", params)
    conn.commit()


def all_activities(conn, start=None, end=None):
    q = "SELECT * FROM activities"
    args = []
    if start and end:
        q += " WHERE date >= ? AND date <= ?"
        args = [start, end]
    q += " ORDER BY date ASC, start_time ASC"
    return [dict(r) for r in conn.execute(q, args)]


def insert_meal(conn, m):
    cols = ["date", "time", "photo_path", "notes", "calories", "protein_g",
            "carbs_g", "fat_g", "ai_description", "ai_items_json", "status",
            "created_at", "analyzed_at"]
    params = {c: m.get(c) for c in cols}
    cur = conn.execute(
        f"INSERT INTO meals ({', '.join(cols)}) "
        f"VALUES ({', '.join(':' + c for c in cols)})", params)
    conn.commit()
    return cur.lastrowid


def update_meal(conn, meal_id, fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=:{k}" for k in fields)
    params = dict(fields, id=meal_id)
    conn.execute(f"UPDATE meals SET {sets} WHERE id=:id", params)
    conn.commit()


def get_meal(conn, meal_id):
    row = conn.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
    return dict(row) if row else None


def all_meals(conn, date=None):
    if date:
        rows = conn.execute("SELECT * FROM meals WHERE date=? ORDER BY time", (date,))
    else:
        rows = conn.execute("SELECT * FROM meals ORDER BY date ASC, time ASC")
    return [dict(r) for r in rows]


def delete_meal(conn, meal_id):
    cur = conn.execute("DELETE FROM meals WHERE id=?", (meal_id,))
    conn.commit()
    return cur.rowcount


KG_PER_LB = 0.45359237
LB_PER_KG = 2.2046226218


def compute_targets(conn):
    """Auto-compute the calorie + macro targets from the user's Garmin TDEE
    (active + resting calories) and their goal weight — so the user only picks a
    target weight and pace, not the numbers. Returns a dict of computed targets."""
    entries = all_entries(conn)
    unit = get_setting(conn, "weight_unit", "lb")

    # TDEE = average of (active + resting) Garmin calories over recent days.
    tdees = [e["active_calories"] + e["resting_calories"] for e in entries[-21:]
             if e.get("active_calories") is not None
             and e.get("resting_calories") is not None]
    tdee = round(sum(tdees) / len(tdees)) if tdees else None

    wkg = next((e["weight_kg"] for e in reversed(entries)
                if e.get("weight_kg") is not None), None)
    cur = (round(wkg * LB_PER_KG, 1) if unit == "lb" else round(wkg, 1)) if wkg else None
    weight_lb = round(wkg * LB_PER_KG, 1) if wkg else None

    def _num(key):
        v = get_setting(conn, key)
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None

    goal = _num("weight_goal")
    pace = _num("weight_pace") or 0.5          # per week, in the display unit
    pace_lb = pace if unit == "lb" else pace * LB_PER_KG

    direction = "maintain"
    if goal is not None and cur is not None:
        thresh = 1.0 if unit == "lb" else 0.5
        if cur - goal > thresh:
            direction = "lose"
        elif cur - goal < -thresh:
            direction = "gain"

    daily_delta = round(pace_lb * 3500 / 7)    # 3500 kcal ≈ 1 lb
    calorie_target = None
    if tdee is not None:
        if direction == "lose":
            calorie_target = max(1400, tdee - daily_delta)
        elif direction == "gain":
            calorie_target = tdee + daily_delta
        else:
            calorie_target = tdee

    protein = carbs = fat = None
    if calorie_target:
        protein = round(0.8 * weight_lb) if weight_lb else round(0.3 * calorie_target / 4)
        fat = round(0.27 * calorie_target / 9)
        carbs = max(0, round((calorie_target - protein * 4 - fat * 9) / 4))

    if tdee is None:
        basis = "Sync a few days of Garmin data first (need active + resting calories)."
    else:
        verb = {"lose": "a deficit", "gain": "a surplus", "maintain": "maintenance"}[direction]
        basis = (f"Based on your Garmin TDEE (~{tdee} kcal/day) and {verb} toward "
                 f"your goal of {goal if goal is not None else '—'} {unit}.")
    return {
        "tdee": tdee, "calorie_target": calorie_target,
        "protein_g": protein, "carbs_g": carbs, "fat_g": fat,
        "direction": direction, "pace": pace, "weight_unit": unit,
        "weight": cur, "weight_goal": goal, "basis": basis,
    }


def daily_features(conn):
    """One numeric dict per date, merging sleep/body metrics (entries) with summed
    meal macros and summed activity load. The cross-domain correlation engine and
    the Diet/Activity dashboards read from this."""
    feats = {}
    for e in all_entries(conn):
        d = dict(e)
        d.pop("_date", None)
        feats[e["date"]] = d
    # meals -> calories_in / protein_g / carbs_g / fat_g per day
    for row in conn.execute(
            "SELECT date, SUM(calories) c, SUM(protein_g) p, SUM(carbs_g) cb, "
            "SUM(fat_g) f, COUNT(*) n FROM meals GROUP BY date"):
        f = feats.setdefault(row["date"], {"date": row["date"]})
        f["calories_in"] = row["c"]
        f["protein_g"] = row["p"]
        f["carbs_g"] = row["cb"]
        f["fat_g"] = row["f"]
        f["meal_count"] = row["n"]
    # activities -> load / calories / count per day
    for row in conn.execute(
            "SELECT date, SUM(training_load) load, SUM(calories) cal, "
            "SUM(duration_min) dur, COUNT(*) n FROM activities GROUP BY date"):
        f = feats.setdefault(row["date"], {"date": row["date"]})
        f["activity_load"] = row["load"]
        f["activity_calories"] = row["cal"]
        f["activity_duration_min"] = row["dur"]
        f["activity_count"] = row["n"]
    return [feats[k] for k in sorted(feats)]


def trailing(entries, n):
    """Most recent n entries (newest last)."""
    return entries[-n:] if entries else []


def avg(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def restless_trend(entries, days=14):
    """
    Is restless_moments trending DOWN over the last `days` nights?

    Uses both a first-half vs second-half average comparison and a simple
    least-squares slope. Returns a dict with the details, or None if there is
    not enough data.
    """
    recent = [e for e in trailing(entries, days)
              if e.get("restless_moments") is not None]
    if len(recent) < 4:
        return None

    vals = [e["restless_moments"] for e in recent]
    half = len(vals) // 2
    first_avg = avg(vals[:half])
    second_avg = avg(vals[half:])

    # least-squares slope of restless_moments vs index
    n = len(vals)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(vals) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, vals))
    den = sum((x - mean_x) ** 2 for x in xs) or 1
    slope = num / den

    improving = slope < 0 and (second_avg is not None and second_avg < first_avg)
    return {
        "n": n,
        "first_avg": first_avg,
        "second_avg": second_avg,
        "slope": slope,
        "improving": improving,
    }


def awakenings_trend(entries, days=14):
    recent = [e for e in trailing(entries, days) if e["awakenings"] is not None]
    if len(recent) < 4:
        return None
    vals = [e["awakenings"] for e in recent]
    half = len(vals) // 2
    first_avg = avg(vals[:half])
    second_avg = avg(vals[half:])
    improving = second_avg is not None and first_avg is not None and second_avg < first_avg
    return {"first_avg": first_avg, "second_avg": second_avg, "improving": improving}


def weekly_blocks(entries):
    """
    Split entries into consecutive 7-night blocks, anchored at the most recent
    night so the latest full week is always a clean block. Returns a list of
    blocks (each a list of entries), newest block last. Partial leading block
    (fewer than 7) is dropped from "full week" analysis by the caller.
    """
    blocks = []
    i = len(entries)
    while i - 7 >= 0:
        blocks.append(entries[i - 7 : i])
        i -= 7
    blocks.reverse()
    return blocks


# --------------------------------------------------------------------------- #
# Prescribed window (CBT-I sleep restriction)
# --------------------------------------------------------------------------- #


def prescribed_window_min(entries):
    """(7-night avg total sleep) + 30, floored at 330. Uses only nights with
    objective sleep data."""
    recent = trailing(scored_entries(entries), 7)
    if not recent:
        return WINDOW_FLOOR_MIN
    a = avg([e["total_sleep_min"] for e in recent])
    return max(WINDOW_FLOOR_MIN, int(round(a + WINDOW_PADDING_MIN)))


def prescribed_bedtime(window_min, wake_time):
    return subtract_minutes_from_time(wake_time, window_min)


# --------------------------------------------------------------------------- #
# Suggestion engine
# --------------------------------------------------------------------------- #


def weekly_adjustment(entries):
    """
    Rule 2 — only meaningful on a full 7-night block. Evaluates the most recent
    full week. Returns (headline, detail) or None if no full week yet.
    """
    blocks = weekly_blocks(scored_entries(entries))
    if not blocks:
        return None
    week = blocks[-1]
    eff = avg([e["efficiency"] for e in week])
    if eff > EFF_HIGH:
        return (
            f"Avg efficiency {eff:.1f}% (>90%): expand the window by 15-30 min.",
            "Move bedtime EARLIER by 15-30 min; keep wake time fixed.",
        )
    elif eff >= EFF_OK_LOW:
        return (
            f"Avg efficiency {eff:.1f}% (85-90%): hold the window steady another week.",
            "No change. Keep the same window for one more week and reassess.",
        )
    else:
        return (
            f"Avg efficiency {eff:.1f}% (<85%): hold, or trim 15 min.",
            "Emphasize consistency. Option to trim the window by 15 min "
            "(later bedtime, wake time fixed).",
        )


def daily_nudges(entries, window_min, wake_time):
    """Rule 3 — nudges based on the latest entry, structured check-in fields,
    and notes (as a fallback)."""
    nudges = []
    if not entries:
        return nudges
    last = entries[-1]                 # latest day (may be a check-in w/o sleep)
    scored = scored_entries(entries)
    last_scored = scored[-1] if scored else None
    notes = (last.get("notes") or "").lower()

    if last_scored is not None and last_scored["efficiency"] < EFF_OK_LOW:
        nudges.append(
            "Last night's efficiency was under 85%. Stimulus-control rule: if you "
            "are awake ~15-20 min, get out of bed, do something calm in dim light, "
            "and return only when sleepy."
        )

    # Caffeine — prefer structured fields, fall back to notes scan.
    hbb = last.get("caffeine_hours_before_bed")
    if last.get("caffeine_mg") and hbb is not None and hbb < 8:
        nudges.append(
            f"You had caffeine ~{hbb:.0f}h before bed ({last.get('caffeine_mg')} mg). "
            "Caffeine has a ~5-6h half-life — pushing your last cup earlier usually "
            "helps sleep quality."
        )
    elif _caffeine_after_noon(notes):
        nudges.append(
            "You noted caffeine in the afternoon/evening. Caffeine has a long "
            "half-life; intake after ~noon can reduce sleep quality."
        )

    if "alcohol" in notes or "wine" in notes or "beer" in notes:
        nudges.append(
            "You noted alcohol. Alcohol is a common cause of mid-night awakenings "
            "and fragmented sleep, even when it helps you fall asleep."
        )

    # Late meal nudge (structured).
    meal_hbb = last.get("last_meal_hours_before_bed")
    if meal_hbb is not None and meal_hbb < 2:
        nudges.append(
            f"Your last meal was ~{meal_hbb:.0f}h before bed. Eating close to "
            "bedtime can fragment sleep — aim to finish ~3h before."
        )

    # bedtime drift vs prescribed (needs a scored night with a bedtime)
    if last_scored is not None and last_scored.get("bedtime"):
        target_bed = prescribed_bedtime(window_min, wake_time)
        actual_bed = parse_time(last_scored["bedtime"])
        drift = _bedtime_drift_min(actual_bed, target_bed)
        if drift > CONSISTENCY_DRIFT_MIN:
            nudges.append(
                f"Your bedtime ({fmt_time(actual_bed)}) was {drift} min off the "
                f"prescribed bedtime ({fmt_time(target_bed)}). Consistency is the "
                f"anchor — aim to hit the window nightly."
            )

    return nudges


def _caffeine_after_noon(notes):
    if "caffeine" not in notes and "coffee" not in notes and "espresso" not in notes:
        return False
    # heuristic: words suggesting afternoon/evening timing
    afternoon_words = (
        "afternoon", "evening", "pm", "lunch", "after noon", "after lunch",
        "late", "1pm", "2pm", "3pm", "4pm", "5pm",
    )
    return any(w in notes for w in afternoon_words)


def _bedtime_drift_min(actual, target):
    """Smallest absolute difference in minutes between two clock times."""
    base = date(2000, 1, 1)
    a = datetime.combine(base, actual)
    t = datetime.combine(base, target)
    diff = abs((a - t).total_seconds()) / 60.0
    return int(round(min(diff, 1440 - diff)))


def positive_reinforcement(entries):
    """Rule 4 — call out improving trends plainly."""
    msgs = []
    rt = restless_trend(entries)
    if rt and rt["improving"]:
        msgs.append(
            f"Nice — restless moments are trending DOWN "
            f"({rt['first_avg']:.0f} -> {rt['second_avg']:.0f} over the last "
            f"{rt['n']} nights). Keep it up."
        )
    at = awakenings_trend(entries)
    if at and at["improving"]:
        msgs.append(
            f"Awakenings are trending DOWN "
            f"({at['first_avg']:.1f} -> {at['second_avg']:.1f}). Good progress."
        )
    return msgs


def weekly_adherence(week):
    """
    Average bedtime drift (minutes) from that week's own prescribed bedtime.
    Lower is better; <= CONSISTENCY_DRIFT_MIN counts as consistent adherence.
    Returns None for an empty week.
    """
    if not week:
        return None
    window = prescribed_window_min(week)
    drifts = []
    for e in week:
        target = prescribed_bedtime(window, parse_time(e["wake_time"]))
        drifts.append(_bedtime_drift_min(parse_time(e["bedtime"]), target))
    return avg(drifts)


def doctor_flags(entries):
    """
    Guardrail — surface a 'see a doctor' recommendation when:
      * efficiency stays < 85% for 3+ consecutive full weeks DESPITE consistent
        adherence (avg bedtime drift within CONSISTENCY_DRIFT_MIN each week), OR
      * notes report persistent daytime fatigue / unrefreshing sleep.

    If efficiency is low for 3+ weeks but adherence has drifted, we do NOT
    escalate to a doctor — instead the report nudges toward tightening
    consistency first (see adherence_note).
    """
    flags = []

    blocks = weekly_blocks(scored_entries(entries))
    if len(blocks) >= 3:
        last3 = blocks[-3:]
        effs = [avg([e["efficiency"] for e in wk]) for wk in last3]
        adher = [weekly_adherence(wk) for wk in last3]
        consistent = all(a is not None and a <= CONSISTENCY_DRIFT_MIN for a in adher)
        if all(e < EFF_OK_LOW for e in effs) and consistent:
            flags.append(
                "Efficiency has stayed below 85% for 3+ consecutive weeks "
                f"({', '.join(f'{e:.0f}%' for e in effs)}) despite consistent "
                "adherence to the window. Consider seeing a doctor — recurrent "
                "awakenings can have medical causes a tracker can't detect."
            )

    # Structured self-ratings over the trailing 2 weeks: persistently low
    # restedness or high daytime sleepiness is the symptom that matters most.
    recent = trailing(entries, 14)
    rested_vals = [e["rested"] for e in recent if e.get("rested") is not None]
    sleepy_vals = [e["daytime_sleepiness"] for e in recent
                   if e.get("daytime_sleepiness") is not None]
    low_rested = len(rested_vals) >= 5 and avg(rested_vals) <= 2
    high_sleepy = len(sleepy_vals) >= 5 and avg(sleepy_vals) >= 4

    fatigue_words = (
        "exhausted", "daytime fatigue", "tired all day", "unrefreshing",
        "unrefreshed", "fatigue", "drowsy all day", "nodding off",
        "fell asleep at", "cant stay awake", "can't stay awake",
    )
    recent_notes = " ".join((e.get("notes") or "").lower() for e in recent)
    notes_fatigue = any(w in recent_notes for w in fatigue_words)

    if low_rested or high_sleepy or notes_fatigue:
        flags.append(
            "You've consistently logged poor daytime energy (low restedness / "
            "high daytime sleepiness / fatigue). Consider seeing a doctor — this "
            "can have causes beyond sleep timing."
        )

    return flags


def adherence_note(entries):
    """One-line adherence summary for the most recent full week, or None."""
    blocks = weekly_blocks(scored_entries(entries))
    if not blocks:
        return None
    a = weekly_adherence(blocks[-1])
    if a is None:
        return None
    verdict = "consistent" if a <= CONSISTENCY_DRIFT_MIN else "drifting"
    return f"This week's adherence: avg {a:.0f} min off prescribed bedtime ({verdict})."


# --------------------------------------------------------------------------- #
# Structured report (for the web API) — reuses the same engine as the CLI
# --------------------------------------------------------------------------- #


def weekly_adjustment_struct(entries):
    """
    Structured form of `weekly_adjustment` for the API: returns a dict with
    direction / suggested minutes / avg efficiency / reason, using the exact
    same thresholds as the prose version. Returns a 'keep logging' maintain
    object until a full 7-night week exists; None only when there are no
    entries at all.
    """
    scored = scored_entries(entries)
    blocks = weekly_blocks(scored)
    if not blocks:
        if scored:
            return {"direction": "maintain", "minutes": 0, "avg_efficiency": None,
                    "reason": "Keep logging — a full 7-night week is needed "
                              "before adjusting your window."}
        return None
    week = blocks[-1]
    eff = round(avg([e["efficiency"] for e in week]), 1)
    if eff > EFF_HIGH:
        return {"direction": "extend", "minutes": 15, "avg_efficiency": eff,
                "reason": f"7-night efficiency is {eff}% (>90%). You can add "
                          "15-30 min to the window — move bedtime earlier, keep "
                          "wake time fixed."}
    if eff >= EFF_OK_LOW:
        return {"direction": "maintain", "minutes": 0, "avg_efficiency": eff,
                "reason": f"7-night efficiency is {eff}% (85-90%). Hold the "
                          "current window for another week."}
    return {"direction": "reduce", "minutes": -15, "avg_efficiency": eff,
            "reason": f"7-night efficiency is {eff}% (<85%). Hold, or trim 15 "
                      "min, and emphasize consistency."}


def restless_trend_struct(entries):
    """Direction + human message for the restless-moments trend (or a flat default)."""
    rt = restless_trend(entries)
    if not rt:
        return {"direction": "flat", "message": "Not enough data for a "
                                                "restlessness trend yet."}
    if rt["improving"]:
        return {"direction": "improving",
                "message": "Restlessness is trending down — your nights are "
                           "getting calmer."}
    if rt["slope"] > 0.1:
        return {"direction": "worsening",
                "message": "Restlessness is trending up over recent nights."}
    return {"direction": "flat", "message": "Restlessness has been steady recently."}


# --------------------------------------------------------------------------- #
# Correlation — what daytime/recovery metrics track with sleep
# --------------------------------------------------------------------------- #


def _pearson(xs, ys):
    """Pearson correlation coefficient, or None if undefined."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / ((sx * sy) ** 0.5)


def _strength(r):
    a = abs(r)
    if a >= 0.6:
        return "strong"
    if a >= 0.4:
        return "moderate"
    if a >= 0.2:
        return "weak"
    return "negligible"


def _factor_message(label, r, n, outcome):
    direction = "higher" if r > 0 else "lower"
    strength = _strength(r)
    outcome_word = "sleep efficiency" if outcome == "efficiency" else "restlessness"
    if strength == "negligible":
        return (f"{label.capitalize()} shows no clear link to {outcome_word} so "
                f"far (r={r:+.2f}, {n} nights).")
    return (f"On days with higher {label}, your {outcome_word} tends to be "
            f"{direction} — a {strength} association (r={r:+.2f}, {n} nights).")


def correlate(entries, outcome="efficiency", min_n=5):
    """
    Correlate each available health factor against a sleep outcome
    ('efficiency' or 'restless'). Returns a list of dicts sorted by |r|,
    strongest first, only for factors with at least `min_n` paired nights.
    """
    okey = "efficiency" if outcome == "efficiency" else "restless_per_hr"
    results = []
    for col, label in CORRELATION_FACTORS:
        pairs = [(e.get(col), e.get(okey)) for e in entries
                 if e.get(col) is not None and e.get(okey) is not None]
        if len(pairs) < min_n:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        r = _pearson(xs, ys)
        if r is None:
            continue
        results.append({
            "column": col, "label": label, "n": len(pairs), "r": round(r, 2),
            "strength": _strength(r), "outcome": outcome,
            "message": _factor_message(label, r, len(pairs), outcome),
            "action": FACTOR_ACTIONS.get(col, ""),
        })
    results.sort(key=lambda d: abs(d["r"]), reverse=True)
    return results


def correlate_lagged(features, driver, outcome, lag=1, min_n=5):
    """Correlate day D's `driver` against day D+lag's `outcome` (e.g. last night's
    sleep vs next-day resting HR). Returns a dict or None."""
    by_date = {f["date"]: f for f in features}
    pairs = []
    for d in sorted(by_date):
        try:
            d2 = (parse_date(d) + timedelta(days=lag)).isoformat()
        except (ValueError, TypeError):
            continue
        a, b = by_date.get(d), by_date.get(d2)
        if a and b and a.get(driver) is not None and b.get(outcome) is not None:
            pairs.append((a[driver], b[outcome]))
    if len(pairs) < min_n:
        return None
    r = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
    if r is None:
        return None
    return {"driver": driver, "outcome": outcome, "lag": lag, "n": len(pairs),
            "r": round(r, 2), "strength": _strength(r)}


def insights_data(conn):
    """Cross-domain insights: what (diet/activity/body) tracks with that night's
    sleep, and what last night's sleep predicts about the next day."""
    feats = daily_features(conn)
    sleep_factors = correlate(feats, "efficiency")

    drivers = [("efficiency", "sleep efficiency"),
               ("hrv_overnight", "overnight HRV"),
               ("total_sleep_min", "total sleep")]
    outcomes = [("resting_hr", "next-day resting HR"),
                ("training_readiness", "next-day training readiness"),
                ("active_calories", "next-day activity calories"),
                ("stress_avg", "next-day stress")]
    next_day = []
    for dcol, dlabel in drivers:
        for ocol, olabel in outcomes:
            res = correlate_lagged(feats, dcol, ocol, 1)
            if res and res["strength"] != "negligible":
                direction = "higher" if res["r"] > 0 else "lower"
                res["label"] = f"{dlabel} → {olabel}"
                res["message"] = (
                    f"Days after higher {dlabel} tend to have {direction} {olabel} "
                    f"(r={res['r']:+.2f}, {res['n']} days).")
                next_day.append(res)
    next_day.sort(key=lambda d: abs(d["r"]), reverse=True)
    return {"sleep_factors": sleep_factors, "next_day_factors": next_day}


def day_facts_text(conn, d):
    """Compact human-readable summary of one day's sleep + activity + diet, for
    the AI daily-summary prompt."""
    lines = [f"Date: {d}"]
    row = next((e for e in all_entries(conn) if e["date"] == d), None)
    if row:
        if row.get("efficiency") is not None:
            lines.append(
                f"Sleep: {minutes_to_hm(row['total_sleep_min'])} "
                f"({row['efficiency']:.0f}% efficiency), bedtime {row['bedtime']} -> "
                f"wake {row['wake_time']}, restless {row['restless_moments']}.")
        for k, lab in [("hrv_overnight", "overnight HRV"), ("resting_hr", "resting HR"),
                       ("stress_avg", "avg stress"), ("body_battery_low", "Body Battery low"),
                       ("training_readiness", "training readiness"), ("steps", "steps"),
                       ("active_calories", "active calories"), ("weight_kg", "weight (kg)")]:
            if row.get(k) is not None:
                lines.append(f"{lab}: {row[k]}")
        for k, lab in [("rested", "felt rested (1-5)"), ("mood", "mood (1-5)"),
                       ("daytime_sleepiness", "daytime sleepiness (1-5)"),
                       ("caffeine_mg", "caffeine mg")]:
            if row.get(k) is not None:
                lines.append(f"{lab}: {row[k]}")
    acts = all_activities(conn, d, d)
    if acts:
        lines.append("Workouts: " + "; ".join(
            f"{a.get('type') or 'activity'} {a.get('duration_min') or '?'}min "
            f"{a.get('calories') or '?'}kcal" for a in acts))
    meals = all_meals(conn, d)
    if meals:
        tot = sum(m.get("calories") or 0 for m in meals)
        lines.append(f"Meals ({len(meals)}, ~{tot} kcal total): " + "; ".join(
            f"{m.get('ai_description') or m.get('notes') or 'meal'} "
            f"({m.get('calories') or '?'}kcal, P{m.get('protein_g') or '?'}/"
            f"C{m.get('carbs_g') or '?'}/F{m.get('fat_g') or '?'})" for m in meals))
    return "\n".join(lines)


def report_data(conn):
    """
    Everything the CLI `report` shows, as a JSON-serializable dict for the web
    API. Pulls from the same functions the CLI uses — no rule is duplicated.
    """
    entries = all_entries(conn)
    wake_str = get_setting(conn, "wake_time", DEFAULT_WAKE_TIME)
    wake_time = parse_time(wake_str)
    window = prescribed_window_min(entries)
    bed = prescribed_bedtime(window, wake_time)

    # last_night = most recent night WITH sleep data (the stat tiles need it).
    scored = scored_entries(entries)
    last = scored[-1] if scored else None
    last_night = None
    if last:
        last_night = {
            "date": last["date"],
            "bedtime": last["bedtime"],
            "wake_time": last["wake_time"],
            "total_sleep_min": last["total_sleep_min"],
            "tib_min": last["tib_min"],
            "efficiency": round(last["efficiency"], 1),
            "restless_moments": last["restless_moments"],
            "awakenings": last["awakenings"],
            "resting_hr": last["resting_hr"],
        }

    recent7 = trailing(scored, 7)
    averages_7 = None
    if recent7:
        hrs = [e["resting_hr"] for e in recent7 if e["resting_hr"] is not None]
        averages_7 = {
            "nights": len(recent7),
            "avg_total_sleep_min": round(avg([e["total_sleep_min"] for e in recent7])),
            "avg_tib_min": round(avg([e["tib_min"] for e in recent7])),
            "avg_efficiency": round(avg([e["efficiency"] for e in recent7]), 1),
            "avg_restless": round(avg([e["restless_moments"] for e in recent7]), 1),
            "avg_resting_hr": round(avg(hrs)) if hrs else None,
        }

    # chart series uses scored nights so gaps don't appear as zero-efficiency.
    series_14 = [
        {"date": e["date"], "efficiency": round(e["efficiency"], 1),
         "restless": e["restless_moments"], "total_sleep_min": e["total_sleep_min"],
         "tib_min": e["tib_min"]}
        for e in trailing(scored, 14)
    ]

    return {
        "wake_time": wake_str,
        "prescribed_window_min": window,
        "prescribed_window_hm": minutes_to_hm(window),
        "prescribed_bedtime": fmt_time(bed),
        "last_night": last_night,
        "averages_7": averages_7,
        "weekly_adjustment": weekly_adjustment_struct(entries),
        "daily_nudges": daily_nudges(entries, window, wake_time),
        "progress": positive_reinforcement(entries),
        "adherence_note": adherence_note(entries) or "",
        "doctor_flags": doctor_flags(entries),
        "restless_trend": restless_trend_struct(entries),
        "series_14": series_14,
        "factors": correlate(entries, "efficiency"),
        "garmin_date_offset": int(get_setting(conn, "garmin_date_offset", 1)),
    }


def stats_data(conn):
    """Overall stats + logging streak, as a dict for the web API."""
    entries = all_entries(conn)
    if not entries:
        return {"total_nights": 0, "streak": 0, "avg_efficiency_all": None,
                "best_efficiency": None, "avg_total_sleep_min": None,
                "first_date": None, "last_date": None}
    scored = scored_entries(entries)
    effs = [e["efficiency"] for e in scored]
    return {
        "total_nights": len(scored),
        "streak": logging_streak(entries),
        "avg_efficiency_all": round(avg(effs), 1) if effs else None,
        "best_efficiency": round(max(effs), 1) if effs else None,
        "avg_total_sleep_min": (round(avg([e["total_sleep_min"] for e in scored]))
                                if scored else None),
        "first_date": entries[0]["date"],
        "last_date": entries[-1]["date"],
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return SPARK_CHARS[0] * len(values)
    out = []
    for v in values:
        if v is None:
            out.append(" ")
            continue
        idx = int((v - lo) / (hi - lo) * (len(SPARK_CHARS) - 1))
        out.append(SPARK_CHARS[idx])
    return "".join(out)


def hr(char="-", n=64):
    return char * n


def render_report(conn):
    entries = all_entries(conn)
    wake_time = parse_time(get_setting(conn, "wake_time", DEFAULT_WAKE_TIME))

    lines = []
    lines.append(hr("="))
    lines.append("SLEEP REPORT  —  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append(hr("="))

    if not entries:
        lines.append("No entries yet. Log a night with `log`, or run `seed`.")
        lines.append("")
        lines.append(DISCLAIMER)
        return "\n".join(lines)

    scored = scored_entries(entries)

    # --- Last night --------------------------------------------------------
    last = scored[-1] if scored else None
    lines.append("")
    if last is None:
        lines.append("LAST NIGHT")
        lines.append("  No sleep data synced yet — run `sync-garmin` or `sync-recent`.")
    else:
        lines.append("LAST NIGHT  (" + last["date"] + ")")
        lines.append(f"  Bedtime / Wake : {last['bedtime']} -> {last['wake_time']}")
        lines.append(f"  Time in bed    : {minutes_to_hm(last['tib_min'])}")
        lines.append(f"  Total sleep    : {minutes_to_hm(last['total_sleep_min'])}")
        lines.append(f"  Efficiency     : {last['efficiency']:.1f}%")
        lines.append(f"  Restless       : {last['restless_moments']}")
        if last["awakenings"] is not None:
            lines.append(f"  Awakenings     : {last['awakenings']}")
        if last["resting_hr"] is not None:
            lines.append(f"  Resting HR     : {last['resting_hr']} bpm")
        if last.get("notes"):
            lines.append(f"  Notes          : {last['notes']}")

    # --- 7-night averages --------------------------------------------------
    recent7 = trailing(scored, 7)
    avg_sleep = avg([e["total_sleep_min"] for e in recent7])
    avg_eff = avg([e["efficiency"] for e in recent7])
    avg_restless = avg([e["restless_moments"] for e in recent7])
    lines.append("")
    lines.append(f"7-NIGHT AVERAGES  (n={len(recent7)})")
    if recent7:
        lines.append(f"  Total sleep    : {minutes_to_hm(avg_sleep)}")
        lines.append(f"  Efficiency     : {avg_eff:.1f}%")
        lines.append(f"  Restless       : {avg_restless:.0f}")
    else:
        lines.append("  (no scored nights yet)")

    # --- Prescribed window -------------------------------------------------
    window = prescribed_window_min(entries)
    bed = prescribed_bedtime(window, wake_time)
    floored = window == WINDOW_FLOOR_MIN and (
        avg_sleep is None or (avg_sleep + WINDOW_PADDING_MIN) < WINDOW_FLOOR_MIN)
    lines.append("")
    lines.append("PRESCRIBED SLEEP WINDOW")
    lines.append(f"  Window length  : {minutes_to_hm(window)}"
                 + ("  (at 5.5h floor)" if floored else ""))
    lines.append(f"  Fixed wake     : {fmt_time(wake_time)}")
    lines.append(f"  -> Bedtime     : {fmt_time(bed)}")

    # --- Weekly adjustment -------------------------------------------------
    lines.append("")
    lines.append("WEEKLY WINDOW ADJUSTMENT")
    wk = weekly_adjustment(entries)
    if wk is None:
        nscored = len(scored)
        need = 7 - (nscored % 7 or 7)
        lines.append(f"  Need a full 7-night block first "
                     f"({nscored} scored; {need} more to next full week).")
    else:
        headline, detail = wk
        lines.append(f"  {headline}")
        lines.append(f"  {detail}")
    adh = adherence_note(entries)
    if adh:
        lines.append(f"  {adh}")

    # --- Daily nudges ------------------------------------------------------
    nudges = daily_nudges(entries, window, wake_time)
    lines.append("")
    lines.append("DAILY NUDGES")
    if nudges:
        for n in nudges:
            lines.append(f"  • {n}")
    else:
        lines.append("  • None — nice work last night.")

    # --- Positive reinforcement -------------------------------------------
    wins = positive_reinforcement(entries)
    if wins:
        lines.append("")
        lines.append("PROGRESS")
        for w in wins:
            lines.append(f"  ✓ {w}")

    # --- Trend mini-view ---------------------------------------------------
    recent14 = trailing(scored, 14)
    lines.append("")
    lines.append("TRENDS (last %d scored nights)" % len(recent14))
    lines.append("  Efficiency : " + sparkline([e["efficiency"] for e in recent14]))
    lines.append("  Restless   : " + sparkline([e["restless_moments"] for e in recent14]))

    # --- Guardrails --------------------------------------------------------
    flags = doctor_flags(entries)
    if flags:
        lines.append("")
        lines.append(hr("!"))
        lines.append("WORTH A CONVERSATION WITH A DOCTOR")
        for f in flags:
            lines.append(f"  ! {f}")
        lines.append(hr("!"))

    # --- Standing notes / disclaimer --------------------------------------
    lines.append("")
    lines.append(hr())
    lines.append(DROWSINESS_NOTE)
    lines.append(DISCLAIMER)
    lines.append(hr())
    return "\n".join(lines)


def render_trend(conn):
    entries = scored_entries(all_entries(conn))
    if not entries:
        return "No scored nights yet (sync sleep from Garmin first)."
    lines = []
    lines.append(f"{'Date':<12}{'TIB':>8}{'Sleep':>8}{'Eff%':>7}{'Restl':>7}{'Awake':>7}{'  Eff bar'}")
    lines.append(hr("-", 64))
    effs = [e["efficiency"] for e in entries]
    lo, hi = min(effs), max(effs)
    for e in entries:
        if hi == lo:
            barlen = 10
        else:
            barlen = int((e["efficiency"] - lo) / (hi - lo) * 20)
        bar = "█" * barlen
        aw = e["awakenings"] if e["awakenings"] is not None else "-"
        lines.append(
            f"{e['date']:<12}"
            f"{minutes_to_hm(e['tib_min']):>8}"
            f"{minutes_to_hm(e['total_sleep_min']):>8}"
            f"{e['efficiency']:>7.1f}"
            f"{e['restless_moments']:>7}"
            f"{str(aw):>7}"
            f"  {bar}"
        )
    lines.append(hr("-", 64))
    lines.append("Efficiency : " + sparkline(effs))
    lines.append("Restless   : " + sparkline([e["restless_moments"] for e in entries]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def upsert_entry(conn, e):
    """
    Insert or update a night by date, MERGING fields: every column except `date`
    uses COALESCE(excluded.col, entries.col), so a partial write (a Garmin sync,
    or a subjective check-in) only updates the fields it provides and never wipes
    the others. A non-null incoming value still overwrites. (Trade-off: you can't
    null a field back out via upsert — use `delete` for that.)
    """
    params = {c: e.get(c) for c in ALL_COLUMNS}
    cols = ", ".join(ALL_COLUMNS)
    vals = ", ".join(f":{c}" for c in ALL_COLUMNS)
    set_merge = ", ".join(
        f"{c} = COALESCE(excluded.{c}, entries.{c})"
        for c in ALL_COLUMNS if c != "date"
    )
    conn.execute(
        f"INSERT INTO entries ({cols}) VALUES ({vals}) "
        f"ON CONFLICT(date) DO UPDATE SET {set_merge}",
        params,
    )
    conn.commit()


def _prompt(label, default=None, required=False, cast=str):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{label}{suffix}: ").strip()
        if not raw:
            if default is not None:
                raw = str(default)
            elif required:
                print("  (required)")
                continue
            else:
                return None
        try:
            return cast(raw)
        except (ValueError, Exception) as exc:  # noqa: BLE001
            print(f"  invalid: {exc}")


def cmd_log(conn, args):
    wake_default = get_setting(conn, "wake_time", DEFAULT_WAKE_TIME)

    if args.bedtime and args.total_sleep is not None:
        # non-interactive
        d = args.date or date.today().isoformat()
        bedtime = fmt_time(parse_time(args.bedtime))
        wake = fmt_time(parse_time(args.wake or wake_default))
        entry = {
            "date": d,
            "bedtime": bedtime,
            "wake_time": wake,
            "total_sleep_min": int(args.total_sleep),
            "restless_moments": int(args.restless or 0),
            "awakenings": int(args.awakenings) if args.awakenings is not None else None,
            "resting_hr": int(args.resting_hr) if args.resting_hr is not None else None,
            "notes": args.notes,
        }
    else:
        # interactive
        print("Log a night (press Enter to accept [defaults]):")
        d = _prompt("Date (YYYY-MM-DD)", default=date.today().isoformat())
        bedtime = fmt_time(_prompt("Bedtime (HH:MM)", required=True, cast=parse_time))
        wake = fmt_time(_prompt("Wake time (HH:MM)", default=wake_default, cast=parse_time))
        total = _prompt("Total sleep (min, Garmin)", required=True, cast=int)
        restless = _prompt("Restless moments", default=0, cast=int)
        awaken = _prompt("Awakenings (optional)", cast=int)
        rhr = _prompt("Resting HR (optional)", cast=int)
        notes = _prompt("Notes (optional)")
        entry = {
            "date": d,
            "bedtime": bedtime,
            "wake_time": wake,
            "total_sleep_min": total,
            "restless_moments": restless,
            "awakenings": awaken,
            "resting_hr": rhr,
            "notes": notes,
        }

    # validate efficiency sanity
    tib = time_in_bed_min(parse_time(entry["bedtime"]), parse_time(entry["wake_time"]))
    if entry["total_sleep_min"] > tib:
        print(f"  ⚠ total sleep ({entry['total_sleep_min']}m) exceeds time in bed "
              f"({tib}m). Total sleep should be actual sleep, not time in bed. Saved anyway.")

    upsert_entry(conn, entry)
    eff = sleep_efficiency(entry["total_sleep_min"], tib)
    print(f"Logged {entry['date']}: TIB {minutes_to_hm(tib)}, "
          f"sleep {minutes_to_hm(entry['total_sleep_min'])}, efficiency {eff:.1f}%.")


def cmd_report(conn, args):
    print(render_report(conn))


def cmd_trend(conn, args):
    print(render_trend(conn))


def cmd_set_wake(conn, args):
    t = parse_time(args.time)
    set_setting(conn, "wake_time", fmt_time(t))
    print(f"Fixed wake time set to {fmt_time(t)}.")


def cmd_list(conn, args):
    for e in all_entries(conn):
        if e.get("efficiency") is not None:
            print(f"{e['date']}  {e['bedtime']}->{e['wake_time']}  "
                  f"sleep {e['total_sleep_min']}m  eff {e['efficiency']:.1f}%  "
                  f"restless {e['restless_moments']}")
        else:
            print(f"{e['date']}  (check-in only — no sleep synced)")


def cmd_delete(conn, args):
    cur = conn.execute("DELETE FROM entries WHERE date = ?", (args.date,))
    conn.commit()
    if cur.rowcount:
        print(f"Deleted entry for {args.date}.")
    else:
        print(f"No entry found for {args.date}.")


CSV_FIELDS = CORE_COLUMNS + METRIC_COLUMN_NAMES + SUBJECTIVE_COLUMN_NAMES


def cmd_export(conn, args):
    rows = conn.execute("SELECT * FROM entries ORDER BY date ASC").fetchall()
    out = open(args.out, "w", newline="") if args.out else sys.stdout
    try:
        writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in CSV_FIELDS})
    finally:
        if args.out:
            out.close()
            print(f"Exported {len(rows)} entries to {args.out}.")


def cmd_import(conn, args):
    with open(args.infile, newline="") as fh:
        reader = csv.DictReader(fh)
        count = 0
        for row in reader:
            def _t(key):
                v = (row.get(key) or "").strip()
                return fmt_time(parse_time(v)) if v else None

            def _i(key):
                v = (row.get(key) or "").strip()
                return int(float(v)) if v else None

            entry = {
                "date": row["date"].strip(),
                "bedtime": _t("bedtime"),
                "wake_time": _t("wake_time"),
                "total_sleep_min": _i("total_sleep_min"),
                "restless_moments": _i("restless_moments"),
                "awakenings": _i("awakenings"),
                "resting_hr": _i("resting_hr"),
                "notes": row.get("notes") or None,
            }
            for col, coltype in OPTIONAL_COLUMNS:
                raw = (row.get(col) or "").strip()
                if raw:
                    if coltype == "REAL":
                        entry[col] = float(raw)
                    elif coltype == "TEXT":
                        entry[col] = raw
                    else:
                        entry[col] = int(float(raw))
            upsert_entry(conn, entry)
            count += 1
    print(f"Imported {count} entries from {args.infile}.")


def logging_streak(entries):
    """Consecutive nights logged ending at the latest entry."""
    if not entries:
        return 0
    streak = 1
    dates = [e["_date"] for e in entries]
    for i in range(len(dates) - 1, 0, -1):
        if (dates[i] - dates[i - 1]).days == 1:
            streak += 1
        else:
            break
    return streak


def cmd_stats(conn, args):
    entries = all_entries(conn)
    if not entries:
        print("No entries yet.")
        return
    scored = scored_entries(entries)
    print(f"Days logged     : {len(entries)} "
          f"({entries[0]['date']} -> {entries[-1]['date']})")
    print(f"Scored nights   : {len(scored)}")
    print(f"Logging streak  : {logging_streak(entries)} day(s)")
    if not scored:
        print("(no sleep synced yet — run sync-garmin / sync-recent)")
        return
    effs = [e["efficiency"] for e in scored]
    best = max(scored, key=lambda e: e["efficiency"])
    worst = min(scored, key=lambda e: e["efficiency"])
    print(f"Avg total sleep : {minutes_to_hm(avg([e['total_sleep_min'] for e in scored]))}")
    print(f"Avg efficiency  : {avg(effs):.1f}%")
    print(f"Avg restless    : {avg([e['restless_moments'] for e in scored]):.0f}")
    print(f"Best night      : {best['date']}  {best['efficiency']:.1f}%")
    print(f"Worst night     : {worst['date']}  {worst['efficiency']:.1f}%")


def cmd_correlate(conn, args):
    """Show which health metrics track with sleep efficiency and restlessness."""
    entries = all_entries(conn)
    if not entries:
        print("No entries yet.")
        return
    for outcome, title in (("efficiency", "SLEEP EFFICIENCY"),
                           ("restless", "RESTLESS MOMENTS")):
        rows = correlate(entries, outcome, min_n=args.min_n)
        print(hr("="))
        print(f"WHAT TRACKS WITH {title}")
        print(hr("="))
        if not rows:
            print(f"  Not enough paired data yet (need ≥{args.min_n} nights with "
                  "the metric). Sync more history from Garmin.")
        else:
            for r in rows:
                print(f"  r={r['r']:+.2f}  {r['strength']:<10} {r['label']} "
                      f"(n={r['n']})")
            print("")
            for r in rows[:3]:
                print(f"  • {r['message']}")
                if r["action"]:
                    print(f"      → {r['action']}")
        print("")
    print("Correlation is not causation, and small samples are noisy — treat "
          "these as hints to explore, not conclusions.")


def cmd_seed(conn, args):
    """Insert the example night so `report` works immediately."""
    if not get_setting(conn, "wake_time"):
        set_setting(conn, "wake_time", "08:27")
    entry = {
        "date": args.date or "2026-06-05",
        "bedtime": "22:58",
        "wake_time": "08:27",
        "total_sleep_min": 440,
        "restless_moments": 80,
        "awakenings": None,
        "resting_hr": 56,
        "notes": "seed example entry",
    }
    upsert_entry(conn, entry)
    tib = time_in_bed_min(parse_time(entry["bedtime"]), parse_time(entry["wake_time"]))
    print(f"Seeded example night {entry['date']} "
          f"(TIB {minutes_to_hm(tib)}, efficiency "
          f"{sleep_efficiency(entry['total_sleep_min'], tib):.1f}%).")


def _garmin_ts_to_datetime(ms):
    """Garmin *Local timestamps are local wall-clock encoded as epoch ms; decode
    with utcfromtimestamp so this machine's tz offset isn't applied twice."""
    if not ms:
        return None
    return datetime.utcfromtimestamp(ms / 1000)


def _garmin_ts_to_time(ms):
    dt = _garmin_ts_to_datetime(ms)
    return dt.time() if dt else None


def _garmin_daily_summary(api, ds):
    """Best-effort daily summary dict across garminconnect versions."""
    for meth in ("get_user_summary", "get_stats"):
        fn = getattr(api, meth, None)
        if fn is None:
            continue
        try:
            data = fn(ds)
            if data:
                return data
        except Exception:  # noqa: BLE001
            continue
    return {}


def fetch_garmin_night(api, ds, wake_default=DEFAULT_WAKE_TIME):
    """
    Pull one night of sleep plus daytime/recovery metrics from a logged-in
    garminconnect `api`. Returns an entry dict ready for upsert_entry, or None
    if Garmin has no sleep record for that date. Each extra metric is fetched
    defensively so a missing one never aborts the night.
    """
    sleep = api.get_sleep_data(ds)
    dto = (sleep or {}).get("dailySleepDTO", {}) or {}
    total_sleep_sec = dto.get("sleepTimeSeconds")
    if not total_sleep_sec:
        return None

    bed_dt = _garmin_ts_to_datetime(dto.get("sleepStartTimestampLocal"))
    bed = bed_dt.time() if bed_dt else None
    wake = _garmin_ts_to_time(dto.get("sleepEndTimestampLocal"))
    entry = {
        "date": ds,
        "bedtime": fmt_time(bed) if bed else "23:00",
        "wake_time": fmt_time(wake) if wake else wake_default,
        "total_sleep_min": int(total_sleep_sec // 60),
        "restless_moments": int(sleep.get("restlessMomentsCount") or 0),
        "awakenings": dto.get("awakeCount"),
        "resting_hr": sleep.get("restingHeartRate"),
        "notes": "synced from Garmin",
        # respiration often rides along with the sleep payload
        "respiration_avg": dto.get("averageRespirationValue")
                           or sleep.get("avgSleepRespirationValue"),
    }

    summary = _garmin_daily_summary(api, ds)
    entry["steps"] = summary.get("totalSteps")
    entry["stress_avg"] = summary.get("averageStressLevel")
    entry["body_battery_high"] = summary.get("bodyBatteryHighestValue")
    entry["body_battery_low"] = summary.get("bodyBatteryLowestValue")
    entry["intensity_moderate_min"] = summary.get("moderateIntensityMinutes")
    entry["intensity_vigorous_min"] = summary.get("vigorousIntensityMinutes")
    entry["active_calories"] = summary.get("activeKilocalories")
    entry["resting_calories"] = summary.get("bmrKilocalories")
    entry["floors_climbed"] = summary.get("floorsAscended")
    entry["daily_distance_m"] = summary.get("totalDistanceMeters")

    _fetch_body_metrics(api, ds, entry)

    try:
        hrv = api.get_hrv_data(ds) or {}
        entry["hrv_overnight"] = (hrv.get("hrvSummary") or {}).get("lastNightAvg")
    except Exception:  # noqa: BLE001
        pass

    if entry["respiration_avg"] is None:
        try:
            resp = api.get_respiration_data(ds) or {}
            entry["respiration_avg"] = resp.get("avgSleepRespirationValue")
        except Exception:  # noqa: BLE001
            pass

    # How Garmin keys this night: calendarDate (ds) minus the bedtime's date.
    # Normally +1 (sleep is filed under the wake-up morning). Transient field
    # (not a column); the caller persists it so check-ins can target the same key.
    if bed_dt is not None:
        try:
            entry["_offset_days"] = (parse_date(ds) - bed_dt.date()).days
        except (ValueError, TypeError):
            pass

    return entry


def _fetch_body_metrics(api, ds, entry):
    """Best-effort weight / body-fat / VO2max / readiness / hydration pulls.
    Each is wrapped so a missing endpoint never aborts the day's sync."""
    try:
        bc = api.get_body_composition(ds) or {}
        avg = (bc.get("totalAverage") or {}) if isinstance(bc, dict) else {}
        grams = avg.get("weight")
        if grams:
            entry["weight_kg"] = round(grams / 1000.0, 2)
        if avg.get("bodyFat") is not None:
            entry["body_fat_pct"] = avg.get("bodyFat")
    except Exception:  # noqa: BLE001
        pass
    try:
        tr = api.get_training_readiness(ds)
        if isinstance(tr, list) and tr:
            entry["training_readiness"] = tr[0].get("score")
        elif isinstance(tr, dict):
            entry["training_readiness"] = tr.get("score")
    except Exception:  # noqa: BLE001
        pass
    try:
        mm = api.get_max_metrics(ds)
        rec = mm[0] if isinstance(mm, list) and mm else (mm or {})
        gen = (rec or {}).get("generic") or {}
        entry["vo2max"] = gen.get("vo2MaxValue") or gen.get("vo2MaxPreciseValue")
    except Exception:  # noqa: BLE001
        pass
    try:
        hy = api.get_hydration_data(ds) or {}
        entry["hydration_ml"] = hy.get("valueInML") or hy.get("dailyAverageinML")
    except Exception:  # noqa: BLE001
        pass


def fetch_garmin_activities(api, start, end):
    """Return a list of activity dicts (ready for upsert_activity) for a date
    range. Defensive: returns [] if the endpoint is unavailable."""
    try:
        raw = api.get_activities_by_date(start, end)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in (raw or []):
        start_local = a.get("startTimeLocal") or ""
        date_part = start_local.split(" ")[0] if start_local else None
        time_part = start_local.split(" ")[1][:5] if " " in start_local else None
        dur_s = a.get("duration") or 0
        out.append({
            "activity_id": str(a.get("activityId")),
            "date": date_part,
            "type": ((a.get("activityType") or {}).get("typeKey")),
            "name": a.get("activityName"),
            "start_time": time_part,
            "duration_min": round(dur_s / 60.0, 1) if dur_s else None,
            "distance_m": a.get("distance"),
            "calories": int(a["calories"]) if a.get("calories") else None,
            "avg_hr": int(a["averageHR"]) if a.get("averageHR") else None,
            "max_hr": int(a["maxHR"]) if a.get("maxHR") else None,
            "training_load": a.get("activityTrainingLoad"),
            "notes": None,
        })
    return out


def _import_garmin():
    try:
        from garminconnect import Garmin
        return Garmin
    except ImportError:
        raise RuntimeError(
            "The `garminconnect` library is not installed.\n"
            "  pip install garminconnect\n"
            "Note: this is an UNOFFICIAL library; Garmin offers no official "
            "consumer sleep API. Manual `log` always works."
        )


def garmin_api():
    """Return a logged-in Garmin client. Prefers saved OAuth tokens (set up once
    via `garmin-login`, so the daily job needs no password); falls back to
    GARMIN_EMAIL/GARMIN_PASSWORD env vars."""
    Garmin = _import_garmin()
    # 1) token resume (hands-free, survives MFA)
    if os.path.isdir(GARMIN_TOKEN_DIR) and os.listdir(GARMIN_TOKEN_DIR):
        try:
            api = Garmin()
            api.login(GARMIN_TOKEN_DIR)
            return api
        except Exception:  # noqa: BLE001 — tokens missing/expired, fall through
            pass
    # 2) email/password fallback
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "Not authorized with Garmin. Run `python sleep_tracker.py garmin-login` "
            "once (handles 2FA, stores a token), or set GARMIN_EMAIL/GARMIN_PASSWORD."
        )
    api = Garmin(email, password)
    api.login()
    return api


def cmd_garmin_login(conn, args):
    """One-time interactive Garmin login that stores an OAuth token so future
    syncs (and the daily job) run without a password and survive 2FA."""
    import getpass
    try:
        Garmin = _import_garmin()
    except RuntimeError as exc:
        print(exc)
        return

    email = os.environ.get("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")

    os.makedirs(GARMIN_TOKEN_DIR, exist_ok=True)
    try:
        # Newer garminconnect supports a 2FA prompt + return-on-mfa flow.
        try:
            api = Garmin(email=email, password=password,
                         prompt_mfa=lambda: input("2FA code (blank if none): ").strip())
        except TypeError:
            api = Garmin(email, password)
        result = api.login()
        # Some versions return ("needs_mfa", state) instead of prompting.
        if isinstance(result, tuple) and result and result[0] == "needs_mfa":
            code = input("2FA code: ").strip()
            api.resume_login(result[1], code)
        # Persist tokens (method name varies across garth versions).
        if hasattr(api, "garth") and hasattr(api.garth, "dump"):
            api.garth.dump(GARMIN_TOKEN_DIR)
        else:  # pragma: no cover
            import garth
            garth.save(GARMIN_TOKEN_DIR)
    except Exception as exc:  # noqa: BLE001
        print(f"Garmin login failed: {exc}")
        return
    print(f"Authorized. Token saved to {GARMIN_TOKEN_DIR}\n"
          "Future syncs run hands-free — no password needed.")


def _sync_one(conn, api, target, wake_default):
    entry = fetch_garmin_night(api, target, wake_default)
    if not entry:
        return None
    upsert_entry(conn, entry)
    if "_offset_days" in entry:
        set_setting(conn, "garmin_date_offset", entry["_offset_days"])
    return entry


def cmd_sync_garmin(conn, args):
    """Pull one night's sleep + health metrics from Garmin Connect."""
    target = args.date or date.today().isoformat()
    wake_default = get_setting(conn, "wake_time", DEFAULT_WAKE_TIME)
    try:
        api = garmin_api()
        entry = _sync_one(conn, api, target, wake_default)
    except RuntimeError as exc:
        print(exc)
        return
    except Exception as exc:  # noqa: BLE001
        print(f"Garmin sync failed: {exc}")
        return

    if not entry:
        print(f"No sleep data returned by Garmin for {target}.")
        return
    print(f"Synced {target} from Garmin: "
          f"sleep {minutes_to_hm(entry['total_sleep_min'])}, "
          f"restless {entry['restless_moments']}"
          + (f", stress {entry['stress_avg']}" if entry.get('stress_avg') else "")
          + (f", steps {entry['steps']}" if entry.get('steps') else "") + ".")


def sync_garmin_range(conn, days=3):
    """Pull the last N days (sleep nights + activities) from Garmin. Logs in once,
    idempotent (merges). Returns a counts dict. Shared by the CLI and the web
    Sync button."""
    import time as _time
    days = max(1, days)
    wake_default = get_setting(conn, "wake_time", DEFAULT_WAKE_TIME)
    api = garmin_api()  # raises RuntimeError if not authorized
    saved = skipped = failed = 0
    today = date.today()
    for i in range(days):
        ds = (today - timedelta(days=i)).isoformat()
        try:
            entry = _sync_one(conn, api, ds, wake_default)
            saved += 1 if entry else 0
            skipped += 0 if entry else 1
        except Exception as exc:  # noqa: BLE001
            print(f"  {ds}: error: {exc}")
            failed += 1
        _time.sleep(0.5)
    # activities for the range (one call)
    acts = 0
    try:
        start = (today - timedelta(days=days - 1)).isoformat()
        for a in fetch_garmin_activities(api, start, today.isoformat()):
            if a.get("activity_id"):
                upsert_activity(conn, a)
                acts += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  activities: error: {exc}")
    set_setting(conn, "last_synced", datetime.now().isoformat(timespec="seconds"))
    return {"saved": saved, "skipped": skipped, "failed": failed, "activities": acts}


def cmd_sync_recent(conn, args):
    """Pull the last N days from Garmin (default 3) — used by the daily job to
    catch up. Idempotent: re-runs merge, never duplicating or wiping check-ins."""
    try:
        r = sync_garmin_range(conn, args.days)
    except RuntimeError as exc:
        print(exc)
        return
    msg = (f"{datetime.now().isoformat(timespec='seconds')}  sync-recent "
           f"{args.days}d: saved {r['saved']}, no-data {r['skipped']}, "
           f"errors {r['failed']}, activities {r['activities']}")
    print(msg)
    try:
        with open(DAILY_SYNC_LOG, "a") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def _launchd_plist_path():
    return os.path.expanduser(
        f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist")


def build_launchd_plist(hour, every_hours=3):
    """Return the LaunchAgent plist dict for the Garmin sync (pure, testable).
    Runs at `hour` each morning AND every `every_hours` hours (Garmin has no
    consumer webhook, so frequent polling is the realistic substitute)."""
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [sys.executable, os.path.abspath(__file__),
                             "sync-recent", "--days", "3"],
        "EnvironmentVariables": {"SLEEP_DB": DEFAULT_DB},
        "StartCalendarInterval": {"Hour": int(hour), "Minute": 0},
        "RunAtLoad": False,
        "StandardOutPath": DAILY_SYNC_LOG,
        "StandardErrorPath": DAILY_SYNC_LOG,
    }
    if every_hours and every_hours > 0:
        plist["StartInterval"] = int(every_hours) * 3600
    return plist


def cmd_install_daily_sync(conn, args):
    """Install a macOS LaunchAgent that runs `sync-recent` every morning."""
    import plistlib
    import subprocess
    if sys.platform != "darwin":
        print("Auto-sync install is macOS-only. On Linux, add a cron/systemd "
              "timer that runs:  python sleep_tracker.py sync-recent --days 3")
        return
    if not (os.path.isdir(GARMIN_TOKEN_DIR) and os.listdir(GARMIN_TOKEN_DIR)):
        print("Run `garmin-login` first — the background job can't do 2FA, so it "
              "needs a stored token.")
        return

    plist_path = _launchd_plist_path()
    os.makedirs(os.path.dirname(plist_path), exist_ok=True)
    with open(plist_path, "wb") as fh:
        plistlib.dump(build_launchd_plist(args.hour, args.every_hours), fh)

    # Reload (try modern bootstrap, fall back to legacy load).
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", plist_path],
                   capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", plist_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        subprocess.run(["launchctl", "unload", plist_path], capture_output=True)
        subprocess.run(["launchctl", "load", "-w", plist_path], capture_output=True)

    print(f"Installed daily Garmin sync at {args.hour:02d}:00.")
    print(f"  Plist: {plist_path}")
    print(f"  Log:   {DAILY_SYNC_LOG}")
    print("Runs while you're logged in (catches up on wake). "
          "Running one sync now to confirm it works...")
    args.days = 3
    cmd_sync_recent(conn, args)


def cmd_uninstall_daily_sync(conn, args):
    """Remove the macOS daily-sync LaunchAgent."""
    import subprocess
    plist_path = _launchd_plist_path()
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", plist_path],
                   capture_output=True)
    subprocess.run(["launchctl", "unload", plist_path], capture_output=True)
    if os.path.exists(plist_path):
        os.remove(plist_path)
        print(f"Removed daily sync ({plist_path}).")
    else:
        print("Daily sync was not installed.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser():
    p = argparse.ArgumentParser(
        description="Personal sleep tracker + CBT-I suggestion engine.",
        epilog=DISCLAIMER,
    )
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite path (default: {DEFAULT_DB})")
    sub = p.add_subparsers(dest="command", required=True)

    pl = sub.add_parser("log", help="log a night (args or interactive)")
    pl.add_argument("--date")
    pl.add_argument("--bedtime")
    pl.add_argument("--wake")
    pl.add_argument("--total-sleep", type=int, dest="total_sleep")
    pl.add_argument("--restless", type=int)
    pl.add_argument("--awakenings", type=int)
    pl.add_argument("--resting-hr", type=int, dest="resting_hr")
    pl.add_argument("--notes")
    pl.set_defaults(func=cmd_log)

    pr = sub.add_parser("report", help="full report + suggestions")
    pr.set_defaults(func=cmd_report)

    pt = sub.add_parser("trend", help="trend table + sparklines")
    pt.set_defaults(func=cmd_trend)

    pc = sub.add_parser("correlate", help="what health metrics track with sleep")
    pc.add_argument("--min-n", type=int, default=5, dest="min_n",
                    help="minimum paired nights required to report a factor")
    pc.set_defaults(func=cmd_correlate)

    pw = sub.add_parser("set-wake", help="set your fixed wake time")
    pw.add_argument("time", help="HH:MM")
    pw.set_defaults(func=cmd_set_wake)

    pli = sub.add_parser("list", help="list all logged nights")
    pli.set_defaults(func=cmd_list)

    pd = sub.add_parser("delete", help="delete a night by date")
    pd.add_argument("date", help="YYYY-MM-DD")
    pd.set_defaults(func=cmd_delete)

    pe = sub.add_parser("export", help="export entries to CSV (stdout if no --out)")
    pe.add_argument("--out", help="output CSV path")
    pe.set_defaults(func=cmd_export)

    pi = sub.add_parser("import", help="import entries from a CSV file")
    pi.add_argument("infile", help="input CSV path")
    pi.set_defaults(func=cmd_import)

    pst = sub.add_parser("stats", help="overall stats + logging streak")
    pst.set_defaults(func=cmd_stats)

    ps = sub.add_parser("seed", help="insert the example night")
    ps.add_argument("--date")
    ps.set_defaults(func=cmd_seed)

    pg = sub.add_parser("sync-garmin", help="pull one night from Garmin Connect")
    pg.add_argument("--date")
    pg.set_defaults(func=cmd_sync_garmin)

    pgl = sub.add_parser("garmin-login",
                         help="one-time Garmin login (stores a token; handles 2FA)")
    pgl.set_defaults(func=cmd_garmin_login)

    psr = sub.add_parser("sync-recent",
                         help="pull the last N days from Garmin (default 3)")
    psr.add_argument("--days", type=int, default=3)
    psr.set_defaults(func=cmd_sync_recent)

    pid = sub.add_parser("install-daily-sync",
                         help="macOS: run the Garmin sync automatically each morning")
    pid.add_argument("--hour", type=int, default=9,
                     help="hour of day for the morning run (0-23, default 9)")
    pid.add_argument("--every-hours", type=int, default=3, dest="every_hours",
                     help="also poll every N hours (default 3; 0 to disable)")
    pid.set_defaults(func=cmd_install_daily_sync)

    pud = sub.add_parser("uninstall-daily-sync",
                         help="remove the automatic daily Garmin sync")
    pud.set_defaults(func=cmd_uninstall_daily_sync)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.db == DEFAULT_DB:
        ensure_db_ready(args.db)
    conn = connect(args.db)
    init_db(conn)
    try:
        args.func(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(130)

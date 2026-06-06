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
import sqlite3
import sys
from datetime import datetime, date, time, timedelta

# --------------------------------------------------------------------------- #
# Constants / configuration
# --------------------------------------------------------------------------- #

DEFAULT_DB = os.environ.get("SLEEP_DB", os.path.join(os.getcwd(), "sleep.db"))

# CBT-I sleep-restriction parameters
WINDOW_PADDING_MIN = 30          # avg total sleep + this = prescribed window
WINDOW_FLOOR_MIN = 330           # hard floor of 5.5 hours
DEFAULT_WAKE_TIME = "08:00"      # used until the user sets their own fixed wake

# Efficiency thresholds (percent)
EFF_HIGH = 90.0                  # > 90 -> expand window
EFF_OK_LOW = 85.0                # 85-90 -> hold; < 85 -> hold/trim + nudges

CONSISTENCY_DRIFT_MIN = 60       # bedtime drift that triggers a consistency nudge

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


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entries (
            date              TEXT PRIMARY KEY,   -- the night's date (YYYY-MM-DD)
            bedtime           TEXT NOT NULL,      -- HH:MM
            wake_time         TEXT NOT NULL,      -- HH:MM
            total_sleep_min   INTEGER NOT NULL,
            restless_moments  INTEGER NOT NULL DEFAULT 0,
            awakenings        INTEGER,
            resting_hr        INTEGER,
            notes             TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.commit()


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
    """Return entries oldest -> newest as a list of dicts with derived fields."""
    rows = conn.execute("SELECT * FROM entries ORDER BY date ASC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        bt = parse_time(d["bedtime"])
        wt = parse_time(d["wake_time"])
        d["_date"] = parse_date(d["date"])
        d["tib_min"] = time_in_bed_min(bt, wt)
        d["efficiency"] = sleep_efficiency(d["total_sleep_min"], d["tib_min"])
        out.append(d)
    return out


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
    recent = trailing(entries, days)
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
    """(7-night avg total sleep) + 30, floored at 330."""
    recent = trailing(entries, 7)
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
    blocks = weekly_blocks(entries)
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
    """Rule 3 — nudges based on the latest entry + its notes."""
    nudges = []
    if not entries:
        return nudges
    last = entries[-1]
    notes = (last.get("notes") or "").lower()

    if last["efficiency"] < EFF_OK_LOW:
        nudges.append(
            "Last night's efficiency was under 85%. Stimulus-control rule: if you "
            "are awake ~15-20 min, get out of bed, do something calm in dim light, "
            "and return only when sleepy."
        )

    if "alcohol" in notes or "wine" in notes or "beer" in notes or "drink" in notes:
        nudges.append(
            "You noted alcohol. Alcohol is a common cause of mid-night awakenings "
            "and fragmented sleep, even when it helps you fall asleep."
        )

    if _caffeine_after_noon(notes):
        nudges.append(
            "You noted caffeine in the afternoon/evening. Caffeine has a long "
            "half-life; intake after ~noon can reduce sleep quality."
        )

    # bedtime drift vs prescribed
    target_bed = prescribed_bedtime(window_min, wake_time)
    actual_bed = parse_time(last["bedtime"])
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

    blocks = weekly_blocks(entries)
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

    fatigue_words = (
        "exhausted", "daytime fatigue", "tired all day", "unrefreshing",
        "unrefreshed", "fatigue", "drowsy all day", "nodding off",
        "fell asleep at", "cant stay awake", "can't stay awake",
    )
    recent_notes = " ".join((e.get("notes") or "").lower() for e in trailing(entries, 14))
    if any(w in recent_notes for w in fatigue_words):
        flags.append(
            "You've logged persistent daytime fatigue / unrefreshing sleep. "
            "Consider seeing a doctor — this can have causes beyond sleep timing."
        )

    return flags


def adherence_note(entries):
    """One-line adherence summary for the most recent full week, or None."""
    blocks = weekly_blocks(entries)
    if not blocks:
        return None
    a = weekly_adherence(blocks[-1])
    if a is None:
        return None
    verdict = "consistent" if a <= CONSISTENCY_DRIFT_MIN else "drifting"
    return f"This week's adherence: avg {a:.0f} min off prescribed bedtime ({verdict})."


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

    # --- Last night --------------------------------------------------------
    last = entries[-1]
    lines.append("")
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
    recent7 = trailing(entries, 7)
    avg_sleep = avg([e["total_sleep_min"] for e in recent7])
    avg_eff = avg([e["efficiency"] for e in recent7])
    avg_restless = avg([e["restless_moments"] for e in recent7])
    lines.append("")
    lines.append(f"7-NIGHT AVERAGES  (n={len(recent7)})")
    lines.append(f"  Total sleep    : {minutes_to_hm(avg_sleep)}")
    lines.append(f"  Efficiency     : {avg_eff:.1f}%")
    lines.append(f"  Restless       : {avg_restless:.0f}")

    # --- Prescribed window -------------------------------------------------
    window = prescribed_window_min(entries)
    bed = prescribed_bedtime(window, wake_time)
    floored = window == WINDOW_FLOOR_MIN and (avg_sleep + WINDOW_PADDING_MIN) < WINDOW_FLOOR_MIN
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
        need = 7 - (len(entries) % 7 or 7)
        lines.append(f"  Need a full 7-night block first "
                     f"({len(entries)} logged; {need} more to next full week).")
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
    recent14 = trailing(entries, 14)
    lines.append("")
    lines.append("TRENDS (last %d nights)" % len(recent14))
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
    entries = all_entries(conn)
    if not entries:
        return "No entries yet."
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
    conn.execute(
        """
        INSERT INTO entries
            (date, bedtime, wake_time, total_sleep_min, restless_moments,
             awakenings, resting_hr, notes)
        VALUES (:date, :bedtime, :wake_time, :total_sleep_min, :restless_moments,
                :awakenings, :resting_hr, :notes)
        ON CONFLICT(date) DO UPDATE SET
            bedtime          = excluded.bedtime,
            wake_time        = excluded.wake_time,
            total_sleep_min  = excluded.total_sleep_min,
            restless_moments = excluded.restless_moments,
            awakenings       = excluded.awakenings,
            resting_hr       = excluded.resting_hr,
            notes            = excluded.notes
        """,
        e,
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
        print(f"{e['date']}  {e['bedtime']}->{e['wake_time']}  "
              f"sleep {e['total_sleep_min']}m  eff {e['efficiency']:.1f}%  "
              f"restless {e['restless_moments']}")


def cmd_delete(conn, args):
    cur = conn.execute("DELETE FROM entries WHERE date = ?", (args.date,))
    conn.commit()
    if cur.rowcount:
        print(f"Deleted entry for {args.date}.")
    else:
        print(f"No entry found for {args.date}.")


CSV_FIELDS = [
    "date", "bedtime", "wake_time", "total_sleep_min", "restless_moments",
    "awakenings", "resting_hr", "notes",
]


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
            entry = {
                "date": row["date"].strip(),
                "bedtime": fmt_time(parse_time(row["bedtime"])),
                "wake_time": fmt_time(parse_time(row["wake_time"])),
                "total_sleep_min": int(row["total_sleep_min"]),
                "restless_moments": int(row.get("restless_moments") or 0),
                "awakenings": int(row["awakenings"]) if row.get("awakenings") else None,
                "resting_hr": int(row["resting_hr"]) if row.get("resting_hr") else None,
                "notes": row.get("notes") or None,
            }
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
    effs = [e["efficiency"] for e in entries]
    best = max(entries, key=lambda e: e["efficiency"])
    worst = min(entries, key=lambda e: e["efficiency"])
    print(f"Nights logged   : {len(entries)} "
          f"({entries[0]['date']} -> {entries[-1]['date']})")
    print(f"Logging streak  : {logging_streak(entries)} night(s)")
    print(f"Avg total sleep : {minutes_to_hm(avg([e['total_sleep_min'] for e in entries]))}")
    print(f"Avg efficiency  : {avg(effs):.1f}%")
    print(f"Avg restless    : {avg([e['restless_moments'] for e in entries]):.0f}")
    print(f"Best night      : {best['date']}  {best['efficiency']:.1f}%")
    print(f"Worst night     : {worst['date']}  {worst['efficiency']:.1f}%")


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


def cmd_sync_garmin(conn, args):
    """
    Optional: pull a night's sleep from Garmin Connect.

    Garmin has no official consumer sleep API, so this uses the community
    `garminconnect` library (an unofficial wrapper around the Garmin Connect
    web endpoints). Install it with:  pip install garminconnect

    Credentials are read from env vars GARMIN_EMAIL and GARMIN_PASSWORD
    (never stored by this tool).
    """
    try:
        from garminconnect import Garmin
    except ImportError:
        print("The `garminconnect` library is not installed.\n"
              "  pip install garminconnect\n"
              "Then set GARMIN_EMAIL and GARMIN_PASSWORD and retry.\n"
              "Note: this is an UNOFFICIAL library; Garmin offers no official "
              "consumer sleep API. Manual `log` always works.")
        return

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        print("Set GARMIN_EMAIL and GARMIN_PASSWORD environment variables first.")
        return

    target = args.date or date.today().isoformat()
    try:
        api = Garmin(email, password)
        api.login()
        sleep = api.get_sleep_data(target)
    except Exception as exc:  # noqa: BLE001
        print(f"Garmin sync failed: {exc}")
        return

    dto = (sleep or {}).get("dailySleepDTO", {}) or {}
    total_sleep_sec = dto.get("sleepTimeSeconds")
    if not total_sleep_sec:
        print(f"No sleep data returned by Garmin for {target}.")
        return

    def _ts_to_time(ms):
        if not ms:
            return None
        return datetime.fromtimestamp(ms / 1000).time()

    bed = _ts_to_time(dto.get("sleepStartTimestampLocal"))
    wake = _ts_to_time(dto.get("sleepEndTimestampLocal"))
    wake_default = get_setting(conn, "wake_time", DEFAULT_WAKE_TIME)

    entry = {
        "date": target,
        "bedtime": fmt_time(bed) if bed else "23:00",
        "wake_time": fmt_time(wake) if wake else wake_default,
        "total_sleep_min": int(total_sleep_sec // 60),
        "restless_moments": int(sleep.get("restlessMomentsCount") or 0),
        "awakenings": dto.get("awakeCount"),
        "resting_hr": sleep.get("restingHeartRate"),
        "notes": "synced from Garmin",
    }
    upsert_entry(conn, entry)
    print(f"Synced {target} from Garmin: "
          f"sleep {minutes_to_hm(entry['total_sleep_min'])}, "
          f"restless {entry['restless_moments']}.")


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

    pg = sub.add_parser("sync-garmin", help="optional: pull a night from Garmin Connect")
    pg.add_argument("--date")
    pg.set_defaults(func=cmd_sync_garmin)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
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

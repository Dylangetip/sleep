# Sleep Tracker + CBT-I Suggestion Engine

Logs your nightly sleep metrics (the ones you pull from Garmin each morning),
computes **sleep efficiency** and trailing averages, and gives **evidence-based
suggestions** grounded in CBT-I (sleep restriction + stimulus control). Use it
as a **single-file Python CLI** or through a **web dashboard** — both share the
same SQLite database and the same tested engine.

> ⚠️ This tool gives **general, evidence-based guidance only — it does not
> diagnose anything.** Sleep restriction can cause daytime drowsiness for the
> first 1–2 weeks; be careful driving or operating machinery while sleepy.

## Web app

A FastAPI server (`app.py`) imports `sleep_tracker.py` and serves a single-page
dashboard from `static/`. No CBT-I rule is reimplemented — every number on the
page comes straight from the engine.

```bash
pip install -r requirements.txt        # fastapi + uvicorn
python3 sleep_tracker.py seed          # optional: one example night so it's not empty
uvicorn app:app --reload               # then open http://127.0.0.1:8000
```

The page shows tonight's target bedtime + prescribed window, last-night and
7-night stats, efficiency/restless charts, daily nudges, progress, the weekly
adjustment, the "what's affecting your sleep" correlations, a highlighted "see a
clinician" callout when doctor-flags fire, a **daily check-in form**, a history
table, and a fixed-wake setting. The CLI and web app share the same database.

### Automatic Garmin sync + daily check-in

Garmin supplies the objective sleep/health numbers; you log the subjective
things only you know. Set it up once on macOS:

```bash
python3 sleep_tracker.py garmin-login        # one-time; stores a token, handles 2FA
python3 sleep_tracker.py install-daily-sync   # pulls the last few nights every morning
```

The **Daily check-in** form (web) records caffeine (mg + time of last cup),
naps, last-meal time, wind-down / screens-before-bed, and 1–5 ratings for mood,
how rested you felt, and daytime sleepiness. A check-in **merges onto** that
night's Garmin sleep (neither overwrites the other), and the numeric inputs feed
the correlation engine — including derived "caffeine/last-meal hours before bed."

Your database now lives in a stable per-user location
(`~/Library/Application Support/SleepTracker/sleep.db` on macOS), so
re-downloading or moving the app folder never touches your data. Override with
the `SLEEP_DB` environment variable.

### API

| Method | Route | Returns |
|--------|-------|---------|
| GET    | `/api/entries`        | All nights with derived `tib_min` + `efficiency` |
| POST   | `/api/entries`        | Upsert a night by date |
| DELETE | `/api/entries/{date}` | Delete a night |
| POST   | `/api/entries`        | Upsert/**merge** a day by date — only `date` required; any subset of sleep or check-in fields |
| GET    | `/api/report`         | Structured `report` (`report_data`) + 14-night series + factors |
| GET/PUT| `/api/settings/wake`  | Get / set the fixed wake time |
| GET    | `/api/stats`          | Overall stats + logging streak |

### Garmin / sync commands (CLI)

| Command | What it does |
|---------|--------------|
| `garmin-login` | One-time interactive login; stores an OAuth token (handles 2FA) so syncs run hands-free |
| `sync-garmin [--date D]` | Pull one night |
| `sync-recent [--days N]` | Pull the last N days (default 3); used by the daily job, idempotent |
| `install-daily-sync [--hour H]` | macOS: schedule `sync-recent` to run every morning |
| `uninstall-daily-sync` | Remove the scheduled sync |
| `backfill.py START [END]` | Bulk-import a date range (one login, many nights) |

## CLI

Standard library only — no install needed.

**Requirements:** Python 3.8+. The web app additionally needs `fastapi` +
`uvicorn` (see `requirements.txt`); optional Garmin sync needs the community
`garminconnect` package (see below).

### Run it

```bash
# 1) Seed the example night so `report` works immediately
python3 sleep_tracker.py seed

# 2) Set your fixed wake time (used to compute the prescribed bedtime)
python3 sleep_tracker.py set-wake 08:27

# 3) See the report
python3 sleep_tracker.py report
```

The database is a local SQLite file (`sleep.db` in the current directory by
default; override with `--db /path/to/file.db` or the `SLEEP_DB` env var).

## Logging a night

One-line example (fields straight from the Garmin app):

```bash
python3 sleep_tracker.py log --date 2026-06-05 --bedtime 22:58 --wake 08:27 \
    --total-sleep 440 --restless 80 --resting-hr 56 --notes "1 coffee am"
```

Or run `python3 sleep_tracker.py log` with no args for interactive prompts.

`total-sleep` is Garmin's **total sleep** (actual sleep), not time in bed —
time in bed is derived from bedtime → wake time (midnight crossing handled).

## What you get

| Command            | What it does                                                            |
|--------------------|------------------------------------------------------------------------|
| `log`              | Add/replace a night (args or interactive prompts).                     |
| `report`           | Last night, 7-night averages, prescribed window, weekly adjustment, daily nudges, progress, trends, and guardrail flags. |
| `trend`            | Full table of every night + efficiency bars and sparklines.            |
| `set-wake HH:MM`   | Set your fixed wake time.                                               |
| `list`             | One line per logged night.                                             |
| `stats`            | Overall averages, best/worst night, and current logging streak.        |
| `delete DATE`      | Remove a night by date (`YYYY-MM-DD`).                                 |
| `export [--out f]` | Export all nights to CSV (stdout if no `--out`).                       |
| `import FILE`      | Import/merge nights from a CSV file.                                   |
| `seed`             | Insert the example night.                                              |
| `sync-garmin`      | Optional: pull a night from Garmin Connect.                            |

CSV uses the columns: `date, bedtime, wake_time, total_sleep_min,
restless_moments, awakenings, resting_hr, notes` — so `export` then `import`
round-trips your data and the CSV is easy to back up or edit by hand.

## The suggestion engine (rules)

1. **Prescribed window** = (7-night avg total sleep) + 30 min, hard floor
   **330 min (5.5 h)**. Given your fixed wake time, it prints the target bedtime.
2. **Weekly window adjustment** (on a full 7-night block):
   - avg efficiency **> 90%** → expand window 15–30 min (earlier bedtime).
   - **85–90%** → hold steady another week.
   - **< 85%** → hold or trim 15 min; emphasize consistency.
3. **Daily nudges** from the latest night + notes: sub-85% efficiency →
   stimulus-control "get out of bed" reminder; alcohol flag; afternoon-caffeine
   flag; bedtime drift > 60 min from prescribed → consistency reminder.
4. **Positive reinforcement** when restless moments / awakenings trend down.

## Guardrails

- Every report prints the no-diagnosis disclaimer and the daytime-drowsiness
  caution (driving/machinery).
- If efficiency stays **< 85% for 3+ consecutive weeks** *despite consistent
  adherence* (the report tracks weekly bedtime adherence to gate this), **or**
  you log persistent daytime fatigue / unrefreshing sleep, the report surfaces a
  recommendation to **see a doctor** — recurrent awakenings can have causes a
  tracker can't detect. If efficiency is low but bedtimes have drifted, it nudges
  you to tighten consistency first instead of escalating.

## Tests

```bash
python3 -m unittest -v
```

A stdlib `unittest` suite (`test_sleep_tracker.py`, no dependencies) covers the
time math, prescribed-window floor, all three weekly-adjustment branches, trend
detection, note-based nudges, the adherence-gated doctor flag, and DB
round-trips.

## Garmin sync (optional)

Garmin has **no official consumer sleep API**. This tool ships an optional
`sync-garmin` command that uses the community
[`garminconnect`](https://pypi.org/project/garminconnect/) library — an
unofficial wrapper around Garmin Connect's web endpoints. It can break if
Garmin changes those endpoints, so **manual `log` is the primary, always-works
path.**

```bash
pip install garminconnect
export GARMIN_EMAIL="you@example.com"
export GARMIN_PASSWORD="..."          # read from env only; never stored by this tool
python3 sleep_tracker.py sync-garmin --date 2026-06-05
```

It maps Garmin's `sleepTimeSeconds` → total sleep, `restlessMomentsCount` →
restless moments, sleep start/end timestamps → bedtime/wake, and resting HR when
available.

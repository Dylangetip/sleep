# Sleep Tracker + CBT-I Suggestion Engine

A single-file Python CLI that logs your nightly sleep metrics (the ones you pull
from Garmin each morning), computes **sleep efficiency** and trailing averages,
and gives **evidence-based suggestions** grounded in CBT-I (sleep restriction +
stimulus control).

> ⚠️ This tool gives **general, evidence-based guidance only — it does not
> diagnose anything.** Sleep restriction can cause daytime drowsiness for the
> first 1–2 weeks; be careful driving or operating machinery while sleepy.

## Requirements

- Python 3.8+ (standard library only for core features — no install needed).
- Optional Garmin sync needs the community `garminconnect` package (see below).

## Run it

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
| `seed`             | Insert the example night.                                              |
| `sync-garmin`      | Optional: pull a night from Garmin Connect.                            |

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
- If efficiency stays **< 85% for 3+ consecutive weeks** despite consistent
  adherence, **or** you log persistent daytime fatigue / unrefreshing sleep,
  the report surfaces a recommendation to **see a doctor** — recurrent
  awakenings can have causes a tracker can't detect.

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

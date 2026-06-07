#!/usr/bin/env python3
"""
app.py — FastAPI web layer for the sleep tracker.

Imports the real, unit-tested engine from `sleep_tracker.py` and exposes it
over a small JSON API, then serves the static single-page frontend. No CBT-I
rule is reimplemented here — every number comes from `sleep_tracker.py`, and
the web app shares the same SQLite database as the CLI.

Run:
    pip install -r requirements.txt
    uvicorn app:app --reload
    # open http://127.0.0.1:8000
"""

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import sleep_tracker as st

st.ensure_db_ready()  # make app-data dir + migrate a legacy ./sleep.db once
DB_PATH = st.DEFAULT_DB
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="Sleep Tracker", docs_url="/api/docs", openapi_url="/api/openapi.json")


# --------------------------------------------------------------------------- #
# DB helper — one short-lived connection per request (SQLite + threadpool)
# --------------------------------------------------------------------------- #

def with_conn(fn):
    conn = st.connect(DB_PATH)
    st.init_db(conn)
    try:
        return fn(conn)
    finally:
        conn.close()


def _to_int(v, default=None):
    if v is None:
        return default
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return default
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _to_float(v, default=None):
    if v is None:
        return default
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _public_entry(e):
    """Strip private fields and round efficiency for JSON output. Sleep fields
    may be None for a check-in-only day."""
    out = {
        "date": e["date"],
        "bedtime": e.get("bedtime"),
        "wake_time": e.get("wake_time"),
        "total_sleep_min": e.get("total_sleep_min"),
        "restless_moments": e.get("restless_moments"),
        "awakenings": e.get("awakenings"),
        "resting_hr": e.get("resting_hr"),
        "notes": e.get("notes"),
        "tib_min": e.get("tib_min"),
        "efficiency": round(e["efficiency"], 1) if e.get("efficiency") is not None else None,
        "caffeine_hours_before_bed": e.get("caffeine_hours_before_bed"),
        "last_meal_hours_before_bed": e.get("last_meal_hours_before_bed"),
    }
    for col in st.METRIC_COLUMN_NAMES + st.SUBJECTIVE_COLUMN_NAMES:
        out[col] = e.get(col)
    return out


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.get("/api/entries")
def get_entries():
    return with_conn(lambda c: [_public_entry(e) for e in st.all_entries(c)])


def _time_or_none(v):
    v = (v or "").strip()
    return st.fmt_time(st.parse_time(v)) if v else None


@app.post("/api/entries")
async def post_entry(request: Request):
    """Upsert a day by date. Only `date` is required — any subset of sleep or
    subjective fields can be sent; upsert merges them (so a check-in won't wipe
    the Garmin sleep numbers, and vice versa)."""
    body = await request.json()
    if not body.get("date"):
        raise HTTPException(400, "date is required")
    try:
        entry = {"date": body["date"].strip()}
        # sleep fields (optional — manual fallback)
        if "bedtime" in body:
            entry["bedtime"] = _time_or_none(body.get("bedtime"))
        if "wake_time" in body:
            entry["wake_time"] = _time_or_none(body.get("wake_time"))
        if "total_sleep_min" in body:
            entry["total_sleep_min"] = _to_int(body.get("total_sleep_min"))
        if "restless_moments" in body:
            entry["restless_moments"] = _to_int(body.get("restless_moments"))
        if "awakenings" in body:
            entry["awakenings"] = _to_int(body.get("awakenings"))
        if "resting_hr" in body:
            entry["resting_hr"] = _to_int(body.get("resting_hr"))
        if "notes" in body:
            entry["notes"] = (body.get("notes") or "").strip() or None
        # subjective check-in fields
        for col, coltype in st.SUBJECTIVE_COLUMNS:
            if col not in body:
                continue
            if coltype == "TEXT":
                entry[col] = _time_or_none(body.get(col))
            else:
                entry[col] = _to_int(body.get(col))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    def _save(conn):
        st.upsert_entry(conn, entry)
        saved = next((e for e in st.all_entries(conn) if e["date"] == entry["date"]), None)
        return _public_entry(saved) if saved else entry

    return with_conn(_save)


@app.delete("/api/entries/{date}")
def delete_entry(date: str):
    def _del(conn):
        cur = conn.execute("DELETE FROM entries WHERE date = ?", (date,))
        conn.commit()
        return {"deleted": date, "rows": cur.rowcount}
    return with_conn(_del)


@app.get("/api/report")
def get_report():
    return with_conn(st.report_data)


@app.get("/api/stats")
def get_stats():
    return with_conn(st.stats_data)


@app.get("/api/settings/wake")
def get_wake():
    return with_conn(lambda c: {"wake": st.get_setting(c, "wake_time", st.DEFAULT_WAKE_TIME)})


@app.put("/api/settings/wake")
async def put_wake(request: Request):
    body = await request.json()
    raw = body.get("wake")
    if not raw:
        raise HTTPException(400, "wake is required")
    try:
        wake = st.fmt_time(st.parse_time(raw))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return with_conn(lambda c: (st.set_setting(c, "wake_time", wake), {"wake": wake})[1])


@app.exception_handler(Exception)
async def _unhandled(request, exc):  # keep API errors as JSON, not HTML
    if isinstance(exc, HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return JSONResponse({"error": str(exc)}, status_code=500)


# Static frontend — mounted last so /api/* routes take precedence.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

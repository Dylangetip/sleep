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


def _public_entry(e):
    """Strip private fields and round efficiency for JSON output."""
    return {
        "date": e["date"],
        "bedtime": e["bedtime"],
        "wake_time": e["wake_time"],
        "total_sleep_min": e["total_sleep_min"],
        "restless_moments": e["restless_moments"],
        "awakenings": e["awakenings"],
        "resting_hr": e["resting_hr"],
        "notes": e["notes"],
        "tib_min": e["tib_min"],
        "efficiency": round(e["efficiency"], 1),
    }


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.get("/api/entries")
def get_entries():
    return with_conn(lambda c: [_public_entry(e) for e in st.all_entries(c)])


@app.post("/api/entries")
async def post_entry(request: Request):
    body = await request.json()
    if not body.get("date") or not body.get("bedtime") or not body.get("wake_time"):
        raise HTTPException(400, "date, bedtime and wake_time are required")
    total = _to_int(body.get("total_sleep_min"))
    if total is None:
        raise HTTPException(400, "total_sleep_min is required")
    try:
        entry = {
            "date": body["date"].strip(),
            "bedtime": st.fmt_time(st.parse_time(body["bedtime"])),
            "wake_time": st.fmt_time(st.parse_time(body["wake_time"])),
            "total_sleep_min": total,
            "restless_moments": _to_int(body.get("restless_moments"), 0),
            "awakenings": _to_int(body.get("awakenings"), None),
            "resting_hr": _to_int(body.get("resting_hr"), None),
            "notes": (body.get("notes") or "").strip() or None,
        }
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

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

import json
import os
import shutil
import uuid
from datetime import datetime

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import sleep_tracker as st

st.ensure_db_ready()  # make app-data dir + migrate a legacy ./sleep.db once
DB_PATH = st.DEFAULT_DB
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MEALS_DIR = os.path.join(st.app_data_dir(), "meals")
os.makedirs(MEALS_DIR, exist_ok=True)
KG_TO_LB = 2.2046226218

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
    for col in st.METRIC_COLUMN_NAMES + st.SUBJECTIVE_COLUMN_NAMES + st.BODY_COLUMN_NAMES:
        out[col] = e.get(col)
    return out


def _api_key(conn):
    return st.get_setting(conn, "anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")


def _meal_model(conn):
    return st.get_setting(conn, "meal_model", "claude-opus-4-8")


def _public_meal(m):
    out = dict(m)
    out["items"] = json.loads(m["ai_items_json"]) if m.get("ai_items_json") else []
    out.pop("ai_items_json", None)
    out["photo_url"] = ("/photos/" + m["photo_path"]) if m.get("photo_path") else None
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


# --------------------------------------------------------------------------- #
# Garmin sync (on-demand)
# --------------------------------------------------------------------------- #

@app.post("/api/sync")
def post_sync(days: int = 3):
    def _go(conn):
        try:
            r = st.sync_garmin_range(conn, days)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc))
        r["last_synced"] = st.get_setting(conn, "last_synced")
        return r
    return with_conn(_go)


@app.get("/api/sync/status")
def sync_status():
    return with_conn(lambda c: {"last_synced": st.get_setting(c, "last_synced")})


# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #

@app.get("/api/activities")
def get_activities(start: str = None, end: str = None):
    return with_conn(lambda c: st.all_activities(c, start, end))


# --------------------------------------------------------------------------- #
# Meals (diet) + Claude vision analysis
# --------------------------------------------------------------------------- #

@app.get("/api/meals")
def get_meals(date: str = None):
    return with_conn(lambda c: [_public_meal(m) for m in st.all_meals(c, date)])


@app.post("/api/meals")
async def post_meal(photo: UploadFile = File(None), date: str = Form(...),
                    time: str = Form(None), notes: str = Form("")):
    rel_path = None
    abs_path = None
    if photo is not None:
        ext = os.path.splitext(photo.filename or "")[1].lower() or ".jpg"
        rel_path = os.path.join(date, uuid.uuid4().hex + ext)
        abs_path = os.path.join(MEALS_DIR, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as fh:
            shutil.copyfileobj(photo.file, fh)

    def _save(conn):
        meal_id = st.insert_meal(conn, {
            "date": date, "time": (time or None), "photo_path": rel_path,
            "notes": (notes or None), "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        if abs_path:
            try:
                import ai
                result = ai.analyze_meal_photo(
                    abs_path, notes or "", _meal_model(conn), _api_key(conn))
                result["status"] = "analyzed"
                result["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
                st.update_meal(conn, meal_id, result)
            except Exception as exc:  # noqa: BLE001 — keep the row, flag failure
                st.update_meal(conn, meal_id, {"status": "failed",
                                               "ai_description": str(exc)[:300]})
        else:
            st.update_meal(conn, meal_id, {"status": "manual"})
        return _public_meal(st.get_meal(conn, meal_id))

    return with_conn(_save)


@app.post("/api/meals/{meal_id}/reanalyze")
def reanalyze_meal(meal_id: int):
    def _go(conn):
        m = st.get_meal(conn, meal_id)
        if not m:
            raise HTTPException(404, "meal not found")
        if not m.get("photo_path"):
            raise HTTPException(400, "meal has no photo to analyze")
        abs_path = os.path.join(MEALS_DIR, m["photo_path"])
        try:
            import ai
            result = ai.analyze_meal_photo(
                abs_path, m.get("notes") or "", _meal_model(conn), _api_key(conn))
            result["status"] = "analyzed"
            result["analyzed_at"] = datetime.now().isoformat(timespec="seconds")
            st.update_meal(conn, meal_id, result)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, str(exc))
        return _public_meal(st.get_meal(conn, meal_id))
    return with_conn(_go)


@app.delete("/api/meals/{meal_id}")
def delete_meal(meal_id: int):
    def _del(conn):
        m = st.get_meal(conn, meal_id)
        if m and m.get("photo_path"):
            try:
                os.remove(os.path.join(MEALS_DIR, m["photo_path"]))
            except OSError:
                pass
        return {"deleted": meal_id, "rows": st.delete_meal(conn, meal_id)}
    return with_conn(_del)


# --------------------------------------------------------------------------- #
# Diet dashboard, insights, AI daily summary
# --------------------------------------------------------------------------- #

def _latest_weight_kg(entries):
    for e in reversed(entries):
        if e.get("weight_kg") is not None:
            return e["weight_kg"]
    return None


@app.get("/api/diet")
def get_diet(date: str = None):
    def _go(conn):
        d = date or datetime.now().date().isoformat()
        unit = st.get_setting(conn, "weight_unit", "lb")
        entries = st.all_entries(conn)
        row = next((e for e in entries if e["date"] == d), {})
        meals = [_public_meal(m) for m in st.all_meals(conn, d)]
        cin = sum(m["calories"] or 0 for m in meals)
        protein = sum(m["protein_g"] or 0 for m in meals)
        carbs = sum(m["carbs_g"] or 0 for m in meals)
        fat = sum(m["fat_g"] or 0 for m in meals)
        cout = (row.get("active_calories") or 0) + (row.get("resting_calories") or 0)
        wkg = _latest_weight_kg(entries)
        conv = (lambda kg: round(kg * KG_TO_LB, 1)) if unit == "lb" else (lambda kg: round(kg, 1))

        def gnum(k):
            v = st.get_setting(conn, k)
            return float(v) if v not in (None, "") else None
        return {
            "date": d, "weight_unit": unit,
            "calories_in": cin, "calories_out": cout or None,
            "net_calories": (cin - cout) if cout else None,
            "protein_g": round(protein, 1), "carbs_g": round(carbs, 1), "fat_g": round(fat, 1),
            "goals": {"calories": gnum("calorie_goal"), "protein": gnum("protein_goal"),
                      "carbs": gnum("carb_goal"), "fat": gnum("fat_goal"),
                      "weight": gnum("weight_goal")},
            "weight": conv(wkg) if wkg is not None else None,
            "weight_series": [{"date": e["date"], "weight": conv(e["weight_kg"])}
                              for e in entries if e.get("weight_kg") is not None],
            "calorie_series": _calorie_series(conn),
            "meals": meals,
        }
    return with_conn(_go)


def _calorie_series(conn):
    rows = conn.execute("SELECT date, SUM(calories) c FROM meals GROUP BY date "
                        "ORDER BY date DESC LIMIT 30").fetchall()
    return [{"date": r["date"], "calories": r["c"]} for r in reversed(rows)]


@app.get("/api/insights")
def get_insights():
    return with_conn(st.insights_data)


@app.get("/api/summary/daily")
def get_daily_summary(date: str = None, refresh: bool = False):
    def _go(conn):
        d = date or datetime.now().date().isoformat()
        cached = conn.execute(
            "SELECT summary, generated_at FROM daily_summaries WHERE date=?", (d,)
        ).fetchone()
        if cached and not refresh:
            return {"date": d, "summary": cached["summary"],
                    "generated_at": cached["generated_at"], "cached": True}
        facts = st.day_facts_text(conn, d)
        try:
            import ai
            text = ai.daily_health_summary(facts, _meal_model(conn), _api_key(conn))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, str(exc))
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO daily_summaries(date,summary,model,generated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET summary=excluded.summary, "
            "model=excluded.model, generated_at=excluded.generated_at",
            (d, text, _meal_model(conn), now))
        conn.commit()
        return {"date": d, "summary": text, "generated_at": now, "cached": False}
    return with_conn(_go)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

SETTING_KEYS = ["weight_unit", "weight_goal", "calorie_goal", "protein_goal",
                "carb_goal", "fat_goal", "meal_model"]


@app.get("/api/settings")
def get_settings():
    def _go(conn):
        out = {k: st.get_setting(conn, k) for k in SETTING_KEYS}
        out["weight_unit"] = out["weight_unit"] or "lb"
        out["meal_model"] = out["meal_model"] or "claude-opus-4-8"
        out["wake"] = st.get_setting(conn, "wake_time", st.DEFAULT_WAKE_TIME)
        out["anthropic_key_set"] = bool(_api_key(conn))
        out["last_synced"] = st.get_setting(conn, "last_synced")
        return out
    return with_conn(_go)


@app.put("/api/settings")
async def put_settings(request: Request):
    body = await request.json()
    def _go(conn):
        for k in SETTING_KEYS:
            if k in body and body[k] not in (None, ""):
                st.set_setting(conn, k, body[k])
        return get_settings()
    return with_conn(_go)


@app.put("/api/settings/anthropic-key")
async def put_anthropic_key(request: Request):
    body = await request.json()
    key = (body.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "key is required")
    return with_conn(lambda c: (st.set_setting(c, "anthropic_api_key", key),
                                {"anthropic_key_set": True})[1])


@app.exception_handler(Exception)
async def _unhandled(request, exc):  # keep API errors as JSON, not HTML
    if isinstance(exc, HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return JSONResponse({"error": str(exc)}, status_code=500)


# Meal photos (read-only) from the app-data dir.
app.mount("/photos", StaticFiles(directory=MEALS_DIR), name="photos")

# Static frontend — mounted last so /api/* and /photos routes take precedence.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

"""
Push daily Garmin health metrics to the Weight Tracker app (Supabase table wt_garmin_daily).

Called from fetch_garmin.py with the already-logged-in client, so Garmin sees one login.
Writes ONLY to Supabase (private, behind row-level security) — nothing is added to this
public repo's data/ folder.

Env vars (GitHub secrets). If any is missing the push is skipped silently:
  WT_SUPABASE_URL, WT_SUPABASE_SERVICE_ROLE_KEY, WT_USER_ID
"""
import datetime as dt
import json
import os
import urllib.error
import urllib.request

DAYS = 3  # Garmin fills in sleep/HRV late, so re-sync a few days each run


def _int(v):
    try:
        return None if v is None else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _safe(fn, *args):
    try:
        return fn(*args) or {}
    except Exception as e:
        print(f"  health: {getattr(fn, '__name__', fn)}{args} failed: {type(e).__name__}")
        return {}


def _day(client, day, user_id):
    stats = _safe(client.get_stats, day)
    sleep_raw = _safe(client.get_sleep_data, day)
    sleep = sleep_raw.get("dailySleepDTO") or {}
    hrv = (_safe(client.get_hrv_data, day).get("hrvSummary")) or {}
    # Garmin has moved sleepScores between the top level and dailySleepDTO across versions
    scores = sleep.get("sleepScores") or sleep_raw.get("sleepScores") or {}
    stress = stats.get("averageStressLevel")
    return {
        "user_id": user_id,
        "date": day,
        "total_kcal": _int(stats.get("totalKilocalories")),
        "active_kcal": _int(stats.get("activeKilocalories")),
        "bmr_kcal": _int(stats.get("bmrKilocalories")),
        "steps": _int(stats.get("totalSteps")),
        "resting_hr": _int(stats.get("restingHeartRate")),
        "stress_avg": _int(stress) if isinstance(stress, (int, float)) and stress >= 0 else None,
        "body_battery_high": _int(stats.get("bodyBatteryHighestValue")),
        "body_battery_low": _int(stats.get("bodyBatteryLowestValue")),
        "sleep_seconds": _int(sleep.get("sleepTimeSeconds")),
        "sleep_score": _int(((scores.get("overall") or {}).get("value"))),
        "hrv_last_night": _int(hrv.get("lastNightAvg")),
    }


def push_health(client):
    url = os.environ.get("WT_SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("WT_SUPABASE_SERVICE_ROLE_KEY", "")
    user_id = os.environ.get("WT_USER_ID", "")
    if not (url and key and user_id):
        print("Weight tracker push skipped (WT_* secrets not set).")
        return

    today = dt.date.today()
    rows = []
    for i in range(DAYS):
        day = (today - dt.timedelta(days=i)).isoformat()
        row = _day(client, day, user_id)
        if any(v is not None for k, v in row.items() if k not in ("user_id", "date")):
            rows.append(row)
    if not rows:
        print("Weight tracker push: no health data returned.")
        return

    req = urllib.request.Request(
        f"{url}/rest/v1/wt_garmin_daily?on_conflict=user_id,date",
        data=json.dumps(rows).encode(),
        method="POST",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"Weight tracker: pushed {len(rows)} day(s) of health metrics (HTTP {r.status}).")
    except urllib.error.HTTPError as e:
        print(f"Weight tracker push failed: HTTP {e.code} {e.read().decode()[:300]}")

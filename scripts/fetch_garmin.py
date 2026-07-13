"""
Fetches recent Garmin Connect activities and merges them into a local JSON store.
Auth uses email/password via garminconnect, with token caching so we don't
log in fresh every run (Garmin rate-limits / flags repeated logins).

Required GitHub Secrets:
  GARMIN_EMAIL
  GARMIN_PASSWORD
"""

import os
import json
import datetime as dt
from pathlib import Path

from garminconnect import Garmin

DATA_DIR = Path("data")
DATA_FILE = DATA_DIR / "activities.json"
TOKEN_DIR = Path(".garmin_tokens")  # cached session, see workflow for persistence


def get_client():
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]

    client = Garmin(email, password)

    # Try resuming a cached session first (avoids repeated fresh logins)
    try:
        client.login(str(TOKEN_DIR))
    except Exception:
        client.login()
        TOKEN_DIR.mkdir(exist_ok=True)
        client.garth.dump(str(TOKEN_DIR))

    return client


def load_existing():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {}


def fetch_recent_activities(client, days_back=14, limit=50):
    activities = client.get_activities(0, limit)
    cutoff = dt.datetime.now() - dt.timedelta(days=days_back)
    recent = []
    for act in activities:
        start = dt.datetime.strptime(act["startTimeLocal"], "%Y-%m-%d %H:%M:%S")
        if start >= cutoff:
            recent.append(act)
    return recent


def fetch_daily_wellness(client, days_back=14):
    """Pulls sleep and body battery for each of the last `days_back` days."""
    wellness = {}
    today = dt.date.today()
    for i in range(days_back):
        day = today - dt.timedelta(days=i)
        day_str = day.isoformat()
        entry = {}

        try:
            sleep = client.get_sleep_data(day_str)
            daily_sleep = sleep.get("dailySleepDTO", {}) if sleep else {}
            sleep_seconds = daily_sleep.get("sleepTimeSeconds")
            entry["sleep_duration_min"] = round(sleep_seconds / 60, 1) if sleep_seconds else None
            entry["sleep_score"] = (sleep.get("sleepScores", {}) or {}).get("overall", {}).get("value") if sleep else None
        except Exception:
            entry["sleep_duration_min"] = None
            entry["sleep_score"] = None

        try:
            bb = client.get_body_battery(day_str, day_str)
            if bb and isinstance(bb, list) and len(bb) > 0:
                entry["body_battery_max"] = bb[0].get("charged") if isinstance(bb[0], dict) else None
                entry["body_battery_min"] = bb[0].get("drained") if isinstance(bb[0], dict) else None
            else:
                entry["body_battery_max"] = None
                entry["body_battery_min"] = None
        except Exception:
            entry["body_battery_max"] = None
            entry["body_battery_min"] = None

        wellness[day_str] = entry

    return wellness


def normalize(act):
    """Pull out the fields we actually care about for the dashboard."""
    return {
        "id": act.get("activityId"),
        "name": act.get("activityName"),
        "type": act.get("activityType", {}).get("typeKey"),
        "start": act.get("startTimeLocal"),
        "duration_s": act.get("duration"),
        "distance_m": act.get("distance"),
        "calories": act.get("calories"),
        "avg_hr": act.get("averageHR"),
        "max_hr": act.get("maxHR"),
        "training_load": act.get("activityTrainingLoad"),
        "avg_power": act.get("avgPower"),
        "normalized_power": act.get("normPower"),
        "elevation_gain": act.get("elevationGain"),
        "avg_pace": act.get("averageSpeed"),
        "vo2max_estimate": act.get("vO2MaxValue"),
    }


def load_existing_wellness():
    wfile = DATA_DIR / "wellness.json"
    if wfile.exists():
        return json.loads(wfile.read_text())
    return {}


def main():
    DATA_DIR.mkdir(exist_ok=True)
    client = get_client()

    store = load_existing()
    recent = fetch_recent_activities(client, days_back=14, limit=50)

    new_count = 0
    for act in recent:
        norm = normalize(act)
        key = str(norm["id"])
        if key not in store:
            new_count += 1
        store[key] = norm

    DATA_FILE.write_text(json.dumps(store, indent=2, default=str))

    wellness_store = load_existing_wellness()
    fresh_wellness = fetch_daily_wellness(client, days_back=14)
    wellness_store.update(fresh_wellness)
    (DATA_DIR / "wellness.json").write_text(json.dumps(wellness_store, indent=2, default=str))

    print(f"Synced. {new_count} new activities. {len(store)} total stored. "
          f"Wellness updated for {len(fresh_wellness)} days.")

    # ── NEW: Tell GitHub Actions if new activities were found ──
    if "GITHUB_ENV" in os.environ:
        with open(os.environ["GITHUB_ENV"], "a") as env_file:
            if new_count > 0:
                env_file.write("GARMIN_NEW_DATA=true\n")
            else:
                env_file.write("GARMIN_NEW_DATA=false\n")

if __name__ == "__main__":
    main()

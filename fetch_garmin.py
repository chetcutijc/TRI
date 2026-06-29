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
    print(f"Synced. {new_count} new activities. {len(store)} total stored.")


if __name__ == "__main__":
    main()

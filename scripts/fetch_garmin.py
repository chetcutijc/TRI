"""
Fetches recent Garmin Connect activities and merges them into a local JSON store.
Auth uses email/password via garminconnect, with token caching so we don't
log in fresh every run (Garmin rate-limits / flags repeated logins).

Required GitHub Secrets:
  GARMIN_EMAIL
  GARMIN_PASSWORD
"""

import os
import sys
import json
import datetime as dt
from pathlib import Path

from garminconnect import Garmin

DATA_DIR = Path("data")
DATA_FILE = DATA_DIR / "activities.json"
TOKEN_DIR = Path(".garmin_tokens")  # cached session, see workflow for persistence


class GarminRateLimited(Exception):
    """Garmin returned 429 — back off rather than retrying."""


def _is_rate_limited(exc):
    """Walk the exception chain looking for a 429 / Too Many Requests.
    The 429 is often buried in a nested cause, not the top-level message."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        msg = str(exc).lower()
        if "429" in msg or "too many requests" in msg or "rate limit" in msg:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _save_tokens(client):
    """Persist the session so the next run can resume instead of logging in.
    Version-tolerant: older garminconnect exposes client.garth.dump(); newer
    versions removed .garth. Never crash the sync just because saving failed."""
    TOKEN_DIR.mkdir(exist_ok=True)
    for attr in ("garth", "client"):
        obj = getattr(client, attr, None)
        dump = getattr(obj, "dump", None) if obj is not None else None
        if callable(dump):
            try:
                dump(str(TOKEN_DIR))
                print(f"Saved Garmin session tokens (via client.{attr}.dump).")
                return
            except Exception as e:
                print(f"WARNING: token save via client.{attr}.dump failed: {e}")
    print("WARNING: no token-save method found on this garminconnect version — "
          "session not cached. Next run will need a fresh login.")


def get_client():
    email = os.environ["GARMIN_EMAIL"]
    password = os.environ["GARMIN_PASSWORD"]

    client = Garmin(email, password)

    # 1. Resume cached session if we have one
    if TOKEN_DIR.exists() and any(TOKEN_DIR.iterdir()):
        try:
            client.login(str(TOKEN_DIR))
            print("Resumed cached Garmin session.")
            _save_tokens(client)  # refresh the cache with any renewed tokens
            return client
        except Exception as e:
            if _is_rate_limited(e):
                # Do NOT immediately retry with a fresh login — that doubles the
                # requests Garmin is already throttling and prolongs the block.
                raise GarminRateLimited(str(e)) from e
            print(f"Cached session unusable ({type(e).__name__}) — trying one fresh login.")

    # 2. One fresh credential login
    client = Garmin(email, password)
    try:
        client.login()
    except Exception as e:
        if _is_rate_limited(e):
            raise GarminRateLimited(str(e)) from e
        raise  # wrong password etc. — fail loudly, don't hide it
    print("Fresh Garmin login succeeded.")
    _save_tokens(client)
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


def _set_github_env(new_data):
    if "GITHUB_ENV" in os.environ:
        with open(os.environ["GITHUB_ENV"], "a") as env_file:
            env_file.write(f"GARMIN_NEW_DATA={'true' if new_data else 'false'}\n")


def main():
    DATA_DIR.mkdir(exist_ok=True)

    try:
        client = get_client()
    except GarminRateLimited:
        # Garmin is throttling logins. Exit cleanly so the rest of the workflow
        # still rebuilds the dashboard from the data we already have, instead of
        # the whole run failing. The block typically clears on its own within hours.
        print("=" * 60)
        print("Garmin rate-limited the login (429). Skipping this fetch —")
        print("dashboard will rebuild from existing data. Do NOT spam Sync Now;")
        print("it resets the throttle window. Next scheduled run will retry.")
        print("=" * 60)
        _set_github_env(False)
        sys.exit(0)

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

    _set_github_env(new_count > 0)


if __name__ == "__main__":
    main()

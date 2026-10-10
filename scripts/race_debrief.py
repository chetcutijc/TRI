"""
scripts/race_debrief.py
Generates a one-time AI debrief for each recently completed race.

For every race in data/races.json that:
  - happened in the last 60 days, and
  - has a Garmin activity logged on race day, and
  - doesn't already have a saved debrief,
it sends target / predicted / actual plus the build-up metrics to Claude and
saves the result to data/race_debriefs.json. Each race costs ONE API call
ever — to regenerate a debrief, delete its entry from that file.

Requires secret: ANTHROPIC_API_KEY
"""

import os
import sys
import json
import datetime as dt
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_dashboard as bd  # reuse the exact same metric calculations

MODEL = "claude-sonnet-4-6"
LOOKBACK_DAYS = 60


def call_claude(prompt, api_key):
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 1500,
        "system": (
            "You are an expert endurance coach writing a post-race debrief for an "
            "amateur triathlete training from Malta. Be specific and honest, refer to "
            "the actual numbers given, and separate likely factors from speculation — "
            "one race cannot prove cause and effect. Respond ONLY with valid JSON "
            "matching the schema provided, no preamble, no markdown fences."
        ),
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=payload, method="POST",
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    text = body["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY not set — skipping race debriefs.")
        return

    df = bd.load_activities()
    wellness = bd.load_wellness()
    plan_full = bd.load_plan_full()
    debriefs = bd.load_race_debriefs()
    today = dt.date.today()
    upcoming = [r for r in bd.RACES if r["date"] > today]
    changed = False

    for race in bd.RACES:
        key = bd.race_key(race)
        age = (today - race["date"]).days
        if age <= 0 or age > LOOKBACK_DAYS or key in debriefs:
            continue

        actual = bd.compute_race_actual(race, df)
        if not actual:
            print(f"{race['name']}: no race-day activity found — skipping debrief.")
            continue

        target = bd.compute_race_target_time(race)
        pred = bd.compute_race_prediction(race, df, as_of=race["date"])
        buildup = bd.compute_race_buildup(race, df, wellness, plan_full) or {}
        obs = [txt for _, txt in buildup.get("observations", [])]

        context = {
            "race": {"name": race["name"], "date": race["date"].isoformat(),
                     "distances": race.get("distances", {}), "note": race.get("note", "")},
            "target": {"total": target["total"], "legs": target["parts"]} if target else None,
            "predicted_from_prior_4_weeks": {"total": pred["total"], "legs": pred["parts"]} if pred else None,
            "actual": {"total": actual["total"], "legs": actual["parts"],
                       "transitions_excluded": actual["legs_separate"] and len(actual["parts"]) > 1},
            "buildup": {k: v for k, v in buildup.items() if k != "observations"},
            "rule_based_observations": obs,
            "next_races": [{"name": r["name"], "date": r["date"].isoformat(),
                            "days_away": (r["date"] - today).days} for r in upcoming[:2]],
        }

        prompt = f"""Post-race data for a debrief:

{json.dumps(context, indent=2, default=str)}

Notes on the data:
- "predicted" is a naive projection (average training pace x race distance over the
  4 weeks before the race). It ignores race-day adrenaline and endurance fade, so
  short races usually beat it and long races usually miss it.
- Only per-activity averages exist — no lap splits — so you cannot judge pacing
  within the race. Don't pretend to.
- buildup: legs[].longest_pct = longest session as % of race distance;
  speed_gain_pct = race speed vs average training speed; hr_gap = race avg HR
  minus training avg HR; taper_ratio = race-week load / normal weekly load.

Respond with ONLY this JSON:
{{
  "headline": "<one sentence: the result in context of target and preparation>",
  "went_well": ["<2-3 specific points grounded in the numbers>"],
  "to_improve": ["<2-3 specific points grounded in the numbers>"],
  "next_race": ["<2-3 concrete changes for the next race listed in next_races, if any>"]
}}"""

        print(f"Generating debrief for {race['name']}...")
        try:
            result = call_claude(prompt, api_key)
        except Exception as e:
            print(f"Debrief failed for {race['name']}: {e} — will retry next sync.")
            continue
        result["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        debriefs[key] = result
        changed = True

    if changed:
        bd.RACE_DEBRIEFS_FILE.write_text(json.dumps(debriefs, indent=2))
        print(f"Saved debriefs to {bd.RACE_DEBRIEFS_FILE}")
    else:
        print("No new race debriefs needed.")


if __name__ == "__main__":
    main()

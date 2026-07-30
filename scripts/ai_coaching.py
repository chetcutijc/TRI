"""
scripts/ai_coaching.py
Runs on Saturday syncs only. Calls the Claude API to:
  1. Analyse the last 4 weeks of training vs the plan
  2. Generate 3-5 coaching suggestions
  3. Propose specific session tweaks for the next 4 weeks
     (never auto-applied — user reviews on the website and downloads
      an adjusted ICS if they accept)

Requires secret:  ANTHROPIC_API_KEY
Reads:            data/activities.json, data/wellness.json,
                  data/plan_full.json,   data/plan_sessions.json
Writes:           data/ai_coaching.json
"""

import json
import os
import datetime as dt
import urllib.request
import urllib.error
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────────
ACTIVITIES_FILE    = Path("data/activities.json")
WELLNESS_FILE      = Path("data/wellness.json")
PLAN_FULL_FILE     = Path("data/plan_full.json")
PLAN_SESSIONS_FILE = Path("data/plan_sessions.json")
COACHING_FILE      = Path("data/ai_coaching.json")

RACES = [
    {"name": "Marathon",            "date": "2027-02-07",
     "note": "Target sub-4h (~5:45/km pace)"},
    {"name": "Ironman Italy Cervia","date": "2027-06-20",
     "note": "Full Ironman — swim 1:50/100m, bike ~190W, run 6:30/km"},
]

MODEL = "claude-sonnet-4-6"   # good balance of quality vs cost


# ── Helpers ───────────────────────────────────────────────────────────────────
def load_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def summarise_activities(acts_store, weeks=4):
    """Return a compact per-session summary for the last N weeks."""
    if not acts_store:
        return []
    cutoff = dt.date.today() - dt.timedelta(weeks=weeks)
    rows = []
    for a in acts_store.values():
        try:
            date = dt.datetime.strptime(a["start"], "%Y-%m-%d %H:%M:%S").date()
        except Exception:
            continue
        if date < cutoff:
            continue
        pace_sec_km = (1000 / a["avg_pace"]) if a.get("avg_pace") else None
        pace_str = (f"{int(pace_sec_km)//60}:{int(pace_sec_km)%60:02d}/km"
                    if pace_sec_km else None)
        rows.append({
            "date":          date.isoformat(),
            "type":          a.get("type"),
            "duration_min":  round(a["duration_s"] / 60) if a.get("duration_s") else None,
            "distance_km":   round(a["distance_m"] / 1000, 1) if a.get("distance_m") else None,
            "avg_hr":        a.get("avg_hr"),
            "avg_pace":      pace_str,
            "avg_power_w":   round(a["avg_power"]) if a.get("avg_power") else None,
            "training_load": round(a["training_load"]) if a.get("training_load") else None,
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def summarise_plan(plan_full, weeks=4):
    """Return upcoming plan sessions for the next N weeks."""
    if not plan_full:
        return []
    today  = dt.date.today()
    cutoff = today + dt.timedelta(weeks=weeks)
    rows = []
    for s in plan_full:
        try:
            date = dt.date.fromisoformat(s["date"])
        except Exception:
            continue
        if date < today or date > cutoff:
            continue
        rows.append({
            "date":         s["date"],
            "day":          s.get("day"),
            "discipline":   s.get("discipline"),
            "summary":      s.get("summary"),
            "duration_min": s.get("duration_min"),
            "pace":         s.get("pace"),
            "power":        s.get("power"),
            "distance":     s.get("distance"),
            "notes":        (s.get("notes") or "")[:200],
        })
    return rows


def summarise_wellness(wellness_store, weeks=4):
    if not wellness_store:
        return []
    cutoff = dt.date.today() - dt.timedelta(weeks=weeks)
    rows = []
    for day, vals in wellness_store.items():
        if dt.date.fromisoformat(day) < cutoff:
            continue
        rows.append({"date": day, **vals})
    return sorted(rows, key=lambda x: x["date"])


# ── Claude API call ───────────────────────────────────────────────────────────
def call_claude(prompt: str, api_key: str) -> dict:
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 2000,
        "system": (
            "You are an expert triathlon coach helping an amateur athlete prepare for "
            "a Marathon (Feb 2027, sub-4h target) and a full Ironman (Jun 2027). "
            "The athlete trains from Malta. Respond ONLY with valid JSON matching the "
            "schema provided — no preamble, no markdown fences."
        ),
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    text = body["content"][0]["text"].strip()
    # Strip markdown fences if Claude adds them anyway
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY not set — skipping AI coaching")
        return

    acts     = load_json(ACTIVITIES_FILE)
    wellness = load_json(WELLNESS_FILE)
    plan     = load_json(PLAN_FULL_FILE) if PLAN_FULL_FILE.exists() else []
    if isinstance(plan, dict):
        plan = list(plan.values())

    recent_acts = summarise_activities(acts, weeks=4)
    upcoming    = summarise_plan(plan, weeks=4)
    well_data   = summarise_wellness(wellness, weeks=4)

    days_to_marathon = (dt.date(2027, 2, 7) - dt.date.today()).days
    days_to_ironman  = (dt.date(2027, 6, 20) - dt.date.today()).days

    prompt = f"""
Here is my training data from the last 4 weeks:

RECENT ACTIVITIES (last 4 weeks):
{json.dumps(recent_acts, indent=2)}

WELLNESS DATA (sleep hrs & body battery, last 4 weeks):
{json.dumps(well_data, indent=2)}

UPCOMING PLAN (next 4 weeks):
{json.dumps(upcoming, indent=2)}

RACE TARGETS:
- Marathon: {days_to_marathon} days away — sub-4h, target pace 5:45/km
- Ironman Italy Cervia: {days_to_ironman} days away — swim 1:50/100m, bike ~190W, run 6:30/km

Analyse my training and respond with ONLY this JSON structure (no extra text):
{{
  "summary": "<2-3 sentence overall assessment of the last 4 weeks>",
  "suggestions": [
    "<suggestion 1 — max 2 sentences, specific and actionable>",
    "<suggestion 2>",
    "<suggestion 3>",
    "<suggestion 4 — optional>",
    "<suggestion 5 — optional>"
  ],
  "proposed_changes": [
    {{
      "date": "YYYY-MM-DD",
      "discipline": "running|cycling|swimming|strength_training|rest",
      "current_session": "<brief description of what the plan currently says>",
      "proposed_session": "<what you suggest instead>",
      "change_type": "increase|decrease|replace|skip|add",
      "reason": "<1 sentence why>"
    }}
  ],
  "generated_at": "{dt.datetime.utcnow().isoformat()}",
  "weeks_analysed": 4,
  "days_to_marathon": {days_to_marathon},
  "days_to_ironman": {days_to_ironman}
}}

Rules:
- Only propose changes where you see clear evidence (e.g. 2+ missed sessions, 
  consistent HR too high, pace trend worsening).
- Keep proposed_changes to max 6 entries — quality over quantity.
- proposed_changes may be empty [] if training is on track.
- Do not change race week sessions or sessions more than 4 weeks away.
- Suggestions should prioritise the nearest race target.
"""

    print("Calling Claude API for coaching analysis...")
    try:
        result = call_claude(prompt, api_key)
    except Exception as e:
        print(f"Claude API call failed: {e}")
        # Write a minimal fallback so the dashboard doesn't break
        result = {
            "summary":          "AI coaching analysis unavailable this week.",
            "suggestions":      [],
            "proposed_changes": [],
            "generated_at":     dt.datetime.utcnow().isoformat(),
            "weeks_analysed":   4,
            "error":            str(e),
        }

    COACHING_FILE.write_text(json.dumps(result, indent=2))
    print(f"Coaching saved → {COACHING_FILE}")
    print(f"Summary: {result.get('summary','')[:120]}")
    print(f"Suggestions: {len(result.get('suggestions',[]))}")
    print(f"Proposed changes: {len(result.get('proposed_changes',[]))}")


if __name__ == "__main__":
    main()

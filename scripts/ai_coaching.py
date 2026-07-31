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

RACES_FILE = Path("data/races.json")


def load_races():
    """Load races from data/races.json, soonest first."""
    if not RACES_FILE.exists():
        print("WARNING: data/races.json not found.")
        return []
    try:
        raw = json.loads(RACES_FILE.read_text())
    except Exception as e:
        print(f"WARNING: could not parse races.json: {e}")
        return []
    out = []
    for r in raw:
        try:
            dt.date.fromisoformat(r["date"])
            out.append(r)
        except Exception:
            print(f"WARNING: skipping malformed race {r!r}")
    return sorted(out, key=lambda x: x["date"])

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


def detect_race_conflicts(plan_full, races, days_before=2, max_easy_min=45):
    """
    Deterministic sanity check (no AI judgment needed): flags any session in
    the last `days_before` days before a race that isn't rest or short/easy.
    E.g. a long bike ride the night before a marathon should never happen —
    this catches that class of bug regardless of what Claude decides.
    """
    if not plan_full or not races:
        return []
    conflicts = []
    for r in races:
        try:
            race_date = dt.date.fromisoformat(r["date"])
        except Exception:
            continue
        window_start = race_date - dt.timedelta(days=days_before)
        for s in plan_full:
            try:
                sdate = dt.date.fromisoformat(s["date"])
            except Exception:
                continue
            if not (window_start <= sdate < race_date):
                continue
            if s.get("discipline") in ("rest", "race", None):
                continue
            dur = s.get("duration_min") or 0
            if dur and dur <= max_easy_min:
                continue
            conflicts.append({
                "race_name": r["name"], "race_date": race_date.isoformat(),
                "session_date": s["date"], "discipline": s.get("discipline", "?"),
                "summary": s.get("summary", ""), "duration_min": dur,
                "days_before_race": (race_date - sdate).days,
            })
    conflicts.sort(key=lambda c: (c["race_date"], c["session_date"]))
    return conflicts


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
            "You are an expert triathlon coach helping an amateur athlete. "
            "Their upcoming races and targets are given in the user message — "
            "prioritise the nearest race when giving advice. "
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

    races = load_races()
    today = dt.date.today()

    # Deterministic pre-race conflict detection (e.g. a long ride the night
    # before a marathon) — these are non-negotiable fixes, not suggestions
    # that depend on Claude's judgment.
    conflicts = detect_race_conflicts(plan, races, days_before=2)
    if conflicts:
        conflict_lines = "\n".join(
            f"- {c['session_date']} [{c['discipline']}] \"{c['summary']}\" "
            f"({c['duration_min']}min) is only {c['days_before_race']} day(s) "
            f"before {c['race_name']} on {c['race_date']}"
            for c in conflicts
        )
        conflicts_block = (
            "The following sessions are dangerously close to a race and are "
            "NOT rest/easy — these MUST be addressed in proposed_changes "
            "(convert to rest or a short shakeout), regardless of the normal "
            "4/8-week change window:\n" + conflict_lines
        )
    else:
        conflicts_block = "None detected."

    # Build a race-target description block from data/races.json
    race_lines = []
    for r in races:
        d = (dt.date.fromisoformat(r["date"]) - today).days
        if d < 0:
            continue
        dist = r.get("distances", {}) or {}
        dbits = []
        if dist.get("swim_m"):  dbits.append(f"swim {dist['swim_m']}m")
        if dist.get("bike_km"): dbits.append(f"bike {dist['bike_km']}km")
        if dist.get("run_km"):  dbits.append(f"run {dist['run_km']}km")
        dist_txt = (" [" + ", ".join(dbits) + "]") if dbits else ""

        tgt = r.get("targets", {}) or {}
        bits = []
        if "run_pace_sec_km" in tgt:
            s = tgt["run_pace_sec_km"]; bits.append(f"run {s//60}:{s%60:02d}/km")
        if "bike_power_w" in tgt:
            bits.append(f"bike ~{tgt['bike_power_w']}W")
        if "bike_speed_kmh" in tgt:
            bits.append(f"bike ~{tgt['bike_speed_kmh']}km/h")
        if "swim_pace_100m_sec" in tgt:
            s = tgt["swim_pace_100m_sec"]; bits.append(f"swim {s//60}:{s%60:02d}/100m")
        detail = (" — " + ", ".join(bits)) if bits else ""
        note = f" ({r['note']})" if r.get("note") else ""
        race_lines.append(f"- {r['name']}: {d} days away{dist_txt}{detail}{note}")
    races_block = "\n".join(race_lines) if race_lines else "- No upcoming races configured"

    # Kept for backwards compatibility with the dashboard's countdown fields
    next_race      = races[0] if races else None
    days_to_next   = ((dt.date.fromisoformat(next_race["date"]) - today).days
                      if next_race else None)

    prompt = f"""
Here is my training data from the last 4 weeks:

RECENT ACTIVITIES (last 4 weeks):
{json.dumps(recent_acts, indent=2)}

WELLNESS DATA (sleep hrs & body battery, last 4 weeks):
{json.dumps(well_data, indent=2)}

UPCOMING PLAN (next 4 weeks):
{json.dumps(upcoming, indent=2)}

RACE TARGETS (in date order — prioritise the nearest):
{races_block}

PRE-RACE SCHEDULING CONFLICTS (auto-detected, non-negotiable):
{conflicts_block}

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
  "generated_at": "{dt.datetime.now(dt.timezone.utc).isoformat()}",
  "weeks_analysed": 4
}}

Rules:
- If PRE-RACE SCHEDULING CONFLICTS lists any items, those changes are MANDATORY
  and must be included in proposed_changes even if it pushes you over 6 entries —
  athlete safety before a race takes priority over the usual limits.
- Only propose changes where you see clear evidence (e.g. 2+ missed sessions, 
  consistent HR too high, pace trend worsening).
- Keep proposed_changes to max 6 entries — quality over quantity.
- proposed_changes may be empty [] if training is on track.
- Suggestions should prioritise the nearest race target.
- Race awareness: if a race in RACE TARGETS above is NOT reflected in the current
  plan structure (e.g. it was added after the plan was built), you may propose
  changes to sessions further out than the usual 4-week window specifically to
  prepare for it — the further out the race, the more gradual the adjustment
  should be:
    * Race 0-3 weeks away: propose a short taper/race-specific prep block in the
      days leading into it (reduced volume, race-pace touches, extra rest).
    * Race 4-8 weeks away: you may propose a gradual build across the intervening
      weeks (e.g. progressively increasing race-discipline volume or race-pace
      work), still capped at 6 total proposed_changes.
    * Race 8+ weeks away: only mention it in "summary" as something to plan for
      later — don't propose concrete session changes yet, there's no urgency.
- Do not change sessions in the week of ANY race unless that race is the one
  being prepared for.
- Don't invent new distances beyond what's realistic from current fitness — the
  goal is arriving fresh and injury-free, not peak performance for an unplanned
  race squeezed into an existing block.
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

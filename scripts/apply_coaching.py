"""
scripts/apply_coaching.py
Triggered manually via the "Apply AI Coaching Changes" workflow (or the
"Apply to Dashboard" button on the site).

Reads proposed changes from data/ai_coaching.json and merges them into
data/plan_full.json so the dashboard reflects them.

Matching strategy (mirrors the dashboard's plan viewer):
  1. Exact match on (date, discipline)
  2. Fallback: if exactly one session exists on that date, use it
  3. change_type == "add": insert a brand new session instead of editing
Anything that still can't be matched is reported loudly rather than
silently skipped.
"""

import json
import re
import datetime as dt
from pathlib import Path

COACHING_FILE  = Path("data/ai_coaching.json")
PLAN_FULL_FILE = Path("data/plan_full.json")
RACES_FILE     = Path("data/races.json")


def load_races():
    if not RACES_FILE.exists():
        return []
    try:
        return json.loads(RACES_FILE.read_text())
    except Exception:
        return []


def detect_race_conflicts(plan_full, races, days_before=2, max_easy_min=45):
    """Same rule as build_dashboard.py's version: flags any non-rest session
    >45min scheduled in the last 2 days before a race."""
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
                "session_date": s["date"], "discipline": s.get("discipline", "?"),
            })
    return conflicts


def main():
    if not COACHING_FILE.exists():
        print("No ai_coaching.json found — nothing to apply.")
        return
    if not PLAN_FULL_FILE.exists():
        print("plan_full.json not found — cannot apply.")
        return

    coaching = json.loads(COACHING_FILE.read_text())
    changes  = coaching.get("proposed_changes", [])
    if not changes:
        print("No proposed changes in ai_coaching.json — nothing to do.")
        return

    plan = json.loads(PLAN_FULL_FILE.read_text())
    races = load_races()
    conflicts_before = detect_race_conflicts(plan, races)
    if conflicts_before:
        print(f"Detected {len(conflicts_before)} pre-race conflict(s) — any change "
              f"touching these sessions will be forced to rest/30min regardless "
              f"of what the AI's change_type says.\n")

    print(f"Loaded {len(plan)} plan sessions, {len(changes)} proposed changes.\n")

    applied   = 0
    unmatched = []

    for change in changes:
        target_date = change.get("date", "")
        disc        = change.get("discipline", "")
        proposed    = change.get("proposed_session", "")
        reason      = change.get("reason", "")
        change_type = change.get("change_type", "replace")

        # ── change_type "add": create a new session, don't edit an existing one ──
        if change_type == "add":
            try:
                day_name = dt.date.fromisoformat(target_date).strftime("%A")
            except Exception:
                day_name = ""
            plan.append({
                "date":         target_date,
                "day":          day_name,
                "discipline":   disc,
                "summary":      "[AI ADDED] " + proposed[:60],
                "duration_min": 60,
                "notes":        f"[AI ADDED] {proposed}\n\nReason: {reason}",
                "pace":         None,
                "power":        None,
                "distance":     None,
            })
            applied += 1
            print(f"  ADD      {target_date} [{disc}] -> new session created")
            continue

        # ── 1. exact match on date + discipline ──
        match = next(
            (s for s in plan
             if s.get("date") == target_date and s.get("discipline") == disc),
            None
        )

        # ── 2. fallback: single session on that date, whatever its discipline ──
        if match is None:
            same_day = [s for s in plan if s.get("date") == target_date]
            if len(same_day) == 1:
                match = same_day[0]
                print(f"  (fallback match on date only for {target_date}: "
                      f"plan says '{match.get('discipline')}', AI said '{disc}')")

        if match is None:
            same_day = [s for s in plan if s.get("date") == target_date]
            unmatched.append({
                "date": target_date,
                "discipline": disc,
                "sessions_on_that_date": [s.get("discipline") for s in same_day],
            })
            continue

        # ── apply the change ──
        original_notes = match.get("notes", "")

        # Was this session flagged as a pre-race conflict? If so, whatever the
        # AI proposed, it MUST end up short/easy — no partial half-measures
        # like a blind 10% duration cut. Checked against the plan state
        # *before* this change is applied.
        is_conflict_fix = any(
            c["session_date"] == target_date and c["discipline"] == disc
            for c in conflicts_before
        )

        if change_type == "skip" or is_conflict_fix:
            match["discipline"]   = "rest"
            match["duration_min"] = 30
            match["summary"]      = match.get("summary", "") + " (AI: skipped)"
            match["notes"]        = f"[AI: SKIPPED] {proposed}\n\nReason: {reason}\n\nOriginal: {original_notes}"
        else:
            match["summary"] = match.get("summary", "") + " (AI adjusted)"
            match["notes"]   = f"[AI ADJUSTED] {proposed}\n\nReason: {reason}\n\nOriginal: {original_notes}"
            if match.get("duration_min"):
                # Prefer an explicit duration Claude actually wrote (e.g. "cap at
                # 30min", "reduce to 45 minutes") over blind percentage math —
                # a flat *0.9 barely changes a 180min session.
                explicit = re.search(r"(\d+)\s*-?\s*\d*\s*min", proposed)
                if explicit:
                    match["duration_min"] = int(explicit.group(1))
                elif change_type == "increase":
                    match["duration_min"] = round(match["duration_min"] * 1.10)
                elif change_type == "decrease":
                    match["duration_min"] = round(match["duration_min"] * 0.90)

        applied += 1
        tag = " [pre-race safety: forced to rest/30min]" if is_conflict_fix else ""
        print(f"  {change_type.upper():<9}{target_date} [{disc}] -> applied{tag}")

    # keep plan chronological (new "add" sessions land in the right place)
    plan.sort(key=lambda s: s.get("date", ""))
    PLAN_FULL_FILE.write_text(json.dumps(plan, indent=2))

    # ── archive to history, then clear so changes aren't applied twice ──
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    history = coaching.get("history", [])
    history.append({
        "applied_at":    now_iso,
        "generated_at":  coaching.get("generated_at", ""),
        "summary":       coaching.get("summary", ""),
        "applied_count": applied,
        "changes":       changes,          # full record of what was suggested
        "unmatched":     unmatched,
    })
    # keep the last 12 rounds so the file doesn't grow forever
    coaching["history"] = history[-12:]

    coaching["applied_at"]    = now_iso
    coaching["applied_count"] = applied
    if unmatched:
        coaching["unmatched"] = unmatched
    coaching["proposed_changes"] = []
    COACHING_FILE.write_text(json.dumps(coaching, indent=2))

    print("\n" + "=" * 55)
    print(f"Applied {applied} of {len(changes)} changes to plan_full.json.")
    if unmatched:
        print(f"\n{len(unmatched)} change(s) could NOT be matched:")
        for u in unmatched:
            found = ", ".join(u["sessions_on_that_date"]) or "no sessions on that date"
            print(f"  - {u['date']} wanted '{u['discipline']}' but plan has: {found}")
    print("=" * 55)


if __name__ == "__main__":
    main()

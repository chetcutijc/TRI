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
import datetime as dt
from pathlib import Path

COACHING_FILE  = Path("data/ai_coaching.json")
PLAN_FULL_FILE = Path("data/plan_full.json")


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

        if change_type == "skip":
            match["discipline"]   = "rest"
            match["duration_min"] = 30
            match["summary"]      = match.get("summary", "") + " (AI: skipped)"
            match["notes"]        = f"[AI: SKIPPED] {proposed}\n\nReason: {reason}\n\nOriginal: {original_notes}"
        else:
            match["summary"] = match.get("summary", "") + " (AI adjusted)"
            match["notes"]   = f"[AI ADJUSTED] {proposed}\n\nReason: {reason}\n\nOriginal: {original_notes}"
            if match.get("duration_min"):
                if change_type == "increase":
                    match["duration_min"] = round(match["duration_min"] * 1.10)
                elif change_type == "decrease":
                    match["duration_min"] = round(match["duration_min"] * 0.90)

        applied += 1
        print(f"  {change_type.upper():<9}{target_date} [{disc}] -> applied")

    # keep plan chronological (new "add" sessions land in the right place)
    plan.sort(key=lambda s: s.get("date", ""))
    PLAN_FULL_FILE.write_text(json.dumps(plan, indent=2))

    # ── record + clear so changes aren't applied twice ──
    coaching["applied_at"]    = dt.datetime.now(dt.timezone.utc).isoformat()
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

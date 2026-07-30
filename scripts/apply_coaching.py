"""
scripts/apply_coaching.py
Triggered manually via GitHub Actions "Apply AI Coaching" workflow.
Reads accepted proposed changes from data/ai_coaching.json and
merges them into data/plan_full.json so the dashboard reflects them.
"""
import json, datetime as dt
from pathlib import Path

COACHING_FILE  = Path("data/ai_coaching.json")
PLAN_FULL_FILE = Path("data/plan_full.json")

def main():
    if not COACHING_FILE.exists():
        print("No ai_coaching.json found — nothing to apply.")
        return

    coaching = json.loads(COACHING_FILE.read_text())
    changes  = coaching.get("proposed_changes", [])
    if not changes:
        print("No proposed changes in ai_coaching.json.")
        return

    if not PLAN_FULL_FILE.exists():
        print("plan_full.json not found.")
        return

    plan = json.loads(PLAN_FULL_FILE.read_text())

    applied = 0
    for change in changes:
        target_date = change.get("date")
        disc        = change.get("discipline")
        proposed    = change.get("proposed_session", "")
        reason      = change.get("reason", "")
        change_type = change.get("change_type", "replace")

        # Find matching session(s) in plan_full.json
        for session in plan:
            if session.get("date") == target_date and session.get("discipline") == disc:
                if change_type == "skip":
                    session["discipline"] = "rest"
                    session["notes"] = f"[AI: skipped — {reason}]"
                    session["duration_min"] = 30
                else:
                    # Merge proposed session text into notes
                    session["notes"] = f"[AI ADJUSTED] {proposed}\n\nOriginal: {session.get('notes','')}\nReason: {reason}"
                    session["summary"] = session["summary"] + " ✏️"
                    # If change_type is increase/decrease and duration exists, adjust it
                    if change_type == "increase" and session.get("duration_min"):
                        session["duration_min"] = round(session["duration_min"] * 1.10)
                    elif change_type == "decrease" and session.get("duration_min"):
                        session["duration_min"] = round(session["duration_min"] * 0.90)
                applied += 1
                break

    PLAN_FULL_FILE.write_text(json.dumps(plan, indent=2))

    # Mark coaching as applied so it's not re-applied accidentally
    coaching["applied_at"] = dt.datetime.utcnow().isoformat()
    coaching["applied_count"] = applied
    coaching["proposed_changes"] = []  # clear so next Saturday is a fresh slate
    COACHING_FILE.write_text(json.dumps(coaching, indent=2))

    print(f"Applied {applied} changes to plan_full.json.")
    print("proposed_changes cleared from ai_coaching.json.")

if __name__ == "__main__":
    main()

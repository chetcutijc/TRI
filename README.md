# Garmin Training Dashboard

Auto-syncs Garmin Connect activity data every 6 hours via GitHub Actions, rebuilds a static dashboard, and optionally emails it to you.

## Setup
1. Push this folder's contents to a new GitHub repo.
2. Settings > Secrets and variables > Actions, add:
   - `GARMIN_EMAIL`
   - `GARMIN_PASSWORD`
   - (optional, for email) `EMAIL_USERNAME` — your Gmail address
   - (optional, for email) `EMAIL_PASSWORD` — a Gmail **App Password**, not your real password (Google Account → Security → 2-Step Verification → App passwords)
3. Settings > Pages, set source to `docs/` folder on `main` branch (only works if repo is public, or you have GitHub Enterprise/Pro for private Pages).
4. Actions tab → run "Sync Garmin Data and Build Dashboard" manually once.
5. After that it runs automatically every 6 hours.

## Data files (in `data/`)
- `activities.json` — auto-populated by the sync script from Garmin
- `wellness.json` — auto-populated: daily sleep + body battery
- `plan.json` — weekly planned minutes per discipline, built from your training plan's ICS export. **Swim minutes here include an assumed 120min/week (Tue+Fri, 60min each)** since the original plan calendar didn't have explicit swim session entries — adjust if your actual swim duration differs.
- `plan_sessions.json` — per-session pace/power targets parsed from the plan (used for the running pace and cycling power comparison tables). Auto-generated, not meant to be hand-edited.
- `manual_log.json` — **you edit this manually.** Strength sessions aren't reliably auto-logged by Garmin, so tick them off here: add a line like `"2026-06-22": true` (using the Monday date of that week) each week you completed your strength session. Edit directly on GitHub (pencil icon) and commit; it'll show up in the dashboard on the next sync.

## What's on the dashboard
- Weekly training volume by discipline, training load trend, HR per session
- On-target % vs plan (weekly volume basis)
- Planned vs completed sessions table (last 8 weeks)
- Running: target pace vs actual pace, per session + average % off target
- Cycling: target power vs actual power, per session + average % off target
- Swimming: total swims, avg distance, avg pace per 100m, distance + pace trend charts
- Sleep duration trend, body battery trend, weekly readiness (load vs sleep)
- Strength session manual tracking table
- Recent sessions table

## Notes
- `garminconnect` is an unofficial library reverse-engineering Garmin's app API. Works well but isn't Garmin-sanctioned — check the project's GitHub if it breaks after a Garmin update.
- Session tokens are cached via GitHub Actions cache to avoid repeated fresh logins, which Garmin can flag.
- Running pace and cycling power targets are matched to actual activities by date (±1 day). If your plan and actual sessions drift apart in timing, matches may be imperfect — spot-check the table occasionally.

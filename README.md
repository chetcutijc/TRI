# Garmin Training Dashboard

Auto-syncs Garmin Connect activity data every 6 hours via GitHub Actions, and rebuilds a static dashboard published to GitHub Pages.

## Setup
1. Create a new GitHub repo and push this folder's contents.
2. Go to Settings > Secrets and variables > Actions, add:
   - GARMIN_EMAIL
   - GARMIN_PASSWORD
3. Go to Settings > Pages, set source to 'docs/' folder on main branch.
4. Go to Actions tab, run 'Sync Garmin Data and Build Dashboard' manually once to confirm it works.
5. After that it runs automatically every 6 hours. Your dashboard URL will be:
   https://<your-username>.github.io/<repo-name>/

## Optional: plan vs actual
Add a data/plan.json file structured as:
{
  "2026-06-29": { "swimming": {"duration_min": 120}, "cycling": {"duration_min": 300} }
}
Keyed by the Monday date of each week. The dashboard will show an on-target % chart automatically once this exists.

## Notes
- garminconnect is an unofficial library reverse-engineering Garmin's app API. It works well but isn't Garmin-sanctioned -- if Garmin changes their API it may need an update (check the project's GitHub for fixes).
- Session tokens are cached via GitHub Actions cache to avoid repeated fresh logins, which Garmin can flag.


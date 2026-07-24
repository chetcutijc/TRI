import json
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go

# ── DIRECTORY SETUP ───────────────────────────────────────────────────────────
DOCS_DIR = Path("docs")
DATA_DIR = Path("data")
DOCS_DIR.mkdir(exist_ok=True)

OUT_HTML = DOCS_DIR / "index.html"
OUT_PRINT = DOCS_DIR / "print.html"

ACTIVITIES_FILE = DATA_DIR / "activities.json"
WELLNESS_FILE = DATA_DIR / "wellness.json"

# URL-encoded Swimming Emoji Favicon (Prevents quote breaking and works across browsers/iOS)
FAVICON_URI = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E🏊%3C/text%3E%3C/svg%3E"


# ── DATA LOADING & PROCESSING ─────────────────────────────────────────────────
def load_json_data(filepath):
    if not filepath.exists():
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


activities_raw = load_json_data(ACTIVITIES_FILE)
wellness_raw = load_json_data(WELLNESS_FILE)

df_act = pd.DataFrame(activities_raw) if activities_raw else pd.DataFrame()
df_well = pd.DataFrame(wellness_raw) if wellness_raw else pd.DataFrame()


# ── CHART BUILDERS ────────────────────────────────────────────────────────────

def create_sleep_chart(df):
    """Generates Sleep Duration chart without 'trace' in the legend."""
    if df.empty or "sleep_duration_hours" not in df.columns:
        return "<p class='no-data'>No sleep data available</p>"

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=df["date"],
            y=df["sleep_duration_hours"],
            name="Sleep Duration",  # Explicit trace name
            marker_color="#5B6EF5",
        )
    )

    fig.update_layout(
        title="Sleep Duration (Hours)",
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis_title=None,
        yaxis_title="Hours",
        showlegend=False,  # Completely hides the legend to remove 'trace'
        template="plotly_white",
        height=300,
    )
    fig.update_xaxes(type="date", tickformat="%b %d")
    return fig.to_html(full_html=False, include_plotlyjs=False)


def create_cycling_chart(df):
    """Generates Cycling Performance chart with proper date/time x-axis."""
    if df.empty or "activity_type" not in df.columns:
        return "<p class='no-data'>No cycling data available</p>"

    df_cycle = df[
        df["activity_type"].str.lower().str.contains("cycling|bike", na=False)
    ].copy()

    if df_cycle.empty:
        return "<p class='no-data'>No cycling sessions recorded</p>"

    # Fix: Explicitly parse start_time to datetime to format X-axis cleanly
    df_cycle["start_time"] = pd.to_datetime(df_cycle["start_time"])
    df_cycle = df_cycle.sort_values("start_time")

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df_cycle["start_time"],
            y=df_cycle["distance_km"],
            mode="lines+markers",
            name="Ride Distance",
            line=dict(color="#2ECC71", width=2),
            marker=dict(size=6),
        )
    )

    fig.update_layout(
        title="Cycling Distance Trends",
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis_title=None,
        yaxis_title="Distance (km)",
        showlegend=False,
        template="plotly_white",
        height=300,
    )
    # Fix: Force clean date formatting on X-axis ticks
    fig.update_xaxes(
        type="date", tickformat="%b %d", hoverformat="%b %d, %Y %H:%M"
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


# ── SWIM COMPLIANCE LOGIC ─────────────────────────────────────────────────────

def get_swim_compliance(df):
    """Tracks Tuesday/Friday pool swims (Target: >=2000m at <=2:30/100m pace)."""
    if df.empty or "activity_type" not in df.columns:
        return []

    df_swim = df[
        df["activity_type"].str.lower().str.contains("swim", na=False)
    ].copy()

    if df_swim.empty:
        return []

    df_swim["start_time"] = pd.to_datetime(df_swim["start_time"])
    df_swim = df_swim.sort_values("start_time", ascending=False)

    records = []
    for _, row in df_swim.iterrows():
        dist_m = row.get("distance_m", 0)
        dur_s = row.get("duration_s", 0)
        dt = row["start_time"]

        pace_s = (dur_s / dist_m * 100) if dist_m > 0 else 999
        dist_pass = dist_m >= 2000
        pace_pass = pace_s <= 150  # 2:30/100m

        pace_min = int(pace_s // 60)
        pace_sec = int(pace_s % 60)

        records.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "day": dt.strftime("%A"),
                "distance": f"{int(dist_m)}m",
                "pace": f"{pace_min}:{pace_sec:02d}/100m",
                "pass": dist_pass and pace_pass,
            }
        )
    return records


# ── HTML GENERATOR ────────────────────────────────────────────────────────────

def render_dashboard_html(is_print_version=False):
    sleep_chart = create_sleep_chart(df_well)
    cycling_chart = create_cycling_chart(df_act)
    swim_records = get_swim_compliance(df_act)

    now_utc = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")

    # Build Swim Compliance Table Rows
    swim_rows = ""
    for r in swim_records[:5]:  # Show recent 5 sessions
        status = (
            "<span style='color:green;font-weight:bold;'>✓ Compliant</span>"
            if r["pass"]
            else "<span style='color:red;font-weight:bold;'>✗ Below Target</span>"
        )
        swim_rows += f"<tr><td>{r['date']} ({r['day']})</td><td>{r['distance']}</td><td>{r['pace']}</td><td>{status}</td></tr>"

    if not swim_rows:
        swim_rows = (
            "<tr><td colspan='4'>No swim sessions recorded.</td></tr>"
        )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🏊🚴🏃 Training Dashboard</title>
    
    <!-- Favicon & Apple Touch Icon -->
    <link rel="icon" href="{FAVICON_URI}">
    <link rel="apple-touch-icon" href="{FAVICON_URI}">
    
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            max-width: 1140px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f8f9fa;
            color: #333;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid #e9ecef;
            padding-bottom: 10px;
            margin-bottom: 20px;
        }}
        .updated {{ color: #6c757d; font-size: 0.85em; }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }}
        .card {{
            background: #fff;
            border-radius: 12px;
            padding: 16px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th, td {{
            text-align: left;
            padding: 8px 12px;
            border-bottom: 1px solid #dee2e6;
        }}
        th {{ background-color: #f1f3f5; font-size: 0.85em; text-transform: uppercase; }}
        .no-data {{ color: #888; text-align: center; padding: 20px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🏊🚴🏃 Training Dashboard</h1>
        <div class="updated">Last updated: {now_utc}</div>
    </div>

    <div class="grid">
        <div class="card">
            {sleep_chart}
        </div>
        <div class="card">
            {cycling_chart}
        </div>
    </div>

    <div class="card">
        <h3>🏊 Pool Swim Compliance Target</h3>
        <p style="font-size: 0.85em; color: #6c757d;">Target: ≥ 2,000m distance | Pace ≤ 2:30/100m</p>
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Distance</th>
                    <th>Pace</th>
                    <th>Compliance</th>
                </tr>
            </thead>
            <tbody>
                {swim_rows}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
    return html_content


# ── EXECUTION & FILE WRITING ──────────────────────────────────────────────────
if __name__ == "__main__":
    # Generate index.html for Web
    OUT_HTML.write_text(render_dashboard_html(is_print_version=False), encoding="utf-8")
    
    # Generate print.html for Headless Chrome PDF conversion
    OUT_PRINT.write_text(render_dashboard_html(is_print_version=True), encoding="utf-8")
    
    print("Dashboard HTML files successfully generated in /docs")

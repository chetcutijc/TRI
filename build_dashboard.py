"""
Builds a static HTML dashboard from data/activities.json.
Output goes to docs/index.html, which GitHub Pages serves automatically.

Plan-vs-actual comparison reads from data/plan.json (you maintain this manually
or generate it once from your 55-week plan - see plan_template.json).
"""

import json
import datetime as dt
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

DATA_FILE = Path("data/activities.json")
PLAN_FILE = Path("data/plan.json")
OUT_FILE = Path("docs/index.html")


def load_activities():
    store = json.loads(DATA_FILE.read_text())
    df = pd.DataFrame(store.values())
    if df.empty:
        return df
    df["start"] = pd.to_datetime(df["start"])
    df["duration_min"] = df["duration_s"] / 60
    df["distance_km"] = df["distance_m"] / 1000
    df = df.sort_values("start")
    return df


def load_plan():
    if PLAN_FILE.exists():
        return json.loads(PLAN_FILE.read_text())
    return {}


def weekly_summary(df):
    df = df.copy()
    df["week"] = df["start"].dt.to_period("W").apply(lambda r: r.start_time)
    summary = df.groupby(["week", "type"]).agg(
        sessions=("id", "count"),
        duration_min=("duration_min", "sum"),
        distance_km=("distance_km", "sum"),
        load=("training_load", "sum"),
    ).reset_index()
    return summary


def on_target_pct(actual_weekly, plan):
    """Compare actual weekly hours vs planned hours per discipline, if plan.json present."""
    if not plan:
        return None
    rows = []
    for week, group in actual_weekly.groupby("week"):
        week_key = week.strftime("%Y-%m-%d")
        planned = plan.get(week_key, {})
        for _, row in group.iterrows():
            planned_min = planned.get(row["type"], {}).get("duration_min")
            if planned_min:
                pct = min(100, round(100 * row["duration_min"] / planned_min))
                rows.append({"week": week, "type": row["type"], "pct_on_target": pct})
    return pd.DataFrame(rows)


def build_html(df, plan):
    if df.empty:
        OUT_FILE.parent.mkdir(exist_ok=True)
        OUT_FILE.write_text("<h1>No activity data yet</h1>")
        return

    weekly = weekly_summary(df)
    ontarget = on_target_pct(weekly, plan)

    fig1 = go.Figure()
    for atype in weekly["type"].unique():
        sub = weekly[weekly["type"] == atype]
        fig1.add_trace(go.Bar(x=sub["week"], y=sub["duration_min"], name=atype))
    fig1.update_layout(barmode="stack", title="Weekly Training Volume (minutes) by Discipline",
                        template="plotly_white")

    fig2 = go.Figure()
    load_weekly = df.copy()
    load_weekly["week"] = load_weekly["start"].dt.to_period("W").apply(lambda r: r.start_time)
    load_by_week = load_weekly.groupby("week")["training_load"].sum().reset_index()
    fig2.add_trace(go.Scatter(x=load_by_week["week"], y=load_by_week["training_load"],
                               mode="lines+markers", name="Training Load"))
    fig2.update_layout(title="Weekly Training Load Trend", template="plotly_white")

    fig3 = go.Figure()
    hr_df = df.dropna(subset=["avg_hr"])
    fig3.add_trace(go.Scatter(x=hr_df["start"], y=hr_df["avg_hr"], mode="markers",
                               name="Avg HR", marker=dict(size=8)))
    fig3.update_layout(title="Average Heart Rate per Session", template="plotly_white")

    fig4 = None
    if ontarget is not None and not ontarget.empty:
        fig4 = go.Figure()
        for atype in ontarget["type"].unique():
            sub = ontarget[ontarget["type"] == atype]
            fig4.add_trace(go.Scatter(x=sub["week"], y=sub["pct_on_target"],
                                       mode="lines+markers", name=atype))
        fig4.update_layout(title="On-Target % vs Plan", yaxis_range=[0, 110],
                            template="plotly_white")

    recent = df.tail(5)[["start", "name", "type", "distance_km", "duration_min", "avg_hr", "training_load"]]
    recent_html = recent.to_html(index=False, classes="table", border=0)

    last_30 = df[df["start"] >= (dt.datetime.now() - dt.timedelta(days=30))]
    total_sessions = len(last_30)
    total_hours = round(last_30["duration_min"].sum() / 60, 1)
    total_km = round(last_30["distance_km"].sum(), 1)
    avg_load = round(last_30["training_load"].mean(), 1) if "training_load" in last_30 else "n/a"

    charts_html = "".join([
        pio.to_html(f, full_html=False, include_plotlyjs=(i == 0))
        for i, f in enumerate([fig1, fig2, fig3] + ([fig4] if fig4 is not None else []))
    ])

    html = f"""
    <html>
    <head>
        <title>Training Dashboard</title>
        <style>
            body {{ font-family: -apple-system, sans-serif; max-width: 1000px; margin: 40px auto; padding: 0 20px; background:#fafafa; }}
            h1 {{ font-size: 1.8em; }}
            .stats {{ display:flex; gap:20px; margin:20px 0; }}
            .card {{ background:white; border-radius:12px; padding:16px 24px; box-shadow:0 1px 4px rgba(0,0,0,0.1); }}
            .card .num {{ font-size:1.6em; font-weight:600; }}
            .table {{ width:100%; border-collapse: collapse; }}
            .table th, .table td {{ padding:8px; border-bottom:1px solid #eee; text-align:left; }}
            .updated {{ color:#888; font-size:0.85em; }}
        </style>
    </head>
    <body>
        <h1>🏊‍♂️🚴‍♂️🏃‍♂️ Training Dashboard</h1>
        <p class="updated">Last updated: {dt.datetime.now().strftime("%Y-%m-%d %H:%M")} UTC</p>

        <div class="stats">
            <div class="card"><div class="num">{total_sessions}</div>Sessions (30d)</div>
            <div class="card"><div class="num">{total_hours}h</div>Volume (30d)</div>
            <div class="card"><div class="num">{total_km}km</div>Distance (30d)</div>
            <div class="card"><div class="num">{avg_load}</div>Avg Load</div>
        </div>

        {charts_html}

        <h2>Recent Sessions</h2>
        {recent_html}
    </body>
    </html>
    """

    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(html)


def main():
    df = load_activities()
    plan = load_plan()
    build_html(df, plan)
    print("Dashboard built at docs/index.html")


if __name__ == "__main__":
    main()

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
WELLNESS_FILE = Path("data/wellness.json")
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


def load_wellness():
    if not WELLNESS_FILE.exists():
        return pd.DataFrame()
    store = json.loads(WELLNESS_FILE.read_text())
    rows = []
    for day, vals in store.items():
        row = {"date": day}
        row.update(vals)
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    return df


PLAN_SESSIONS_FILE = Path("data/plan_sessions.json")
MANUAL_LOG_FILE = Path("data/manual_log.json")


def load_plan():
    if PLAN_FILE.exists():
        return json.loads(PLAN_FILE.read_text())
    return {}


def load_plan_sessions():
    if PLAN_SESSIONS_FILE.exists():
        return json.loads(PLAN_SESSIONS_FILE.read_text())
    return []


def load_manual_log():
    if MANUAL_LOG_FILE.exists():
        return json.loads(MANUAL_LOG_FILE.read_text())
    return {}


def sec_per_km_from_speed(speed_m_s):
    """Garmin avg_pace field is avg_speed in m/s. Convert to sec/km."""
    if not speed_m_s or speed_m_s <= 0:
        return None
    return 1000 / speed_m_s


def fmt_pace(sec_per_km):
    if sec_per_km is None:
        return "n/a"
    m = int(sec_per_km // 60)
    s = int(sec_per_km % 60)
    return f"{m}:{s:02d}/km"


def running_target_vs_actual(df, plan_sessions):
    """Matches each actual run to the closest planned run session (same date, +/-1 day)
    that has a pace target, and computes how far off target the actual pace was."""
    run_sessions = [s for s in plan_sessions if s["discipline"] == "running" and s["pace_low_sec_km"]]
    if not run_sessions or df.empty:
        return pd.DataFrame()

    runs = df[df["type"].apply(normalize_type) == "running"].copy()
    if runs.empty:
        return pd.DataFrame()

    rows = []
    for _, act in runs.iterrows():
        act_date = act["start"].date()
        candidates = [s for s in run_sessions if abs((dt.date.fromisoformat(s["date"]) - act_date).days) <= 1]
        if not candidates:
            continue
        target = min(candidates, key=lambda s: abs((dt.date.fromisoformat(s["date"]) - act_date).days))

        actual_pace = sec_per_km_from_speed(act.get("avg_pace"))
        target_mid = (target["pace_low_sec_km"] + target["pace_high_sec_km"]) / 2
        if actual_pace is None:
            continue
        diff_sec = actual_pace - target_mid  # positive = slower than target
        pct_off = round(100 * diff_sec / target_mid, 1)

        rows.append({
            "date": act["start"].strftime("%Y-%m-%d"),
            "session": target["summary"],
            "target_pace": f"{fmt_pace(target['pace_low_sec_km'])}-{fmt_pace(target['pace_high_sec_km'])}",
            "actual_pace": fmt_pace(actual_pace),
            "diff_sec_per_km": round(diff_sec, 0),
            "pct_off_target": pct_off,
        })

    return pd.DataFrame(rows)


def cycling_target_vs_actual(df, plan_sessions):
    """Same as running but for power targets on rides."""
    bike_sessions = [s for s in plan_sessions if s["discipline"] == "cycling" and s["power_low_w"]]
    if not bike_sessions or df.empty:
        return pd.DataFrame()

    rides = df[df["type"].apply(normalize_type) == "cycling"].copy()
    if rides.empty:
        return pd.DataFrame()

    rows = []
    for _, act in rides.iterrows():
        act_date = act["start"].date()
        candidates = [s for s in bike_sessions if abs((dt.date.fromisoformat(s["date"]) - act_date).days) <= 1]
        if not candidates:
            continue
        target = min(candidates, key=lambda s: abs((dt.date.fromisoformat(s["date"]) - act_date).days))

        actual_power = act.get("avg_power")
        if not actual_power:
            continue
        target_mid = (target["power_low_w"] + target["power_high_w"]) / 2
        diff_w = actual_power - target_mid
        pct_off = round(100 * diff_w / target_mid, 1)

        rows.append({
            "date": act["start"].strftime("%Y-%m-%d"),
            "session": target["summary"],
            "target_power": f"{target['power_low_w']}-{target['power_high_w']}W",
            "actual_power": f"{round(actual_power)}W",
            "diff_w": round(diff_w, 0),
            "pct_off_target": pct_off,
        })

    return pd.DataFrame(rows)


def swim_summary_stats(df):
    swims = df[df["type"].apply(normalize_type) == "swimming"].copy()
    if swims.empty:
        return pd.DataFrame(), None
    swims["pace_per_100m_sec"] = swims.apply(
        lambda r: (r["duration_s"] / (r["distance_m"] / 100)) if r["distance_m"] else None, axis=1
    )
    return swims, swims["pace_per_100m_sec"].mean()


def weekly_summary(df):
    df = df.copy()
    df["week"] = df["start"].dt.to_period("W").apply(lambda r: r.start_time)
    df["type"] = df["type"].apply(normalize_type)
    summary = df.groupby(["week", "type"]).agg(
        sessions=("id", "count"),
        duration_min=("duration_min", "sum"),
        distance_km=("distance_km", "sum"),
        load=("training_load", "sum"),
    ).reset_index()
    return summary


GARMIN_TYPE_MAP = {
    "lap_swimming": "swimming",
    "open_water_swimming": "swimming",
    "swimming": "swimming",
    "road_biking": "cycling",
    "cycling": "cycling",
    "indoor_cycling": "cycling",
    "virtual_ride": "cycling",
    "gravel_cycling": "cycling",
    "mountain_biking": "cycling",
    "running": "running",
    "treadmill_running": "running",
    "trail_running": "running",
    "indoor_running": "running",
    "strength_training": "strength_training",
    "fitness_equipment": "strength_training",
}


def normalize_type(garmin_type):
    if not garmin_type:
        return garmin_type
    return GARMIN_TYPE_MAP.get(garmin_type, garmin_type)


def on_target_pct(actual_weekly, plan):
    """Compare actual weekly hours vs planned hours per discipline, if plan.json present."""
    if not plan:
        return None
    rows = []
    for week, group in actual_weekly.groupby("week"):
        week_key = week.strftime("%Y-%m-%d")
        planned = plan.get(week_key, {})
        for _, row in group.iterrows():
            disc = normalize_type(row["type"])
            planned_min = planned.get(disc, {}).get("duration_min")
            if planned_min:
                pct = min(100, round(100 * row["duration_min"] / planned_min))
                rows.append({"week": week, "type": disc, "pct_on_target": pct})
    return pd.DataFrame(rows)


def planned_vs_completed_table(df, plan):
    """Builds a week-by-week table: planned session count vs completed session count, per discipline."""
    if not plan or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["week"] = df["start"].dt.to_period("W").apply(lambda r: r.start_time)
    df["norm_type"] = df["type"].apply(normalize_type)
    actual_counts = df.groupby(["week", "norm_type"]).size().reset_index(name="completed")

    rows = []
    for week_str, disciplines in plan.items():
        week_dt = pd.Timestamp(week_str)
        for disc, vals in disciplines.items():
            planned_min = vals.get("duration_min", 0)
            match = actual_counts[(actual_counts["week"] == week_dt) & (actual_counts["norm_type"] == disc)]
            completed = int(match["completed"].iloc[0]) if not match.empty else 0
            rows.append({
                "week": week_dt,
                "discipline": disc,
                "planned_min": planned_min,
                "completed_sessions": completed,
            })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    cutoff = dt.datetime.now() - dt.timedelta(weeks=8)
    out = out[out["week"] >= cutoff].sort_values(["week", "discipline"])
    return out


def build_html(df, plan, wellness, plan_sessions, manual_log):
    if df.empty:
        OUT_FILE.parent.mkdir(exist_ok=True)
        OUT_FILE.write_text("<h1>No activity data yet</h1>")
        return

    weekly = weekly_summary(df)
    ontarget = on_target_pct(weekly, plan)
    plan_vs_actual = planned_vs_completed_table(df, plan)
    run_compare = running_target_vs_actual(df, plan_sessions)
    bike_compare = cycling_target_vs_actual(df, plan_sessions)
    swims, avg_pace_100m = swim_summary_stats(df)

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

    fig5 = None  # sleep duration trend
    fig6 = None  # body battery trend
    fig7 = None  # combined readiness: load vs sleep vs body battery
    if not wellness.empty:
        if "sleep_duration_min" in wellness.columns and wellness["sleep_duration_min"].notna().any():
            fig5 = go.Figure()
            sw = wellness.dropna(subset=["sleep_duration_min"])
            fig5.add_trace(go.Scatter(x=sw["date"], y=sw["sleep_duration_min"] / 60,
                                       mode="lines+markers", name="Sleep (hrs)"))
            fig5.add_hline(y=7, line_dash="dot", annotation_text="7h target", line_color="gray")
            fig5.update_layout(title="Sleep Duration Trend", yaxis_title="Hours",
                                template="plotly_white")

        if "body_battery_max" in wellness.columns and wellness["body_battery_max"].notna().any():
            fig6 = go.Figure()
            bw = wellness.dropna(subset=["body_battery_max"])
            fig6.add_trace(go.Scatter(x=bw["date"], y=bw["body_battery_max"],
                                       mode="lines+markers", name="Body Battery (charged)"))
            if "body_battery_min" in bw.columns:
                fig6.add_trace(go.Scatter(x=bw["date"], y=bw["body_battery_min"],
                                           mode="lines+markers", name="Body Battery (drained)"))
            fig6.update_layout(title="Body Battery Trend", yaxis_title="Level",
                                template="plotly_white")

        # readiness combo: weekly training load vs avg sleep vs avg body battery drain
        if "sleep_duration_min" in wellness.columns:
            ww = wellness.copy()
            ww["week"] = ww["date"].dt.to_period("W").apply(lambda r: r.start_time)
            sleep_weekly = ww.groupby("week")["sleep_duration_min"].mean().reset_index()
            bb_weekly = ww.groupby("week")["body_battery_min"].mean().reset_index() \
                if "body_battery_min" in ww.columns else None

            fig7 = make_subplots(specs=[[{"secondary_y": True}]])
            fig7.add_trace(go.Bar(x=load_by_week["week"], y=load_by_week["training_load"],
                                   name="Training Load"), secondary_y=False)
            fig7.add_trace(go.Scatter(x=sleep_weekly["week"], y=sleep_weekly["sleep_duration_min"] / 60,
                                       mode="lines+markers", name="Avg Sleep (hrs)"), secondary_y=True)
            fig7.update_layout(title="Weekly Readiness: Training Load vs Avg Sleep",
                                template="plotly_white")
            fig7.update_yaxes(title_text="Training Load", secondary_y=False)
            fig7.update_yaxes(title_text="Avg Sleep (hrs)", secondary_y=True)

    recent = df.tail(5)[["start", "name", "type", "distance_km", "duration_min", "avg_hr", "training_load"]]
    recent_html = recent.to_html(index=False, classes="table", border=0)

    plan_table_html = ""
    if not plan_vs_actual.empty:
        pv = plan_vs_actual.copy()
        pv["week"] = pv["week"].dt.strftime("%Y-%m-%d")
        pv["planned_min"] = pv["planned_min"].round(0).astype(int)
        pv = pv.rename(columns={
            "week": "Week", "discipline": "Discipline",
            "planned_min": "Planned (min)", "completed_sessions": "Completed Sessions"
        })
        plan_table_html = pv.to_html(index=False, classes="table", border=0)

    run_compare_html = ""
    run_avg_pct_off = None
    if not run_compare.empty:
        run_avg_pct_off = round(run_compare["pct_off_target"].mean(), 1)
        rc = run_compare.tail(10).rename(columns={
            "date": "Date", "session": "Planned Session", "target_pace": "Target Pace",
            "actual_pace": "Actual Pace", "diff_sec_per_km": "Diff (sec/km)", "pct_off_target": "% Off Target"
        })
        run_compare_html = rc.to_html(index=False, classes="table", border=0)

    bike_compare_html = ""
    bike_avg_pct_off = None
    if not bike_compare.empty:
        bike_avg_pct_off = round(bike_compare["pct_off_target"].mean(), 1)
        bc = bike_compare.tail(10).rename(columns={
            "date": "Date", "session": "Planned Session", "target_power": "Target Power",
            "actual_power": "Actual Power", "diff_w": "Diff (W)", "pct_off_target": "% Off Target"
        })
        bike_compare_html = bc.to_html(index=False, classes="table", border=0)

    swim_stats_html = ""
    if swims is not None and not swims.empty:
        avg_dist = round(swims["distance_m"].mean())
        avg_pace_per_100 = f"{int(avg_pace_100m // 60)}:{int(avg_pace_100m % 60):02d}/100m" if avg_pace_100m else "n/a"
        swim_stats_html = f"""
        <div class="stats" style="grid-template-columns: repeat(3, 1fr);">
            <div class="card"><div class="num">{len(swims)}</div><div class="label">Total Swims</div></div>
            <div class="card"><div class="num">{avg_dist}m</div><div class="label">Avg Distance</div></div>
            <div class="card"><div class="num">{avg_pace_per_100}</div><div class="label">Avg Pace</div></div>
        </div>
        """

    manual_strength_html = """
    <p class="subtext">
    Strength sessions can't be auto-verified the same way as cardio (Garmin doesn't reliably log gym work).
    To track these manually: edit <code>data/manual_log.json</code> in your repo, add a line like
    <code>"2026-06-22": true</code> (using the Monday date of the week) for each week you completed your strength session,
    then this section will reflect it on the next sync.
    </p>
    """
    clean_log = {k: v for k, v in manual_log.items() if not k.startswith("_")}
    if clean_log:
        rows = "".join(f"<tr><td>{wk}</td><td>{'✅ Completed' if done else '❌ Missed'}</td></tr>"
                        for wk, done in sorted(clean_log.items(), reverse=True)[:8])
        manual_strength_html += f"""
        <table class="table">
            <tr><th>Week</th><th>Strength Session</th></tr>
            {rows}
        </table>
        """

    last_30 = df[df["start"] >= (dt.datetime.now() - dt.timedelta(days=30))]
    total_sessions = len(last_30)
    total_hours = round(last_30["duration_min"].sum() / 60)
    total_km = round(last_30["distance_km"].sum())
    avg_load = round(last_30["training_load"].mean()) if "training_load" in last_30 and last_30["training_load"].notna().any() else "n/a"

    avg_sleep_hrs = "n/a"
    avg_bb = "n/a"
    if not wellness.empty:
        recent_wellness = wellness[wellness["date"] >= (dt.datetime.now() - dt.timedelta(days=30))]
        if "sleep_duration_min" in recent_wellness.columns and recent_wellness["sleep_duration_min"].notna().any():
            avg_sleep_hrs = round(recent_wellness["sleep_duration_min"].mean() / 60, 1)
        if "body_battery_max" in recent_wellness.columns and recent_wellness["body_battery_max"].notna().any():
            avg_bb = round(recent_wellness["body_battery_max"].mean())

    fig8 = None  # swim distance trend
    fig9 = None  # swim pace trend
    if swims is not None and not swims.empty:
        fig8 = go.Figure()
        fig8.add_trace(go.Scatter(x=swims["start"], y=swims["distance_m"], mode="lines+markers",
                                   name="Distance (m)"))
        fig8.update_layout(title="Swim Distance Trend", yaxis_title="meters", template="plotly_white")

        fig9 = go.Figure()
        pace_df = swims.dropna(subset=["pace_per_100m_sec"])
        fig9.add_trace(go.Scatter(x=pace_df["start"], y=pace_df["pace_per_100m_sec"], mode="lines+markers",
                                   name="Pace /100m (sec)"))
        fig9.update_layout(title="Swim Pace Trend (sec per 100m, lower = faster)", template="plotly_white")

    all_figs = [f for f in [fig1, fig2, fig3, fig4, fig5, fig6, fig7, fig8, fig9] if f is not None]

    PALETTE = ["#5B6EF5", "#00C2A8", "#FF7A59", "#FFC75A", "#9B7DFF", "#36C5F0"]
    for f in all_figs:
        f.update_layout(
            height=300,
            margin=dict(l=40, r=20, t=48, b=36),
            font=dict(family="-apple-system, Helvetica, Arial, sans-serif", size=12, color="#2c2c34"),
            title_font=dict(size=14, color="#1a1a22"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                        font=dict(size=10)),
            plot_bgcolor="white",
            paper_bgcolor="white",
            colorway=PALETTE,
            hovermode="x unified",
        )
        f.update_xaxes(showgrid=False, linecolor="#e3e3ea")
        f.update_yaxes(showgrid=True, gridcolor="#f0f0f5", linecolor="#e3e3ea")

    charts_html = "".join([
        f'<div class="chart-cell">{pio.to_html(f, full_html=False, include_plotlyjs=(i == 0), config={"displayModeBar": False, "responsive": True})}</div>'
        for i, f in enumerate(all_figs)
    ])

    html = f"""
    <html>
    <head>
        <title>Training Dashboard</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                max-width: 1100px; margin: 0 auto; padding: 32px 24px 60px;
                background: #f6f7fb; color: #1a1a22;
            }}
            .topbar {{
                display: flex; justify-content: space-between; align-items: center;
                margin-bottom: 24px; flex-wrap: wrap; gap: 12px;
            }}
            h1 {{ font-size: 1.6em; margin: 0; font-weight: 700; letter-spacing: -0.3px; }}
            .updated {{ color: #8a8a96; font-size: 0.82em; margin: 2px 0 0; }}
            .btn {{
                background: #5B6EF5; color: white; border: none; padding: 10px 18px;
                border-radius: 8px; font-size: 0.85em; font-weight: 600; cursor: pointer;
                box-shadow: 0 2px 6px rgba(91,110,245,0.35);
            }}
            .btn:hover {{ background: #4757d8; }}
            h2 {{ font-size: 1.15em; margin: 36px 0 6px; font-weight: 700; color: #1a1a22; }}
            .subtext {{ color: #8a8a96; font-size: 0.82em; margin: 0 0 14px; line-height: 1.4; }}
            .stats {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; margin: 18px 0 8px; }}
            .card {{
                background: white; border-radius: 14px; padding: 16px 14px;
                box-shadow: 0 1px 3px rgba(20,20,40,0.06); text-align: center;
            }}
            .card .num {{ font-size: 1.5em; font-weight: 700; color: #1a1a22; }}
            .card .label {{ font-size: 0.72em; color: #8a8a96; margin-top: 2px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px; }}
            .chart-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }}
            .chart-cell {{
                background: white; border-radius: 14px; padding: 8px 12px;
                box-shadow: 0 1px 3px rgba(20,20,40,0.06); overflow: hidden;
            }}
            .table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(20,20,40,0.06); }}
            .table th {{ background: #f0f1f8; font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.3px; color: #6b6b78; padding: 10px 12px; text-align: left; }}
            .table td {{ padding: 10px 12px; border-bottom: 1px solid #f0f0f5; font-size: 0.88em; }}
            .table tr:last-child td {{ border-bottom: none; }}
            @media (max-width: 700px) {{
                .chart-grid {{ grid-template-columns: 1fr; }}
                .stats {{ grid-template-columns: repeat(2, 1fr); }}
            }}
            @media print {{
                .btn {{ display: none; }}
                body {{ background: white; }}
                .chart-grid {{ grid-template-columns: 1fr 1fr; }}
                .card, .chart-cell, .table {{ box-shadow: none; border: 1px solid #eee; }}
            }}
        </style>
    </head>
    <body>
        <div class="topbar">
            <div>
                <h1>🏊‍♂️🚴‍♂️🏃‍♂️ Training Dashboard</h1>
                <p class="updated">Last updated: {dt.datetime.now().strftime("%Y-%m-%d %H:%M")} UTC</p>
            </div>
            <button class="btn" onclick="window.print()">Export as PDF</button>
        </div>

        <div class="stats">
            <div class="card"><div class="num">{total_sessions}</div><div class="label">Sessions (30d)</div></div>
            <div class="card"><div class="num">{total_hours}h</div><div class="label">Volume (30d)</div></div>
            <div class="card"><div class="num">{total_km}km</div><div class="label">Distance (30d)</div></div>
            <div class="card"><div class="num">{avg_load}</div><div class="label">Avg Load</div></div>
            <div class="card"><div class="num">{avg_sleep_hrs}h</div><div class="label">Avg Sleep (30d)</div></div>
            <div class="card"><div class="num">{avg_bb}</div><div class="label">Avg Body Battery</div></div>
        </div>

        <div class="chart-grid">
        {charts_html}
        </div>

        <h2>Planned vs Completed (last 8 weeks)</h2>
        <p class="subtext">Planned minutes are aggregated per discipline per week from your training plan. Completed sessions counts how many actual Garmin activities of that type were logged that week — this compares session count against planned volume, not a 1:1 match.</p>
        {plan_table_html if plan_table_html else "<p class='subtext'>No plan data matched to recent weeks yet.</p>"}

        <h2>Running: Target Pace vs Actual</h2>
        <p class="subtext">Matches each run to the closest planned session (±1 day) with a pace target. Positive % means slower than target.</p>
        {f'<div class="stats" style="grid-template-columns: 1fr;"><div class="card"><div class="num">{run_avg_pct_off}%</div><div class="label">Avg Off Target — Running</div></div></div>' if run_avg_pct_off is not None else ""}
        {run_compare_html if run_compare_html else "<p class='subtext'>No matched running sessions with pace targets yet.</p>"}

        <h2>Cycling: Target Power vs Actual</h2>
        <p class="subtext">Same logic as running, comparing actual average power to the planned power target range.</p>
        {f'<div class="stats" style="grid-template-columns: 1fr;"><div class="card"><div class="num">{bike_avg_pct_off}%</div><div class="label">Avg Off Target — Cycling</div></div></div>' if bike_avg_pct_off is not None else ""}
        {bike_compare_html if bike_compare_html else "<p class='subtext'>No matched cycling sessions with power targets yet.</p>"}

        <h2>Swimming Overview</h2>
        {swim_stats_html if swim_stats_html else "<p class='subtext'>No swim data yet.</p>"}

        <h2>Strength Sessions (Manual Tracking)</h2>
        {manual_strength_html}

        <h2>Recent Sessions</h2>
        {recent_html}
    </body>
    </html>
    """

    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(html)


def build_pdf(df, plan, wellness, plan_sessions, manual_log):
    """Builds a condensed, print-friendly PDF version of the dashboard.
    Charts are rendered as static PNGs (via kaleido) since PDF can't run the
    interactive JS that the HTML dashboard uses."""
    import base64
    from io import BytesIO

    try:
        from weasyprint import HTML
    except ImportError:
        print("weasyprint not installed, skipping PDF generation")
        return

    OUT_PDF = Path("docs/dashboard.pdf")
    OUT_PDF.parent.mkdir(exist_ok=True)

    if df.empty:
        HTML(string="<h1>No activity data yet</h1>").write_pdf(str(OUT_PDF))
        return

    weekly = weekly_summary(df)

    last_30 = df[df["start"] >= (dt.datetime.now() - dt.timedelta(days=30))]
    total_sessions = len(last_30)
    total_hours = round(last_30["duration_min"].sum() / 60)
    total_km = round(last_30["distance_km"].sum())
    avg_load = round(last_30["training_load"].mean()) if "training_load" in last_30 and last_30["training_load"].notna().any() else "n/a"

    avg_sleep_hrs = "n/a"
    avg_bb = "n/a"
    if not wellness.empty:
        recent_wellness = wellness[wellness["date"] >= (dt.datetime.now() - dt.timedelta(days=30))]
        if "sleep_duration_min" in recent_wellness.columns and recent_wellness["sleep_duration_min"].notna().any():
            avg_sleep_hrs = round(recent_wellness["sleep_duration_min"].mean() / 60, 1)
        if "body_battery_max" in recent_wellness.columns and recent_wellness["body_battery_max"].notna().any():
            avg_bb = round(recent_wellness["body_battery_max"].mean())

    PALETTE = ["#5B6EF5", "#00C2A8", "#FF7A59", "#FFC75A", "#9B7DFF", "#36C5F0"]

    def fig_to_data_uri(fig):
        fig.update_layout(
            height=320, width=480,
            margin=dict(l=40, r=20, t=44, b=36),
            font=dict(family="Helvetica, Arial, sans-serif", size=11, color="#2c2c34"),
            title_font=dict(size=13, color="#1a1a22"),
            plot_bgcolor="white", paper_bgcolor="white",
            colorway=PALETTE, showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=9)),
        )
        try:
            img_bytes = fig.to_image(format="png", scale=2)
        except Exception as e:
            print(f"Chart image render failed: {e}")
            return None
        b64 = base64.b64encode(img_bytes).decode()
        return f"data:image/png;base64,{b64}"

    # Weekly volume chart
    fig1 = go.Figure()
    for atype in weekly["type"].unique():
        sub = weekly[weekly["type"] == atype]
        fig1.add_trace(go.Bar(x=sub["week"], y=sub["duration_min"], name=atype))
    fig1.update_layout(barmode="stack", title="Weekly Volume (min)")
    img1 = fig_to_data_uri(fig1)

    # Training load
    load_weekly = df.copy()
    load_weekly["week"] = load_weekly["start"].dt.to_period("W").apply(lambda r: r.start_time)
    load_by_week = load_weekly.groupby("week")["training_load"].sum().reset_index()
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=load_by_week["week"], y=load_by_week["training_load"], mode="lines+markers"))
    fig2.update_layout(title="Training Load Trend")
    img2 = fig_to_data_uri(fig2)

    # Sleep
    img3 = None
    if not wellness.empty and "sleep_duration_min" in wellness.columns and wellness["sleep_duration_min"].notna().any():
        sw = wellness.dropna(subset=["sleep_duration_min"])
        fig3 = go.Figure()
        fig3.add_trace(go.Scatter(x=sw["date"], y=sw["sleep_duration_min"] / 60, mode="lines+markers"))
        fig3.update_layout(title="Sleep Duration (hrs)")
        img3 = fig_to_data_uri(fig3)

    # Body battery
    img4 = None
    if not wellness.empty and "body_battery_max" in wellness.columns and wellness["body_battery_max"].notna().any():
        bw = wellness.dropna(subset=["body_battery_max"])
        fig4 = go.Figure()
        fig4.add_trace(go.Scatter(x=bw["date"], y=bw["body_battery_max"], mode="lines+markers"))
        fig4.update_layout(title="Body Battery")
        img4 = fig_to_data_uri(fig4)

    charts_html = "".join(
        f'<div class="chart-cell"><img src="{img}" style="width:100%;"/></div>'
        for img in [img1, img2, img3, img4] if img
    )

    recent = df.tail(8)[["start", "name", "type", "distance_km", "duration_min", "avg_hr"]].copy()
    recent["start"] = recent["start"].dt.strftime("%b %d")
    recent["distance_km"] = recent["distance_km"].round(1)
    recent["duration_min"] = recent["duration_min"].round(0).astype(int)
    recent_html = recent.to_html(index=False, classes="table", border=0)

    clean_log = {k: v for k, v in manual_log.items() if not k.startswith("_")}
    strength_html = ""
    if clean_log:
        rows = "".join(f"<tr><td>{wk}</td><td>{'Completed' if done else 'Missed'}</td></tr>"
                        for wk, done in sorted(clean_log.items(), reverse=True)[:6])
        strength_html = f'<table class="table"><tr><th>Week</th><th>Strength</th></tr>{rows}</table>'

    html = f"""
    <html>
    <head>
        <style>
            @page {{ size: A4; margin: 16mm; }}
            body {{ font-family: Helvetica, Arial, sans-serif; color: #1a1a22; }}
            h1 {{ font-size: 20pt; margin-bottom: 2pt; }}
            h2 {{ font-size: 13pt; margin-top: 18pt; margin-bottom: 4pt; border-bottom: 1px solid #eee; padding-bottom: 3pt; }}
            .updated {{ color: #888; font-size: 9pt; margin-top: 0; }}
            .stats {{ display: flex; gap: 10pt; margin: 10pt 0; flex-wrap: wrap; }}
            .card {{ border: 1px solid #eee; border-radius: 6pt; padding: 8pt 12pt; text-align: center; flex: 1; min-width: 70pt; }}
            .card .num {{ font-size: 14pt; font-weight: 700; }}
            .card .label {{ font-size: 7pt; color: #888; text-transform: uppercase; }}
            .chart-grid {{ display: flex; flex-wrap: wrap; gap: 8pt; }}
            .chart-cell {{ width: 48%; border: 1px solid #eee; border-radius: 6pt; padding: 4pt; }}
            .table {{ width: 100%; border-collapse: collapse; font-size: 8.5pt; }}
            .table th {{ background: #f5f5fa; padding: 5pt; text-align: left; }}
            .table td {{ padding: 5pt; border-bottom: 1px solid #f0f0f5; }}
        </style>
    </head>
    <body>
        <h1>Training Dashboard</h1>
        <p class="updated">Generated {dt.datetime.now().strftime("%Y-%m-%d %H:%M")} UTC</p>

        <div class="stats">
            <div class="card"><div class="num">{total_sessions}</div><div class="label">Sessions (30d)</div></div>
            <div class="card"><div class="num">{total_hours}h</div><div class="label">Volume (30d)</div></div>
            <div class="card"><div class="num">{total_km}km</div><div class="label">Distance (30d)</div></div>
            <div class="card"><div class="num">{avg_load}</div><div class="label">Avg Load</div></div>
            <div class="card"><div class="num">{avg_sleep_hrs}h</div><div class="label">Avg Sleep</div></div>
            <div class="card"><div class="num">{avg_bb}</div><div class="label">Body Battery</div></div>
        </div>

        <h2>Trends</h2>
        <div class="chart-grid">{charts_html}</div>

        <h2>Recent Sessions</h2>
        {recent_html}

        <h2>Strength Sessions</h2>
        {strength_html if strength_html else "<p>No manual entries yet.</p>"}

        <p style="color:#aaa; font-size:7pt; margin-top:14pt;">
        Full interactive dashboard with all charts and plan-vs-actual comparisons available at docs/index.html in your repo.
        </p>
    </body>
    </html>
    """

    HTML(string=html).write_pdf(str(OUT_PDF))
    print(f"PDF built at {OUT_PDF}")


def main():
    df = load_activities()
    plan = load_plan()
    wellness = load_wellness()
    plan_sessions = load_plan_sessions()
    manual_log = load_manual_log()
    build_html(df, plan, wellness, plan_sessions, manual_log)
    print("Dashboard built at docs/index.html")
    build_pdf(df, plan, wellness, plan_sessions, manual_log)


if __name__ == "__main__":
    main()

"""
build_dashboard.py
Builds docs/index.html (interactive) and docs/dashboard.pdf (email-friendly)
from Garmin activity data, wellness data, training plan, and manual logs.
"""

import json
import datetime as dt
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

# ── File paths ─────────────────────────────────────────────────────────────
DATA_FILE       = Path("data/activities.json")
WELLNESS_FILE   = Path("data/wellness.json")
PLAN_FILE       = Path("data/plan.json")
PLAN_SESSIONS_FILE = Path("data/plan_sessions.json")
MANUAL_LOG_FILE = Path("data/manual_log.json")
OUT_HTML        = Path("docs/index.html")
OUT_PDF         = Path("docs/dashboard.pdf")

# ── Race targets ────────────────────────────────────────────────────────────
RACES = [
    {
        "name": "Marathon",
        "emoji": "🏃",
        "date": dt.date(2027, 2, 7),
        "disciplines": ["running"],
        "targets": {"run_pace_sec_km": 5*60+45},
        "note": "Target: sub-4h (~5:45/km)",
    },
    {
        "name": "Ironman Italy Cervia",
        "emoji": "🏊🚴🏃",
        "date": dt.date(2027, 6, 20),
        "disciplines": ["swimming", "cycling", "running"],
        "targets": {
            "swim_pace_100m_sec": 110,    # 1:50/100m
            "bike_power_w": 190,           # ~82% FTP
            "run_pace_sec_km": 6*60+30,   # 6:30/km IM marathon
        },
        "note": "Full Ironman",
    },
]

PALETTE = {
    "running":           "#5B6EF5",
    "cycling":           "#00C2A8",
    "swimming":          "#36C5F0",
    "strength_training": "#FF7A59",
    "other":             "#9B7DFF",
    "load":              "#FFC75A",
    "sleep":             "#9B7DFF",
    "battery":           "#00C2A8",
}

GARMIN_TYPE_MAP = {
    "lap_swimming": "swimming", "open_water_swimming": "swimming", "swimming": "swimming",
    "road_biking": "cycling", "cycling": "cycling", "indoor_cycling": "cycling",
    "virtual_ride": "cycling", "gravel_cycling": "cycling", "mountain_biking": "cycling",
    "running": "running", "treadmill_running": "running", "trail_running": "running",
    "indoor_running": "running", "strength_training": "strength_training",
    "fitness_equipment": "strength_training",
}


# ── Loaders ─────────────────────────────────────────────────────────────────
def load_activities():
    store = json.loads(DATA_FILE.read_text())
    df = pd.DataFrame(store.values())
    if df.empty:
        return df
    df["start"] = pd.to_datetime(df["start"])
    df["duration_min"] = df["duration_s"] / 60
    df["distance_km"] = df["distance_m"] / 1000
    df["type"] = df["type"].apply(lambda t: GARMIN_TYPE_MAP.get(t, t) if t else t)
    return df.sort_values("start")


def load_wellness():
    if not WELLNESS_FILE.exists():
        return pd.DataFrame()
    store = json.loads(WELLNESS_FILE.read_text())
    rows = [{"date": d, **v} for d, v in store.items()]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")


def load_plan():
    return json.loads(PLAN_FILE.read_text()) if PLAN_FILE.exists() else {}


def load_plan_sessions():
    return json.loads(PLAN_SESSIONS_FILE.read_text()) if PLAN_SESSIONS_FILE.exists() else []


def load_manual_log():
    return json.loads(MANUAL_LOG_FILE.read_text()) if MANUAL_LOG_FILE.exists() else {}


# ── Helpers ──────────────────────────────────────────────────────────────────
def fmt_pace(sec_per_km):
    if not sec_per_km or pd.isna(sec_per_km):
        return "n/a"
    return f"{int(sec_per_km)//60}:{int(sec_per_km)%60:02d}/km"


def speed_to_pace(speed_m_s):
    """Garmin stores avg_pace as m/s. Convert to sec/km."""
    if not speed_m_s or speed_m_s == 0:
        return None
    return 1000 / speed_m_s


def days_until(race_date):
    return (race_date - dt.date.today()).days


# ── Data computations ────────────────────────────────────────────────────────
def weekly_by_discipline(df):
    df = df.copy()
    df["week"] = df["start"].dt.to_period("W").apply(lambda r: r.start_time)
    return df.groupby(["week", "type"]).agg(
        sessions=("id", "count"),
        duration_min=("duration_min", "sum"),
        distance_km=("distance_km", "sum"),
        load=("training_load", "sum"),
        avg_hr=("avg_hr", "mean"),
        avg_pace=("avg_pace", "mean"),
        avg_power=("avg_power", "mean"),
    ).reset_index()


def discipline_trends(df):
    """Per-discipline weekly averages for pace (run/swim) and power/speed (bike)."""
    df = df.copy()
    df["week"] = df["start"].dt.to_period("W").apply(lambda r: r.start_time)
    trends = {}
    for disc in ["running", "cycling", "swimming"]:
        sub = df[df["type"] == disc].copy()
        if sub.empty:
            continue
        wk = sub.groupby("week").agg(
            avg_pace=("avg_pace", "mean"),
            avg_power=("avg_power", "mean"),
            avg_hr=("avg_hr", "mean"),
            distance_km=("distance_km", "mean"),
        ).reset_index()

        # Convert m/s → sec/km, filtering out zero/null values
        wk["pace_sec_km"] = wk["avg_pace"].apply(
            lambda v: speed_to_pace(v) if v and v > 0.1 else None
        )

        if disc == "swimming":
            # Pool swim pace: sec/km ÷ 10 = sec/100m
            wk["pace_sec_100m"] = wk["pace_sec_km"].apply(
                lambda x: round(x / 10, 1) if x and x > 0 else None
            )

        if disc == "cycling":
            # Speed fallback for when no power meter is fitted
            wk["speed_kmh"] = wk["avg_pace"].apply(
                lambda v: round(v * 3.6, 1) if v and v > 0.1 else None
            )

        trends[disc] = wk
    return trends


def on_target_pct(weekly, plan):
    if not plan:
        return pd.DataFrame()
    rows = []
    for week, group in weekly.groupby("week"):
        wk_key = week.strftime("%Y-%m-%d")
        planned = plan.get(wk_key, {})
        for _, row in group.iterrows():
            pm = planned.get(row["type"], {}).get("duration_min")
            if pm:
                pct = min(100, round(100 * row["duration_min"] / pm))
                rows.append({"week": week, "type": row["type"], "pct": pct})
    return pd.DataFrame(rows)


def session_compliance(df, plan_sessions, weeks_back=8):
    """Match planned sessions to actual Garmin sessions by date ±1 day."""
    if not plan_sessions or df.empty:
        return {}
    cutoff = dt.date.today() - dt.timedelta(weeks=weeks_back)
    recent_ps = [ps for ps in plan_sessions
                 if dt.date.fromisoformat(ps["date"]) >= cutoff]

    result = {}
    for ps in recent_ps:
        ps_date = dt.date.fromisoformat(ps["date"])
        disc = ps["discipline"]
        wk = (ps_date - dt.timedelta(days=ps_date.weekday())).isoformat()

        # find actual activity on same day ±1
        mask = (
            (df["type"] == disc) &
            (df["start"].dt.date >= ps_date - dt.timedelta(days=1)) &
            (df["start"].dt.date <= ps_date + dt.timedelta(days=1))
        )
        candidates = df[mask]

        if candidates.empty:
            entry = {
                "date": ps_date.isoformat(), "discipline": disc,
                "session": ps.get("summary", ""),
                "planned": _target_str(ps), "actual": "—",
                "status": "⬜ Missed",
            }
        else:
            act = candidates.sort_values("start").iloc[0]
            entry = _evaluate(ps, act, disc)

        result.setdefault(wk, []).append(entry)

    return result


def _target_str(ps):
    parts = []
    if ps.get("planned_duration_min"):
        parts.append(f"{ps['planned_duration_min']}min")
    if ps.get("target_distance_km"):
        parts.append(f"{ps['target_distance_km']}km")
    if ps.get("pace_low_sec_km"):
        lo, hi = fmt_pace(ps["pace_low_sec_km"]), fmt_pace(ps["pace_high_sec_km"])
        parts.append(f"{lo}–{hi}/km" if lo != hi else f"{lo}/km")
    if ps.get("power_low_w"):
        lo, hi = ps["power_low_w"], ps["power_high_w"]
        parts.append(f"{lo}–{hi}W" if lo != hi else f"{lo}W")
    return " · ".join(parts) or ps.get("summary", "")


def _evaluate(ps, act, disc):
    actual_dur = round(act["duration_min"])
    actual_dist_km = round(act["distance_km"], 1) if act.get("distance_km") else None
    actual_pace = speed_to_pace(act.get("avg_pace")) if disc in ("running", "swimming") else None
    actual_power = act.get("avg_power") if disc == "cycling" else None
    actual_speed_kmh = round(act.get("avg_pace", 0) * 3.6, 1) if disc == "cycling" and act.get("avg_pace") else None
    planned_dur = ps.get("planned_duration_min")
    planned_dist = ps.get("target_distance_km")

    # Duration adherence
    dur_ok = None
    if planned_dur:
        ratio = actual_dur / planned_dur
        dur_ok = "on" if ratio >= 0.90 else "slight" if ratio >= 0.75 else "off"

    # Distance adherence (if target specified)
    dist_ok = None
    if planned_dist and actual_dist_km:
        ratio = actual_dist_km / planned_dist
        dist_ok = "on" if ratio >= 0.90 else "slight" if ratio >= 0.75 else "off"

    # Pace adherence (running)
    pace_ok = None
    if disc == "running" and ps.get("pace_low_sec_km") and actual_pace:
        lo, hi = ps["pace_low_sec_km"], ps["pace_high_sec_km"]
        if actual_pace < lo * 0.95 or lo <= actual_pace <= hi:
            pace_ok = "on"
        elif actual_pace <= hi * 1.08:
            pace_ok = "slight"
        else:
            pace_ok = "off"

    # Power adherence (cycling)
    power_ok = None
    if disc == "cycling" and ps.get("power_low_w") and actual_power:
        lo, hi = ps["power_low_w"], ps["power_high_w"]
        if actual_power > hi * 1.05 or lo <= actual_power <= hi:
            power_ok = "on"
        elif actual_power >= lo * 0.92:
            power_ok = "slight"
        else:
            power_ok = "off"

    RANK = {"off": 2, "slight": 1, "on": 0, None: -1}
    worst = max(
        [dur_ok, dist_ok, pace_ok or power_ok],
        key=lambda s: RANK.get(s, -1)
    )
    status = (
        "❌ Off Target" if worst == "off" else
        "⚠️ Slightly Off" if worst == "slight" else
        "✅ On Target"
    )

    # Build actual string with diffs vs plan
    parts = []

    # Duration
    if planned_dur:
        diff = actual_dur - planned_dur
        parts.append(f"{actual_dur}min ({'+' if diff>0 else ''}{diff}min vs plan)")
    else:
        parts.append(f"{actual_dur}min")

    # Distance
    if actual_dist_km:
        if planned_dist:
            diff = round(actual_dist_km - planned_dist, 1)
            parts.append(f"{actual_dist_km}km ({'+' if diff>0 else ''}{diff}km vs plan)")
        else:
            parts.append(f"{actual_dist_km}km")

    # Pace (running) or Speed+Power (cycling)
    if disc == "running" and actual_pace:
        pace_str = fmt_pace(actual_pace)
        if ps.get("pace_low_sec_km"):
            mid = (ps["pace_low_sec_km"] + ps["pace_high_sec_km"]) / 2
            d = round(actual_pace - mid)
            pace_str += f" ({'+' if d>0 else ''}{d}s vs target)"
        parts.append(pace_str)

    if disc == "cycling":
        if actual_speed_kmh:
            parts.append(f"{actual_speed_kmh} km/h")
        if actual_power:
            pwr = f"{round(actual_power)}W"
            if ps.get("power_low_w"):
                mid = (ps["power_low_w"] + ps["power_high_w"]) / 2
                d = round(actual_power - mid)
                pwr += f" ({'+' if d>0 else ''}{d}W vs target)"
            parts.append(pwr)

    if disc == "swimming" and actual_pace:
        p100 = actual_pace / 10
        parts.append(f"{int(p100)//60}:{int(p100)%60:02d}/100m")

    return {
        "date": act["start"].date().isoformat(),
        "discipline": disc,
        "session": ps.get("summary", ""),
        "planned": _target_str(ps),
        "actual": " · ".join(parts),
        "status": status,
    }


# ── Chart builders ───────────────────────────────────────────────────────────
def STYLE():
    return dict(
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(family="-apple-system,Helvetica,Arial,sans-serif", size=12, color="#2c2c34"),
        title_font=dict(size=14, color="#1a1a22"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=10)),
        hovermode="x unified", height=300,
        margin=dict(l=44, r=28, t=48, b=36),
    )


def chart_volume(weekly):
    fig = go.Figure()
    added = False
    for disc in ["running", "cycling", "swimming", "strength_training"]:
        sub = weekly[weekly["type"] == disc]
        if sub.empty:
            continue
        fig.add_trace(go.Bar(x=sub["week"], y=sub["duration_min"].round(),
                             name=disc.replace("_", " ").title(),
                             marker_color=PALETTE.get(disc, "#ccc")))
        added = True
    if not added:
        return None
    fig.update_layout(barmode="stack", title="Weekly Volume (min)", **STYLE())
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0f0f5")
    return fig


def chart_load_and_hr(weekly, df):
    """Training load bars + avg HR per discipline as lines — dual axis."""
    df = df.copy()
    df["week"] = df["start"].dt.to_period("W").apply(lambda r: r.start_time)
    load_wk = df.groupby("week")["training_load"].sum().reset_index()
    if load_wk.empty or load_wk["training_load"].isna().all():
        return None

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=load_wk["week"], y=load_wk["training_load"].round(),
                          name="Training Load", marker_color=PALETTE["load"], opacity=0.7),
                  secondary_y=False)
    hr_added = False
    for disc in ["running", "cycling", "swimming"]:
        sub = df[df["type"] == disc].groupby("week")["avg_hr"].mean().reset_index()
        if sub.empty or sub["avg_hr"].isna().all():
            continue
        fig.add_trace(go.Scatter(x=sub["week"], y=sub["avg_hr"].round(),
                                  mode="lines+markers", name=f"HR {disc}",
                                  marker_color=PALETTE.get(disc)),
                      secondary_y=True)
        hr_added = True
    fig.update_layout(title="Training Load & Avg HR by Discipline", **STYLE())
    fig.update_yaxes(title_text="Load", secondary_y=False, showgrid=True, gridcolor="#f0f0f5")
    if hr_added:
        fig.update_yaxes(title_text="Avg HR (bpm)", secondary_y=True, showgrid=False)
    fig.update_xaxes(showgrid=False)
    return fig


def chart_pace_trends(trends):
    """Run pace + swim pace on left axis (as decimal min, e.g. 6:30 = 6.5),
    bike power OR speed on right axis. Avoids custom tickvals that can blank the chart."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    added = False
    right_label = "Power (W)"

    def sec_to_decmin(sec):
        """Convert sec/km to decimal minutes for clean axis (6:30 → 6.5)."""
        return round(sec / 60, 2) if sec else None

    def decmin_label(val):
        """Decimal minutes → M:SS string for hover."""
        mins = int(val)
        secs = round((val - mins) * 60)
        return f"{mins}:{secs:02d}"

    if "running" in trends:
        rd = trends["running"].dropna(subset=["pace_sec_km"])
        if not rd.empty:
            y = rd["pace_sec_km"].apply(sec_to_decmin)
            labels = y.apply(lambda v: f"{decmin_label(v)}/km" if v else "n/a")
            fig.add_trace(go.Scatter(
                x=rd["week"], y=y,
                mode="lines+markers", name="Run Pace",
                marker_color=PALETTE["running"],
                customdata=labels,
                hovertemplate="%{x|%b %d}<br>Run: %{customdata}<extra></extra>",
            ), secondary_y=False)
            added = True

    if "swimming" in trends:
        sd = trends["swimming"].dropna(subset=["pace_sec_100m"])
        if not sd.empty:
            y = sd["pace_sec_100m"].apply(sec_to_decmin)
            labels = y.apply(lambda v: f"{decmin_label(v)}/100m" if v else "n/a")
            fig.add_trace(go.Scatter(
                x=sd["week"], y=y,
                mode="lines+markers", name="Swim Pace (/100m)",
                line=dict(dash="dot"), marker_color=PALETTE["swimming"],
                customdata=labels,
                hovertemplate="%{x|%b %d}<br>Swim: %{customdata}<extra></extra>",
            ), secondary_y=False)
            added = True

    if "cycling" in trends:
        cd_power = trends["cycling"].dropna(subset=["avg_power"])
        cd_speed = trends["cycling"].dropna(subset=["speed_kmh"])
        if not cd_power.empty:
            fig.add_trace(go.Scatter(
                x=cd_power["week"], y=cd_power["avg_power"].round(),
                mode="lines+markers", name="Bike Power (W)",
                marker_color=PALETTE["cycling"],
                hovertemplate="%{x|%b %d}<br>Power: %{y}W<extra></extra>",
            ), secondary_y=True)
            right_label = "Power (W)"
            added = True
        elif not cd_speed.empty:
            fig.add_trace(go.Scatter(
                x=cd_speed["week"], y=cd_speed["speed_kmh"],
                mode="lines+markers", name="Bike Speed (km/h)",
                marker_color=PALETTE["cycling"],
                hovertemplate="%{x|%b %d}<br>Speed: %{y} km/h<extra></extra>",
            ), secondary_y=True)
            right_label = "Speed (km/h)"
            added = True

    # Race target lines
    for race in RACES:
        race_dt = pd.Timestamp(race["date"])
        t = race["targets"]
        if "run_pace_sec_km" in t:
            fig.add_vline(x=race_dt, line_dash="dash", line_color="#FF7A59", opacity=0.4)
            target_decmin = sec_to_decmin(t["run_pace_sec_km"])
            fig.add_annotation(x=race_dt, y=target_decmin, yref="y",
                                text=f"{race['emoji']} {decmin_label(target_decmin)}/km",
                                showarrow=False, font=dict(size=9, color="#FF7A59"),
                                bgcolor="white", bordercolor="#FF7A59", borderwidth=1)
        if "bike_power_w" in t:
            fig.add_annotation(x=race_dt, y=t["bike_power_w"], yref="y2",
                                text=f"{race['emoji']} {t['bike_power_w']}W",
                                showarrow=False, font=dict(size=9, color="#FF7A59"),
                                bgcolor="white", bordercolor="#FF7A59", borderwidth=1)

    if not added:
        return None

    fig.update_layout(title="Pace & Power Trends", **STYLE())
    fig.update_yaxes(
        title_text="Pace (min/km or min/100m — lower is faster)",
        secondary_y=False, showgrid=True, gridcolor="#f0f0f5",
        autorange="reversed",
        tickformat=".2f",
    )
    fig.update_yaxes(title_text=right_label, secondary_y=True, showgrid=False)
    fig.update_xaxes(showgrid=False)
    return fig


def chart_distance_trends(trends):
    """Avg session distance per week, per discipline — adjusted axes."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    added = False
    for disc in ["running", "swimming"]:
        if disc not in trends:
            continue
        d = trends[disc].dropna(subset=["distance_km"])
        if d.empty:
            continue
        col = PALETTE.get(disc)
        y = d["distance_km"] * (1000 if disc == "swimming" else 1)
        label = f"Swim (m)" if disc == "swimming" else "Run (km)"
        fig.add_trace(go.Scatter(x=d["week"], y=y.round(1), mode="lines+markers",
                                  name=label, marker_color=col), secondary_y=False)
        added = True
    if "cycling" in trends:
        d = trends["cycling"].dropna(subset=["distance_km"])
        if not d.empty:
            fig.add_trace(go.Scatter(x=d["week"], y=d["distance_km"].round(1),
                                      mode="lines+markers", name="Bike (km)",
                                      marker_color=PALETTE["cycling"]), secondary_y=True)
            added = True
    if not added:
        return None
    fig.update_layout(title="Avg Session Distance per Week", **STYLE())
    fig.update_yaxes(title_text="Run (km) / Swim (m)", secondary_y=False,
                     showgrid=True, gridcolor="#f0f0f5")
    fig.update_yaxes(title_text="Bike (km)", secondary_y=True, showgrid=False)
    fig.update_xaxes(showgrid=False)
    return fig


def chart_on_target(ontarget):
    if ontarget.empty:
        return None
    fig = go.Figure()
    for disc in ontarget["type"].unique():
        sub = ontarget[ontarget["type"] == disc]
        fig.add_trace(go.Scatter(x=sub["week"], y=sub["pct"],
                                  mode="lines+markers",
                                  name=disc.replace("_", " ").title(),
                                  marker_color=PALETTE.get(disc, "#ccc")))
    fig.add_hline(y=80, line_dash="dot", line_color="#00C2A8", annotation_text="80% target")
    fig.update_layout(title="On-Target % vs Plan", yaxis_range=[0, 110], **STYLE())
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0f0f5")
    return fig


def chart_sleep(wellness):
    if wellness.empty or "sleep_duration_min" not in wellness.columns:
        return None
    sw = wellness.dropna(subset=["sleep_duration_min"])
    if sw.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=sw["date"], y=(sw["sleep_duration_min"]/60).round(1),
                              mode="lines+markers", name="Sleep (hrs)",
                              marker_color=PALETTE["sleep"]))
    fig.add_hline(y=7, line_dash="dot", line_color="#888", annotation_text="7h target")
    fig.update_layout(title="Sleep Duration", yaxis_title="Hours", **STYLE())
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0f0f5")
    return fig


def chart_body_battery(wellness):
    if wellness.empty or "body_battery_max" not in wellness.columns:
        return None
    bw = wellness.dropna(subset=["body_battery_max"])
    if bw.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=bw["date"], y=bw["body_battery_max"].round(),
                              mode="lines+markers", name="Charged", marker_color=PALETTE["battery"]))
    if "body_battery_min" in bw.columns:
        fig.add_trace(go.Scatter(x=bw["date"], y=bw["body_battery_min"].round(),
                                  mode="lines+markers", name="Drained",
                                  line=dict(dash="dot"), marker_color="#FFC75A"))
    fig.update_layout(title="Body Battery", **STYLE())
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0f0f5")
    return fig


# ── Race countdown cards HTML ─────────────────────────────────────────────────
def race_cards_html():
    cards = ""
    for r in RACES:
        days = days_until(r["date"])
        t = r["targets"]
        targets_str = []
        if "run_pace_sec_km" in t:
            p = t["run_pace_sec_km"]
            targets_str.append(f"Run: {p//60}:{p%60:02d}/km")
        if "bike_power_w" in t:
            targets_str.append(f"Bike: {t['bike_power_w']}W")
        if "swim_pace_100m_sec" in t:
            p = t["swim_pace_100m_sec"]
            targets_str.append(f"Swim: {p//60}:{p%60:02d}/100m")
        targets_line = " · ".join(targets_str)
        color = "#00C2A8" if days > 90 else "#FFC75A" if days > 30 else "#FF7A59"
        cards += f"""
        <div class="race-card">
            <div class="race-emoji">{r['emoji']}</div>
            <div class="race-name">{r['name']}</div>
            <div class="race-date">{r['date'].strftime('%b %d, %Y')}</div>
            <div class="race-days" style="color:{color}">
                {'In ' + str(days) + ' days' if days > 0 else 'RACE DAY!' if days == 0 else str(abs(days)) + ' days ago'}
            </div>
            <div class="race-targets">{targets_line}</div>
            <div class="race-note">{r['note']}</div>
        </div>"""
    return cards


# ── Compliance HTML table ─────────────────────────────────────────────────────
def compliance_html(weeks_data):
    if not weeks_data:
        return "<p class='subtext'>No matched sessions in the last 8 weeks yet — sessions will match once your plan dates align with actual Garmin activities.</p>"
    html = ""
    for wk, sessions in sorted(weeks_data.items(), reverse=True):
        label = dt.date.fromisoformat(wk).strftime("Week of %b %d, %Y")
        on = sum(1 for s in sessions if "✅" in s["status"])
        slight = sum(1 for s in sessions if "⚠️" in s["status"])
        off = sum(1 for s in sessions if "❌" in s["status"])
        missed = sum(1 for s in sessions if "⬜" in s["status"])
        total = len(sessions)
        pct = round(100 * on / total) if total else 0
        badge_color = "#00C2A8" if pct >= 80 else "#FFC75A" if pct >= 50 else "#FF7A59"
        chips = (
            f"<span class='chip green'>✅ {on} on target</span>"
            + (f"<span class='chip yellow'>⚠️ {slight} slightly off</span>" if slight else "")
            + (f"<span class='chip red'>❌ {off} off target</span>" if off else "")
            + (f"<span class='chip grey'>⬜ {missed} missed</span>" if missed else "")
        )
        rows = "".join(f"""<tr>
            <td class='date-cell'>{s['date']}</td>
            <td class='disc-cell'>{s['discipline'].replace('_',' ').title()}</td>
            <td class='dim'>{s['session']}</td>
            <td class='target-cell'>{s['planned']}</td>
            <td>{s['actual']}</td>
            <td class='status-cell'>{s['status']}</td>
        </tr>""" for s in sessions)
        html += f"""
        <div class="week-block">
            <div class="week-header">
                <strong>{label}</strong>
                <div class="week-chips">
                    {chips}
                    <span class="badge" style="background:{badge_color}">{pct}% on target</span>
                </div>
            </div>
            <table class="table">
                <tr><th>Date</th><th>Discipline</th><th>Session</th>
                    <th>Target</th><th>Actual</th><th>Status</th></tr>
                {rows}
            </table>
        </div>"""
    return html


# ── HTML dashboard ────────────────────────────────────────────────────────────
def build_html(df, plan, wellness, plan_sessions, manual_log):
    OUT_HTML.parent.mkdir(exist_ok=True)
    if df.empty:
        OUT_HTML.write_text("<h1>No data yet</h1>")
        return

    weekly  = weekly_by_discipline(df)
    trends  = discipline_trends(df)
    ontarget = on_target_pct(weekly, plan)
    compliance = session_compliance(df, plan_sessions)

    last30 = df[df["start"] >= (dt.datetime.now() - dt.timedelta(days=30))]
    total_sessions = len(last30)
    total_hours    = round(last30["duration_min"].sum() / 60)
    avg_load       = round(last30["training_load"].mean()) if last30["training_load"].notna().any() else "n/a"

    avg_sleep = avg_bb = "n/a"
    if not wellness.empty:
        rw = wellness[wellness["date"] >= (dt.datetime.now() - dt.timedelta(days=30))]
        if "sleep_duration_min" in rw.columns and rw["sleep_duration_min"].notna().any():
            avg_sleep = round(rw["sleep_duration_min"].mean() / 60, 1)
        if "body_battery_max" in rw.columns and rw["body_battery_max"].notna().any():
            avg_bb = round(rw["body_battery_max"].mean())

    def disc30(disc):
        return last30[last30["type"] == disc].copy()

    # Swimming
    sw30 = disc30("swimming")
    swim_sessions  = len(sw30)
    swim_total_km  = f"{round(sw30['distance_m'].sum()/1000,1)}km" if not sw30.empty else "n/a"
    swim_avg_dist  = f"{round(sw30['distance_m'].mean())}m" if not sw30.empty else "n/a"
    swim_avg_pace  = "n/a"
    if not sw30.empty:
        raw = sw30["avg_pace"].dropna().apply(speed_to_pace)
        if not raw.empty:
            p = raw.mean() / 10
            swim_avg_pace = f"{int(p)//60}:{int(p)%60:02d}/100m"

    # Running
    ru30 = disc30("running")
    run_sessions   = len(ru30)
    run_total_km   = f"{round(ru30['distance_km'].sum())}km" if not ru30.empty else "n/a"
    run_avg_dist   = f"{round(ru30['distance_km'].mean(),1)}km" if not ru30.empty else "n/a"
    run_avg_pace   = "n/a"
    if not ru30.empty:
        raw = ru30["avg_pace"].dropna().apply(speed_to_pace)
        if not raw.empty:
            run_avg_pace = fmt_pace(raw.mean())

    # Cycling
    cy30 = disc30("cycling")
    bike_sessions  = len(cy30)
    bike_total_km  = f"{round(cy30['distance_km'].sum())}km" if not cy30.empty else "n/a"
    bike_avg_speed = "n/a"
    bike_avg_watts = "n/a"
    if not cy30.empty:
        speeds = cy30["avg_pace"].dropna()
        if not speeds.empty:
            bike_avg_speed = f"{round(speeds.mean()*3.6,1)} km/h"
        watts = cy30["avg_power"].dropna()
        if not watts.empty:
            bike_avg_watts = f"{round(watts.mean())}W"

    figs = [
        chart_volume(weekly),
        chart_load_and_hr(weekly, df),
        chart_pace_trends(trends),
        chart_distance_trends(trends),
        chart_on_target(ontarget),
        chart_sleep(wellness),
        chart_body_battery(wellness),
    ]
    figs = [f for f in figs if f is not None]

    charts_html = "".join(
        f'<div class="chart-cell">{pio.to_html(f, full_html=False, include_plotlyjs=(i==0), config={"displayModeBar": False, "responsive": True})}</div>'
        for i, f in enumerate(figs)
    )

    clean_log = {k: v for k, v in manual_log.items() if not k.startswith("_")}
    strength_rows = "".join(
        f"<tr><td>{wk}</td><td>{'✅ Completed' if done else '❌ Missed'}</td></tr>"
        for wk, done in sorted(clean_log.items(), reverse=True)[:8]
    ) if clean_log else "<tr><td colspan='2' style='color:#999'>No entries yet — edit data/manual_log.json</td></tr>"

    recent = df.tail(6)[["start","name","type","distance_km","duration_min","avg_hr"]].copy()
    recent["start"] = recent["start"].dt.strftime("%b %d")
    recent["distance_km"] = recent["distance_km"].round(1)
    recent["duration_min"] = recent["duration_min"].round(0).astype(int)
    recent_html = recent.to_html(index=False, classes="table", border=0)

    OUT_HTML.write_text(f"""<!DOCTYPE html>
<html>
<head>
<title>Training Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box;}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
     max-width:1140px;margin:0 auto;padding:28px 20px 60px;background:#f6f7fb;color:#1a1a22;}}
.topbar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:10px;}}
h1{{font-size:1.55em;margin:0;font-weight:800;letter-spacing:-.3px;}}
.updated{{color:#9a9aaa;font-size:.8em;margin:2px 0 0;}}
.btn{{background:#5B6EF5;color:#fff;border:none;padding:9px 18px;border-radius:8px;
      font-size:.84em;font-weight:700;cursor:pointer;box-shadow:0 2px 6px rgba(91,110,245,.3);}}
.btn:hover{{background:#4757d8;}}
/* stat cards */
.stats{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:16px 0;}}
.card{{background:#fff;border-radius:12px;padding:14px 10px;
       box-shadow:0 1px 3px rgba(20,20,40,.06);text-align:center;}}
.card .num{{font-size:1.45em;font-weight:800;color:#1a1a22;}}
.card .label{{font-size:.68em;color:#9a9aaa;margin-top:2px;font-weight:600;
              text-transform:uppercase;letter-spacing:.3px;}}
/* race cards */
.races{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin:16px 0;}}
.race-card{{background:#fff;border-radius:12px;padding:16px 18px;
            box-shadow:0 1px 3px rgba(20,20,40,.06);}}
.race-emoji{{font-size:1.6em;}}
.race-name{{font-weight:700;font-size:1em;margin:4px 0 2px;}}
.race-date{{color:#9a9aaa;font-size:.8em;}}
.race-days{{font-size:1.3em;font-weight:800;margin:6px 0 4px;}}
.race-targets{{font-size:.78em;color:#5B6EF5;font-weight:600;}}
.race-note{{font-size:.72em;color:#9a9aaa;margin-top:2px;}}
/* discipline grid */
.disc-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:14px 0;}}
.disc-block{{background:#fff;border-radius:12px;padding:14px 14px 12px;
             box-shadow:0 1px 3px rgba(20,20,40,.06);}}
.disc-title{{font-weight:800;font-size:.9em;margin-bottom:10px;}}
.disc-count{{font-weight:400;color:#9a9aaa;font-size:.82em;margin-left:6px;}}
.disc-stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;}}
.disc-stats .card{{box-shadow:none;background:#f8f8fc;padding:10px 6px;}}
@media(max-width:700px){{
  .disc-grid{{grid-template-columns:1fr;}}
}}
.chart-cell{{background:#fff;border-radius:12px;padding:6px 10px;
             box-shadow:0 1px 3px rgba(20,20,40,.06);overflow:hidden;}}
/* tables */
h2{{font-size:1.1em;margin:32px 0 4px;font-weight:800;}}
.subtext{{color:#9a9aaa;font-size:.8em;margin:0 0 12px;line-height:1.4;}}
.table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;
        overflow:hidden;box-shadow:0 1px 3px rgba(20,20,40,.06);margin-bottom:6px;}}
.table th{{background:#f0f1f8;font-size:.74em;text-transform:uppercase;letter-spacing:.3px;
           color:#6b6b78;padding:9px 11px;text-align:left;}}
.table td{{padding:9px 11px;border-bottom:1px solid #f0f0f5;font-size:.85em;}}
.table tr:last-child td{{border-bottom:none;}}
.date-cell{{color:#9a9aaa;white-space:nowrap;}}
.disc-cell{{font-weight:700;}}
.dim{{color:#888;}}
.target-cell{{color:#5B6EF5;}}
.status-cell{{white-space:nowrap;}}
/* week blocks */
.week-block{{margin-bottom:24px;}}
.week-header{{display:flex;justify-content:space-between;align-items:center;
              margin-bottom:8px;flex-wrap:wrap;gap:6px;}}
.week-chips{{display:flex;flex-wrap:wrap;gap:5px;align-items:center;}}
.chip{{border-radius:10px;padding:2px 9px;font-size:.72em;font-weight:600;}}
.chip.green{{background:#e8f9f5;color:#00A888;}}
.chip.yellow{{background:#fff8e0;color:#b88a00;}}
.chip.red{{background:#ffeae8;color:#d94f3a;}}
.chip.grey{{background:#f3f3f5;color:#888;}}
.badge{{border-radius:20px;padding:3px 12px;font-size:.75em;font-weight:700;color:#fff;}}
/* responsive */
@media(max-width:700px){{
  .chart-grid{{grid-template-columns:1fr;}}
  .stats{{grid-template-columns:repeat(3,1fr);}}
  .races{{grid-template-columns:1fr;}}
}}
@media print{{
  .btn{{display:none;}}
  body{{background:#fff;}}
  .chart-grid{{grid-template-columns:1fr 1fr;}}
  .card,.chart-cell,.table,.race-card{{box-shadow:none;border:1px solid #eee;}}
}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <h1>🏊‍♂️🚴‍♂️🏃‍♂️ Training Dashboard</h1>
    <p class="updated">Last updated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} UTC</p>
  </div>
  <button class="btn" onclick="window.print()">Export PDF</button>
</div>

<h2>Race Targets</h2>
<div class="races">{race_cards_html()}</div>

<h2>Last 30 Days — Overview</h2>
<div class="stats">
  <div class="card"><div class="num">{total_sessions}</div><div class="label">Total Sessions</div></div>
  <div class="card"><div class="num">{total_hours}h</div><div class="label">Total Volume</div></div>
  <div class="card"><div class="num">{avg_load}</div><div class="label">Avg Load</div></div>
  <div class="card"><div class="num">{avg_sleep}h</div><div class="label">Avg Sleep</div></div>
  <div class="card"><div class="num">{avg_bb}</div><div class="label">Body Battery</div></div>
</div>

<div class="disc-grid">
  <div class="disc-block" style="border-top:3px solid {PALETTE['swimming']}">
    <div class="disc-title">🏊 Swimming <span class="disc-count">{swim_sessions} sessions</span></div>
    <div class="disc-stats">
      <div class="card"><div class="num">{swim_total_km}</div><div class="label">Total Distance</div></div>
      <div class="card"><div class="num">{swim_avg_dist}</div><div class="label">Avg per Session</div></div>
      <div class="card"><div class="num">{swim_avg_pace}</div><div class="label">Avg Pace</div></div>
    </div>
  </div>
  <div class="disc-block" style="border-top:3px solid {PALETTE['running']}">
    <div class="disc-title">🏃 Running <span class="disc-count">{run_sessions} sessions</span></div>
    <div class="disc-stats">
      <div class="card"><div class="num">{run_total_km}</div><div class="label">Total Distance</div></div>
      <div class="card"><div class="num">{run_avg_dist}</div><div class="label">Avg per Session</div></div>
      <div class="card"><div class="num">{run_avg_pace}</div><div class="label">Avg Pace</div></div>
    </div>
  </div>
  <div class="disc-block" style="border-top:3px solid {PALETTE['cycling']}">
    <div class="disc-title">🚴 Cycling <span class="disc-count">{bike_sessions} sessions</span></div>
    <div class="disc-stats">
      <div class="card"><div class="num">{bike_total_km}</div><div class="label">Total Distance</div></div>
      <div class="card"><div class="num">{bike_avg_speed}</div><div class="label">Avg Speed</div></div>
      <div class="card"><div class="num">{bike_avg_watts}</div><div class="label">Avg Power</div></div>
    </div>
  </div>
</div>

<h2>Trends</h2>
<div class="chart-grid">{charts_html}</div>

<h2>Session Compliance — Planned vs Actual</h2>
<p class="subtext">Each planned session matched to a Garmin activity (±1 day). Status reflects both duration completion and pace/power adherence.</p>
{compliance_html(compliance)}

<h2>Recent Sessions</h2>
{recent_html}
</body>
</html>""")
    print("HTML dashboard built.")


# ── PDF dashboard ─────────────────────────────────────────────────────────────
def build_pdf(df, plan, wellness, plan_sessions, manual_log):
    import base64
    try:
        from weasyprint import HTML
    except ImportError:
        print("weasyprint not available — skipping PDF")
        return

    OUT_PDF.parent.mkdir(exist_ok=True)
    if df.empty:
        HTML(string="<h1>No data yet</h1>").write_pdf(str(OUT_PDF))
        return

    weekly  = weekly_by_discipline(df)
    trends  = discipline_trends(df)
    ontarget = on_target_pct(weekly, plan)
    compliance = session_compliance(df, plan_sessions)

    last30 = df[df["start"] >= (dt.datetime.now() - dt.timedelta(days=30))]
    total_sessions = len(last30)
    total_hours    = round(last30["duration_min"].sum() / 60)
    avg_load       = round(last30["training_load"].mean()) if last30["training_load"].notna().any() else "n/a"

    avg_sleep = avg_bb = "n/a"
    if not wellness.empty:
        rw = wellness[wellness["date"] >= (dt.datetime.now() - dt.timedelta(days=30))]
        if "sleep_duration_min" in rw.columns and rw["sleep_duration_min"].notna().any():
            avg_sleep = round(rw["sleep_duration_min"].mean() / 60, 1)
        if "body_battery_max" in rw.columns and rw["body_battery_max"].notna().any():
            avg_bb = round(rw["body_battery_max"].mean())

    def disc30(disc):
        return last30[last30["type"] == disc].copy()

    sw30 = disc30("swimming")
    swim_sessions  = len(sw30)
    swim_total_km  = f"{round(sw30['distance_m'].sum()/1000,1)}km" if not sw30.empty else "n/a"
    swim_avg_dist  = f"{round(sw30['distance_m'].mean())}m" if not sw30.empty else "n/a"
    swim_avg_pace  = "n/a"
    if not sw30.empty:
        raw = sw30["avg_pace"].dropna().apply(speed_to_pace)
        if not raw.empty:
            p = raw.mean() / 10
            swim_avg_pace = f"{int(p)//60}:{int(p)%60:02d}/100m"

    ru30 = disc30("running")
    run_sessions   = len(ru30)
    run_total_km   = f"{round(ru30['distance_km'].sum())}km" if not ru30.empty else "n/a"
    run_avg_dist   = f"{round(ru30['distance_km'].mean(),1)}km" if not ru30.empty else "n/a"
    run_avg_pace   = "n/a"
    if not ru30.empty:
        raw = ru30["avg_pace"].dropna().apply(speed_to_pace)
        if not raw.empty:
            run_avg_pace = fmt_pace(raw.mean())

    cy30 = disc30("cycling")
    bike_sessions  = len(cy30)
    bike_total_km  = f"{round(cy30['distance_km'].sum())}km" if not cy30.empty else "n/a"
    bike_avg_speed = "n/a"
    bike_avg_watts = "n/a"
    if not cy30.empty:
        speeds = cy30["avg_pace"].dropna()
        if not speeds.empty:
            bike_avg_speed = f"{round(speeds.mean()*3.6,1)} km/h"
        watts = cy30["avg_power"].dropna()
        if not watts.empty:
            bike_avg_watts = f"{round(watts.mean())}W"

    def to_img(fig, w=446, h=210):
        fig.update_layout(
            height=h, width=w,
            margin=dict(l=32, r=12, t=34, b=24),
            font=dict(family="Helvetica,Arial,sans-serif", size=9.5, color="#2c2c34"),
            title_font=dict(size=11, color="#1a1a22"),
            plot_bgcolor="white", paper_bgcolor="white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=7.5)),
        )
        try:
            b64 = base64.b64encode(fig.to_image(format="png", scale=2)).decode()
            return f'<img src="data:image/png;base64,{b64}" style="width:100%"/>'
        except Exception as e:
            print(f"Chart render failed: {e}")
            return ""

    figs_html = "".join(f'<div class="ci">{to_img(f)}</div>' for f in [
        chart_volume(weekly),
        chart_load_and_hr(weekly, df),
        chart_pace_trends(trends),
        chart_distance_trends(trends),
        chart_on_target(ontarget),
        chart_sleep(wellness),
        chart_body_battery(wellness),
    ] if f is not None)

    # compact compliance for PDF — strip session column, truncate to last 4 weeks
    compliance_rows = ""
    for wk, sessions in sorted(compliance.items(), reverse=True)[:4]:
        label = dt.date.fromisoformat(wk).strftime("Week of %b %d")
        on = sum(1 for s in sessions if "✅" in s["status"])
        pct = round(100*on/len(sessions)) if sessions else 0
        color = "#00C2A8" if pct>=80 else "#FFC75A" if pct>=50 else "#FF7A59"
        compliance_rows += f'<tr><td colspan="5" style="background:#f8f8fc;font-weight:700;font-size:8pt;padding:4pt 5pt">{label} <span style="color:{color};margin-left:6pt">{pct}% on target</span></td></tr>'
        for s in sessions:
            compliance_rows += f"""<tr>
              <td>{s['date']}</td>
              <td style="font-weight:600">{s['discipline'].replace('_',' ').title()}</td>
              <td style="color:#5B6EF5">{s['planned']}</td>
              <td>{s['actual']}</td>
              <td>{s['status']}</td>
            </tr>"""

    # race targets block
    race_html = ""
    for r in RACES:
        days = days_until(r["date"])
        t = r["targets"]
        targets = []
        if "run_pace_sec_km" in t:
            p = t["run_pace_sec_km"]
            targets.append(f"Run {p//60}:{p%60:02d}/km")
        if "bike_power_w" in t:
            targets.append(f"Bike {t['bike_power_w']}W")
        if "swim_pace_100m_sec" in t:
            p = t["swim_pace_100m_sec"]
            targets.append(f"Swim {p//60}:{p%60:02d}/100m")
        days_str = f"In {days} days" if days > 0 else "RACE DAY!" if days == 0 else f"{abs(days)} days ago"
        color = "#00C2A8" if days>90 else "#FFC75A" if days>30 else "#FF7A59"
        race_html += f"""<div class="rcard">
          <div style="font-size:13pt">{r['emoji']}</div>
          <div style="font-weight:700;font-size:9.5pt">{r['name']}</div>
          <div style="font-size:7.5pt;color:#888">{r['date'].strftime('%b %d, %Y')} · {r['note']}</div>
          <div style="font-size:11pt;font-weight:800;color:{color};margin:3pt 0">{days_str}</div>
          <div style="font-size:7.5pt;color:#5B6EF5;font-weight:600">{' · '.join(targets)}</div>
        </div>"""

    clean_log = {k: v for k, v in manual_log.items() if not k.startswith("_")}
    strength_rows = "".join(
        f"<tr><td>{wk}</td><td>{'Completed' if done else 'Missed'}</td></tr>"
        for wk, done in sorted(clean_log.items(), reverse=True)[:6]
    ) or "<tr><td colspan='2'>No entries yet</td></tr>"

    recent = df.tail(8)[["start","name","type","distance_km","duration_min","avg_hr"]].copy()
    recent["start"] = recent["start"].dt.strftime("%b %d")
    recent["distance_km"] = recent["distance_km"].round(1)
    recent["duration_min"] = recent["duration_min"].round(0).astype(int)
    recent_html = recent.to_html(index=False, classes="table", border=0)

    HTML(string=f"""<html><head><style>
@page{{size:A4;margin:11mm 13mm;}}
body{{font-family:Helvetica,Arial,sans-serif;color:#1a1a22;font-size:8.5pt;}}
h1{{font-size:15pt;margin:0 0 1pt;font-weight:800;}}
h2{{font-size:10pt;margin:9pt 0 3pt;border-bottom:1px solid #eee;padding-bottom:2pt;font-weight:700;}}
.updated{{color:#999;font-size:7pt;margin:0 0 7pt;}}
/* stat grid */
.stats{{display:flex;flex-wrap:wrap;gap:5pt;margin:5pt 0;}}
.card{{border:1px solid #eee;border-radius:4pt;padding:5pt 7pt;text-align:center;flex:1;min-width:50pt;}}
.card .num{{font-size:11pt;font-weight:800;}}
.card .label{{font-size:5.5pt;color:#999;text-transform:uppercase;}}
/* race cards */
.races{{display:flex;gap:6pt;margin:5pt 0;}}
.rcard{{border:1px solid #eee;border-radius:4pt;padding:7pt 9pt;flex:1;}}
/* charts — 3 columns so 6 fit neatly in 2 rows */
.charts{{display:flex;flex-wrap:wrap;gap:4pt;margin:4pt 0;}}
.ci{{width:32%;border:1px solid #eee;border-radius:3pt;padding:2pt;}}
/* discipline grid */
.dg{{display:flex;gap:5pt;margin:5pt 0;}}
.db{{border:1px solid #eee;border-radius:4pt;padding:6pt 8pt;flex:1;}}
.dt{{font-weight:700;font-size:8.5pt;margin-bottom:4pt;}}
.dc{{font-weight:400;color:#999;font-size:7.5pt;}}
.db .stats{{gap:3pt;margin:0;}}
.db .card{{padding:4pt 5pt;min-width:0;}}
/* tables */
.table{{width:100%;border-collapse:collapse;font-size:7pt;margin-bottom:3pt;
        page-break-inside:avoid;table-layout:fixed;}}
.table th{{background:#f5f5fa;padding:3.5pt 4pt;text-align:left;
           font-size:6.2pt;text-transform:uppercase;overflow:hidden;}}
.table td{{padding:3.5pt 4pt;border-bottom:1px solid #f0f0f5;
           word-wrap:break-word;overflow-wrap:break-word;}}
/* week blocks — keep each week together on same page */
.wblock{{margin-bottom:7pt;page-break-inside:avoid;}}
.wlabel{{font-weight:700;font-size:8.5pt;margin-bottom:3pt;}}
</style></head><body>
<h1>🏊‍♂️🚴‍♂️🏃‍♂️ Training Dashboard</h1>
<p class="updated">Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} UTC</p>

<h2>Race Targets</h2>
<div class="races">{race_html}</div>

<h2>Last 30 Days — Overview</h2>
<div class="stats">
  <div class="card"><div class="num">{total_sessions}</div><div class="label">Sessions</div></div>
  <div class="card"><div class="num">{total_hours}h</div><div class="label">Volume</div></div>
  <div class="card"><div class="num">{avg_load}</div><div class="label">Avg Load</div></div>
  <div class="card"><div class="num">{avg_sleep}h</div><div class="label">Avg Sleep</div></div>
  <div class="card"><div class="num">{avg_bb}</div><div class="label">Body Battery</div></div>
</div>
<div class="dg">
  <div class="db" style="border-top:2.5pt solid #36C5F0">
    <div class="dt">🏊 Swimming <span class="dc">({swim_sessions})</span></div>
    <div class="stats">
      <div class="card"><div class="num">{swim_total_km}</div><div class="label">Total Dist</div></div>
      <div class="card"><div class="num">{swim_avg_dist}</div><div class="label">Avg/Session</div></div>
      <div class="card"><div class="num">{swim_avg_pace}</div><div class="label">Avg Pace</div></div>
    </div>
  </div>
  <div class="db" style="border-top:2.5pt solid #5B6EF5">
    <div class="dt">🏃 Running <span class="dc">({run_sessions})</span></div>
    <div class="stats">
      <div class="card"><div class="num">{run_total_km}</div><div class="label">Total Dist</div></div>
      <div class="card"><div class="num">{run_avg_dist}</div><div class="label">Avg/Session</div></div>
      <div class="card"><div class="num">{run_avg_pace}</div><div class="label">Avg Pace</div></div>
    </div>
  </div>
  <div class="db" style="border-top:2.5pt solid #00C2A8">
    <div class="dt">🚴 Cycling <span class="dc">({bike_sessions})</span></div>
    <div class="stats">
      <div class="card"><div class="num">{bike_total_km}</div><div class="label">Total Dist</div></div>
      <div class="card"><div class="num">{bike_avg_speed}</div><div class="label">Avg Speed</div></div>
      <div class="card"><div class="num">{bike_avg_watts}</div><div class="label">Avg Power</div></div>
    </div>
  </div>
</div>

<h2>Trends</h2>
<div class="charts">{figs_html}</div>

<h2>Session Compliance — Last 4 Weeks</h2>
<table class="table">
  <colgroup>
    <col style="width:13%"/><col style="width:12%"/><col style="width:25%"/>
    <col style="width:25%"/><col style="width:25%"/>
  </colgroup>
  <tr><th>Date</th><th>Discipline</th><th>Target</th><th>Actual</th><th>Status</th></tr>
  {compliance_rows if compliance_rows else '<tr><td colspan="5">No matched sessions yet</td></tr>'}
</table>

<h2>Recent Sessions</h2>
{recent_html}
</body></html>""").write_pdf(str(OUT_PDF))
    print("PDF dashboard built.")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    df           = load_activities()
    plan         = load_plan()
    wellness     = load_wellness()
    plan_sessions = load_plan_sessions()
    manual_log   = load_manual_log()
    build_html(df, plan, wellness, plan_sessions, manual_log)
    build_pdf(df, plan, wellness, plan_sessions, manual_log)


if __name__ == "__main__":
    main()

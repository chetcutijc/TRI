"""
build_dashboard.py
Builds docs/index.html (interactive) and docs/print.html (email-friendly)
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
DATA_FILE = Path("data/activities.json")
WELLNESS_FILE = Path("data/wellness.json")
PLAN_FILE = Path("data/plan.json")
PLAN_SESSIONS_FILE = Path("data/plan_sessions.json")
MANUAL_LOG_FILE = Path("data/manual_log.json")
OUT_HTML = Path("docs/index.html")
OUT_PDF = Path("docs/dashboard.pdf")
OUT_PRINT = Path("docs/print.html")

# Favicon / iOS touch icon
FAVICON_URI = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E🏊%3C/text%3E%3C/svg%3E"
APPLE_TOUCH_ICON = "/apple-touch-icon.png"

# ── Race targets ────────────────────────────────────────────────────────────
RACES = [
    {
        "name": "Marathon",
        "emoji": "🏃",
        "date": dt.date(2027, 2, 7),
        "disciplines": ["running"],
        "targets": {"run_pace_sec_km": 5 * 60 + 45},
        "note": "Target: sub-4h (~5:45/km)",
    },
    {
        "name": "Ironman Italy Cervia",
        "emoji": "🏊🚴🏃",
        "date": dt.date(2027, 6, 20),
        "disciplines": ["swimming", "cycling", "running"],
        "targets": {
            "swim_pace_100m_sec": 110,
            "bike_power_w": 190,
            "run_pace_sec_km": 6 * 60 + 30,
        },
        "note": "Full Ironman",
    },
]

PALETTE = {
    "running": "#5B6EF5",
    "cycling": "#00C2A8",
    "swimming": "#36C5F0",
    "strength_training": "#FF7A59",
    "other": "#9B7DFF",
    "load": "#FFC75A",
    "sleep": "#9B7DFF",
    "battery": "#00C2A8",
}

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
    if not speed_m_s or speed_m_s == 0:
        return None
    return 1000 / speed_m_s


def session_avg_pace_str(row):
    disc = row.get("type", "")
    speed = row.get("avg_pace")
    power = row.get("avg_power")

    if disc == "running" and speed and speed > 0.1:
        return fmt_pace(speed_to_pace(speed))

    if disc == "swimming" and speed and speed > 0.1:
        sec_100m = speed_to_pace(speed) / 10
        return f"{int(sec_100m)//60}:{int(sec_100m)%60:02d}/100m"

    if disc == "cycling":
        if power and power > 0:
            kmh = speed * 3.6 if speed and speed > 0.1 else None
            return f"{round(kmh,1)} km/h · {round(power)}W" if kmh else f"{round(power)}W"
        if speed and speed > 0.1:
            return f"{round(speed * 3.6, 1)} km/h"

    return "—"


def session_benefit(row):
    disc = row.get("type", "")
    avg_hr = row.get("avg_hr")
    max_hr = row.get("max_hr") or 185
    dur = row.get("duration_min", 0)

    if not avg_hr:
        return "—", "#aaa"

    hr_pct = avg_hr / max_hr

    if hr_pct < 0.60:
        zone = 1
    elif hr_pct < 0.70:
        zone = 2
    elif hr_pct < 0.80:
        zone = 3
    elif hr_pct < 0.88:
        zone = 4
    else:
        zone = 5

    if zone == 1 and dur < 30:
        return "🔄 Active Recovery", "#9B7DFF"
    if zone == 1 and dur >= 30:
        return "♻️ Recovery", "#9B7DFF"
    if zone == 2 and dur >= 60:
        return "🏗️ Base Building", "#00C2A8"
    if zone == 2 and dur < 60:
        return "✅ Aerobic", "#00C2A8"
    if zone == 3 and disc == "running":
        return "🎯 Tempo Run", "#5B6EF5"
    if zone == 3 and disc == "cycling":
        return "🎯 Sweet Spot", "#5B6EF5"
    if zone == 3:
        return "🎯 Aerobic Dev", "#5B6EF5"
    if zone == 4:
        return "⚡ Threshold", "#FFC75A"
    if zone == 5 and dur < 30:
        return "⚠️ Very Hard / Short", "#FF7A59"
    if zone == 5:
        return "🔥 High Intensity", "#FF7A59"

    return "✅ Productive", "#00C2A8"


def build_recent_html(df, n=8):
    recent = df.tail(n).copy().iloc[::-1]
    rows = ""
    for _, row in recent.iterrows():
        date = row["start"].strftime("%b %d")
        name = str(row.get("name", ""))[:28]
        disc = str(row.get("type", "")).replace("_", " ").title()
        dist = f"{round(row['distance_km'],1)}km" if row.get("distance_km") else "—"
        dur = f"{round(row['duration_min'])}min" if row.get("duration_min") else "—"
        hr = f"{round(row['avg_hr'])} bpm" if row.get("avg_hr") else "—"
        pace = session_avg_pace_str(row)
        benefit, bcolor = session_benefit(row)
        rows += f'<tr><td class="date-cell">{date}</td><td class="dim" title="{name}">{name[:22]}{"…" if len(name)>22 else ""}</td><td class="disc-cell">{disc}</td><td>{dist}</td><td>{dur}</td><td>{hr}</td><td style="font-weight:600">{pace}</td><td><span style="background:{bcolor}18;color:{bcolor};border-radius:8px;padding:2px 8px;font-size:.78em;font-weight:600;white-space:nowrap">{benefit}</span></td></tr>'
    return f'<table class="table"><tr><th>Date</th><th>Session</th><th>Discipline</th><th>Distance</th><th>Duration</th><th>Avg HR</th><th>Avg Pace</th><th>Benefit</th></tr>{rows}</table>'


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

        wk["pace_sec_km"] = wk["avg_pace"].apply(lambda v: speed_to_pace(v) if v and v > 0.1 else None)

        if disc == "swimming":
            wk["pace_sec_100m"] = wk["pace_sec_km"].apply(lambda x: round(x / 10, 1) if x and x > 0 else None)

        if disc == "cycling":
            wk["speed_kmh"] = wk["avg_pace"].apply(lambda v: round(v * 3.6, 1) if v and v > 0.1 else None)

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
    if not plan_sessions or df.empty:
        return {}
    today = dt.date.today()
    cutoff = today - dt.timedelta(weeks=weeks_back)
    recent_ps = [ps for ps in plan_sessions if cutoff <= dt.date.fromisoformat(ps["date"]) <= today]

    result = {}
    for ps in recent_ps:
        ps_date = dt.date.fromisoformat(ps["date"])
        disc = ps["discipline"]
        wk = (ps_date - dt.timedelta(days=ps_date.weekday())).isoformat()

        mask = (
            (df["type"] == disc) &
            (df["start"].dt.date >= ps_date - dt.timedelta(days=1)) &
            (df["start"].dt.date <= ps_date + dt.timedelta(days=1))
        )
        candidates = df[mask]

        if candidates.empty:
            entry = {
                "date": ps_date.isoformat(),
                "discipline": disc,
                "session": ps.get("summary", ""),
                "planned": _target_str(ps),
                "actual": "—",
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

    dur_ok = None
    if planned_dur:
        ratio = actual_dur / planned_dur
        dur_ok = "on" if ratio >= 0.90 else "slight" if ratio >= 0.75 else "off"

    dist_ok = None
    if planned_dist and actual_dist_km:
        ratio = actual_dist_km / planned_dist
        dist_ok = "on" if ratio >= 0.90 else "slight" if ratio >= 0.75 else "off"

    pace_ok = None
    if disc == "running" and ps.get("pace_low_sec_km") and actual_pace:
        lo, hi = ps["pace_low_sec_km"], ps["pace_high_sec_km"]
        if actual_pace < lo * 0.95 or lo <= actual_pace <= hi:
            pace_ok = "on"
        elif actual_pace <= hi * 1.08:
            pace_ok = "slight"
        else:
            pace_ok = "off"

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
    worst = max([dur_ok, dist_ok, pace_ok or power_ok], key=lambda s: RANK.get(s, -1))
    status = "❌ Off Target" if worst == "off" else "⚠️ Slightly Off" if worst == "slight" else "✅ On Target"

    parts = []

    if planned_dur:
        diff = actual_dur - planned_dur
        parts.append(f"{actual_dur}min ({'+' if diff > 0 else ''}{diff}min vs plan)")
    else:
        parts.append(f"{actual_dur}min")

    if actual_dist_km:
        if planned_dist:
            diff = round(actual_dist_km - planned_dist, 1)
            parts.append(f"{actual_dist_km}km ({'+' if diff > 0 else ''}{diff}km vs plan)")
        else:
            parts.append(f"{actual_dist_km}km")

    if disc == "running" and actual_pace:
        pace_str = fmt_pace(actual_pace)
        if ps.get("pace_low_sec_km"):
            mid = (ps["pace_low_sec_km"] + ps["pace_high_sec_km"]) / 2
            d = round(actual_pace - mid)
            pace_str += f" ({'+' if d > 0 else ''}{d}s vs target)"
        parts.append(pace_str)

    if disc == "cycling":
        if actual_speed_kmh:
            parts.append(f"{actual_speed_kmh} km/h")
        if actual_power:
            pwr = f"{round(actual_power)}W"
            if ps.get("power_low_w"):
                mid = (ps["power_low_w"] + ps["power_high_w"]) / 2
                d = round(actual_power - mid)
                pwr += f" ({'+' if d > 0 else ''}{d}W vs target)"
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
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="-apple-system,Helvetica,Arial,sans-serif", size=12, color="#2c2c34"),
        title_font=dict(size=14, color="#1a1a22"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=10)),
        hovermode="x unified",
        height=300,
        margin=dict(l=44, r=28, t=48, b=36),
    )


def chart_volume(weekly):
    fig = go.Figure()
    added = False
    for disc in ["running", "cycling", "swimming", "strength_training"]:
        sub = weekly[weekly["type"] == disc]
        if sub.empty:
            continue
        fig.add_trace(go.Bar(
            x=sub["week"], y=sub["duration_min"].round(),
            name=disc.replace("_", " ").title(),
            marker_color=PALETTE.get(disc, "#ccc"),
        ))
        added = True
    if not added:
        return None
    fig.update_layout(barmode="stack", title="Weekly Volume (min)", **STYLE())
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="#f0f0f5")
    return fig


def chart_load_and_hr(weekly, df):
    df = df.copy()
    df["week"] = df["start"].dt.to_period("W").apply(lambda r: r.start_time)
    load_wk = df.groupby("week")["training_load"].sum().reset_index()
    if load_wk.empty or load_wk["training_load"].isna().all():
        return None

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        x=load_wk["week"], y=load_wk["training_load"].round(),
        name="Training Load", marker_color=PALETTE["load"], opacity=0.7
    ), secondary_y=False)

    hr_added = F

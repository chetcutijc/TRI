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


def session_avg_pace_str(row):
    """Human-readable avg pace string tailored per discipline."""
    disc = row.get("type", "")
    speed = row.get("avg_pace")        # m/s from Garmin
    power = row.get("avg_power")

    if disc == "running" and speed and speed > 0.1:
        sec_km = speed_to_pace(speed)
        return fmt_pace(sec_km)

    if disc == "swimming" and speed and speed > 0.1:
        sec_km = speed_to_pace(speed)
        sec_100m = sec_km / 10
        return f"{int(sec_100m)//60}:{int(sec_100m)%60:02d}/100m"

    if disc == "cycling":
        if power and power > 0:
            kmh = speed * 3.6 if speed and speed > 0.1 else None
            if kmh:
                return f"{round(kmh,1)} km/h · {round(power)}W"
            return f"{round(power)}W"
        if speed and speed > 0.1:
            return f"{round(speed * 3.6, 1)} km/h"

    return "—"


def session_benefit(row):
    """
    Classify how a session contributes to race targets using:
    - HR zone (avg_hr as % of estimated max)
    - Duration
    - Training load
    Returns (label, colour).
    """
    disc      = row.get("type", "")
    avg_hr    = row.get("avg_hr")
    max_hr    = row.get("max_hr") or 185      # default for trained triathlete
    dur       = row.get("duration_min", 0)
    load      = row.get("training_load") or 0

    if not avg_hr:
        return "—", "#aaa"

    hr_pct = avg_hr / max_hr

    # ── Zone classification ──────────────────────────────────────────────
    if hr_pct < 0.60:
        zone = 1   # very easy / recovery
    elif hr_pct < 0.70:
        zone = 2   # aerobic base
    elif hr_pct < 0.80:
        zone = 3   # aerobic development / tempo
    elif hr_pct < 0.88:
        zone = 4   # threshold
    else:
        zone = 5   # VO2max / race pace

    # ── Benefit label ────────────────────────────────────────────────────
    if zone == 1 and dur < 30:
        return "🔄 Active Recovery", "#9B7DFF"

    if zone == 1 and dur >= 30:
        return "♻️ Recovery", "#9B7DFF"

    if zone == 2 and dur >= 60:
        return "🏗️ Base Building", "#00C2A8"    # long aerobic — most valuable for IM

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
        return "⚠️ Very Hard / Short", "#FF7A59"   # high intensity but too brief

    if zone == 5:
        return "🔥 High Intensity", "#FF7A59"

    return "✅ Productive", "#00C2A8"


def build_recent_html(df, n=8):
    """Build an enhanced recent sessions table with avg pace and benefit columns."""
    recent = df.tail(n).copy().iloc[::-1]   # most recent first
    rows = ""
    for _, row in recent.iterrows():
        date   = row["start"].strftime("%b %d")
        name   = str(row.get("name", ""))[:28]
        disc   = str(row.get("type", "")).replace("_", " ").title()
        dist   = f"{round(row['distance_km'],1)}km" if row.get("distance_km") else "—"
        dur    = f"{round(row['duration_min'])}min" if row.get("duration_min") else "—"
        hr     = f"{round(row['avg_hr'])} bpm" if row.get("avg_hr") else "—"
        pace   = session_avg_pace_str(row)
        benefit, bcolor = session_benefit(row)
        rows += f"""<tr>
            <td class="date-cell">{date}</td>
            <td class="dim" title="{name}">{name[:22]}{'…' if len(name)>22 else ''}</td>
            <td class="disc-cell">{disc}</td>
            <td>{dist}</td>
            <td>{dur}</td>
            <td>{hr}</td>
            <td style="font-weight:600">{pace}</td>
            <td><span style="background:{bcolor}18;color:{bcolor};border-radius:8px;
                padding:2px 8px;font-size:.78em;font-weight:600;white-space:nowrap">{benefit}</span></td>
        </tr>"""
    return f"""<table class="table">
        <tr>
            <th>Date</th><th>Session</th><th>Discipline</th>
            <th>Distance</th><th>Duration</th><th>Avg HR</th>
            <th>Avg Pace</th><th>Benefit</th>
        </tr>
        {rows}
    </table>"""


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
    """Match planned sessions to actual Garmin sessions by date ±1 day.
    Only includes sessions between (today - weeks_back) and today."""
    if not plan_sessions or df.empty:
        return {}
    today = dt.date.today()
    cutoff = today - dt.timedelta(weeks=weeks_back)
    recent_ps = [ps for ps in plan_sessions
                 if cutoff <= dt.date.fromisoformat(ps["date"]) <= today]

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

    recent_html = build_recent_html(df, n=8)

    OUT_HTML.write_text(f"""<!DOCTYPE html>
<html>
<head>
<title>🏊🚴🏃 Training Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏊</text></svg>">
<meta name="theme-color" content="#5B6EF5">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Training">
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
/* charts — 2 column grid on desktop, 1 column on mobile */
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px;}}
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
  .disc-grid{{grid-template-columns:1fr;}}
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

# ── Main ──────────────────────────────────────────────────────────────────────

def build_print_html(df, plan, wellness, plan_sessions, manual_log):
    """
    Builds docs/print.html — mobile/print-optimised for Chrome headless PDF export.
    Charts are rendered as static SVG via kaleido (server-side), so Chrome gets
    plain markup with no JS timing issues — charts are always visible in the PDF.
    Compliance limited to last 4 weeks, past sessions only.
    """
    OUT_PRINT = Path("docs/print.html")
    OUT_PRINT.parent.mkdir(exist_ok=True)

    if df.empty:
        OUT_PRINT.write_text("<h1>No activity data yet</h1>")
        return

    # ── Data ────────────────────────────────────────────────────────────────
    weekly    = weekly_by_discipline(df)
    trends    = discipline_trends(df)
    ontarget  = on_target_pct(weekly, plan)
    compliance = session_compliance(df, plan_sessions, weeks_back=4)

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

    def d30(disc):
        return last30[last30["type"] == disc]

    sw30 = d30("swimming")
    swim_sessions = len(sw30)
    swim_total    = f"{round(sw30['distance_m'].sum()/1000,1)}km" if not sw30.empty else "n/a"
    swim_avg_dist = f"{round(sw30['distance_m'].mean())}m" if not sw30.empty else "n/a"
    swim_pace     = "n/a"
    if not sw30.empty:
        raw = sw30["avg_pace"].dropna().apply(speed_to_pace)
        if not raw.empty:
            p = raw.mean() / 10
            swim_pace = f"{int(p)//60}:{int(p)%60:02d}/100m"

    ru30 = d30("running")
    run_sessions  = len(ru30)
    run_total     = f"{round(ru30['distance_km'].sum())}km" if not ru30.empty else "n/a"
    run_avg_dist  = f"{round(ru30['distance_km'].mean(),1)}km" if not ru30.empty else "n/a"
    run_pace      = "n/a"
    if not ru30.empty:
        raw = ru30["avg_pace"].dropna().apply(speed_to_pace)
        if not raw.empty:
            run_pace = fmt_pace(raw.mean())

    cy30 = d30("cycling")
    bike_sessions  = len(cy30)
    bike_total     = f"{round(cy30['distance_km'].sum())}km" if not cy30.empty else "n/a"
    bike_speed     = "n/a"
    bike_watts     = "n/a"
    if not cy30.empty:
        sp = cy30["avg_pace"].dropna()
        if not sp.empty:
            bike_speed = f"{round(sp.mean()*3.6,1)} km/h"
        wp = cy30["avg_power"].dropna()
        if not wp.empty:
            bike_watts = f"{round(wp.mean())}W"

    # ── Chart style ──────────────────────────────────────────────────────────
    PRINT_W, PRINT_H = 500, 220
    PRINT_STYLE = dict(
        height=PRINT_H, width=PRINT_W,
        margin=dict(l=52, r=16, t=36, b=32),
        font=dict(family="Helvetica,Arial,sans-serif", size=10, color="#1a1a22"),
        title_font=dict(size=11, color="#1a1a22"),
        plot_bgcolor="white", paper_bgcolor="white",
        colorway=[PALETTE["running"], PALETTE["cycling"], PALETTE["swimming"],
                  PALETTE["load"], PALETTE["sleep"], PALETTE["battery"]],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=8)),
        showlegend=True,
    )

    def svg(fig):
        """Render figure as inline SVG string via kaleido. Falls back to empty string."""
        fig.update_layout(**PRINT_STYLE)
        fig.update_xaxes(showgrid=False, linecolor="#e3e3ea")
        fig.update_yaxes(showgrid=True, gridcolor="#f0f0f5", linecolor="#e3e3ea")
        try:
            raw = fig.to_image(format="svg")
            return raw.decode("utf-8")
        except Exception as e:
            print(f"  SVG render failed: {e}")
            return ""

    # ── Build charts ─────────────────────────────────────────────────────────
    charts = []   # list of (title, svg_string)

    # 1. Weekly volume
    fig = go.Figure()
    for disc in ["swimming", "running", "cycling", "strength_training"]:
        sub = weekly[weekly["type"] == disc]
        if not sub.empty:
            fig.add_trace(go.Bar(
                x=sub["week"], y=sub["duration_min"].round(),
                name=disc.replace("_", " ").title(),
                marker_color=PALETTE.get(disc, "#ccc"),
            ))
    fig.update_layout(barmode="stack")
    s = svg(fig)
    if s:
        charts.append(("Weekly Volume (min)", s))

    # 2. Weekly training load
    df2 = df.copy()
    df2["week"] = df2["start"].dt.to_period("W").apply(lambda r: r.start_time)
    lw = df2.groupby("week")["training_load"].sum().reset_index()
    if not lw.empty and lw["training_load"].notna().any():
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=lw["week"], y=lw["training_load"].round(),
            marker_color=PALETTE["load"],
        ))
        s = svg(fig)
        if s:
            charts.append(("Weekly Training Load", s))

    # 3. Run pace trend
    if "running" in trends:
        rd = trends["running"].dropna(subset=["pace_sec_km"])
        if not rd.empty:
            fig = go.Figure()
            y = rd["pace_sec_km"].apply(lambda x: round(x/60, 2) if x else None)
            fig.add_trace(go.Scatter(
                x=rd["week"], y=y, mode="lines+markers",
                name="Run Pace", marker_color=PALETTE["running"],
            ))
            fig.update_yaxes(autorange="reversed", title_text="min/km")
            s = svg(fig)
            if s:
                charts.append(("Run Pace Trend", s))

    # 4. Swim pace trend
    if "swimming" in trends:
        sd = trends["swimming"].dropna(subset=["pace_sec_100m"])
        if not sd.empty:
            fig = go.Figure()
            y = sd["pace_sec_100m"].apply(lambda x: round(x/60, 2) if x else None)
            fig.add_trace(go.Scatter(
                x=sd["week"], y=y, mode="lines+markers",
                name="Swim Pace", marker_color=PALETTE["swimming"],
            ))
            fig.update_yaxes(autorange="reversed", title_text="min/100m")
            s = svg(fig)
            if s:
                charts.append(("Swim Pace Trend", s))

    # 5. Cycling power or speed
    if "cycling" in trends:
        cd = trends["cycling"]
        pw = cd.dropna(subset=["avg_power"])
        sp = cd.dropna(subset=["speed_kmh"])
        fig = go.Figure()
        if not pw.empty:
            fig.add_trace(go.Scatter(
                x=pw["week"], y=pw["avg_power"].round(),
                mode="lines+markers", name="Power (W)",
                marker_color=PALETTE["cycling"],
            ))
            fig.update_yaxes(title_text="Watts")
            s = svg(fig)
            if s:
                charts.append(("Cycling Power Trend", s))
        elif not sp.empty:
            fig.add_trace(go.Scatter(
                x=sp["week"], y=sp["speed_kmh"],
                mode="lines+markers", name="Speed (km/h)",
                marker_color=PALETTE["cycling"],
            ))
            fig.update_yaxes(title_text="km/h")
            s = svg(fig)
            if s:
                charts.append(("Cycling Speed Trend", s))

    # 6. On-target %
    if not ontarget.empty:
        fig = go.Figure()
        for disc in ontarget["type"].unique():
            sub = ontarget[ontarget["type"] == disc]
            fig.add_trace(go.Scatter(
                x=sub["week"], y=sub["pct"],
                mode="lines+markers",
                name=disc.replace("_", " ").title(),
                marker_color=PALETTE.get(disc, "#ccc"),
            ))
        fig.add_hline(y=80, line_dash="dot", line_color="#bbb",
                      annotation_text="80% target", annotation_font_size=9)
        fig.update_yaxes(range=[0, 110], title_text="%")
        s = svg(fig)
        if s:
            charts.append(("On-Target % vs Plan", s))

    # 7. Sleep duration
    if not wellness.empty and "sleep_duration_min" in wellness.columns:
        sw = wellness.dropna(subset=["sleep_duration_min"])
        if not sw.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=sw["date"],
                y=(sw["sleep_duration_min"]/60).round(1),
                mode="lines+markers", marker_color=PALETTE["sleep"],
            ))
            fig.add_hline(y=7, line_dash="dot", line_color="#bbb",
                          annotation_text="7h", annotation_font_size=9)
            fig.update_yaxes(title_text="Hours")
            s = svg(fig)
            if s:
                charts.append(("Sleep Duration", s))

    # 8. Body battery
    if not wellness.empty and "body_battery_max" in wellness.columns:
        bw = wellness.dropna(subset=["body_battery_max"])
        if not bw.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=bw["date"], y=bw["body_battery_max"].round(),
                mode="lines+markers", name="Charged",
                marker_color=PALETTE["battery"],
            ))
            if "body_battery_min" in bw.columns:
                fig.add_trace(go.Scatter(
                    x=bw["date"], y=bw["body_battery_min"].round(),
                    mode="lines", name="Drained",
                    line=dict(dash="dot"), marker_color="#FFC75A",
                ))
            s = svg(fig)
            if s:
                charts.append(("Body Battery", s))

    # ── Pair charts into 2-col table rows ────────────────────────────────────
    chart_rows = ""
    for i in range(0, len(charts), 2):
        ltitle, lsvg = charts[i]
        if i + 1 < len(charts):
            rtitle, rsvg = charts[i+1]
            rcell = f'<td style="width:50%;padding:3px 3px 8px;vertical-align:top"><div class="ctitle">{rtitle}</div>{rsvg}</td>'
        else:
            rcell = '<td style="width:50%"></td>'
        chart_rows += f"""<tr>
            <td style="width:50%;padding:3px 3px 8px;vertical-align:top">
                <div class="ctitle">{ltitle}</div>{lsvg}
            </td>
            {rcell}
        </tr>"""

    # ── Race countdown ───────────────────────────────────────────────────────
    race_rows = ""
    for r in RACES:
        days = days_until(r["date"])
        t = r["targets"]
        tgt = []
        if "run_pace_sec_km" in t:
            p = t["run_pace_sec_km"]
            tgt.append(f"Run {p//60}:{p%60:02d}/km")
        if "bike_power_w" in t:
            tgt.append(f"Bike {t['bike_power_w']}W")
        if "swim_pace_100m_sec" in t:
            p = t["swim_pace_100m_sec"]
            tgt.append(f"Swim {p//60}:{p%60:02d}/100m")
        days_str = f"In {days} days" if days > 0 else "RACE DAY!"
        col = "#00C2A8" if days > 90 else "#FFC75A" if days > 30 else "#FF7A59"
        race_rows += f"""<tr>
            <td>{r['emoji']} <strong>{r['name']}</strong></td>
            <td>{r['date'].strftime('%b %d, %Y')}</td>
            <td style="color:{col};font-weight:700">{days_str}</td>
            <td style="color:#5B6EF5;font-size:.85em">{' · '.join(tgt)}</td>
        </tr>"""

    # ── Session compliance (last 4 weeks, past only) ─────────────────────────
    comp_rows = ""
    for wk, sessions in sorted(compliance.items(), reverse=True):
        label = dt.date.fromisoformat(wk).strftime("Week of %b %d")
        on  = sum(1 for s in sessions if "✅" in s["status"])
        pct = round(100 * on / len(sessions)) if sessions else 0
        col = "#00C2A8" if pct >= 80 else "#FFC75A" if pct >= 50 else "#FF7A59"
        comp_rows += (
            f'<tr><td colspan="5" style="background:#f5f5fa;font-weight:700;'
            f'padding:5px 6px;font-size:.82em">'
            f'{label} <span style="color:{col}">— {pct}% on target</span></td></tr>'
        )
        for s in sessions:
            comp_rows += f"""<tr>
                <td>{s['date']}</td>
                <td style="font-weight:600">{s['discipline'].replace('_',' ').title()}</td>
                <td style="color:#5B6EF5;font-size:.82em">{s['planned']}</td>
                <td style="font-size:.82em">{s['actual']}</td>
                <td style="white-space:nowrap">{s['status']}</td>
            </tr>"""

    # ── Recent sessions ──────────────────────────────────────────────────────
    recent_rows = ""
    for _, row in df.tail(8).iloc[::-1].iterrows():
        pace_str = session_avg_pace_str(row)
        benefit, bcol = session_benefit(row)
        recent_rows += f"""<tr>
            <td>{row['start'].strftime('%b %d')}</td>
            <td style="font-weight:600">{str(row.get('type','') or '').replace('_',' ').title()}</td>
            <td>{round(row['distance_km'],1) if row.get('distance_km') else '—'}km</td>
            <td>{round(row['duration_min'])}min</td>
            <td style="font-weight:600">{pace_str}</td>
            <td style="color:{bcol};font-weight:600;font-size:.85em">{benefit}</td>
        </tr>"""

    # ── Write HTML ───────────────────────────────────────────────────────────
    OUT_PRINT.write_text(f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Training Dashboard</title>
<style>
@page {{ size: A4; margin: 11mm 13mm; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: Helvetica, Arial, sans-serif;
    color: #1a1a22; font-size: 9pt;
    max-width: 720px; margin: 0 auto; padding: 10px;
}}
h1 {{ font-size: 15pt; font-weight: 800; margin-bottom: 2pt; }}
h2 {{
    font-size: 10pt; font-weight: 700; margin: 12pt 0 5pt;
    border-bottom: 2px solid #f0f0f5; padding-bottom: 2pt; color: #1a1a22;
}}
.updated {{ color: #999; font-size: 7pt; margin-bottom: 8pt; }}
/* stat cards */
.stats {{ display: flex; flex-wrap: wrap; gap: 5pt; margin: 5pt 0 8pt; }}
.card {{
    border: 1px solid #eee; border-radius: 5pt;
    padding: 5pt 7pt; text-align: center; flex: 1; min-width: 50pt;
}}
.card .num {{ font-size: 11pt; font-weight: 800; }}
.card .lbl {{ font-size: 5.5pt; color: #999; text-transform: uppercase; letter-spacing: .3px; }}
/* discipline grid */
.dg {{ display: flex; gap: 5pt; margin: 5pt 0 10pt; }}
.db {{ border: 1px solid #eee; border-radius: 5pt; padding: 6pt 8pt; flex: 1; }}
.db .dt {{ font-weight: 700; font-size: 8pt; margin-bottom: 4pt; }}
.db .stats {{ gap: 3pt; margin: 0; }}
.db .card {{ padding: 4pt 4pt; min-width: 0; background: #f8f8fc; border: none; }}
/* charts */
.ctitle {{
    font-size: 7.5pt; font-weight: 700; color: #5B6EF5;
    text-transform: uppercase; letter-spacing: .4px; margin-bottom: 2pt;
}}
.chart-table {{ width: 100%; border-collapse: collapse; margin-bottom: 4pt; }}
.chart-table td svg {{ width: 100% !important; height: auto !important; }}
/* tables */
table.t {{ width: 100%; border-collapse: collapse; font-size: 7.8pt; margin-bottom: 5pt; }}
table.t th {{
    background: #f0f1f8; padding: 4pt 5pt; text-align: left;
    font-size: 6.5pt; text-transform: uppercase; letter-spacing: .3px; color: #6b6b78;
}}
table.t td {{ padding: 4pt 5pt; border-bottom: 1px solid #f5f5f8; }}
table.t colgroup col {{ overflow: hidden; }}
@media print {{
    body {{ padding: 0; }}
    h2 {{ page-break-after: avoid; }}
    table.t {{ page-break-inside: avoid; }}
    .chart-table tr {{ page-break-inside: avoid; }}
}}
</style>
</head>
<body>

<h1>🏊‍♂️🚴‍♂️🏃‍♂️ Training Dashboard</h1>
<p class="updated">Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} UTC</p>

<h2>Race Targets</h2>
<table class="t">
  <tr><th>Race</th><th>Date</th><th>Countdown</th><th>Targets</th></tr>
  {race_rows}
</table>

<h2>Last 30 Days — Overview</h2>
<div class="stats">
  <div class="card"><div class="num">{total_sessions}</div><div class="lbl">Sessions</div></div>
  <div class="card"><div class="num">{total_hours}h</div><div class="lbl">Volume</div></div>
  <div class="card"><div class="num">{avg_load}</div><div class="lbl">Avg Load</div></div>
  <div class="card"><div class="num">{avg_sleep}h</div><div class="lbl">Avg Sleep</div></div>
  <div class="card"><div class="num">{avg_bb}</div><div class="lbl">Body Battery</div></div>
</div>
<div class="dg">
  <div class="db" style="border-top:2.5pt solid {PALETTE['swimming']}">
    <div class="dt">🏊 Swimming ({swim_sessions})</div>
    <div class="stats">
      <div class="card"><div class="num">{swim_total}</div><div class="lbl">Total</div></div>
      <div class="card"><div class="num">{swim_avg_dist}</div><div class="lbl">Avg</div></div>
      <div class="card"><div class="num">{swim_pace}</div><div class="lbl">Pace</div></div>
    </div>
  </div>
  <div class="db" style="border-top:2.5pt solid {PALETTE['running']}">
    <div class="dt">🏃 Running ({run_sessions})</div>
    <div class="stats">
      <div class="card"><div class="num">{run_total}</div><div class="lbl">Total</div></div>
      <div class="card"><div class="num">{run_avg_dist}</div><div class="lbl">Avg</div></div>
      <div class="card"><div class="num">{run_pace}</div><div class="lbl">Pace</div></div>
    </div>
  </div>
  <div class="db" style="border-top:2.5pt solid {PALETTE['cycling']}">
    <div class="dt">🚴 Cycling ({bike_sessions})</div>
    <div class="stats">
      <div class="card"><div class="num">{bike_total}</div><div class="lbl">Total</div></div>
      <div class="card"><div class="num">{bike_speed}</div><div class="lbl">Speed</div></div>
      <div class="card"><div class="num">{bike_watts}</div><div class="lbl">Power</div></div>
    </div>
  </div>
</div>

<h2>Trends</h2>
<table class="chart-table">{chart_rows}</table>

<h2>Session Compliance — Last 4 Weeks</h2>
<table class="t">
  <colgroup>
    <col style="width:11%"/><col style="width:13%"/>
    <col style="width:24%"/><col style="width:30%"/><col style="width:22%"/>
  </colgroup>
  <tr><th>Date</th><th>Discipline</th><th>Target</th><th>Actual</th><th>Status</th></tr>
  {comp_rows if comp_rows else '<tr><td colspan="5" style="color:#999;padding:8pt">No sessions matched in the last 4 weeks yet.</td></tr>'}
</table>

<h2>Recent Sessions</h2>
<table class="t">
  <tr><th>Date</th><th>Type</th><th>Distance</th><th>Duration</th><th>Pace</th><th>Benefit</th></tr>
  {recent_rows}
</table>

</body>
</html>""")
    print(f"Print HTML built at {OUT_PRINT} ({len(charts)} charts rendered as SVG)")

def main():
    df            = load_activities()
    plan          = load_plan()
    wellness      = load_wellness()
    plan_sessions = load_plan_sessions()
    manual_log    = load_manual_log()
    build_html(df, plan, wellness, plan_sessions, manual_log)
    build_print_html(df, plan, wellness, plan_sessions, manual_log)


if __name__ == "__main__":
    main()

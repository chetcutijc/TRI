"""
build_dashboard.py
Builds docs/index.html (interactive) and docs/dashboard.pdf (email-friendly)
from Garmin activity data, wellness data, training plan, and manual logs.
"""

import json
import re
import datetime as dt
import struct
import zlib
import base64
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots


def make_touch_icon_b64(canvas=180, bg="#5B6EF5"):
    """Generate a triathlon composite emoji home-screen icon as base64 PNG.
    Layout: 🏊 🚴 on top row, 🏃 centred below — on a rounded indigo square.
    Uses NotoColorEmoji.ttf (pre-installed on GitHub Actions ubuntu runners).
    Falls back to a solid-colour square if Pillow or the font is unavailable."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import io as _io

        FONT_PATH = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

        def render_emoji(emoji):
            font = ImageFont.truetype(FONT_PATH, 109)  # 109 = NotoColorEmoji native size
            tmp  = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
            draw = ImageDraw.Draw(tmp)
            bbox = draw.textbbox((0, 0), emoji, font=font, embedded_color=True)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            img  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.text((-bbox[0], -bbox[1]), emoji, font=font, embedded_color=True)
            return img

        bg_rgb = tuple(int(bg[i:i+2], 16) for i in (1, 3, 5)) + (255,)
        icon   = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        draw   = ImageDraw.Draw(icon)
        draw.rounded_rectangle([0, 0, canvas-1, canvas-1],
                                radius=canvas // 5, fill=bg_rgb)

        emojis  = ["🏊", "🚴", "🏃"]
        resized = [render_emoji(e).resize((68, 68), Image.LANCZOS) for e in emojis]

        gap   = 6
        top_y = 10
        x0    = (canvas - (68 * 2 + gap)) // 2
        icon.paste(resized[0], (x0,            top_y), resized[0])
        icon.paste(resized[1], (x0 + 68 + gap, top_y), resized[1])
        icon.paste(resized[2], ((canvas - 68) // 2, top_y + 68 + 6), resized[2])

        buf = _io.BytesIO()
        icon.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    except Exception as e:
        print(f"Triathlon icon generation failed ({e}), falling back to solid colour")
        r, g, b_ = 91, 110, 245
        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        sig  = b"\x89PNG\r\n\x1a\n"
        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", canvas, canvas, 8, 2, 0, 0, 0))
        raw  = b"".join(b"\x00" + bytes([r, g, b_]) * canvas for _ in range(canvas))
        idat = chunk(b"IDAT", zlib.compress(raw, 9))
        iend = chunk(b"IEND", b"")
        return base64.b64encode(b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend).decode()


# Pre-computed once at module load
TOUCH_ICON_B64 = make_touch_icon_b64()

# ── File paths ─────────────────────────────────────────────────────────────
DATA_FILE       = Path("data/activities.json")
WELLNESS_FILE   = Path("data/wellness.json")
PLAN_FILE       = Path("data/plan.json")
PLAN_SESSIONS_FILE = Path("data/plan_sessions.json")
PLAN_FULL_FILE     = Path("data/plan_full.json")
COACHING_FILE      = Path("data/ai_coaching.json")
MANUAL_LOG_FILE = Path("data/manual_log.json")
OUT_HTML        = Path("docs/index.html")
OUT_PDF         = Path("docs/dashboard.pdf")

# ── Race targets ────────────────────────────────────────────────────────────
# Races live in data/races.json so you can add/edit them without touching code.
RACES_FILE = Path("data/races.json")
ATHLETE_NOTES_FILE = Path("data/athlete_notes.json")


def load_races():
    """Load races from data/races.json, sorted soonest-first.
    Falls back to an empty list if the file is missing or malformed."""
    if not RACES_FILE.exists():
        print("WARNING: data/races.json not found — no races will be shown.")
        return []
    try:
        raw = json.loads(RACES_FILE.read_text())
    except Exception as e:
        print(f"WARNING: could not parse races.json ({e}) — no races will be shown.")
        return []

    races = []
    for r in raw:
        try:
            r = dict(r)
            r["date"] = dt.date.fromisoformat(r["date"])
            r.setdefault("emoji", "🏁")
            r.setdefault("note", "")
            r.setdefault("targets", {})
            races.append(r)
        except Exception as e:
            print(f"WARNING: skipping malformed race entry {r!r}: {e}")
    return sorted(races, key=lambda x: x["date"])


RACES = load_races()

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


def load_plan_full():
    return json.loads(PLAN_FULL_FILE.read_text()) if PLAN_FULL_FILE.exists() else []


def load_coaching():
    return json.loads(COACHING_FILE.read_text()) if COACHING_FILE.exists() else {}


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
        lo = fmt_pace(ps["pace_low_sec_km"]).replace("/km", "")
        hi = fmt_pace(ps["pace_high_sec_km"]).replace("/km", "")
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
        if right_label.startswith("Power") and "bike_power_w" in t:
            fig.add_annotation(x=race_dt, y=t["bike_power_w"], yref="y2",
                                text=f"{race['emoji']} {t['bike_power_w']}W",
                                showarrow=False, font=dict(size=9, color="#FF7A59"),
                                bgcolor="white", bordercolor="#FF7A59", borderwidth=1)
        elif right_label.startswith("Speed") and "bike_speed_kmh" in t:
            fig.add_annotation(x=race_dt, y=t["bike_speed_kmh"], yref="y2",
                                text=f"{race['emoji']} {t['bike_speed_kmh']}km/h",
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


def race_manager_html():
    """Add/remove races directly from the website.

    Writes to data/races.json via the GitHub Contents API using the same
    browser-stored token as the 'Apply to Dashboard' button, then triggers
    a rebuild so the dashboard reflects the change.
    """
    existing = [
        {"name": r["name"], "date": r["date"].isoformat(), "emoji": r.get("emoji", "🏁")}
        for r in RACES
    ]

    return f"""
<div style="margin:10px 0 4px">
    <button class="btn" onclick="toggleRaceForm()"
        style="background:#5B6EF5;font-size:.8em">＋ Add / Manage Races</button>
    <span id="race-status" style="font-size:.76em;color:#9a9aaa;margin-left:10px"></span>
</div>

<div id="race-form" style="display:none;background:#fff;border-radius:12px;
     padding:16px 18px;box-shadow:0 1px 3px rgba(20,20,40,.06);margin-bottom:14px">

  <div style="font-size:.9em;font-weight:700;margin-bottom:10px">Add a race</div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px">
      <label style="font-size:.75em;color:#6b6b78">Name
          <input id="rf-name" type="text" placeholder="Malta Half Marathon"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
      <label style="font-size:.75em;color:#6b6b78">Date
          <input id="rf-date" type="date"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
      <label style="font-size:.75em;color:#6b6b78">Emoji
          <input id="rf-emoji" type="text" value="🏁" maxlength="8"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
      <label style="font-size:.75em;color:#6b6b78">Note
          <input id="rf-note" type="text" placeholder="Tune-up race"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
  </div>

  <div style="font-size:.75em;font-weight:600;color:#6b6b78;margin:12px 0 6px">
      Distance — quick presets</div>
  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
      <button type="button" class="filter-btn" onclick="preset('5k')">5K</button>
      <button type="button" class="filter-btn" onclick="preset('10k')">10K</button>
      <button type="button" class="filter-btn" onclick="preset('half')">Half Marathon</button>
      <button type="button" class="filter-btn" onclick="preset('full')">Marathon</button>
      <button type="button" class="filter-btn" onclick="preset('sprint')">Sprint Tri</button>
      <button type="button" class="filter-btn" onclick="preset('olympic')">Olympic Tri</button>
      <button type="button" class="filter-btn" onclick="preset('70.3')">Ironman 70.3</button>
      <button type="button" class="filter-btn" onclick="preset('140.6')">Full Ironman</button>
      <button type="button" class="filter-btn" onclick="preset('clear')">Clear</button>
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px">
      <label style="font-size:.75em;color:#6b6b78">Swim (m)
          <input id="rf-dswim" type="number" step="any" placeholder="3800"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
      <label style="font-size:.75em;color:#6b6b78">Bike (km)
          <input id="rf-dbike" type="number" step="any" placeholder="180"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
      <label style="font-size:.75em;color:#6b6b78">Run (km)
          <input id="rf-drun" type="number" step="any" placeholder="42.2"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
  </div>

  <div style="font-size:.75em;font-weight:600;color:#6b6b78;margin:12px 0 6px">
      Targets (optional — leave blank if not relevant)</div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px">
      <label style="font-size:.75em;color:#6b6b78">Run pace (mm:ss /km)
          <input id="rf-run" type="text" placeholder="5:45"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
      <label style="font-size:.75em;color:#6b6b78">Bike power (W)
          <input id="rf-bike" type="number" placeholder="190"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
      <label style="font-size:.75em;color:#6b6b78">Bike speed (km/h)
          <input id="rf-bikespeed" type="number" step="any" placeholder="32"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
      <label style="font-size:.75em;color:#6b6b78">Swim pace (mm:ss /100m)
          <input id="rf-swim" type="text" placeholder="1:50"
              style="width:100%;padding:7px;border:1px solid #e3e3ea;border-radius:6px;font-size:1.05em"></label>
  </div>
  <p style="font-size:.7em;color:#bbb;margin-top:4px">
      Speed enables a target finish-time estimate for the bike leg — power alone can't derive a time.
  </p>

  <div style="margin-top:14px">
      <button class="btn" onclick="saveRace()" style="background:#00C2A8;font-size:.8em">Save Race</button>
      <button class="btn" onclick="toggleRaceForm()"
          style="background:#f0f1f8;color:#6b6b78;font-size:.8em">Cancel</button>
  </div>

  <div style="font-size:.85em;font-weight:700;margin:18px 0 8px;
              border-top:1px solid #f0f0f5;padding-top:14px">Current races</div>
  <div id="race-list"></div>

  <p style="font-size:.72em;color:#bbb;margin-top:12px">
      Saves to <code>data/races.json</code> and rebuilds the dashboard (~1–2 min).
      Your GitHub token needs <strong>Contents: Read and write</strong> in addition to
      Actions — if saving fails with a 403, update the token permissions
      <a href="https://github.com/settings/tokens?type=beta" target="_blank"
         style="color:#5B6EF5">here</a>.
  </p>
</div>

<script>
var EXISTING_RACES = {json.dumps(existing)};

function toggleRaceForm() {{
    var f = document.getElementById("race-form");
    f.style.display = (f.style.display === "none") ? "" : "none";
    if (f.style.display === "") renderRaceList();
}}

function renderRaceList() {{
    var el = document.getElementById("race-list");
    if (!EXISTING_RACES.length) {{ el.innerHTML = "<p style='font-size:.8em;color:#9a9aaa'>None yet.</p>"; return; }}
    el.innerHTML = EXISTING_RACES.map(function(r, i) {{
        return "<div style='display:flex;justify-content:space-between;align-items:center;" +
               "padding:6px 0;border-bottom:1px solid #f5f5f8;font-size:.82em'>" +
               "<span>" + r.emoji + " <strong>" + r.name + "</strong> " +
               "<span style='color:#9a9aaa'>" + r.date + "</span></span>" +
               "<button onclick='deleteRace(" + i + ")' style='background:none;border:none;" +
               "color:#FF7A59;cursor:pointer;font-size:1em;font-weight:600'>Remove</button></div>";
    }}).join("");
}}

var RACE_PRESETS = {{
    "5k":      {{swim:null, bike:null, run:5}},
    "10k":     {{swim:null, bike:null, run:10}},
    "half":    {{swim:null, bike:null, run:21.1}},
    "full":    {{swim:null, bike:null, run:42.2}},
    "sprint":  {{swim:750,  bike:20,   run:5}},
    "olympic": {{swim:1500, bike:40,   run:10}},
    "70.3":    {{swim:1900, bike:90,   run:21.1}},
    "140.6":   {{swim:3800, bike:180,  run:42.2}},
    "clear":   {{swim:null, bike:null, run:null}}
}};

function preset(key) {{
    var p = RACE_PRESETS[key];
    if (!p) return;
    document.getElementById("rf-dswim").value = p.swim === null ? "" : p.swim;
    document.getElementById("rf-dbike").value = p.bike === null ? "" : p.bike;
    document.getElementById("rf-drun").value  = p.run  === null ? "" : p.run;
}}

function paceToSec(v) {{
    if (!v) return null;
    var m = String(v).trim().match(/^([0-9]{{1,2}}):([0-9]{{2}})$/);
    if (!m) return null;
    return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}}

function b64encodeUtf8(str) {{
    var bytes = new TextEncoder().encode(str);
    var bin = "";
    bytes.forEach(function(b) {{ bin += String.fromCharCode(b); }});
    return btoa(bin);
}}
function b64decodeUtf8(b64) {{
    var bin = atob(b64.split(String.fromCharCode(10)).join(""));
    var bytes = Uint8Array.from(bin, function(c) {{ return c.charCodeAt(0); }});
    return new TextDecoder().decode(bytes);
}}

function setRaceStatus(msg, colour) {{
    var s = document.getElementById("race-status");
    s.textContent = msg;
    s.style.color = colour || "#9a9aaa";
}}

// Read races.json -> mutate -> write back -> trigger rebuild
function updateRacesFile(mutator, successMsg) {{
    var token = getGhToken();
    if (!token) {{ setRaceStatus("No token provided.", "#FF7A59"); return; }}

    var url = "https://api.github.com/repos/" + GH_OWNER + "/" + GH_REPO + "/contents/data/races.json";
    var headers = {{
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }};

    setRaceStatus("Saving...");

    fetch(url, {{ headers: headers }})
      .then(function(res) {{
          if (!res.ok) throw new Error("Could not read races.json (status " + res.status + ")");
          return res.json();
      }})
      .then(function(file) {{
          var races = JSON.parse(b64decodeUtf8(file.content));
          var updated = mutator(races);
          if (updated === null) return null;
          updated.sort(function(a, b) {{ return a.date < b.date ? -1 : 1; }});
          return fetch(url, {{
              method: "PUT",
              headers: headers,
              body: JSON.stringify({{
                  message: "Update races via dashboard",
                  content: b64encodeUtf8(JSON.stringify(updated, null, 2)),
                  sha: file.sha
              }})
          }});
      }})
      .then(function(res) {{
          if (res === null) return;
          if (!res.ok) {{
              return res.json().then(function(b) {{
                  throw new Error(res.status + ": " + (b.message || "write failed"));
              }});
          }}
          setRaceStatus(successMsg + " Rebuilding dashboard...", "#00C2A8");
          // kick off a rebuild so the change shows up
          return fetch("https://api.github.com/repos/" + GH_OWNER + "/" + GH_REPO +
                       "/actions/workflows/sync.yml/dispatches", {{
              method: "POST", headers: headers, body: JSON.stringify({{ ref: "main" }})
          }}).then(function() {{
              setRaceStatus(successMsg + " Dashboard rebuilding — refresh in ~2 min.", "#00C2A8");
          }});
      }})
      .catch(function(err) {{
          setRaceStatus("\u274c " + err.message, "#FF7A59");
          console.error(err);
      }});
}}

function saveRace() {{
    var name = document.getElementById("rf-name").value.trim();
    var date = document.getElementById("rf-date").value;
    if (!name || !date) {{ setRaceStatus("Name and date are required.", "#FF7A59"); return; }}

    var targets = {{}};
    var run  = paceToSec(document.getElementById("rf-run").value);
    var swim = paceToSec(document.getElementById("rf-swim").value);
    var bike = parseInt(document.getElementById("rf-bike").value, 10);
    var bikeSpeed = parseFloat(document.getElementById("rf-bikespeed").value);
    if (run)  targets.run_pace_sec_km    = run;
    if (swim) targets.swim_pace_100m_sec = swim;
    if (!isNaN(bike)) targets.bike_power_w = bike;
    if (!isNaN(bikeSpeed)) targets.bike_speed_kmh = bikeSpeed;

    var distances = {{}};
    var dSwim = parseFloat(document.getElementById("rf-dswim").value);
    var dBike = parseFloat(document.getElementById("rf-dbike").value);
    var dRun  = parseFloat(document.getElementById("rf-drun").value);
    if (!isNaN(dSwim)) distances.swim_m  = dSwim;
    if (!isNaN(dBike)) distances.bike_km = dBike;
    if (!isNaN(dRun))  distances.run_km  = dRun;

    var race = {{
        name: name,
        emoji: document.getElementById("rf-emoji").value.trim() || "🏁",
        date: date,
        note: document.getElementById("rf-note").value.trim(),
        distances: distances,
        targets: targets
    }};

    updateRacesFile(function(races) {{ races.push(race); return races; }},
                    "\u2705 Added " + name + ".");
}}

function deleteRace(idx) {{
    var r = EXISTING_RACES[idx];
    if (!r) return;
    if (!confirm("Remove " + r.name + " (" + r.date + ")?")) return;
    updateRacesFile(function(races) {{
        return races.filter(function(x) {{
            return !(x.name === r.name && x.date === r.date);
        }});
    }}, "\u2705 Removed " + r.name + ".");
}}
</script>"""


def fmt_hms(total_seconds):
    """Seconds -> H:MM:SS (or MM:SS if under an hour)."""
    if total_seconds is None or total_seconds <= 0:
        return None
    total_seconds = round(total_seconds)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace_general(sec_per_km):
    """Format an overall pace (sec/km) as M:SS/km, or H:MM/km if very slow (e.g. swim-heavy)."""
    if not sec_per_km or sec_per_km <= 0:
        return None
    sec_per_km = round(sec_per_km)
    m, s = divmod(sec_per_km, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}/km"
    return f"{m}:{s:02d}/km"


def race_total_distance_km(race):
    """Sum of all set distances for a race, in km."""
    d = race.get("distances", {}) or {}
    total = 0
    have_any = False
    if d.get("swim_m"):
        total += d["swim_m"] / 1000
        have_any = True
    if d.get("bike_km"):
        total += d["bike_km"]
        have_any = True
    if d.get("run_km"):
        total += d["run_km"]
        have_any = True
    return total if have_any else None


def compute_race_prediction(race, df):
    """
    Rough finish-time estimate per discipline, based on your average pace/power/speed
    over the last 4 weeks of actual Garmin activities, applied to the race's distances.

    This is a simple linear projection (recent pace x race distance) per leg — it does
    NOT account for endurance fade over longer distances or transitions, so treat it as
    a ballpark, not a race predictor.

    Returns: {"total_sec": float, "total": "H:MM:SS", "parts": [(emoji, label, pace_str, time_str), ...]}
    or None if there's not enough data to estimate anything.
    """
    dist = race.get("distances", {}) or {}
    if not dist or df is None or df.empty:
        return None

    recent = df[df["start"] >= (dt.datetime.now() - dt.timedelta(weeks=4))]
    if recent.empty:
        return None

    total_sec = 0
    parts = []
    have_any = False

    if dist.get("run_km"):
        ru = recent[recent["type"] == "running"]
        paces = ru["avg_pace"].dropna().apply(speed_to_pace)  # sec/km
        paces = paces[paces > 0]
        if not paces.empty:
            sec_km = paces.mean()
            run_sec = sec_km * dist["run_km"]
            total_sec += run_sec
            have_any = True
            parts.append(("🏃", "Run", fmt_pace(sec_km), fmt_hms(run_sec)))

    if dist.get("bike_km"):
        cy = recent[recent["type"] == "cycling"]
        speeds = cy["avg_pace"].dropna()  # m/s
        speeds = speeds[speeds > 0.1]
        if not speeds.empty:
            kmh = speeds.mean() * 3.6
            bike_sec = (dist["bike_km"] / kmh) * 3600
            total_sec += bike_sec
            have_any = True
            parts.append(("🚴", "Bike", f"{kmh:.1f}km/h", fmt_hms(bike_sec)))

    if dist.get("swim_m"):
        sw = recent[recent["type"] == "swimming"]
        paces = sw["avg_pace"].dropna().apply(speed_to_pace)  # sec/km -> /100m
        paces = paces[paces > 0]
        if not paces.empty:
            sec_100m = paces.mean() / 10
            swim_sec = sec_100m * (dist["swim_m"] / 100)
            total_sec += swim_sec
            have_any = True
            m, s = divmod(round(sec_100m), 60)
            parts.append(("🏊", "Swim", f"{m}:{s:02d}/100m", fmt_hms(swim_sec)))

    if not have_any:
        return None

    return {"total_sec": total_sec, "total": fmt_hms(total_sec), "parts": parts}


def compute_race_target_time(race):
    """
    Target finish time per discipline, derived from targets x distances.
    Bike only contributes a time if a target SPEED is set — power alone can't
    derive a duration.

    Returns: {"total_sec": float, "total": "H:MM:SS", "parts": [(emoji, label, pace_str, time_str), ...]}
    or None if no leg has both a distance and a usable target.
    """
    dist = race.get("distances", {}) or {}
    tgt  = race.get("targets", {}) or {}
    if not dist:
        return None

    total_sec = 0
    parts = []
    have_any = False

    if dist.get("run_km") and tgt.get("run_pace_sec_km"):
        sec_km = tgt["run_pace_sec_km"]
        run_sec = sec_km * dist["run_km"]
        total_sec += run_sec
        have_any = True
        parts.append(("🏃", "Run", fmt_pace(sec_km), fmt_hms(run_sec)))

    if dist.get("bike_km") and tgt.get("bike_speed_kmh"):
        kmh = tgt["bike_speed_kmh"]
        bike_sec = (dist["bike_km"] / kmh) * 3600
        total_sec += bike_sec
        have_any = True
        parts.append(("🚴", "Bike", f"{kmh}km/h", fmt_hms(bike_sec)))
    elif dist.get("bike_km") and tgt.get("bike_power_w"):
        # power alone can't derive a time — show the power target with no time
        parts.append(("🚴", "Bike", f"{tgt['bike_power_w']}W", "—"))

    if dist.get("swim_m") and tgt.get("swim_pace_100m_sec"):
        sec_100m = tgt["swim_pace_100m_sec"]
        swim_sec = sec_100m * (dist["swim_m"] / 100)
        total_sec += swim_sec
        have_any = True
        m, s = divmod(round(sec_100m), 60)
        parts.append(("🏊", "Swim", f"{m}:{s:02d}/100m", fmt_hms(swim_sec)))

    if not have_any:
        return None

    return {"total_sec": total_sec, "total": fmt_hms(total_sec), "parts": parts}


def race_cards_html(df=None):
    cards = ""

    for r in RACES:
        days = days_until(r["date"])
        t = r["targets"]
        targets_str = []
        if "run_pace_sec_km" in t:
            p = t["run_pace_sec_km"]
            targets_str.append(f"Run: {p//60}:{p%60:02d}/km")
        if "bike_power_w" in t and "bike_speed_kmh" in t:
            targets_str.append(f"Bike: {t['bike_power_w']}W ({t['bike_speed_kmh']}km/h)")
        elif "bike_power_w" in t:
            targets_str.append(f"Bike: {t['bike_power_w']}W")
        elif "bike_speed_kmh" in t:
            targets_str.append(f"Bike: {t['bike_speed_kmh']}km/h")
        if "swim_pace_100m_sec" in t:
            p = t["swim_pace_100m_sec"]
            targets_str.append(f"Swim: {p//60}:{p%60:02d}/100m")
        targets_line = " · ".join(targets_str)

        # Distances (optional) — shown as a separate line above the pace/power targets
        d = r.get("distances", {}) or {}
        dist_str = []
        if d.get("swim_m"):
            sm = d["swim_m"]
            dist_str.append(f"🏊 {sm/1000:.1f}km" if sm >= 1000 else f"🏊 {sm}m")
        if d.get("bike_km"):
            dist_str.append(f"🚴 {d['bike_km']}km")
        if d.get("run_km"):
            dist_str.append(f"🏃 {d['run_km']}km")
        dist_line = " · ".join(dist_str)

        # Target and predicted finish times + overall pace
        target     = compute_race_target_time(r)
        prediction = compute_race_prediction(r, df) if df is not None else None

        is_multi = sum(1 for k in ("swim_m","bike_km","run_km") if (r.get("distances") or {}).get(k)) > 1

        time_html = ""
        if target or prediction:
            if is_multi:
                # Per-discipline breakdown table: one row per leg, target vs predicted side by side
                leg_order = ["Swim", "Bike", "Run"]
                tgt_by_leg  = {label: (pace, tm) for _, label, pace, tm in (target["parts"] if target else [])}
                pred_by_leg = {label: (pace, tm) for _, label, pace, tm in (prediction["parts"] if prediction else [])}
                emoji_by_leg = {"Swim": "🏊", "Bike": "🚴", "Run": "🏃"}

                leg_rows = ""
                for label in leg_order:
                    if label not in tgt_by_leg and label not in pred_by_leg:
                        continue
                    t_pace, t_time = tgt_by_leg.get(label, ("—", "—"))
                    p_pace, p_time = pred_by_leg.get(label, ("—", "—"))
                    leg_rows += f"""<tr>
                        <td style="font-size:.78em;font-weight:600;color:#555;padding:3px 4px">{emoji_by_leg[label]} {label}</td>
                        <td style="font-size:.78em;color:#5B6EF5;text-align:right;padding:3px 4px">{t_time} <span style="color:#9a9aaa">({t_pace})</span></td>
                        <td style="font-size:.78em;color:#00C2A8;text-align:right;padding:3px 4px">{p_time} <span style="color:#9a9aaa">({p_pace})</span></td>
                    </tr>"""

                total_row = ""
                if target or prediction:
                    t_total = target["total"] if target else "—"
                    p_total = prediction["total"] if prediction else "—"
                    total_row = f"""<tr style="border-top:1px solid #e3e3ea">
                        <td style="font-size:.8em;font-weight:800;color:#1a1a22;padding:5px 4px">Total</td>
                        <td style="font-size:.85em;font-weight:800;color:#5B6EF5;text-align:right;padding:5px 4px">🎯 {t_total}</td>
                        <td style="font-size:.85em;font-weight:800;color:#00C2A8;text-align:right;padding:5px 4px">📈 ~{p_total}</td>
                    </tr>"""

                time_html = f"""
                <div style="background:#f8f8fc;border-radius:8px;padding:8px 10px;margin:6px 0">
                    <table style="width:100%;border-collapse:collapse">
                        <tr>
                            <td></td>
                            <td style="font-size:.68em;color:#5B6EF5;text-align:right;font-weight:700;text-transform:uppercase">Target</td>
                            <td style="font-size:.68em;color:#00C2A8;text-align:right;font-weight:700;text-transform:uppercase">Predicted</td>
                        </tr>
                        {leg_rows}
                        {total_row}
                    </table>
                    <p style="font-size:.68em;color:#bbb;margin:4px 0 0">
                        Predicted uses your avg pace/speed per discipline over the last 4 weeks — a rough
                        linear estimate, not a race predictor (ignores fatigue over distance, transitions).
                    </p>
                </div>"""
            else:
                # Single-discipline race: simple two-row time + pace box
                rows = []
                if target:
                    _, _, pace, _ = target["parts"][0]
                    rows.append(
                        f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
                        f'<span style="color:#5B6EF5;font-weight:700;font-size:.85em">🎯 Target</span>'
                        f'<span style="color:#5B6EF5;font-weight:700;font-size:.9em">{target["total"]} · {pace}</span>'
                        f'</div>'
                    )
                if prediction:
                    _, _, pace, _ = prediction["parts"][0]
                    rows.append(
                        f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
                        f'<span style="color:#00C2A8;font-weight:700;font-size:.85em">📈 Predicted</span>'
                        f'<span style="color:#00C2A8;font-weight:700;font-size:.9em">~{prediction["total"]} · {pace}</span>'
                        f'</div>'
                    )
                time_html = (
                    '<div style="background:#f8f8fc;border-radius:8px;padding:8px 10px;'
                    'margin:6px 0;display:flex;flex-direction:column;gap:4px">'
                    + "".join(rows) + "</div>"
                )

        color = "#00C2A8" if days > 90 else "#FFC75A" if days > 30 else "#FF7A59"

        cards += f"""
        <div class="race-card">
            <div class="race-emoji">{r['emoji']}</div>
            <div class="race-name">{r['name']}</div>
            <div class="race-date">{r['date'].strftime('%b %d, %Y')}</div>
            <div class="race-days" style="color:{color}">
                {'In ' + str(days) + ' days' if days > 0 else 'RACE DAY!' if days == 0 else str(abs(days)) + ' days ago'}
            </div>
            {f'<div class="race-dist">{dist_line}</div>' if dist_line else ''}
            {time_html}
            <div class="race-targets">{targets_line}</div>
            <div class="race-note">{r['note']}</div>
        </div>"""
    return cards




# ── AI Coaching section HTML ──────────────────────────────────────────────────
def athlete_notes_manager_html():
    """
    Free-text context box: 'been sick this week', 'travelling Oct 1-4', etc.
    Writes to data/athlete_notes.json via the GitHub Contents API using the
    same browser-stored token as the Race Manager, then triggers a fresh
    Sync Now so AI Coaching picks it up on its next run.
    """
    notes = []
    if ATHLETE_NOTES_FILE.exists():
        try:
            notes = json.loads(ATHLETE_NOTES_FILE.read_text()).get("notes", [])
        except Exception:
            notes = []

    cutoff = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    recent = [n for n in notes if n.get("date", "") >= cutoff]
    recent_sorted = sorted(recent, key=lambda n: n.get("date", ""), reverse=True)

    notes_html = ""
    if recent_sorted:
        rows = "".join(f"""<div style="display:flex;justify-content:space-between;
                align-items:baseline;padding:6px 0;border-bottom:1px solid #f5f5f8;font-size:.82em">
            <span><strong style="color:#9a9aaa">{n.get('date','')}</strong> — {n.get('text','')}</span>
        </div>""" for n in recent_sorted)
        notes_html = f"""<div style="margin-top:10px">
            <div style="font-size:.75em;font-weight:600;color:#6b6b78;margin-bottom:4px">
                Active context (last 14 days — auto-expires, feeds into AI Coaching):
            </div>
            {rows}
        </div>"""
    else:
        notes_html = '<p class="subtext" style="margin-top:8px">No active notes. Anything you add here is used by AI Coaching for 14 days.</p>'

    return f"""
<div style="background:#fff;border-radius:14px;padding:18px 20px;
            box-shadow:0 1px 3px rgba(20,20,40,.06);margin-bottom:16px">
    <div style="font-size:.92em;font-weight:800;margin-bottom:4px">🗣️ Tell Your Coach</div>
    <p class="subtext" style="margin:0 0 10px">
        Add context Claude can't see in your Garmin data — illness, travel, life stress,
        an injury niggle. This gets factored into the next AI Coaching run.
    </p>
    <textarea id="an-text" rows="2" placeholder="e.g. Been sick since Tuesday, low energy. Or: travelling Oct 1-4, only hotel gym access."
        style="width:100%;padding:9px;border:1px solid #e3e3ea;border-radius:8px;
               font-size:.95em;font-family:inherit;resize:vertical"></textarea>
    <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button class="btn" onclick="saveAthleteNote()" style="background:#5B6EF5;font-size:.8em">
            Save & Trigger Sync
        </button>
        <span id="note-status" style="font-size:.74em;color:#9a9aaa"></span>
    </div>
    {notes_html}
</div>

<script>
function saveAthleteNote() {{
    var text = document.getElementById("an-text").value.trim();
    if (!text) {{ return; }}
    var status = document.getElementById("note-status");
    var token = getGhToken();
    if (!token) {{ status.textContent = "No token provided."; status.style.color = "#FF7A59"; return; }}

    var url = "https://api.github.com/repos/" + GH_OWNER + "/" + GH_REPO + "/contents/data/athlete_notes.json";
    var headers = {{
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }};

    status.textContent = "Saving...";
    status.style.color = "#9a9aaa";

    fetch(url, {{ headers: headers }})
      .then(function(res) {{
          if (!res.ok) throw new Error("Could not read athlete_notes.json (status " + res.status + ")");
          return res.json();
      }})
      .then(function(file) {{
          var data = JSON.parse(b64decodeUtf8(file.content));
          var today = new Date().toISOString().slice(0, 10);
          data.notes = data.notes || [];
          data.notes.push({{ date: today, text: text }});
          return fetch(url, {{
              method: "PUT",
              headers: headers,
              body: JSON.stringify({{
                  message: "Add athlete note via dashboard",
                  content: b64encodeUtf8(JSON.stringify(data, null, 2)),
                  sha: file.sha
              }})
          }});
      }})
      .then(function(res) {{
          if (!res.ok) {{
              return res.json().then(function(b) {{
                  throw new Error(res.status + ": " + (b.message || "write failed"));
              }});
          }}
          document.getElementById("an-text").value = "";
          status.textContent = "\\u2705 Saved. Triggering Sync Now so AI Coaching picks it up...";
          status.style.color = "#00C2A8";
          return fetch("https://api.github.com/repos/" + GH_OWNER + "/" + GH_REPO +
                       "/actions/workflows/sync.yml/dispatches", {{
              method: "POST", headers: headers, body: JSON.stringify({{ ref: "main" }})
          }});
      }})
      .then(function() {{
          status.textContent = "\\u2705 Saved and Sync Now triggered \\u2014 refresh in a few minutes.";
          status.style.color = "#00C2A8";
      }})
      .catch(function(err) {{
          status.textContent = "\\u274c " + err.message;
          status.style.color = "#FF7A59";
          console.error(err);
      }});
}}
</script>
"""


def coaching_html(coaching):
    """Renders the AI coaching summary + suggestions card.
    Proposed changes themselves are shown in the unified Training Plan table
    below (highlighted rows), not duplicated here."""
    if not coaching or not coaching.get("summary"):
        return """<div style="background:#f8f8fc;border-radius:12px;padding:20px 24px;color:#9a9aaa;font-size:.88em">
            No AI coaching data yet — analysis runs on Saturday syncs.<br>
            Make sure <code>ANTHROPIC_API_KEY</code> is set in your repo secrets.
        </div>"""

    gen_at      = coaching.get("generated_at","")[:10]
    summary     = coaching.get("summary","")
    suggestions = coaching.get("suggestions", [])
    changes     = coaching.get("proposed_changes", [])

    sugg_html = "".join(f"""
        <div style="display:flex;gap:12px;margin-bottom:12px;align-items:flex-start">
            <span style="background:#5B6EF5;color:#fff;border-radius:50%;
                         width:22px;height:22px;display:flex;align-items:center;
                         justify-content:center;font-size:.72em;font-weight:800;
                         flex-shrink:0;margin-top:1px">{i+1}</span>
            <span style="font-size:.88em;line-height:1.5;color:#2c2c34">{s}</span>
        </div>""" for i, s in enumerate(suggestions) if s)

    if changes:
        changes_note = f"""<div style="background:#eef0fd;border-radius:8px;
            padding:12px 16px;font-size:.85em;color:#5B6EF5;margin-top:12px;
            display:flex;align-items:center;gap:8px">
            📋 <strong>{len(changes)} plan adjustment{'s' if len(changes)!=1 else ''} proposed</strong>
            — see highlighted rows in the Training Plan table below.
            Download the ICS from there, or run <strong>Apply AI Coaching</strong>
            in GitHub Actions to update the dashboard too.
        </div>"""
    else:
        changes_note = """<div style="background:#e8f9f5;border-radius:8px;
            padding:12px 16px;font-size:.85em;color:#00A888;margin-top:12px">
            ✅ No plan adjustments proposed — training looks on track for this period.
        </div>"""

    # Countdown chip per upcoming race — works with any number of races
    _palette = ["#FF7A59", "#5B6EF5", "#00C2A8", "#FFC75A", "#9B7DFF"]
    countdown_cards = ""
    for i, r in enumerate([x for x in RACES if days_until(x["date"]) >= 0][:4]):
        d = days_until(r["date"])
        countdown_cards += f"""<div style="text-align:center">
            <div style="font-size:1.1em;font-weight:800;color:{_palette[i % len(_palette)]}">{d}</div>
            <div style="font-size:.65em;color:#9a9aaa;text-transform:uppercase">days to {r['name'][:18]}</div>
        </div>"""
    if not countdown_cards:
        countdown_cards = '<div style="font-size:.75em;color:#9a9aaa">No upcoming races</div>'

    return f"""
    <div style="background:#fff;border-radius:14px;padding:20px 24px;
                box-shadow:0 1px 3px rgba(20,20,40,.06);margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;
                    align-items:flex-start;flex-wrap:wrap;gap:8px;margin-bottom:14px">
            <div>
                <div style="font-size:.95em;font-weight:700;color:#1a1a22">Weekly Assessment</div>
                <div style="font-size:.78em;color:#9a9aaa">Generated {gen_at} · Claude {coaching.get('weeks_analysed',4)}-week analysis</div>
            </div>
            <div style="display:flex;gap:16px">
                {countdown_cards}
            </div>
        </div>
        <p style="font-size:.88em;line-height:1.6;color:#2c2c34;
                  border-left:3px solid #5B6EF5;padding-left:12px;margin-bottom:16px">{summary}</p>
        <div style="font-size:.85em;font-weight:700;color:#1a1a22;margin-bottom:10px">
            💡 Coaching Suggestions
        </div>
        {sugg_html}
        {changes_note}
    </div>"""


# ── Plan viewer ───────────────────────────────────────────────────────────────
DISC_COLORS = {
    "swimming":          "#36C5F0",
    "cycling":           "#00C2A8",
    "running":           "#5B6EF5",
    "strength_training": "#FF7A59",
    "rest":              "#9B7DFF",
    "race":              "#FF3B30",
    "other":             "#aaaaaa",
}
DISC_LABELS = {
    "swimming":          "🏊 Swim",
    "cycling":           "🚴 Bike",
    "running":           "🏃 Run",
    "strength_training": "💪 Strength",
    "rest":              "😴 Rest",
    "race":              "🏁 Race Day",
    "other":             "📋 Other",
}
CHANGE_COLORS = {
    "increase": ("#00C2A8", "#e8f9f5"),
    "decrease": ("#FFC75A", "#fff8e0"),
    "replace":  ("#5B6EF5", "#eef0fd"),
    "skip":     ("#FF7A59", "#ffeae8"),
    "add":      ("#00C2A8", "#e8f9f5"),
}


def evaluate_adherence(change, df):
    """Did the athlete actually follow this AI suggestion?

    Returns (status_label, colour, detail_string).
    Compares the suggestion against real Garmin activities on that date (+/-1 day).
    """
    try:
        target = dt.date.fromisoformat(change.get("date", ""))
    except Exception:
        return ("?", "#aaa", "invalid date")

    if target > dt.date.today():
        return ("Upcoming", "#9a9aaa", "not due yet")

    disc = change.get("discipline", "")
    ct   = change.get("change_type", "replace")

    if df is None or df.empty:
        return ("No data", "#aaa", "no activity data")

    window = df[
        (df["start"].dt.date >= target - dt.timedelta(days=1)) &
        (df["start"].dt.date <= target + dt.timedelta(days=1))
    ]
    same_disc = window[window["type"] == disc]

    # ── SKIP: success means the session did NOT happen ──
    if ct == "skip":
        if same_disc.empty:
            return ("Followed", "#00C2A8", "session correctly skipped")
        mins = round(same_disc["duration_min"].sum())
        return ("Not followed", "#FF7A59", f"{mins}min logged anyway")

    # ── ADD: success means an activity of that discipline appeared ──
    if ct == "add":
        if not same_disc.empty:
            mins = round(same_disc["duration_min"].sum())
            return ("Followed", "#00C2A8", f"{mins}min logged")
        return ("Not followed", "#FF7A59", "no session logged")

    # ── everything else needs an actual session to judge ──
    if same_disc.empty:
        return ("Missed", "#FF7A59", "no session logged")

    act_min = round(same_disc["duration_min"].sum())
    act_km  = round(same_disc["distance_km"].sum(), 1) if same_disc["distance_km"].notna().any() else None
    detail  = f"{act_min}min" + (f" / {act_km}km" if act_km else "")

    # Try to read the original planned duration out of the suggestion text
    m = re.search(r"(\d+)\s*min", str(change.get("current_session", "")))
    planned = int(m.group(1)) if m else None

    if ct == "decrease" and planned:
        if act_min <= planned * 0.95:
            return ("Followed", "#00C2A8", f"{detail} (below {planned}min)")
        return ("Not followed", "#FF7A59", f"{detail} (target was under {planned}min)")

    if ct == "increase" and planned:
        if act_min >= planned:
            return ("Followed", "#00C2A8", f"{detail} (met {planned}min)")
        return ("Partial", "#FFC75A", f"{detail} (target was {planned}min+)")

    # replace / unknown: we can confirm it happened but not verify the content
    return ("Completed", "#5B6EF5", detail)


def adherence_html(coaching, df):
    """Renders a history table of past AI suggestions and whether they were followed."""
    history = (coaching or {}).get("history", [])
    if not history:
        return ""

    # flatten most-recent-first, cap at 20 rows so it stays readable
    rows_data = []
    for round_ in reversed(history):
        for c in round_.get("changes", []):
            rows_data.append((round_.get("generated_at", "")[:10], c))
    rows_data = rows_data[:20]
    if not rows_data:
        return ""

    followed = sum(1 for _, c in rows_data
                   if evaluate_adherence(c, df)[0] in ("Followed", "Completed"))
    total_judged = sum(1 for _, c in rows_data
                       if evaluate_adherence(c, df)[0] not in ("Upcoming", "No data", "?"))
    pct = round(100 * followed / total_judged) if total_judged else 0
    badge = "#00C2A8" if pct >= 70 else "#FFC75A" if pct >= 40 else "#FF7A59"

    rows = ""
    for gen_date, c in rows_data:
        label, colour, detail = evaluate_adherence(c, df)
        ct = c.get("change_type", "")
        rows += f"""<tr>
            <td style="white-space:nowrap;color:#9a9aaa;font-size:.8em">{c.get('date','')}</td>
            <td style="font-size:.8em;font-weight:600">{c.get('discipline','').replace('_',' ').title()}</td>
            <td style="text-align:center">
                <span style="background:#f0f1f8;color:#6b6b78;border-radius:5px;
                    padding:1px 7px;font-size:.7em;font-weight:700">{ct.upper()}</span></td>
            <td style="font-size:.78em;color:#666;max-width:280px">{str(c.get('proposed_session',''))[:110]}</td>
            <td style="text-align:center;white-space:nowrap">
                <span style="background:{colour}22;color:{colour};border-radius:6px;
                    padding:2px 9px;font-size:.75em;font-weight:700">{label}</span></td>
            <td style="font-size:.74em;color:#9a9aaa">{detail}</td>
        </tr>"""

    return f"""
    <div style="display:flex;justify-content:space-between;align-items:center;
                flex-wrap:wrap;gap:8px;margin-bottom:10px">
        <p class="subtext" style="margin:0">
            Past AI suggestions checked against what you actually logged in Garmin.
        </p>
        <span style="background:{badge};color:#fff;border-radius:20px;
            padding:3px 14px;font-size:.78em;font-weight:700">
            {pct}% adherence ({followed}/{total_judged})</span>
    </div>
    <table class="table">
        <tr><th>Date</th><th>Discipline</th><th>Type</th>
            <th>Suggestion</th><th>Outcome</th><th>Actual</th></tr>
        {rows}
    </table>"""


def build_ics_content(changes, gen_at=""):
    """Build RFC 5545 compliant ICS text from AI proposed changes.

    Handles the three things that silently break Apple Calendar imports:
      1. Escaping reserved chars (backslash, semicolon, comma, newline)
      2. Mandatory DTSTAMP property on every VEVENT
      3. Line folding at 75 octets (long descriptions otherwise corrupt the file)
    """
    def esc(s):
        s = str(s or "")
        s = s.replace("\\", "\\\\")
        s = s.replace(";", "\\;")
        s = s.replace(",", "\\,")
        s = s.replace("\r\n", "\\n").replace("\n", "\\n")
        return s

    def fold(line):
        if len(line.encode("utf-8")) <= 75:
            return line
        out, cur = [], ""
        for ch in line:
            candidate = cur + ch
            limit = 75 if not out else 74   # continuation lines lose 1 char to the leading space
            if len(candidate.encode("utf-8")) > limit:
                out.append(cur)
                cur = ch
            else:
                cur = candidate
        if cur:
            out.append(cur)
        return "\r\n ".join(out)

    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Jean Triathlon AI Coaching//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for i, c in enumerate(changes):
        d    = str(c.get("date", "")).replace("-", "")
        disc = str(c.get("discipline", "")).replace("_", " ")
        prop = str(c.get("proposed_session", ""))
        summary = f"[AI ADJUSTED] {disc} \u2014 {prop[:60]}"
        desc    = f"{prop}\n\nReason: {c.get('reason','')}\n\nAI coaching {gen_at}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:ai-coaching-{d}-{i}@jean-tri",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{d}T070000",
            f"DTEND:{d}T090000",
            fold("SUMMARY:" + esc(summary)),
            fold("DESCRIPTION:" + esc(desc)),
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def status_strip_html(races, race_conflicts, coaching):
    """One-glance status bar: next race, conflicts, pending AI suggestions.
    Sits right at the top so the important stuff doesn't require scrolling."""
    chips = []

    upcoming = [r for r in races if days_until(r["date"]) >= 0]
    if upcoming:
        nxt = upcoming[0]
        d = days_until(nxt["date"])
        chips.append(
            f'<div class="status-chip" style="background:#eef0fd">'
            f'<span style="font-size:1.1em">{nxt["emoji"]}</span>'
            f'<span style="font-weight:700;color:#5B6EF5">{d}d</span>'
            f'<span style="color:#6b6b78;font-size:.82em">to {nxt["name"][:20]}</span>'
            f'</div>'
        )

    if race_conflicts:
        chips.append(
            f'<div class="status-chip" style="background:#fff5f4">'
            f'<span style="font-size:1.1em">\u26a0\ufe0f</span>'
            f'<span style="font-weight:700;color:#c0392b">{len(race_conflicts)}</span>'
            f'<span style="color:#6b6b78;font-size:.82em">pre-race conflict{"s" if len(race_conflicts)!=1 else ""}</span>'
            f'</div>'
        )

    pending = (coaching or {}).get("proposed_changes", [])
    if pending:
        chips.append(
            f'<div class="status-chip" style="background:#eef0fd">'
            f'<span style="font-size:1.1em">\U0001F916</span>'
            f'<span style="font-weight:700;color:#5B6EF5">{len(pending)}</span>'
            f'<span style="color:#6b6b78;font-size:.82em">AI suggestion{"s" if len(pending)!=1 else ""} pending</span>'
            f'</div>'
        )
    elif coaching and coaching.get("summary"):
        chips.append(
            f'<div class="status-chip" style="background:#e8f9f5">'
            f'<span style="font-size:1.1em">\u2705</span>'
            f'<span style="color:#00A888;font-size:.82em;font-weight:600">Training on track</span>'
            f'</div>'
        )

    if not chips:
        return ""

    return f'<div class="status-strip">{"".join(chips)}</div>'


def detect_race_conflicts(plan_full, races, days_before=2, max_easy_min=45):
    """
    Deterministic (no AI needed) sanity check: flags any session scheduled in
    the last `days_before` days before a race that isn't rest/short-easy.

    This catches things like a long bike ride the night before a marathon —
    a rule basic enough that it shouldn't need Claude's judgment, and should
    never silently exist in the plan.

    Returns a list of dicts: {race_name, race_date, session_date, discipline,
    summary, duration_min, days_before_race}
    """
    if not plan_full or not races:
        return []

    conflicts = []
    for r in races:
        race_date = r["date"]
        window_start = race_date - dt.timedelta(days=days_before)

        for s in plan_full:
            try:
                sdate = dt.date.fromisoformat(s["date"])
            except Exception:
                continue
            if not (window_start <= sdate <= race_date):  # <= includes race day itself
                continue
            if s.get("discipline") in ("rest", "race", None):
                continue
            # Skip the race's own entry on race day itself (e.g. a plan
            # session literally titled "*** RACE DAY: FULL MARATHON ***")
            # — that's the race, not a conflicting training session.
            summary_lower = (s.get("summary") or "").lower()
            if sdate == race_date and ("race day" in summary_lower or "***" in summary_lower):
                continue
            dur = s.get("duration_min") or 0
            # A short easy session (e.g. a shakeout jog) close to race day is fine.
            if dur and dur <= max_easy_min:
                continue
            conflicts.append({
                "race_name":        r["name"],
                "race_date":        race_date.isoformat(),
                "session_date":     s["date"],
                "discipline":       s.get("discipline", "?"),
                "summary":          s.get("summary", ""),
                "duration_min":     dur,
                "days_before_race": (race_date - sdate).days,
            })

    conflicts.sort(key=lambda c: (c["race_date"], c["session_date"]))
    return conflicts


def race_conflicts_html(conflicts):
    """Renders the pre-race conflict warning box, if any conflicts exist."""
    if not conflicts:
        return ""

    rows = "".join(f"""<tr>
        <td style="white-space:nowrap;font-size:.8em;color:#9a9aaa">{c['session_date']}</td>
        <td style="font-size:.8em;font-weight:700">{c['discipline'].replace('_',' ').title()}</td>
        <td style="font-size:.8em;color:#555">{c['summary']}</td>
        <td style="font-size:.8em;text-align:center">{c['duration_min']}min</td>
        <td style="font-size:.8em;color:#FF7A59;font-weight:600">
            {c['days_before_race']} day{'s' if c['days_before_race']!=1 else ''} before {c['race_name']}
        </td>
    </tr>""" for c in conflicts)

    return f"""
    <div style="background:#fff5f4;border:1px solid #ffd6d1;border-radius:12px;
                padding:16px 20px;margin-bottom:20px">
        <div style="font-size:.95em;font-weight:800;color:#c0392b;margin-bottom:4px">
            ⚠️ Pre-Race Conflicts Detected
        </div>
        <p style="font-size:.82em;color:#8a5a54;margin:0 0 12px">
            These sessions are scheduled too close to a race and aren't easy/short —
            they risk arriving fatigued or injured. Consider replacing them with
            rest or a short shakeout, either manually or via AI Coaching.
        </p>
        <table class="table">
            <tr><th>Date</th><th>Discipline</th><th>Session</th><th>Duration</th><th>Conflict</th></tr>
            {rows}
        </table>
    </div>"""



def plan_viewer_html(plan_full, coaching=None):
    """Unified plan table with AI suggestions merged as extra columns.
    Race days from data/races.json are merged in as special \U0001F3C1 rows so
    a newly added race shows up here immediately, without needing to
    edit plan_full.json."""
    if not plan_full and not RACES:
        return "<p class=\'subtext\'>No plan data \u2014 add data/plan_full.json to the repo.</p>"

    today     = dt.date.today()
    two_weeks = today + dt.timedelta(weeks=2)

    # Synthesize a pseudo-session for each race so it appears in the table
    # even though it lives in a separate file (races.json) from the plan.
    plan_full = list(plan_full or [])
    for r in RACES:
        race_date = r["date"].isoformat()
        dist = r.get("distances", {}) or {}
        dist_bits = []
        if dist.get("swim_m"):  dist_bits.append(f"{dist['swim_m']}m swim")
        if dist.get("bike_km"): dist_bits.append(f"{dist['bike_km']}km bike")
        if dist.get("run_km"):  dist_bits.append(f"{dist['run_km']}km run")
        plan_full.append({
            "date":         race_date,
            "day":          r["date"].strftime("%A"),
            "discipline":   "race",
            "summary":      f"{r['emoji']} RACE DAY: {r['name']}",
            "duration_min": None,
            "notes":        r.get("note", ""),
            "pace":         None,
            "power":        None,
            "distance":     " \u00b7 ".join(dist_bits) if dist_bits else None,
        })

    # Sort chronologically — race days were appended after the regular
    # sessions above, so without this they'd all land at the bottom.
    plan_full.sort(key=lambda s: s.get("date", ""))

    # Build a lookup: date+discipline \u2192 proposed change from coaching.
    # Falls back to date-only matching if discipline labels don\'t line up
    # exactly (e.g. AI says "cycling", plan session was parsed as "other").
    ai_changes = {}
    ai_changes_by_date = {}
    all_changes = coaching.get("proposed_changes", []) if coaching else []
    for c in all_changes:
        key = (c.get("date",""), c.get("discipline",""))
        ai_changes[key] = c
        ai_changes_by_date.setdefault(c.get("date",""), []).append(c)

    used_change_ids = set()

    def session_row(s):
        date   = dt.date.fromisoformat(s["date"])
        is_past   = date < today
        is_today  = date == today
        disc   = s.get("discipline", "other")
        color  = DISC_COLORS.get(disc, "#aaa")
        label  = DISC_LABELS.get(disc, disc)
        dur    = f"{s['duration_min']}min" if s.get("duration_min") else "—"
        targets = []
        if s.get("distance"):  targets.append(f"📏 {s['distance']}")
        if s.get("pace"):      targets.append(f"⏱ {s['pace']}")
        if s.get("power"):     targets.append(f"⚡ {s['power']}")
        target_str   = " · ".join(targets)
        notes_short  = (s.get("notes") or "").split(" | ")[0][:120]

        # AI proposal for this session — exact match first, then date-only fallback
        ai = ai_changes.get((s["date"], disc))
        if not ai:
            same_day = ai_changes_by_date.get(s["date"], [])
            unclaimed = [c for c in same_day if id(c) not in used_change_ids]
            ai = unclaimed[0] if len(same_day) == 1 and unclaimed else None

        if ai:
            used_change_ids.add(id(ai))
            ct         = ai.get("change_type","replace")
            text_color, bg_color = CHANGE_COLORS.get(ct, ("#888","#f8f8f8"))
            ai_cell = f"""<td style="font-size:.78em;color:{text_color};
                font-weight:600;line-height:1.4">{ai.get('proposed_session','')}<br>
                <span style="font-size:.9em;color:#888;font-weight:400">{ai.get('reason','')}</span></td>
            <td style="text-align:center">
                <span style="background:{bg_color};color:{text_color};border-radius:5px;
                    padding:2px 7px;font-size:.7em;font-weight:700">
                    {ct.upper()}</span></td>"""
            row_style = f"border-left:3px solid {text_color};background:{bg_color}88"
        else:
            ai_cell = "<td style=\'color:#eee;font-size:.78em\''>—</td><td></td>"

            row_style = ""

        opacity  = "opacity:.45" if is_past else ""
        highlight = "background:#fffbea !important" if is_today else ""

        return (
            f"<tr data-disc=\'{disc}\' data-date=\'{s['date']}\' "
            f"style=\'{row_style};{opacity}{highlight}\'>"
            f"<td style=\'white-space:nowrap;color:#9a9aaa;font-size:.8em\'>"
            f"{date.strftime('%a %b %d')}</td>"
            f"<td><span style=\'background:{color}22;color:{color};border-radius:6px;"
            f"padding:2px 7px;font-size:.72em;font-weight:700\'>{label}</span></td>"
            f"<td style=\'font-size:.82em;color:#555\'>{s.get('summary','')}</td>"
            f"<td style=\'font-size:.8em;color:#9a9aaa;text-align:center\'>{dur}</td>"
            f"<td style=\'font-size:.78em;color:#5B6EF5;font-weight:600\'>{target_str}</td>"
            f"<td style=\'font-size:.76em;color:#666;max-width:180px;word-break:normal;"
            f"overflow-wrap:break-word\'>{notes_short}</td>"
            f"{ai_cell}</tr>"
        )

    rows_2wk  = ""
    rows_full = ""
    has_ai    = bool(all_changes)   # true if ANY proposal exists, matched or not

    for s in plan_full:
        date = dt.date.fromisoformat(s["date"])
        row  = session_row(s)
        if today <= date <= two_weeks:
            rows_2wk  += row
        if date >= today:
            rows_full += row

    if not rows_2wk:
        rows_2wk = "<tr><td colspan=\'8\' style=\'color:#9a9aaa;padding:16px\'>No sessions in the next 2 weeks.</td></tr>"

    ai_header = ("<th>🤖 AI Suggestion</th><th>Change</th>" if has_ai
                 else "<th style=\'color:#ddd\'>AI Suggestion</th><th></th>")

    ai_legend = ""
    ics_button = ""
    if has_ai:
        changes_list = all_changes   # include every proposal, even unmatched ones
        ai_legend = """<div style="display:flex;gap:10px;flex-wrap:wrap;
            font-size:.75em;margin-bottom:6px;align-items:center">
            <span style="font-weight:600;color:#555">AI proposals:</span>
            <span style="background:#e8f9f5;color:#00C2A8;border-radius:4px;padding:1px 7px;font-weight:600">INCREASE</span>
            <span style="background:#fff8e0;color:#b88a00;border-radius:4px;padding:1px 7px;font-weight:600">DECREASE</span>
            <span style="background:#eef0fd;color:#5B6EF5;border-radius:4px;padding:1px 7px;font-weight:600">REPLACE</span>
            <span style="background:#ffeae8;color:#FF7A59;border-radius:4px;padding:1px 7px;font-weight:600">SKIP</span>
        </div>"""
        unmatched_count = len(all_changes) - len(used_change_ids)
        if unmatched_count > 0:
            ai_legend += f"""<p style="font-size:.72em;color:#b88a00;margin:4px 0 6px">
                ⚠️ {unmatched_count} proposal(s) didn\'t line up with a specific row above
                (date/discipline mismatch) but are still included in the ICS download below.
            </p>"""
        gen_at = coaching.get("generated_at","")[:10] if coaching else ""
        ics_button = f"""<div style="margin-bottom:12px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
            <button class="btn" onclick="downloadCoachingICS()"
                style="background:#00C2A8;font-size:.8em">⬇️ Download Adjusted ICS</button>
            <button class="btn" onclick="applyToDashboard()"
                style="background:#5B6EF5;font-size:.8em">🚀 Apply to Dashboard</button>
            <span id="apply-status" style="font-size:.74em;color:#9a9aaa"></span>
        </div>
        <p style="font-size:.72em;color:#bbb;margin:-6px 0 4px">
            📥 On iPhone: after tapping Download, choose <strong>Save to Files</strong>
            (not "Open") — then open the <strong>Files app</strong> and tap the saved
            <code>.ics</code> file, which shows an "Add All Events" option for every
            session at once. Opening it directly in Safari only adds one at a time.
        </p>
        <p style="font-size:.72em;color:#bbb;margin:-6px 0 12px">
            "Apply to Dashboard" triggers the GitHub Actions workflow that merges these
            changes into your plan. First time only: it asks for a GitHub token
            (saved in this browser only —
            <a href="https://github.com/settings/personal-access-tokens/new" target="_blank"
               style="color:#5B6EF5">create one here</a>,
            fine-grained, scoped to this repo, Actions: Read and Write).
        </p>
        <script>
        var COACHING_CHANGES = {json.dumps(changes_list)};
        var ICS_CONTENT = {json.dumps(build_ics_content(changes_list, gen_at))};
        function downloadCoachingICS() {{
            var blob = new Blob([ICS_CONTENT], {{type:"application/octet-stream"}});
            var a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = "ai_coaching_adjustments.ics";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }}

        </script>"""

    def filter_bar(suffix):
        return f"""<div class="plan-filter-bar" style="margin-bottom:10px">
            <span style="font-size:.8em;font-weight:600;color:#555">Filter:</span>
            <button class="filter-btn active" onclick="filterDisc{suffix}(\'all\')">All</button>
            <button class="filter-btn" onclick="filterDisc{suffix}(\'swimming\')">🏊 Swim</button>
            <button class="filter-btn" onclick="filterDisc{suffix}(\'cycling\')">🚴 Bike</button>
            <button class="filter-btn" onclick="filterDisc{suffix}(\'running\')">🏃 Run</button>
            <button class="filter-btn" onclick="filterDisc{suffix}(\'strength_training\')">💪 Strength</button>
            <button class="filter-btn" onclick="filterDisc{suffix}(\'rest\')">😴 Rest</button>
            <button class="filter-btn" onclick="filterDisc{suffix}(\'race\')">🏁 Race</button>
        </div>"""

    header_row = f"<tr><th>Date</th><th>Discipline</th><th>Session</th><th>Duration</th><th>Targets</th><th>Notes</th>{ai_header}</tr>"

    return f"""
<div class="plan-controls">
    <button class="plan-btn active" onclick="showPlan(\'two-weeks\')">Next 2 Weeks</button>
    <button class="plan-btn" onclick="showPlan(\'full\')">Full Plan</button>
</div>
{ai_legend}
{ics_button}

<div id="plan-two-weeks">
{filter_bar("A")}
<table class="table plan-table" id="two-week-table">{header_row}{rows_2wk}</table>
</div>

<div id="plan-full" style="display:none">
{filter_bar("B")}
<table class="table plan-table" id="full-plan-table">{header_row}{rows_full}</table>
</div>

<script>
function showPlan(view) {{
    document.getElementById("plan-two-weeks").style.display = view==="two-weeks" ? "" : "none";
    document.getElementById("plan-full").style.display      = view==="full"       ? "" : "none";
    document.querySelectorAll(".plan-btn").forEach(b => b.classList.remove("active"));
    event.target.classList.add("active");
}}
function filterDiscA(disc) {{
    document.querySelectorAll("#two-week-table tr[data-disc]").forEach(r => {{
        r.style.display = (disc==="all" || r.dataset.disc===disc) ? "" : "none";
    }});
    event.target.closest(".plan-filter-bar").querySelectorAll(".filter-btn").forEach(b=>b.classList.remove("active"));
    event.target.classList.add("active");
}}
function filterDiscB(disc) {{
    document.querySelectorAll("#full-plan-table tr[data-disc]").forEach(r => {{
        r.style.display = (disc==="all" || r.dataset.disc===disc) ? "" : "none";
    }});
    event.target.closest(".plan-filter-bar").querySelectorAll(".filter-btn").forEach(b=>b.classList.remove("active"));
    event.target.classList.add("active");
}}
</script>"""


# ── Compliance HTML table ─────────────────────────────────────────────────────
def compliance_html(weeks_data):
    if not weeks_data:
        return "<p class='subtext'>No matched sessions in the last 4 weeks yet — sessions will match once your plan dates align with actual Garmin activities.</p>"
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
def build_html(df, plan, wellness, plan_sessions, manual_log, plan_full=None, coaching=None):
    OUT_HTML.parent.mkdir(exist_ok=True)
    if df.empty:
        OUT_HTML.write_text("<h1>No data yet</h1>")
        return

    weekly  = weekly_by_discipline(df)
    trends  = discipline_trends(df)
    ontarget = on_target_pct(weekly, plan)
    compliance = session_compliance(df, plan_sessions, weeks_back=4)
    race_conflicts = detect_race_conflicts(plan_full or [], RACES)

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
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E🏊%3C/text%3E%3C/svg%3E">
<link rel="apple-touch-icon" href="data:image/png;base64,{TOUCH_ICON_B64}">
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
.status-strip{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px;}}
.status-chip{{display:flex;align-items:center;gap:6px;border-radius:20px;
              padding:7px 14px;box-shadow:0 1px 3px rgba(20,20,40,.06);}}
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
.races{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:16px 0;}}
.race-card{{background:#fff;border-radius:12px;padding:16px 18px;
            box-shadow:0 1px 3px rgba(20,20,40,.06);}}
.race-emoji{{font-size:1.6em;}}
.race-name{{font-weight:700;font-size:1em;margin:4px 0 2px;}}
.race-date{{color:#9a9aaa;font-size:.8em;}}
.race-days{{font-size:1.3em;font-weight:800;margin:6px 0 4px;}}
.race-dist{{font-size:.8em;color:#1a1a22;font-weight:700;margin-bottom:2px;}}
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
.coaching-badge{{display:inline-block;border-radius:6px;padding:2px 8px;
               font-size:.72em;font-weight:700;}}
.plan-controls{{display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap;}}
.plan-btn{{background:#f0f1f8;border:none;border-radius:8px;padding:8px 16px;
           font-size:.83em;font-weight:600;color:#6b6b78;cursor:pointer;}}
.plan-btn.active{{background:#5B6EF5;color:#fff;}}
.plan-btn:hover:not(.active){{background:#e0e2f0;}}
.plan-filter-bar{{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap;align-items:center;}}
.filter-btn{{background:#f8f8fc;border:1px solid #eee;border-radius:6px;
             padding:4px 10px;font-size:.76em;font-weight:600;cursor:pointer;color:#555;}}
.filter-btn.active{{background:#5B6EF5;color:#fff;border-color:#5B6EF5;}}
.plan-table td:last-child{{
    white-space:normal;
    overflow-wrap:break-word;
    word-break:normal;
    min-width:120px;
    max-width:200px;
    line-height:1.4;
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
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button class="btn" onclick="window.print()">Export PDF</button>
      <button class="btn" onclick="triggerWorkflow(\'sync.yml\', \'sync-status\', \'Sync Now\')"
          style="background:#00C2A8">🔄 Sync Now</button>
  </div>
</div>
<p id="sync-status" style="font-size:.76em;color:#9a9aaa;margin:-4px 0 12px"></p>

{status_strip_html(RACES, race_conflicts, coaching)}

<script>
// Shared GitHub API helpers used by Sync Now, Apply to Dashboard, and the Race Manager.
var GH_OWNER = "chetcutijc";
var GH_REPO  = "TRI";

function getGhToken() {{
    var token = localStorage.getItem("gh_pat_apply_coaching");
    if (!token) {{
        token = prompt(
            "Paste your GitHub fine-grained token.\\n" +
            "Needs 'Actions: Read and write' AND 'Contents: Read and write' on this repo.\\n" +
            "Saved only in this browser."
        );
        if (token) localStorage.setItem("gh_pat_apply_coaching", token);
    }}
    return token;
}}

function triggerWorkflow(workflowFile, statusElId, label) {{
    var status = document.getElementById(statusElId);
    var token = getGhToken();
    if (!token) {{
        status.textContent = "No token provided.";
        status.style.color = "#FF7A59";
        return;
    }}
    status.textContent = "Triggering " + label + "...";
    status.style.color = "#9a9aaa";

    fetch("https://api.github.com/repos/" + GH_OWNER + "/" + GH_REPO +
          "/actions/workflows/" + workflowFile + "/dispatches", {{
        method: "POST",
        headers: {{
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }},
        body: JSON.stringify({{ ref: "main" }})
    }}).then(function(res) {{
        if (res.status === 204) {{
            status.textContent = "\u2705 " + label + " triggered! Check GitHub Actions \u2014 dashboard updates in a few minutes.";
            status.style.color = "#00C2A8";
            return;
        }}
        if (res.status === 401 || res.status === 403) {{
            res.json().then(function(body) {{
                var msg = (body && body.message) ? body.message : "permission denied";
                status.textContent = "\u274c " + res.status + ": " + msg +
                    " \u2014 your token may be missing a required permission.";
                status.style.color = "#FF7A59";
            }}).catch(function() {{
                status.textContent = "\u274c Token invalid, expired, or missing permissions.";
                status.style.color = "#FF7A59";
            }});
            return;
        }}
        res.json().then(function(body) {{
            var msg = (body && body.message) ? body.message : "unknown error";
            status.textContent = "\u274c " + res.status + ": " + msg;
            status.style.color = "#FF7A59";
            console.error("GitHub API error:", res.status, body);
        }}).catch(function() {{
            status.textContent = "\u274c Failed (status " + res.status + ").";
            status.style.color = "#FF7A59";
        }});
    }}).catch(function(err) {{
        status.textContent = "\u274c Network error: " + err;
        status.style.color = "#FF7A59";
    }});
}}

// Guards "Apply to Dashboard" against the most common failure mode: tapping
// it before a just-triggered Sync Now run has actually finished committing.
// If Apply runs against a stale/empty ai_coaching.json, it silently does
// nothing — this checks GitHub's own run status first and blocks with a
// clear message instead of that silent no-op.
function applyToDashboard() {{
    var status = document.getElementById("apply-status");
    var token = getGhToken();
    if (!token) {{
        status.textContent = "No token provided.";
        status.style.color = "#FF7A59";
        return;
    }}
    status.textContent = "Checking sync status...";
    status.style.color = "#9a9aaa";

    fetch("https://api.github.com/repos/" + GH_OWNER + "/" + GH_REPO +
          "/actions/workflows/sync.yml/runs?per_page=1", {{
        headers: {{
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }}
    }}).then(function(res) {{ return res.json(); }})
      .then(function(body) {{
          var runs = (body && body.workflow_runs) || [];
          var latest = runs[0];
          if (latest && (latest.status === "in_progress" || latest.status === "queued")) {{
              status.textContent = "\u23f3 A Sync Now run is still in progress \u2014 " +
                  "wait for it to finish (check the Actions tab for a green check) " +
                  "before applying, or the suggestions may be stale.";
              status.style.color = "#FFC75A";
              return;
          }}
          triggerWorkflow("apply_coaching.yml", "apply-status", "Apply AI Coaching");
      }})
      .catch(function() {{
          // If the status check itself fails, don't block the user entirely —
          // just proceed, since this is a best-effort safety check.
          triggerWorkflow("apply_coaching.yml", "apply-status", "Apply AI Coaching");
      }});
}}
</script>

<h2>Race Targets</h2>
<div class="races">{race_cards_html(df)}</div>
{race_manager_html()}

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

{race_conflicts_html(race_conflicts)}

{athlete_notes_manager_html()}

<h2>🤖 AI Coaching</h2>
<p class="subtext">Claude analyses your last 4 weeks vs the plan every Saturday. Proposed changes are suggestions only — you download the ICS to apply them.</p>
{coaching_html(coaching)}

<h2>📊 AI Suggestion Adherence</h2>
{adherence_html(coaching, df) or "<p class='subtext'>No applied suggestions yet — history appears here once you tap 'Apply to Dashboard'.</p>"}

<h2>Session Compliance — Last 4 Weeks</h2>
<p class="subtext">Each planned session matched to a Garmin activity (±1 day). Status reflects both duration completion and pace/power adherence.</p>
{compliance_html(compliance)}

<h2>Training Plan</h2>
<p class="subtext">Sessions from your 55-week plan. Past sessions are faded. Today is highlighted.</p>
{plan_viewer_html(plan_full or [], coaching)}

<h2>Recent Sessions</h2>
{recent_html}
</body>
</html>""")
    print("HTML dashboard built.")


# ── PDF dashboard ─────────────────────────────────────────────────────────────

# ── Main ──────────────────────────────────────────────────────────────────────

def build_print_html(df, plan, wellness, plan_sessions, manual_log, plan_full=None, coaching=None):
    """
    Builds docs/print.html — the source for the emailed PDF.

    Deliberately kept SHORT and focused, unlike the full website: just the
    things worth reading on a phone in under a minute —
      1. Race targets (target vs predicted time/pace)
      2. Last 30 days overview (stats + per-discipline)
      3. Pre-race scheduling conflicts (if any)
      4. AI coaching summary + suggestions (if any)

    No charts, no full session tables, no training plan — those live on the
    website. This keeps the PDF something you'd actually read end to end.
    """
    OUT_PRINT = Path("docs/print.html")
    OUT_PRINT.parent.mkdir(exist_ok=True)

    if df.empty:
        OUT_PRINT.write_text("<h1>No activity data yet</h1>")
        return

    plan_full = plan_full or []
    race_conflicts = detect_race_conflicts(plan_full, RACES)

    # ── Last 30 days: overview + per-discipline ──────────────────────────────
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
    swim_pace     = "n/a"
    if not sw30.empty:
        raw = sw30["avg_pace"].dropna().apply(speed_to_pace)
        if not raw.empty:
            p = raw.mean() / 10
            swim_pace = f"{int(p)//60}:{int(p)%60:02d}/100m"

    ru30 = d30("running")
    run_sessions = len(ru30)
    run_total    = f"{round(ru30['distance_km'].sum())}km" if not ru30.empty else "n/a"
    run_pace     = "n/a"
    if not ru30.empty:
        raw = ru30["avg_pace"].dropna().apply(speed_to_pace)
        if not raw.empty:
            run_pace = fmt_pace(raw.mean())

    cy30 = d30("cycling")
    bike_sessions = len(cy30)
    bike_total    = f"{round(cy30['distance_km'].sum())}km" if not cy30.empty else "n/a"
    bike_speed    = "n/a"
    if not cy30.empty:
        sp = cy30["avg_pace"].dropna()
        if not sp.empty:
            bike_speed = f"{round(sp.mean()*3.6,1)} km/h"

    # ── Race targets ─────────────────────────────────────────────────────────
    race_html = ""
    for r in RACES:
        d = days_until(r["date"])
        target     = compute_race_target_time(r)
        prediction = compute_race_prediction(r, df)
        days_str   = f"In {d} days" if d > 0 else "RACE DAY!" if d == 0 else f"{abs(d)} days ago"
        col = "#00C2A8" if d > 90 else "#FFC75A" if d > 30 else "#FF7A59"

        times = ""
        if target:
            times += f'<div style="color:#5B6EF5;font-weight:700">Target: {target["total"]}</div>'
        if prediction:
            times += f'<div style="color:#00C2A8;font-weight:700">Predicted: ~{prediction["total"]}</div>'

        race_html += f"""<div class="rcard">
            <div style="font-size:13pt">{r['emoji']}</div>
            <div style="font-weight:700;font-size:9.5pt">{r['name']}</div>
            <div style="font-size:7.5pt;color:#888">{r['date'].strftime('%b %d, %Y')}</div>
            <div style="font-size:11pt;font-weight:800;color:{col};margin:3pt 0">{days_str}</div>
            {times}
        </div>"""

    # ── Pre-race conflicts ───────────────────────────────────────────────────
    conflict_html = ""
    if race_conflicts:
        rows = "".join(f"""<tr>
            <td>{c['session_date']}</td>
            <td style="font-weight:600">{c['discipline'].replace('_',' ').title()}</td>
            <td>{c['summary']}</td>
            <td>{c['duration_min']}min</td>
            <td style="color:#c0392b;font-weight:600">{c['days_before_race']}d before {c['race_name']}</td>
        </tr>""" for c in race_conflicts)
        conflict_html = f"""
        <h2 style="color:#c0392b">⚠️ Pre-Race Conflicts</h2>
        <table class="t">
            <tr><th>Date</th><th>Discipline</th><th>Session</th><th>Duration</th><th>Conflict</th></tr>
            {rows}
        </table>"""

    # ── AI coaching summary + suggestions ────────────────────────────────────
    coaching_html_block = ""
    if coaching and coaching.get("summary"):
        suggestions = coaching.get("suggestions", [])
        changes     = coaching.get("proposed_changes", [])
        sugg_rows = "".join(f"<li>{s}</li>" for s in suggestions if s)

        change_rows = ""
        if changes:
            rows = "".join(f"""<tr>
                <td>{c.get('date','')}</td>
                <td style="font-weight:600">{c.get('discipline','').replace('_',' ').title()}</td>
                <td style="color:#5B6EF5">{str(c.get('proposed_session',''))[:90]}</td>
                <td style="text-align:center">{c.get('change_type','').upper()}</td>
            </tr>""" for c in changes)
            change_rows = f"""
            <p class="subtext">Proposed plan changes (review on the website, apply via "Apply to Dashboard"):</p>
            <table class="t">
                <tr><th>Date</th><th>Discipline</th><th>Proposed</th><th>Type</th></tr>
                {rows}
            </table>"""

        coaching_html_block = f"""
        <h2>🤖 AI Coaching</h2>
        <p style="font-size:8.5pt;line-height:1.5;border-left:2pt solid #5B6EF5;padding-left:8pt">{coaching['summary']}</p>
        {f'<ul class="sugg">{sugg_rows}</ul>' if sugg_rows else ''}
        {change_rows}"""

    # ── Write HTML ───────────────────────────────────────────────────────────
    OUT_PRINT.write_text(f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Training Dashboard</title>
<style>
@page {{ size: A4; margin: 12mm 14mm; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: Helvetica, Arial, sans-serif; color: #1a1a22; font-size: 9pt; }}
h1 {{ font-size: 16pt; font-weight: 800; margin-bottom: 2pt; }}
h2 {{ font-size: 11pt; font-weight: 700; margin: 16pt 0 6pt; border-bottom: 2px solid #f0f0f5; padding-bottom: 3pt; }}
.updated {{ color: #999; font-size: 7.5pt; margin-bottom: 10pt; }}
.subtext {{ color: #999; font-size: 7.5pt; margin: 2pt 0 6pt; }}
.stats {{ display: flex; flex-wrap: wrap; gap: 6pt; margin: 6pt 0; }}
.card {{ border: 1px solid #eee; border-radius: 5pt; padding: 6pt 8pt; text-align: center; flex: 1; min-width: 55pt; }}
.card .num {{ font-size: 12pt; font-weight: 800; }}
.card .lbl {{ font-size: 6pt; color: #999; text-transform: uppercase; }}
.races {{ display: flex; gap: 8pt; margin: 6pt 0; }}
.rcard {{ border: 1px solid #eee; border-radius: 5pt; padding: 8pt 10pt; flex: 1; }}
.dg {{ display: flex; gap: 6pt; margin: 8pt 0; }}
.db {{ border: 1px solid #eee; border-radius: 5pt; padding: 7pt 9pt; flex: 1; }}
.db .dt {{ font-weight: 700; font-size: 8.5pt; margin-bottom: 4pt; }}
.db .stats {{ gap: 3pt; margin: 0; }}
.db .card {{ padding: 4pt 4pt; min-width: 0; background: #f8f8fc; border: none; }}
table.t {{ width: 100%; border-collapse: collapse; font-size: 8pt; margin-bottom: 6pt; }}
table.t th {{ background: #f5f5fa; padding: 4pt 5pt; text-align: left; font-size: 6.8pt; text-transform: uppercase; }}
table.t td {{ padding: 4pt 5pt; border-bottom: 1px solid #f0f0f5; }}
ul.sugg {{ margin: 6pt 0 6pt 14pt; font-size: 8.5pt; line-height: 1.6; }}
</style>
</head>
<body>

<h1>🏊‍♂️🚴‍♂️🏃‍♂️ Training Dashboard</h1>
<p class="updated">Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} UTC</p>

<h2>Race Targets</h2>
<div class="races">{race_html}</div>

<h2>Last 30 Days</h2>
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
      <div class="card"><div class="num">{swim_pace}</div><div class="lbl">Pace</div></div>
    </div>
  </div>
  <div class="db" style="border-top:2.5pt solid {PALETTE['running']}">
    <div class="dt">🏃 Running ({run_sessions})</div>
    <div class="stats">
      <div class="card"><div class="num">{run_total}</div><div class="lbl">Total</div></div>
      <div class="card"><div class="num">{run_pace}</div><div class="lbl">Pace</div></div>
    </div>
  </div>
  <div class="db" style="border-top:2.5pt solid {PALETTE['cycling']}">
    <div class="dt">🚴 Cycling ({bike_sessions})</div>
    <div class="stats">
      <div class="card"><div class="num">{bike_total}</div><div class="lbl">Total</div></div>
      <div class="card"><div class="num">{bike_speed}</div><div class="lbl">Speed</div></div>
    </div>
  </div>
</div>

{conflict_html}
{coaching_html_block}

</body>
</html>""")
    print(f"Print HTML built at {OUT_PRINT}")


def coaching_email_text(coaching):
    """Returns a plain-text coaching summary for inclusion in the email."""
    if not coaching or not coaching.get("summary"):
        return ""
    lines = []
    lines.append("=" * 60)
    lines.append("🤖 AI COACHING REPORT")
    lines.append(f"   Generated: {coaching.get('generated_at','')[:10]}")
    upcoming = [r for r in RACES if days_until(r["date"]) >= 0][:4]
    if upcoming:
        lines.append("   " + "  |  ".join(
            f"{days_until(r['date'])} days to {r['name']}" for r in upcoming))
    lines.append("=" * 60)
    lines.append("")
    lines.append("OVERALL ASSESSMENT:")
    lines.append(coaching.get("summary",""))
    lines.append("")
    suggestions = coaching.get("suggestions", [])
    if suggestions:
        lines.append("COACHING SUGGESTIONS:")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"  {i}. {s}")
    lines.append("")
    changes = coaching.get("proposed_changes", [])
    if changes:
        lines.append("PROPOSED PLAN ADJUSTMENTS (suggestions only — not auto-applied):")
        for c in changes:
            lines.append(f"  • {c.get('date','')} [{c.get('discipline','').replace('_',' ').upper()}]")
            lines.append(f"    Current:  {c.get('current_session','')}")
            lines.append(f"    Proposed: {c.get('proposed_session','')}")
            lines.append(f"    Reason:   {c.get('reason','')}")
            lines.append("")
        lines.append("  → Open the dashboard website to review the unified")
        lines.append("    plan table (highlighted rows = AI proposals).")
        lines.append("  → Download ICS from the website to update your phone calendar.")
        lines.append("  → Tap 'Sync Now' then 'Apply to Dashboard' on the website to")
        lines.append("    update plan_full.json so changes show on the dashboard too.")
    else:
        lines.append("✅ No plan adjustments proposed — training is on track.")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    df            = load_activities()
    plan          = load_plan()
    wellness      = load_wellness()
    plan_sessions = load_plan_sessions()
    plan_full     = load_plan_full()
    manual_log    = load_manual_log()
    coaching      = load_coaching()
    build_html(df, plan, wellness, plan_sessions, manual_log, plan_full, coaching)
    build_print_html(df, plan, wellness, plan_sessions, manual_log, plan_full, coaching)
    # Write coaching email text for use in email step
    email_coaching = coaching_email_text(coaching)
    if email_coaching:
        Path("data/coaching_email.txt").write_text(email_coaching)

    # Write a physical .ics file for the AI's proposed changes so it can be
    # emailed as an attachment — Mail's "Add All N Events" import flow works
    # properly, unlike Safari's one-at-a-time download preview on iPhone.
    ics_path = Path("docs/ai_coaching_adjustments.ics")
    proposed = (coaching or {}).get("proposed_changes", [])
    if proposed:
        gen_at = (coaching or {}).get("generated_at", "")[:10]
        ics_path.write_text(build_ics_content(proposed, gen_at))
        print(f"ICS written to {ics_path} ({len(proposed)} events)")
    elif ics_path.exists():
        # No pending changes this run — remove any stale file from a
        # previous round so the email step doesn't attach outdated events.
        ics_path.unlink()


if __name__ == "__main__":
    main()

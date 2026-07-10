"""Weekly summary report: this week vs last week, workouts, anomalies.

Usage: python -m pulseboard.report [--week-ending YYYY-MM-DD] [--format md|html]
       [--out PATH] [--notify] [--check-freshness] [--loop]

One-shot by design — schedule it with cron/systemd (or the compose "reports"
profile / Helm CronJob, which use --loop). Informational only, not medical
advice.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pulseboard.db import Database
from pulseboard.insights import Anomaly, detect_anomalies
from pulseboard.notify import notify_all

logger = logging.getLogger(__name__)

DISCLAIMER = "Informational only, not medical advice."
STALE_AFTER_SECONDS = 2 * 24 * 3600

# (canonical metric, aggregation, weekly reduce "sum"|"mean", label, unit)
REPORT_METRICS: tuple[tuple[str, str, str, str, str], ...] = (
    ("steps", "sum", "sum", "Steps", ""),
    ("distance_walking_running", "sum", "sum", "Distance", "km"),
    ("active_energy", "sum", "sum", "Active energy", "kcal"),
    ("apple_exercise_time", "sum", "sum", "Exercise", "min"),
    ("workouts_count", "sum", "sum", "Workouts", ""),
    ("sleep_hours", "sum", "mean", "Sleep (avg/night)", "h"),
    ("resting_heart_rate", "avg", "mean", "Resting HR (avg)", "bpm"),
    ("heart_rate_variability_sdnn", "avg", "mean", "HRV SDNN (avg)", "ms"),
)


@dataclass(frozen=True)
class MetricComparison:
    label: str
    unit: str
    this_week: float | None
    last_week: float | None
    delta_pct: float | None  # None when either side is missing or last week is 0
    days_with_data: int


@dataclass(frozen=True)
class WorkoutLine:
    date: str
    activity_type: str
    duration_min: float
    energy_kcal: float
    distance_km: float


@dataclass(frozen=True)
class WeeklyReport:
    week_start: str  # Monday, ISO date
    week_end: str  # Sunday, ISO date
    comparisons: list[MetricComparison]
    workouts: list[WorkoutLine]
    anomalies: list[Anomaly]
    freshness_seconds: float | None


def _week_window(week_ending: date_type) -> tuple[date_type, date_type]:
    """The Monday..Sunday week containing week_ending (anchored to its end)."""
    sunday = week_ending + timedelta(days=(6 - week_ending.weekday()))
    return sunday - timedelta(days=6), sunday


def _reduce(db: Database, metric: str, aggregation: str, reduce: str, start: str, end: str) -> tuple[float | None, int]:
    stats = db.range_stats(metric, aggregation, start, end)
    value = stats["total"] if reduce == "sum" else stats["mean"]
    return (round(float(value), 2) if value is not None else None, int(stats["days"]))


def freshness_seconds(db: Database, now: datetime | None = None) -> float | None:
    last_ingest = db.last_ingest_at()
    if last_ingest is None:
        return None
    ingested = datetime.fromisoformat(last_ingest)
    if ingested.tzinfo is None:
        ingested = ingested.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - ingested).total_seconds()


def build_weekly_report(db: Database, week_ending: date_type | None = None) -> WeeklyReport:
    week_ending = week_ending or date_type.today()
    this_start, this_end = _week_window(week_ending)
    last_start, last_end = this_start - timedelta(days=7), this_end - timedelta(days=7)

    comparisons: list[MetricComparison] = []
    for metric, aggregation, reduce, label, unit in REPORT_METRICS:
        this_value, days = _reduce(db, metric, aggregation, reduce, this_start.isoformat(), this_end.isoformat())
        last_value, _ = _reduce(db, metric, aggregation, reduce, last_start.isoformat(), last_end.isoformat())
        delta_pct = None
        if this_value is not None and last_value is not None and last_value != 0:
            delta_pct = round((this_value - last_value) / abs(last_value) * 100.0, 1)
        comparisons.append(MetricComparison(label, unit, this_value, last_value, delta_pct, days))

    workouts = [
        WorkoutLine(
            date=row["date"],
            activity_type=row["activity_type"],
            duration_min=float(row["duration_min"]),
            energy_kcal=float(row["energy_kcal"]),
            distance_km=float(row["distance_km"]),
        )
        for row in db.workouts_between(this_start.isoformat(), this_end.isoformat())
    ]

    return WeeklyReport(
        week_start=this_start.isoformat(),
        week_end=this_end.isoformat(),
        comparisons=comparisons,
        workouts=workouts,
        anomalies=detect_anomalies(db),
        freshness_seconds=freshness_seconds(db),
    )


def _format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    text = f"{value:,.0f}" if value >= 1000 else f"{value:g}"
    return f"{text} {unit}".strip()


def _format_delta(delta_pct: float | None) -> str:
    if delta_pct is None:
        return "—"
    arrow = "▲" if delta_pct > 0 else ("▼" if delta_pct < 0 else "→")
    return f"{arrow} {delta_pct:+.1f}%"


def render_markdown(report: WeeklyReport) -> str:
    lines = [
        f"# PulseBoard weekly report — {report.week_start} .. {report.week_end}",
        "",
        "| Metric | This week | Last week | Change | Days |",
        "|---|---|---|---|---|",
    ]
    for c in report.comparisons:
        lines.append(
            f"| {c.label} | {_format_value(c.this_week, c.unit)} | "
            f"{_format_value(c.last_week, c.unit)} | {_format_delta(c.delta_pct)} | {c.days_with_data} |"
        )
    lines.append("")

    if report.workouts:
        lines += ["## Workouts", ""]
        for w in report.workouts:
            details = f"{w.duration_min:g} min"
            if w.distance_km:
                details += f", {w.distance_km:g} km"
            if w.energy_kcal:
                details += f", {w.energy_kcal:g} kcal"
            lines.append(f"- {w.date} — {w.activity_type} ({details})")
        lines.append("")

    if report.anomalies:
        lines += ["## Anomalies (vs your own 30-day baseline)", ""]
        for a in report.anomalies:
            lines.append(f"- {a.metric} on {a.date}: {a.value:g} (z = {a.zscore:+.1f}, baseline ≈ {a.baseline_mean:g})")
        lines.append("")

    if report.freshness_seconds is not None and report.freshness_seconds > STALE_AFTER_SECONDS:
        days = report.freshness_seconds / 86400.0
        lines += [f"⚠️ Data may be stale: last ingest was {days:.1f} days ago. See docs/SHORTCUT.md.", ""]

    lines += ["---", "", f"_{DISCLAIMER}_", ""]
    return "\n".join(lines)


def render_html(report: WeeklyReport) -> str:
    """Minimal standalone HTML wrapper around the same data (for email etc.)."""
    rows = "".join(
        f"<tr><td>{c.label}</td><td>{_format_value(c.this_week, c.unit)}</td>"
        f"<td>{_format_value(c.last_week, c.unit)}</td><td>{_format_delta(c.delta_pct)}</td>"
        f"<td>{c.days_with_data}</td></tr>"
        for c in report.comparisons
    )
    workouts = (
        "".join(f"<li>{w.date} — {w.activity_type} ({w.duration_min:g} min)</li>" for w in report.workouts)
        or "<li>none recorded</li>"
    )
    anomalies = (
        "".join(f"<li>{a.metric} on {a.date}: {a.value:g} (z = {a.zscore:+.1f})</li>" for a in report.anomalies)
        or "<li>none</li>"
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>PulseBoard weekly report</title></head>
<body style="font-family: sans-serif; max-width: 720px; margin: 2em auto;">
<h1>PulseBoard weekly report — {report.week_start} .. {report.week_end}</h1>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse;">
<tr><th>Metric</th><th>This week</th><th>Last week</th><th>Change</th><th>Days</th></tr>
{rows}
</table>
<h2>Workouts</h2><ul>{workouts}</ul>
<h2>Anomalies</h2><ul>{anomalies}</ul>
<hr><p><em>{DISCLAIMER}</em></p>
</body></html>
"""


def notification_summary(report: WeeklyReport) -> tuple[str, str]:
    """Short (title, body) for push channels: top 3 moves + anomaly count."""
    title = f"PulseBoard week {report.week_start}"
    moved = sorted(
        (c for c in report.comparisons if c.delta_pct is not None),
        key=lambda c: abs(c.delta_pct or 0),
        reverse=True,
    )[:3]
    lines = [f"{c.label}: {_format_value(c.this_week, c.unit)} ({_format_delta(c.delta_pct)})" for c in moved]
    if not lines:
        lines = ["Not enough data for week-over-week comparison yet."]
    if report.anomalies:
        lines.append(f"{len(report.anomalies)} anomaly(ies) vs baseline — see the report.")
    lines.append(DISCLAIMER)
    return title, "\n".join(lines)


def next_run_at(now: datetime, weekday: int = 0, hour: int = 8) -> datetime:
    """Next occurrence of weekday (0=Monday) at hour:00 strictly after now."""
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    days_ahead = (weekday - candidate.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _run_once(args: argparse.Namespace) -> int:
    db = Database(args.db)
    try:
        if args.check_freshness:
            age = freshness_seconds(db)
            if age is None or age > STALE_AFTER_SECONDS:
                days = "never" if age is None else f"{age / 86400.0:.1f} days ago"
                message = f"PulseBoard has not received data recently (last ingest: {days})."
                print(message)
                if args.notify:
                    notify_all("PulseBoard: data is stale", message + " See docs/SHORTCUT.md.")
                return 1
            print(f"Data is fresh: last ingest {age / 3600.0:.1f} h ago.")
            return 0

        week_ending = date_type.fromisoformat(args.week_ending) if args.week_ending else None
        report = build_weekly_report(db, week_ending)
        rendered = render_html(report) if args.format == "html" else render_markdown(report)

        if args.out:
            out_dir = Path(args.out)
            if args.out.endswith("/") or out_dir.is_dir():
                extension = "html" if args.format == "html" else "md"
                out_path = out_dir / f"pulseboard-week-{report.week_start}.{extension}"
            else:
                out_path = out_dir
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(rendered)
            print(f"Report written to {out_path}")
        else:
            print(rendered)

        if args.notify:
            title, body = notification_summary(report)
            channels = notify_all(title, body)
            print(f"Notified via: {', '.join(channels) if channels else 'no channel configured'}")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pulseboard.report",
        description="Weekly PulseBoard report (Mon-Sun, vs the previous week). Informational only.",
    )
    parser.add_argument("--db", default=None, help="SQLite path (default: $PULSEBOARD_DB_PATH or data/pulseboard.db)")
    parser.add_argument("--week-ending", default=None, help="Any date inside the target week (default: today)")
    parser.add_argument("--format", choices=("md", "html"), default="md")
    parser.add_argument("--out", default=None, help="Output file or directory (default: stdout)")
    parser.add_argument("--notify", action="store_true", help="Push a short summary via ntfy/Telegram (env-configured)")
    parser.add_argument(
        "--check-freshness",
        action="store_true",
        help="Exit 1 (and optionally --notify) when no data arrived for 2+ days, instead of reporting",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running: sleep until every Monday 08:00 and emit the report (for containers)",
    )
    args = parser.parse_args(argv)

    if not args.loop:
        return _run_once(args)

    logging.basicConfig(level=logging.INFO)
    while True:
        wake_at = next_run_at(datetime.now(timezone.utc).astimezone())
        logger.info("Next weekly report at %s", wake_at.isoformat())
        time.sleep(max((wake_at - datetime.now(timezone.utc).astimezone()).total_seconds(), 0))
        try:
            _run_once(args)
        except Exception:  # keep the loop alive; cron semantics
            logger.exception("Weekly report run failed")


if __name__ == "__main__":
    raise SystemExit(main())

"""Derived trend metrics computed from SQLite history (Phase 2).

These power the trend gauges on /metrics so Grafana alert rules can stay
simple Prometheus threshold checks instead of embedding SQL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulseboard.db import Database

ROLLING_DAYS = 7

# (canonical metric, aggregation, prometheus gauge name)
ROLLING_GAUGES: tuple[tuple[str, str, str], ...] = (
    ("steps", "sum", "pulseboard_steps_7d_avg"),
    ("sleep_hours", "sum", "pulseboard_sleep_hours_7d_avg"),
    ("resting_heart_rate", "avg", "pulseboard_resting_heart_rate_7d_avg_bpm"),
    ("heart_rate_variability_sdnn", "avg", "pulseboard_hrv_sdnn_7d_avg_ms"),
)


def rolling_average(db: "Database", metric: str, aggregation: str, days: int = ROLLING_DAYS) -> float | None:
    """Mean of the last N stored days (fewer if less history exists)."""
    rows = db.history(metric, aggregation, days=days)
    if not rows:
        return None
    values = [float(row["value"]) for row in rows]
    return round(sum(values) / len(values), 2)


def rising_days(db: "Database", metric: str, aggregation: str, max_days: int = 14) -> int:
    """Consecutive most-recent days with a strict day-over-day increase.

    [55, 56, 58, 60] -> 3; a flat or falling latest day -> 0. Gaps in the
    stored dates are treated as consecutive observations.
    """
    rows = db.history(metric, aggregation, days=max_days)
    values = [float(row["value"]) for row in rows]
    count = 0
    for i in range(len(values) - 1, 0, -1):
        if values[i] > values[i - 1]:
            count += 1
        else:
            break
    return count

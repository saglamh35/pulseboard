"""Correlation and anomaly insights computed from SQLite history.

Informational only, not medical advice — and correlation is not causation.
Method notes live in docs/INSIGHTS.md; keep both in sync.

Everything here is stdlib-only (hand-rolled Pearson, statistics.stdev) and
follows the trends.py/score.py style: pure functions over Database.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulseboard.db import Database

DISCLAIMER = "Informational only, not medical advice. Correlation is not causation. See docs/INSIGHTS.md."

WINDOW_DAYS = 90
MIN_SAMPLES = 14

BASELINE_DAYS = 30
MIN_BASELINE_DAYS = 7
ANOMALY_THRESHOLD = 2.0


@dataclass(frozen=True)
class CorrelationPair:
    """Two daily series to correlate; lag_days shifts metric_b forward, so
    lag_days=1 pairs metric_a on day d with metric_b on day d+1."""

    key: str  # Prometheus `pair` label value
    metric_a: str
    agg_a: str
    metric_b: str
    agg_b: str
    lag_days: int
    description: str


CORRELATION_PAIRS: tuple[CorrelationPair, ...] = (
    CorrelationPair(
        "sleep_vs_next_day_hrv",
        "sleep_hours",
        "sum",
        "heart_rate_variability_sdnn",
        "avg",
        1,
        "Hours slept vs. next-day HRV (SDNN)",
    ),
    CorrelationPair(
        "activity_vs_next_day_resting_hr",
        "active_energy",
        "sum",
        "resting_heart_rate",
        "avg",
        1,
        "Active energy burned vs. next-day resting heart rate",
    ),
    CorrelationPair(
        "workout_minutes_vs_next_day_hrv",
        "workouts_duration_min",
        "sum",
        "heart_rate_variability_sdnn",
        "avg",
        1,
        "Workout minutes vs. next-day HRV (SDNN)",
    ),
    CorrelationPair(
        "steps_vs_sleep_same_day",
        "steps",
        "sum",
        "sleep_hours",
        "sum",
        0,
        "Daily steps vs. that night's sleep hours",
    ),
)

# (canonical metric, aggregation) watched for day-vs-baseline anomalies.
ANOMALY_METRICS: tuple[tuple[str, str], ...] = (
    ("resting_heart_rate", "avg"),
    ("heart_rate_variability_sdnn", "avg"),
    ("sleep_hours", "sum"),
    ("steps", "sum"),
    ("respiratory_rate", "avg"),
)


@dataclass(frozen=True)
class Anomaly:
    metric: str
    date: str
    value: float
    zscore: float
    baseline_mean: float


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r; None for <2 points or a zero-variance series."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    var_x = sum(d * d for d in dx)
    var_y = sum(d * d for d in dy)
    if var_x == 0 or var_y == 0:
        return None
    cov = sum(a * b for a, b in zip(dx, dy))
    return cov / (var_x**0.5 * var_y**0.5)


def aligned_pairs(db: "Database", pair: CorrelationPair, days: int = WINDOW_DAYS) -> list[tuple[float, float]]:
    """(a, b) tuples where b is observed lag_days after a; days with either
    side missing are dropped."""
    series_a = db.series(pair.metric_a, pair.agg_a, days=days)
    series_b = db.series(pair.metric_b, pair.agg_b, days=days)
    pairs: list[tuple[float, float]] = []
    for day, value_a in series_a.items():
        shifted = (date_type.fromisoformat(day) + timedelta(days=pair.lag_days)).isoformat()
        value_b = series_b.get(shifted)
        if value_b is not None:
            pairs.append((value_a, value_b))
    return pairs


def _correlation_from_pairs(pairs: list[tuple[float, float]]) -> tuple[float, int] | None:
    if len(pairs) < MIN_SAMPLES:
        return None
    r = pearson([a for a, _ in pairs], [b for _, b in pairs])
    if r is None:
        return None
    return round(r, 3), len(pairs)


def correlation(db: "Database", pair: CorrelationPair) -> tuple[float, int] | None:
    """(pearson r, sample count) over the window; None below MIN_SAMPLES or
    when a series is constant."""
    return _correlation_from_pairs(aligned_pairs(db, pair))


def _zscore_from_rows(rows: list) -> float | None:
    """z-score of the last row's value vs. the mean/stdev of the earlier
    rows; None with <MIN_BASELINE_DAYS of baseline or zero variance."""
    if len(rows) < MIN_BASELINE_DAYS + 1:
        return None
    values = [float(row["value"]) for row in rows]
    baseline, latest = values[:-1], values[-1]
    mean = sum(baseline) / len(baseline)
    stdev = statistics.stdev(baseline)
    if stdev == 0:
        return None
    return round((latest - mean) / stdev, 2)


def zscore_latest(db: "Database", metric: str, aggregation: str, baseline_days: int = BASELINE_DAYS) -> float | None:
    """Latest day's value vs. the mean/stdev of the prior baseline days
    (latest excluded); None with <MIN_BASELINE_DAYS of baseline or zero
    variance."""
    return _zscore_from_rows(db.history(metric, aggregation, days=baseline_days + 1))


def detect_anomalies(db: "Database", threshold: float = ANOMALY_THRESHOLD) -> list[Anomaly]:
    """Watchlist metrics whose latest day deviates ≥ threshold stdevs from
    their 30-day baseline."""
    anomalies: list[Anomaly] = []
    for metric, aggregation in ANOMALY_METRICS:
        rows = db.history(metric, aggregation, days=BASELINE_DAYS + 1)
        z = _zscore_from_rows(rows)
        if z is None or abs(z) < threshold:
            continue
        values = [float(row["value"]) for row in rows]
        baseline_mean = sum(values[:-1]) / len(values[:-1])
        anomalies.append(
            Anomaly(
                metric=metric,
                date=rows[-1]["date"],
                value=values[-1],
                zscore=z,
                baseline_mean=round(baseline_mean, 2),
            )
        )
    return anomalies


def insights_summary(db: "Database") -> dict[str, object]:
    """JSON body for GET /insights."""
    correlations = []
    for pair in CORRELATION_PAIRS:
        pairs = aligned_pairs(db, pair)
        result = _correlation_from_pairs(pairs)
        correlations.append(
            {
                "pair": pair.key,
                "description": pair.description,
                "lag_days": pair.lag_days,
                "r": result[0] if result else None,
                "samples": result[1] if result else len(pairs),
            }
        )
    return {
        "window_days": WINDOW_DAYS,
        "baseline_days": BASELINE_DAYS,
        "correlations": correlations,
        "anomalies": [
            {
                "metric": a.metric,
                "date": a.date,
                "value": a.value,
                "zscore": a.zscore,
                "baseline_mean": a.baseline_mean,
            }
            for a in detect_anomalies(db)
        ],
        "disclaimer": DISCLAIMER,
    }

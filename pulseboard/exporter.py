"""Prometheus exporter: exposes the latest daily values from SQLite as gauges.

A custom Collector queries SQLite on every scrape, so /metrics always
reflects the current DB state without any refresh loop. Prometheus only
sees "today's" (latest-date) value per metric — history lives in SQLite
and is charted in Grafana directly from there.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterator

from prometheus_client import CollectorRegistry, make_asgi_app
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from pulseboard.goals import GOAL_DEFS, SLEEP_DEBT_DAYS, sleep_debt_hours, streak_days
from pulseboard.insights import ANOMALY_METRICS, CORRELATION_PAIRS, WINDOW_DAYS, correlation, zscore_latest
from pulseboard.metrics import REGISTRY
from pulseboard.readiness import compute_readiness_score
from pulseboard.score import compute_health_score
from pulseboard.training_load import ACUTE_DAYS, CHRONIC_DAYS, compute_training_load
from pulseboard.trends import ROLLING_DAYS, ROLLING_GAUGES, rising_days, rolling_average

if TYPE_CHECKING:
    from pulseboard.db import Database

# Aggregations exposed as an `agg` label on a single gauge (e.g. heart_rate
# min/avg/max). Single-aggregation metrics get a plain, label-less gauge.
_LABELLED_AGGS = ("min", "avg", "max")


class PulseboardCollector(Collector):
    def __init__(self, db: "Database") -> None:
        self._db = db

    def collect(self) -> Iterator[GaugeMetricFamily]:
        rows = self._db.latest_values()
        by_metric: dict[str, list] = {}
        for row in rows:
            by_metric.setdefault(row["metric"], []).append(row)

        for metric_name, metric_rows in by_metric.items():
            definition = REGISTRY.get(metric_name)
            if definition is None:
                continue
            if len(definition.aggregations) > 1:
                family = GaugeMetricFamily(
                    definition.prom_name,
                    f"{definition.description} ({definition.unit})",
                    labels=["agg"],
                )
                for row in metric_rows:
                    if row["aggregation"] in _LABELLED_AGGS:
                        family.add_metric([row["aggregation"]], row["value"])
                yield family
            else:
                family = GaugeMetricFamily(
                    definition.prom_name,
                    f"{definition.description} ({definition.unit})",
                )
                family.add_metric([], metric_rows[0]["value"])
                yield family

        score = compute_health_score(self._db)
        if score is not None:
            score_family = GaugeMetricFamily(
                "pulseboard_health_score",
                "Composite 0-100 daily score (informational only, not medical advice); see docs/SCORE.md",
            )
            score_family.add_metric([], score)
            yield score_family

        readiness = compute_readiness_score(self._db)
        if readiness is not None:
            readiness_family = GaugeMetricFamily(
                "pulseboard_readiness_score",
                "Morning readiness 0-100 from HRV, resting HR and last night's sleep "
                "(informational only, not medical advice); see docs/SCORE.md",
            )
            readiness_family.add_metric([], readiness)
            yield readiness_family

        yield from self._goal_families()
        yield from self._training_load_families()

        for metric_name, aggregation, prom_name in ROLLING_GAUGES:
            average = rolling_average(self._db, metric_name, aggregation)
            if average is None:
                continue
            trend_family = GaugeMetricFamily(
                prom_name, f"Rolling {ROLLING_DAYS}-day mean of {metric_name} ({aggregation})"
            )
            trend_family.add_metric([], average)
            yield trend_family

        if self._db.history("resting_heart_rate", "avg", days=1):
            rising_family = GaugeMetricFamily(
                "pulseboard_resting_heart_rate_rising_days",
                "Consecutive days the daily resting heart rate has strictly increased",
            )
            rising_family.add_metric([], rising_days(self._db, "resting_heart_rate", "avg"))
            yield rising_family

        yield from self._freshness_families()
        yield from self._insight_families()

    def _goal_families(self) -> Iterator[GaugeMetricFamily]:
        """Goal streaks, goal targets and sleep debt (docs/GOALS.md)."""
        streak_family = GaugeMetricFamily(
            "pulseboard_goal_streak_days",
            "Consecutive days the daily goal was met, ending at the metric's latest stored day; see docs/GOALS.md",
            labels=["metric"],
        )
        target_family = GaugeMetricFamily(
            "pulseboard_goal_target",
            "Configured daily goal value per metric (registry-declared); see docs/GOALS.md",
            labels=["metric"],
        )
        has_streaks = False
        for definition in GOAL_DEFS:
            streak = streak_days(self._db, definition.name)
            if streak is None:
                continue
            streak_family.add_metric([definition.name], streak)
            assert definition.goal is not None
            target_family.add_metric([definition.name], definition.goal.value)
            has_streaks = True
        if has_streaks:
            yield streak_family
            yield target_family

        debt = sleep_debt_hours(self._db)
        if debt is not None:
            debt_family = GaugeMetricFamily(
                "pulseboard_sleep_debt_hours",
                f"Cumulative sleep shortfall vs the daily sleep goal over the last {SLEEP_DEBT_DAYS} nights "
                "(informational only, not medical advice); see docs/GOALS.md",
            )
            debt_family.add_metric([], debt)
            yield debt_family

    def _training_load_families(self) -> Iterator[GaugeMetricFamily]:
        """Acute/chronic workout load and their ratio (docs/TRAINING_LOAD.md)."""
        load = compute_training_load(self._db)
        if load is None:
            return
        acute_family = GaugeMetricFamily(
            f"pulseboard_training_load_acute_{ACUTE_DAYS}d_minutes",
            f"Total workout minutes over the last {ACUTE_DAYS} days",
        )
        acute_family.add_metric([], load.acute_minutes)
        yield acute_family
        chronic_family = GaugeMetricFamily(
            f"pulseboard_training_load_chronic_{CHRONIC_DAYS}d_minutes",
            f"Total workout minutes over the last {CHRONIC_DAYS} days",
        )
        chronic_family.add_metric([], load.chronic_minutes)
        yield chronic_family
        if load.acwr is not None:
            acwr_family = GaugeMetricFamily(
                "pulseboard_training_load_acwr",
                "Acute:chronic workload ratio — 7-day vs 28-day daily-average workout minutes "
                "(informational only, not medical advice); see docs/TRAINING_LOAD.md",
            )
            acwr_family.add_metric([], load.acwr)
            yield acwr_family

    def _insight_families(self) -> Iterator[GaugeMetricFamily]:
        """Correlation and anomaly gauges from pulseboard.insights; computed
        per scrape like everything else (trivial at personal-data scale)."""
        corr_family = GaugeMetricFamily(
            "pulseboard_correlation",
            f"Pearson r between two daily series over the last {WINDOW_DAYS} days "
            "(informational only; correlation is not causation)",
            labels=["pair"],
        )
        samples_family = GaugeMetricFamily(
            "pulseboard_correlation_samples",
            "Number of aligned day pairs behind pulseboard_correlation",
            labels=["pair"],
        )
        has_correlations = False
        for pair in CORRELATION_PAIRS:
            result = correlation(self._db, pair)
            if result is None:
                continue
            r, n = result
            corr_family.add_metric([pair.key], r)
            samples_family.add_metric([pair.key], n)
            has_correlations = True
        if has_correlations:
            yield corr_family
            yield samples_family

        zscore_family = GaugeMetricFamily(
            "pulseboard_zscore",
            "Latest day's value vs. its 30-day baseline, in standard deviations "
            "(informational only, not medical advice)",
            labels=["metric"],
        )
        has_zscores = False
        for metric, aggregation in ANOMALY_METRICS:
            z = zscore_latest(self._db, metric, aggregation)
            if z is None:
                continue
            zscore_family.add_metric([metric], z)
            has_zscores = True
        if has_zscores:
            yield zscore_family

    def _freshness_families(self) -> Iterator[GaugeMetricFamily]:
        """Two distinct freshness signals: when did the phone last POST
        anything (ingest), and how new is the newest data day (staleness
        alerts key off the latter — a backfill of old days bumps only the
        former)."""
        last_ingest = self._db.last_ingest_at()
        if last_ingest is not None:
            ingest_family = GaugeMetricFamily(
                "pulseboard_last_ingest_timestamp_seconds",
                "Unix time of the most recent successful ingest (any dates)",
            )
            ingest_family.add_metric([], datetime.fromisoformat(last_ingest).timestamp())
            yield ingest_family

        latest_date = self._db.latest_metric_date()
        if latest_date is not None:
            data_family = GaugeMetricFamily(
                "pulseboard_latest_data_timestamp_seconds",
                "Unix time (midnight UTC) of the newest day we have data for",
            )
            midnight = datetime.fromisoformat(latest_date).replace(tzinfo=timezone.utc)
            data_family.add_metric([], midnight.timestamp())
            yield data_family


def build_metrics_app(db: "Database"):
    """ASGI app serving /metrics from a dedicated registry (no default
    process/python collectors — only PulseBoard gauges)."""
    registry = CollectorRegistry()
    registry.register(PulseboardCollector(db))
    return make_asgi_app(registry)

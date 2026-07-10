"""Prometheus exporter: exposes the latest daily values from SQLite as gauges.

A custom Collector queries SQLite on every scrape, so /metrics always
reflects the current DB state without any refresh loop. Prometheus only
sees "today's" (latest-date) value per metric — history lives in SQLite
and is charted in Grafana directly from there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from prometheus_client import CollectorRegistry, make_asgi_app
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from pulseboard.metrics import REGISTRY
from pulseboard.score import compute_health_score

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


def build_metrics_app(db: "Database"):
    """ASGI app serving /metrics from a dedicated registry (no default
    process/python collectors — only PulseBoard gauges)."""
    registry = CollectorRegistry()
    registry.register(PulseboardCollector(db))
    return make_asgi_app(registry)

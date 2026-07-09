"""Canonical metric registry: the single source of truth for metric names,
units, aggregations, and Prometheus gauge names.

Phase 0 starts with `steps` only; the full Phase 1 set is added on top of
this structure.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDef:
    name: str  # canonical name, used as the `metric` column in SQLite
    unit: str
    aggregations: tuple[str, ...]  # allowed aggregations; first one is the default
    prom_name: str  # Prometheus gauge name
    description: str

    @property
    def default_aggregation(self) -> str:
        return self.aggregations[0]


_DEFS: tuple[MetricDef, ...] = (MetricDef("steps", "count", ("sum",), "pulseboard_steps", "Daily step count"),)

REGISTRY: dict[str, MetricDef] = {d.name: d for d in _DEFS}

# Apple HealthKit record type -> canonical name (used by the backfill CLI).
HEALTHKIT_TO_CANONICAL: dict[str, str] = {
    "HKQuantityTypeIdentifierStepCount": "steps",
}

# Health Auto Export metric name -> canonical name (used by the HAE adapter).
HAE_TO_CANONICAL: dict[str, str] = {
    "step_count": "steps",
}


def get_metric(name: str) -> MetricDef | None:
    return REGISTRY.get(name)

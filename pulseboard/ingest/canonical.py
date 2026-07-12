"""Canonical ingest payload: {"date": "...", "metrics": [{name, value, unit?, aggregation?}]}.

Unknown metrics and unsupported aggregations are skipped (logged, reported
in the response) — a partially-valid payload never fails the whole request.
"""

from __future__ import annotations

import logging
from datetime import date as date_type

from pydantic import BaseModel, Field

from pulseboard.db import MetricRecord
from pulseboard.metrics import REGISTRY

logger = logging.getLogger(__name__)


class CanonicalMetric(BaseModel):
    name: str
    # json.loads accepts NaN/Infinity literals; reject them here so they can't
    # reach the DB and leak into Prometheus gauges.
    value: float = Field(allow_inf_nan=False)
    unit: str | None = None
    aggregation: str | None = None


class CanonicalPayload(BaseModel):
    date: date_type
    metrics: list[CanonicalMetric] = Field(default_factory=list)


def normalize(payload: CanonicalPayload, source: str = "canonical") -> tuple[list[MetricRecord], list[str]]:
    """Turn a validated payload into MetricRecords; returns (records, skipped names)."""
    records: list[MetricRecord] = []
    skipped: list[str] = []
    for item in payload.metrics:
        definition = REGISTRY.get(item.name)
        if definition is None:
            logger.warning("Skipping unknown metric %r", item.name)
            skipped.append(item.name)
            continue
        aggregation = item.aggregation or definition.default_aggregation
        if aggregation not in definition.aggregations:
            logger.warning("Skipping metric %r: unsupported aggregation %r", item.name, aggregation)
            skipped.append(item.name)
            continue
        records.append(
            MetricRecord(
                date=payload.date.isoformat(),
                metric=item.name,
                value=float(item.value),
                unit=item.unit or definition.unit,
                aggregation=aggregation,
                source=source,
            )
        )
    return records, skipped

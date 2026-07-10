"""Adapter for the Health Auto Export iOS app's REST export payload.

HAE posts {"data": {"metrics": [{"name", "units", "data": [...]}]}} where each
data point carries a "date" plus either a single "qty" or min/avg/max fields
(capitalization varies across HAE versions). Everything is mapped onto the
canonical metric registry; unknown metric names are skipped, never an error.
"""

from __future__ import annotations

import logging
from datetime import date as date_type

from pulseboard.db import MetricRecord, WorkoutRecord
from pulseboard.metrics import HAE_TO_CANONICAL, REGISTRY

logger = logging.getLogger(__name__)

SOURCE = "health_auto_export"

# HAE sleep_analysis point field -> per-stage canonical metric
_SLEEP_STAGE_FIELDS = (
    ("core", "sleep_core_hours"),
    ("deep", "sleep_deep_hours"),
    ("rem", "sleep_rem_hours"),
    ("awake", "sleep_awake_hours"),
)


def is_hae_payload(payload: dict) -> bool:
    return isinstance(payload.get("data"), dict)


def _parse_date(raw: object) -> str | None:
    """HAE dates look like '2026-07-09 00:12:34 +0200'; keep the date part."""
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date_type.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def _get_number(point: dict, *keys: str) -> float | None:
    for key in keys:
        value = point.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def normalize_hae(payload: dict) -> tuple[list[MetricRecord], list[str]]:
    """Map an HAE payload to MetricRecords; returns (records, skipped names)."""
    records: list[MetricRecord] = []
    skipped: list[str] = []

    for entry in payload.get("data", {}).get("metrics") or []:
        if not isinstance(entry, dict):
            continue
        hae_name = str(entry.get("name"))
        canonical_name = HAE_TO_CANONICAL.get(hae_name)
        definition = REGISTRY.get(canonical_name) if canonical_name else None
        if definition is None:
            logger.warning("Skipping unknown HAE metric %r", hae_name)
            skipped.append(hae_name)
            continue

        for point in entry.get("data") or []:
            if not isinstance(point, dict):
                continue
            day = _parse_date(point.get("date"))
            if day is None:
                logger.warning("Skipping HAE point without a parseable date for %r", hae_name)
                continue

            if hae_name == "sleep_analysis":
                value = _get_number(point, "asleep", "totalSleep", "qty")
                if value is not None:
                    records.append(MetricRecord(day, definition.name, value, definition.unit, "sum", SOURCE))
                for field, stage_metric in _SLEEP_STAGE_FIELDS:
                    stage_value = _get_number(point, field)
                    if stage_value is not None:
                        records.append(MetricRecord(day, stage_metric, stage_value, "h", "sum", SOURCE))
                continue

            if len(definition.aggregations) > 1:
                found_any = False
                for agg, keys in (("min", ("min", "Min")), ("avg", ("avg", "Avg")), ("max", ("max", "Max"))):
                    value = _get_number(point, *keys)
                    if value is not None and agg in definition.aggregations:
                        records.append(MetricRecord(day, definition.name, value, definition.unit, agg, SOURCE))
                        found_any = True
                if not found_any:
                    value = _get_number(point, "qty")
                    if value is not None:
                        records.append(
                            MetricRecord(
                                day, definition.name, value, definition.unit, definition.default_aggregation, SOURCE
                            )
                        )
                continue

            value = _get_number(point, "qty")
            if value is None:
                logger.warning("Skipping HAE point without a numeric value for %r", hae_name)
                continue
            records.append(
                MetricRecord(day, definition.name, value, definition.unit, definition.default_aggregation, SOURCE)
            )

    return records, skipped


def _quantity(entry: dict, *keys: str) -> float:
    """HAE quantities are either plain numbers or {"qty": n, "units": ...}."""
    for key in keys:
        value = entry.get(key)
        if isinstance(value, dict):
            value = value.get("qty")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def extract_workouts(payload: dict) -> list[WorkoutRecord]:
    """Map HAE data.workouts[] entries to per-workout drilldown rows."""
    workouts: list[WorkoutRecord] = []
    for entry in payload.get("data", {}).get("workouts") or []:
        if not isinstance(entry, dict):
            continue
        start_raw = entry.get("start")
        day = _parse_date(start_raw)
        if day is None:
            logger.warning("Skipping HAE workout without a parseable start date")
            continue
        workouts.append(
            WorkoutRecord(
                start=str(start_raw),
                date=day,
                activity_type=str(entry.get("name", "Unknown")),
                duration_min=round(_quantity(entry, "duration"), 2),
                energy_kcal=round(_quantity(entry, "activeEnergyBurned", "activeEnergy"), 2),
                distance_km=round(_quantity(entry, "distance"), 3),
                source=SOURCE,
            )
        )
    return workouts

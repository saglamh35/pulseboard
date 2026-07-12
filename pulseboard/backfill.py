"""Backfill daily metrics from an Apple Health export.xml.

Usage: python -m pulseboard.backfill /path/to/export.xml [--db path]

The export can be hundreds of MB, so the file is streamed with
xml.etree.ElementTree.iterparse and elements are cleared as soon as they are
processed — the whole document is never held in memory. Records are
aggregated in memory per (date, metric) as running sums / min-avg-max /
latest, then upserted in one batch; re-running on the same file is a no-op
thanks to the UNIQUE(date, metric, aggregation) upsert.
"""

from __future__ import annotations

import argparse
import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import BinaryIO
from xml.etree.ElementTree import iterparse

from pulseboard.db import Database, MetricRecord, WorkoutRecord
from pulseboard.metrics import HEALTHKIT_TO_CANONICAL, REGISTRY

logger = logging.getLogger(__name__)

SOURCE = "export_xml"

_SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
_ASLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisAsleep",
    "HKCategoryValueSleepAnalysisAsleepUnspecified",
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
}
_SLEEP_STAGE_METRIC = {
    "HKCategoryValueSleepAnalysisAsleepCore": "sleep_core_hours",
    "HKCategoryValueSleepAnalysisAsleepDeep": "sleep_deep_hours",
    "HKCategoryValueSleepAnalysisAsleepREM": "sleep_rem_hours",
    "HKCategoryValueSleepAnalysisAwake": "sleep_awake_hours",
}
_STAND_HOUR_TYPE = "HKCategoryTypeIdentifierAppleStandHour"
_STOOD_VALUE = "HKCategoryValueAppleStandHourStood"
_MINDFUL_TYPE = "HKCategoryTypeIdentifierMindfulSession"

_APPLE_TS_FORMAT = "%Y-%m-%d %H:%M:%S %z"


class _EntityDeclGuard:
    """Binary-file wrapper that rejects XML entity declarations.

    ElementTree expands internal DTD entities, so a crafted export.xml could
    mount an entity-expansion (billion laughs) attack. Real Apple exports
    carry a DOCTYPE with only ELEMENT/ATTLIST declarations — never ENTITY —
    and in well-formed XML the byte sequence ``<!ENTITY`` cannot appear
    outside the DTD (a literal ``<`` is illegal in text and attribute
    values), so scanning the raw bytes is a safe, stdlib-only guard."""

    _PATTERN = b"<!ENTITY"

    def __init__(self, fileobj: BinaryIO) -> None:
        self._file = fileobj
        self._tail = b""  # carry-over so the pattern can't hide across a chunk boundary

    def read(self, size: int = -1) -> bytes:
        chunk = self._file.read(size)
        window = self._tail + chunk
        if self._PATTERN in window:
            raise ValueError("export.xml declares XML entities; refusing to parse (entity-expansion risk)")
        self._tail = window[-(len(self._PATTERN) - 1) :]
        return chunk


def _parse_ts(raw: str) -> datetime:
    return datetime.strptime(raw, _APPLE_TS_FORMAT)


def _parse_finite(raw: str) -> float | None:
    """float() that treats non-finite values ("nan", "inf") as unparseable."""
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _convert_value(canonical: str, value: float, unit: str) -> float:
    """Normalize export units onto the canonical ones where they can differ."""
    if canonical == "distance_walking_running" and unit == "mi":
        return value * 1.609344
    if canonical in ("active_energy", "basal_energy", "workouts_energy_kcal") and unit.lower() == "kj":
        return value / 4.184
    if canonical in ("blood_oxygen_saturation", "body_fat_percentage") and value <= 1.0:
        return value * 100.0  # exported as a 0-1 fraction
    if canonical == "walking_speed" and unit == "m/s":
        return value * 3.6
    if canonical == "wrist_temperature" and unit == "degF":
        return (value - 32.0) / 1.8
    return value


class _Aggregator:
    """Running per-(date, metric) aggregates; bounded memory regardless of
    how many raw records the export contains."""

    def __init__(self) -> None:
        self.sums: dict[tuple[str, str], float] = defaultdict(float)
        # (count, total, min, max) for metrics exposed as min/avg/max
        self.stats: dict[tuple[str, str], list[float]] = {}
        # (timestamp, value): last observation wins for "latest" metrics
        self.latest: dict[tuple[str, str], tuple[str, float]] = {}
        # per-workout drilldown rows (one per <Workout> element)
        self.workouts: list[WorkoutRecord] = []

    def add(self, canonical: str, day: str, timestamp: str, value: float) -> None:
        definition = REGISTRY[canonical]
        key = (day, canonical)
        if definition.aggregations == ("latest",):
            current = self.latest.get(key)
            if current is None or timestamp >= current[0]:
                self.latest[key] = (timestamp, value)
        elif len(definition.aggregations) > 1:
            stat = self.stats.get(key)
            if stat is None:
                self.stats[key] = [1.0, value, value, value]
            else:
                stat[0] += 1
                stat[1] += value
                stat[2] = min(stat[2], value)
                stat[3] = max(stat[3], value)
        else:
            self.sums[key] += value

    def to_records(self) -> list[MetricRecord]:
        records: list[MetricRecord] = []
        for (day, canonical), total in self.sums.items():
            definition = REGISTRY[canonical]
            records.append(MetricRecord(day, canonical, round(total, 4), definition.unit, "sum", SOURCE))
        for (day, canonical), (count, total, minimum, maximum) in self.stats.items():
            definition = REGISTRY[canonical]
            values = {"avg": round(total / count, 4), "min": minimum, "max": maximum}
            for agg in definition.aggregations:
                records.append(MetricRecord(day, canonical, values[agg], definition.unit, agg, SOURCE))
        for (day, canonical), (_, value) in self.latest.items():
            definition = REGISTRY[canonical]
            records.append(MetricRecord(day, canonical, value, definition.unit, "latest", SOURCE))
        return records


def _handle_record(elem, agg: _Aggregator) -> None:
    record_type = elem.get("type", "")
    start = elem.get("startDate", "")
    if len(start) < 10:
        return

    if record_type == _SLEEP_TYPE:
        value_kind = elem.get("value", "")
        stage_metric = _SLEEP_STAGE_METRIC.get(value_kind)
        if value_kind not in _ASLEEP_VALUES and stage_metric is None:
            return  # InBed and other non-sleep intervals
        end_raw = elem.get("endDate", "")
        if len(end_raw) < 10:
            return
        try:
            hours = (_parse_ts(end_raw) - _parse_ts(start)).total_seconds() / 3600.0
        except ValueError:
            return
        hours = max(hours, 0.0)
        # Attribute the interval to the morning it ends: that night's sleep.
        # Awake intervals feed their stage metric but never the asleep total.
        if value_kind in _ASLEEP_VALUES:
            agg.add("sleep_hours", end_raw[:10], end_raw, hours)
        if stage_metric is not None:
            agg.add(stage_metric, end_raw[:10], end_raw, hours)
        return

    if record_type == _STAND_HOUR_TYPE:
        if elem.get("value") == _STOOD_VALUE:
            agg.add("apple_stand_hours", start[:10], start, 1.0)
        return

    if record_type == _MINDFUL_TYPE:
        # Interval record like sleep: the duration is the value, in minutes.
        end_raw = elem.get("endDate", "")
        if len(end_raw) < 10:
            return
        try:
            minutes = (_parse_ts(end_raw) - _parse_ts(start)).total_seconds() / 60.0
        except ValueError:
            return
        if minutes > 0:
            agg.add("mindful_minutes", start[:10], start, minutes)
        return

    canonical = HEALTHKIT_TO_CANONICAL.get(record_type)
    if canonical is None:
        return
    value = _parse_finite(elem.get("value", ""))
    if value is None:
        return
    agg.add(canonical, start[:10], start, _convert_value(canonical, value, elem.get("unit", "")))


def _handle_workout(elem, agg: _Aggregator) -> None:
    start = elem.get("startDate", "")
    if len(start) < 10:
        return
    day = start[:10]
    agg.add("workouts_count", day, start, 1.0)
    duration = _parse_finite(elem.get("duration", "")) or 0.0
    if elem.get("durationUnit", "min") == "s":
        duration /= 60.0
    if duration:
        agg.add("workouts_duration_min", day, start, duration)
    energy = _parse_finite(elem.get("totalEnergyBurned", "")) or 0.0
    if energy:
        unit = elem.get("totalEnergyBurnedUnit", "kcal")
        energy = _convert_value("workouts_energy_kcal", energy, unit)
        agg.add("workouts_energy_kcal", day, start, energy)
    distance = _parse_finite(elem.get("totalDistance", "")) or 0.0
    if elem.get("totalDistanceUnit", "km") == "mi":
        distance *= 1.609344
    activity = elem.get("workoutActivityType", "Unknown").removeprefix("HKWorkoutActivityType")
    agg.workouts.append(
        WorkoutRecord(
            start=start,
            date=day,
            activity_type=activity,
            duration_min=round(duration, 2),
            energy_kcal=round(energy, 2),
            distance_km=round(distance, 3),
            source=SOURCE,
        )
    )


def parse_export(path: str) -> tuple[list[MetricRecord], list[WorkoutRecord]]:
    """Stream export.xml and return (daily metric records, per-workout rows)."""
    agg = _Aggregator()
    with open(path, "rb") as fileobj:
        context = iterparse(_EntityDeclGuard(fileobj), events=("start", "end"))
        _, root = next(context)  # grab the document root so processed children can be dropped
        for event, elem in context:
            if event != "end":
                continue
            if elem.tag == "Record":
                _handle_record(elem, agg)
            elif elem.tag == "Workout":
                _handle_workout(elem, agg)
            else:
                continue
            elem.clear()
            root.clear()
    return agg.to_records(), agg.workouts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pulseboard.backfill",
        description="Backfill daily metrics from an Apple Health export.xml (streaming, idempotent).",
    )
    parser.add_argument("export_path", help="Path to the unzipped export.xml")
    parser.add_argument("--db", default=None, help="SQLite path (default: $PULSEBOARD_DB_PATH or data/pulseboard.db)")
    args = parser.parse_args(argv)

    records, workouts = parse_export(args.export_path)
    if not records and not workouts:
        print("No supported records found in the export.")
        return 1

    db = Database(args.db)
    try:
        rows_before = db.count_rows()
        db.upsert_records(records)
        db.upsert_workouts(workouts)
        rows_after = db.count_rows()
    finally:
        db.close()

    dates = sorted({r.date for r in records})
    print(f"Upserted {len(records)} daily rows into {db.path} ({rows_after - rows_before} new, rest updated)")
    print(f"Dates covered: {dates[0]} .. {dates[-1]} ({len(dates)} days)")
    print(f"Metrics: {', '.join(sorted({r.metric for r in records}))}")
    print(f"Workouts: {len(workouts)} sessions upserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

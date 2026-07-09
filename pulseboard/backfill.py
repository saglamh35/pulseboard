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
from collections import defaultdict
from datetime import datetime
from xml.etree.ElementTree import iterparse

from pulseboard.db import Database, MetricRecord
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
_STAND_HOUR_TYPE = "HKCategoryTypeIdentifierAppleStandHour"
_STOOD_VALUE = "HKCategoryValueAppleStandHourStood"

_APPLE_TS_FORMAT = "%Y-%m-%d %H:%M:%S %z"


def _parse_ts(raw: str) -> datetime:
    return datetime.strptime(raw, _APPLE_TS_FORMAT)


def _convert_value(canonical: str, value: float, unit: str) -> float:
    """Normalize export units onto the canonical ones where they can differ."""
    if canonical == "distance_walking_running" and unit == "mi":
        return value * 1.609344
    if canonical in ("active_energy", "basal_energy", "workouts_energy_kcal") and unit.lower() == "kj":
        return value / 4.184
    if canonical == "blood_oxygen_saturation" and value <= 1.0:
        return value * 100.0  # exported as a 0-1 fraction
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
        if elem.get("value") not in _ASLEEP_VALUES:
            return
        end_raw = elem.get("endDate", "")
        if len(end_raw) < 10:
            return
        try:
            hours = (_parse_ts(end_raw) - _parse_ts(start)).total_seconds() / 3600.0
        except ValueError:
            return
        # Attribute the interval to the morning it ends: that night's sleep.
        agg.add("sleep_hours", end_raw[:10], end_raw, max(hours, 0.0))
        return

    if record_type == _STAND_HOUR_TYPE:
        if elem.get("value") == _STOOD_VALUE:
            agg.add("apple_stand_hours", start[:10], start, 1.0)
        return

    canonical = HEALTHKIT_TO_CANONICAL.get(record_type)
    if canonical is None:
        return
    try:
        value = float(elem.get("value", ""))
    except ValueError:
        return
    agg.add(canonical, start[:10], start, _convert_value(canonical, value, elem.get("unit", "")))


def _handle_workout(elem, agg: _Aggregator) -> None:
    start = elem.get("startDate", "")
    if len(start) < 10:
        return
    day = start[:10]
    agg.add("workouts_count", day, start, 1.0)
    try:
        duration = float(elem.get("duration", ""))
    except ValueError:
        duration = 0.0
    if elem.get("durationUnit", "min") == "s":
        duration /= 60.0
    if duration:
        agg.add("workouts_duration_min", day, start, duration)
    try:
        energy = float(elem.get("totalEnergyBurned", ""))
    except ValueError:
        energy = 0.0
    if energy:
        unit = elem.get("totalEnergyBurnedUnit", "kcal")
        agg.add("workouts_energy_kcal", day, start, _convert_value("workouts_energy_kcal", energy, unit))


def parse_export(path: str) -> list[MetricRecord]:
    """Stream export.xml and return aggregated daily records."""
    agg = _Aggregator()
    context = iterparse(path, events=("start", "end"))
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
    return agg.to_records()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pulseboard.backfill",
        description="Backfill daily metrics from an Apple Health export.xml (streaming, idempotent).",
    )
    parser.add_argument("export_path", help="Path to the unzipped export.xml")
    parser.add_argument("--db", default=None, help="SQLite path (default: $PULSEBOARD_DB_PATH or data/pulseboard.db)")
    args = parser.parse_args(argv)

    records = parse_export(args.export_path)
    if not records:
        print("No supported records found in the export.")
        return 1

    db = Database(args.db)
    try:
        rows_before = db.count_rows()
        db.upsert_records(records)
        rows_after = db.count_rows()
    finally:
        db.close()

    dates = sorted({r.date for r in records})
    print(f"Upserted {len(records)} daily rows into {db.path} ({rows_after - rows_before} new, rest updated)")
    print(f"Dates covered: {dates[0]} .. {dates[-1]} ({len(dates)} days)")
    print(f"Metrics: {', '.join(sorted({r.metric for r in records}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

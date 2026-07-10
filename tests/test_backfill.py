from pathlib import Path

import pytest

from pulseboard.backfill import _convert_value, main, parse_export
from pulseboard.db import Database

SAMPLE_XML = str(Path(__file__).parent.parent / "samples" / "sample_export.xml")


@pytest.fixture(scope="module")
def parsed():
    return parse_export(SAMPLE_XML)


@pytest.fixture(scope="module")
def records(parsed):
    return parsed[0]


def by_key(records):
    return {(r.metric, r.date, r.aggregation): r.value for r in records}


class TestParseExport:
    def test_cumulative_metrics_sum_per_day(self, records):
        values = by_key(records)
        assert values[("steps", "2026-07-01", "sum")] == 8000
        assert values[("steps", "2026-07-02", "sum")] == 12000
        assert values[("steps", "2026-07-03", "sum")] == 9500
        assert values[("distance_walking_running", "2026-07-01", "sum")] == 6.0
        assert values[("active_energy", "2026-07-03", "sum")] == 250.5
        assert values[("apple_exercise_time", "2026-07-02", "sum")] == 42

    def test_heart_rate_min_avg_max(self, records):
        values = by_key(records)
        assert values[("heart_rate", "2026-07-01", "min")] == 55
        assert values[("heart_rate", "2026-07-01", "avg")] == 85
        assert values[("heart_rate", "2026-07-01", "max")] == 120
        assert values[("heart_rate", "2026-07-02", "avg")] == 80

    def test_oxygen_fraction_converted_to_percent(self, records):
        values = by_key(records)
        assert values[("blood_oxygen_saturation", "2026-07-01", "min")] == 96
        assert values[("blood_oxygen_saturation", "2026-07-01", "avg")] == 97
        assert values[("blood_oxygen_saturation", "2026-07-01", "max")] == 98

    def test_sleep_sums_asleep_intervals_per_night(self, records):
        values = by_key(records)
        # 4h deep + 3h REM ending on the morning of 07-01; in-bed interval ignored
        assert values[("sleep_hours", "2026-07-01", "sum")] == 7.0
        assert values[("sleep_hours", "2026-07-02", "sum")] == 6.8

    def test_stand_hours_count_only_stood(self, records):
        assert by_key(records)[("apple_stand_hours", "2026-07-01", "sum")] == 2

    def test_latest_metrics_keep_one_value_per_day(self, records):
        values = by_key(records)
        assert values[("body_mass", "2026-07-01", "latest")] == 78.5
        assert values[("body_mass", "2026-07-03", "latest")] == 78.2
        assert values[("vo2_max", "2026-07-02", "latest")] == 41.5

    def test_workout_rollups(self, records):
        values = by_key(records)
        assert values[("workouts_count", "2026-07-02", "sum")] == 1
        assert values[("workouts_duration_min", "2026-07-02", "sum")] == 31.5
        assert values[("workouts_energy_kcal", "2026-07-02", "sum")] == 342

    def test_unsupported_types_are_ignored(self, records):
        assert all(r.metric != "dietary_water" for r in records)
        assert all(r.source == "export_xml" for r in records)


class TestConvertValue:
    def test_miles_to_km(self):
        assert _convert_value("distance_walking_running", 1.0, "mi") == pytest.approx(1.609344)

    def test_kilojoule_to_kcal(self):
        assert _convert_value("active_energy", 418.4, "kJ") == pytest.approx(100.0)

    def test_plain_values_pass_through(self):
        assert _convert_value("steps", 100.0, "count") == 100.0


class TestBackfillCli:
    def test_backfill_and_rerun_is_idempotent(self, tmp_path, capsys):
        db_path = str(tmp_path / "test.db")
        assert main([SAMPLE_XML, "--db", db_path]) == 0
        out = capsys.readouterr().out
        assert "2026-07-01 .. 2026-07-03" in out

        db = Database(db_path)
        count_first = db.count_rows()
        assert count_first > 0
        db.close()

        assert main([SAMPLE_XML, "--db", db_path]) == 0
        db = Database(db_path)
        assert db.count_rows() == count_first
        row = db.history("steps")[0]
        assert row["source"] == "export_xml"
        db.close()

    def test_empty_export_returns_error(self, tmp_path):
        empty = tmp_path / "empty.xml"
        empty.write_text("<HealthData></HealthData>")
        assert main([str(empty), "--db", str(tmp_path / "test.db")]) == 1


class TestSleepStages:
    def test_stage_intervals_feed_stage_metrics(self, records):
        values = by_key(records)
        assert values[("sleep_deep_hours", "2026-07-01", "sum")] == 4.0
        assert values[("sleep_rem_hours", "2026-07-01", "sum")] == 3.0
        assert values[("sleep_awake_hours", "2026-07-01", "sum")] == 0.25

    def test_awake_excluded_from_asleep_total(self, records):
        assert by_key(records)[("sleep_hours", "2026-07-01", "sum")] == 7.0

    def test_unspecified_sleep_has_no_stage_rows(self, records):
        values = by_key(records)
        assert ("sleep_deep_hours", "2026-07-02", "sum") not in values
        assert values[("sleep_hours", "2026-07-02", "sum")] == 6.8


class TestWorkoutDrilldown:
    def test_parse_export_yields_workout_rows(self, parsed):
        workouts = parsed[1]
        assert len(workouts) == 1
        workout = workouts[0]
        assert workout.activity_type == "Running"
        assert workout.date == "2026-07-02"
        assert workout.duration_min == 31.5
        assert workout.energy_kcal == 342
        assert workout.distance_km == 5.2
        assert workout.source == "export_xml"

    def test_cli_upserts_workouts_idempotently(self, tmp_path):
        db_path = str(tmp_path / "w.db")
        assert main([SAMPLE_XML, "--db", db_path]) == 0
        assert main([SAMPLE_XML, "--db", db_path]) == 0
        db = Database(db_path)
        assert db.count_workouts() == 1
        row = db.recent_workouts()[0]
        assert row["activity_type"] == "Running"
        db.close()

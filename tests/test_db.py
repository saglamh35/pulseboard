import threading

from pulseboard.db import Database, MetricRecord, WorkoutRecord


def make_record(**overrides) -> MetricRecord:
    defaults = dict(
        date="2026-07-01",
        metric="steps",
        value=8250.0,
        unit="count",
        aggregation="sum",
        source="canonical",
    )
    defaults.update(overrides)
    return MetricRecord(**defaults)


class TestUpsert:
    def test_insert_new_rows(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        stored = db.upsert_records([make_record(), make_record(date="2026-07-02", value=9100.0)])
        assert stored == 2
        assert db.count_rows() == 2

    def test_same_key_updates_in_place(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records([make_record(value=1000.0)])
        db.upsert_records([make_record(value=2000.0, source="export_xml")])
        assert db.count_rows() == 1
        row = db.history("steps")[0]
        assert row["value"] == 2000.0
        assert row["source"] == "export_xml"

    def test_different_aggregations_are_separate_rows(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records(
            [
                make_record(metric="heart_rate", aggregation="min", value=48.0),
                make_record(metric="heart_rate", aggregation="max", value=161.0),
            ]
        )
        assert db.count_rows() == 2

    def test_reingest_is_idempotent(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        records = [make_record(), make_record(date="2026-07-02")]
        db.upsert_records(records)
        db.upsert_records(records)
        assert db.count_rows() == 2


class TestQueries:
    def test_latest_values_picks_max_date_per_metric(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records(
            [
                make_record(date="2026-07-01", value=8000.0),
                make_record(date="2026-07-03", value=12000.0),
                make_record(date="2026-07-02", value=9000.0),
            ]
        )
        rows = db.latest_values()
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-07-03"
        assert rows[0]["value"] == 12000.0

    def test_history_is_date_ascending(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records(
            [
                make_record(date="2026-07-03"),
                make_record(date="2026-07-01"),
                make_record(date="2026-07-02"),
            ]
        )
        dates = [row["date"] for row in db.history("steps")]
        assert dates == ["2026-07-01", "2026-07-02", "2026-07-03"]

    def test_history_date_window_is_inclusive(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        for day in ("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"):
            db.upsert_records([make_record(date=day)])
        rows = db.history("steps", "sum", start="2026-07-02", end="2026-07-03")
        assert [row["date"] for row in rows] == ["2026-07-02", "2026-07-03"]

    def test_history_days_limit_keeps_most_recent(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records([make_record(date=f"2026-07-0{i}") for i in range(1, 6)])
        dates = [row["date"] for row in db.history("steps", days=2)]
        assert dates == ["2026-07-04", "2026-07-05"]


class TestFreshness:
    def test_empty_db_returns_none(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        assert db.last_ingest_at() is None
        assert db.latest_metric_date() is None

    def test_latest_metric_date_is_max_date(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records([make_record(date="2026-07-03"), make_record(date="2026-07-01")])
        assert db.latest_metric_date() == "2026-07-03"

    def test_last_ingest_at_covers_workouts_too(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        workout = WorkoutRecord(
            start="2026-07-02 18:00:00 +0200",
            date="2026-07-02",
            activity_type="Running",
            duration_min=30.0,
            energy_kcal=300.0,
            distance_km=5.0,
            source="canonical",
        )
        db.upsert_workouts([workout])
        assert db.last_ingest_at() is not None


class TestRangeQueries:
    def test_series_maps_dates_to_values(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records([make_record(date="2026-07-01", value=100.0), make_record(date="2026-07-02", value=200.0)])
        assert db.series("steps", "sum", days=7) == {"2026-07-01": 100.0, "2026-07-02": 200.0}

    def test_range_stats_window_is_inclusive(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records([make_record(date=f"2026-07-0{i}", value=float(i * 1000)) for i in range(1, 6)])
        stats = db.range_stats("steps", "sum", "2026-07-02", "2026-07-04")
        assert stats["total"] == 9000.0
        assert stats["mean"] == 3000.0
        assert stats["days"] == 3

    def test_range_stats_empty_window(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        stats = db.range_stats("steps", "sum", "2026-07-01", "2026-07-07")
        assert stats["total"] is None
        assert stats["mean"] is None
        assert stats["days"] == 0

    def test_workouts_between(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        workouts = [
            WorkoutRecord("2026-07-01 08:00:00 +0200", "2026-07-01", "Running", 30.0, 300.0, 5.0, "canonical"),
            WorkoutRecord("2026-07-05 08:00:00 +0200", "2026-07-05", "Cycling", 60.0, 500.0, 20.0, "canonical"),
        ]
        db.upsert_workouts(workouts)
        rows = db.workouts_between("2026-07-01", "2026-07-03")
        assert [row["activity_type"] for row in rows] == ["Running"]

    def test_earliest_workout_date(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        assert db.earliest_workout_date() is None
        db.upsert_workouts(
            [
                WorkoutRecord("2026-07-05 08:00:00 +0200", "2026-07-05", "Cycling", 60.0, 500.0, 20.0, "canonical"),
                WorkoutRecord("2026-07-01 08:00:00 +0200", "2026-07-01", "Running", 30.0, 300.0, 5.0, "canonical"),
            ]
        )
        assert db.earliest_workout_date() == "2026-07-01"

    def test_workout_rollups_for_dates(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_workouts(
            [
                WorkoutRecord("2026-07-01 08:00:00 +0200", "2026-07-01", "Running", 30.0, 300.0, 5.0, "canonical"),
                WorkoutRecord("2026-07-01 18:00:00 +0200", "2026-07-01", "Yoga", 20.0, 80.0, 0.0, "canonical"),
                WorkoutRecord("2026-07-05 08:00:00 +0200", "2026-07-05", "Cycling", 60.0, 500.0, 20.0, "canonical"),
            ]
        )
        assert db.workout_rollups_for_dates([]) == []
        rows = {row["date"]: row for row in db.workout_rollups_for_dates(["2026-07-01", "2026-07-02"])}
        assert set(rows) == {"2026-07-01"}
        assert rows["2026-07-01"]["count"] == 2
        assert rows["2026-07-01"]["duration_min"] == 50.0
        assert rows["2026-07-01"]["energy_kcal"] == 380.0


class TestConcurrentAccess:
    def test_parallel_writes_and_reads(self, tmp_path):
        """The one shared connection is hit from the event loop and the
        Starlette threadpool at once; the internal lock must serialize it."""
        db = Database(str(tmp_path / "test.db"))
        errors: list[Exception] = []

        def worker(thread_id: int) -> None:
            try:
                for i in range(20):
                    date = f"2026-07-{(i % 28) + 1:02d}"
                    db.upsert_records([make_record(date=date, metric=f"metric_{thread_id}", value=float(i))])
                    db.latest_values()
                    db.history(f"metric_{thread_id}")
                    db.count_rows()
            except Exception as exc:  # pragma: no cover - only on regression
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        # 8 metrics x 20 distinct dates, upserts idempotent per (date, metric)
        assert db.count_rows() == 8 * 20
        db.close()

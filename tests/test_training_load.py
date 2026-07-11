from datetime import date, timedelta

from fastapi.testclient import TestClient

from pulseboard.app import create_app
from pulseboard.db import Database, WorkoutRecord
from pulseboard.training_load import compute_training_load

ANCHOR = date(2026, 7, 9)


def seed_workouts(db: Database, *days_and_minutes: tuple[int, float]) -> None:
    """(days before ANCHOR, duration_min) pairs."""
    db.upsert_workouts(
        [
            WorkoutRecord(
                start=f"{(ANCHOR - timedelta(days=offset)).isoformat()} 18:00:00 +0000",
                date=(ANCHOR - timedelta(days=offset)).isoformat(),
                activity_type="Running",
                duration_min=minutes,
                energy_kcal=0.0,
                distance_km=0.0,
                source="test",
            )
            for offset, minutes in days_and_minutes
        ]
    )


class TestComputeTrainingLoad:
    def test_no_workouts_returns_none(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        assert compute_training_load(db, anchor=ANCHOR) is None

    def test_even_load_gives_ratio_one(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_workouts(db, *((offset, 30.0) for offset in range(28)))
        load = compute_training_load(db, anchor=ANCHOR)
        assert load is not None
        assert load.acute_minutes == 210.0
        assert load.chronic_minutes == 840.0
        assert load.acwr == 1.0

    def test_doubled_last_week_gives_high_ratio(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_workouts(db, *((offset, 60.0 if offset < 7 else 30.0) for offset in range(28)))
        load = compute_training_load(db, anchor=ANCHOR)
        assert load is not None
        # acute 420/7 = 60; chronic (420 + 630)/28 = 37.5 -> 1.6
        assert load.acwr == 1.6

    def test_short_history_gives_no_ratio(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_workouts(db, (0, 45.0), (3, 45.0))
        load = compute_training_load(db, anchor=ANCHOR)
        assert load is not None
        assert load.acute_minutes == 90.0
        assert load.acwr is None

    def test_rest_week_with_real_chronic_gives_zero(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_workouts(db, (10, 60.0), (14, 60.0), (20, 60.0))
        load = compute_training_load(db, anchor=ANCHOR)
        assert load is not None
        assert load.acute_minutes == 0.0
        assert load.acwr == 0.0

    def test_workouts_older_than_chronic_window_ignored(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed_workouts(db, (0, 30.0), (14, 30.0), (40, 500.0))
        load = compute_training_load(db, anchor=ANCHOR)
        assert load is not None
        assert load.chronic_minutes == 60.0


class TestTrainingLoadInExporter:
    def test_gauges_appear_after_hae_workout_ingest(self, tmp_path):
        client = TestClient(create_app(str(tmp_path / "test.db")))
        today = date.today()
        workouts = [
            {
                "name": "Running",
                "start": f"{(today - timedelta(days=offset)).isoformat()} 18:00:00 +0000",
                "end": f"{(today - timedelta(days=offset)).isoformat()} 18:30:00 +0000",
                "duration": 30.0,
            }
            for offset in (0, 7, 14, 20)
        ]
        response = client.post("/ingest", json={"data": {"metrics": [], "workouts": workouts}})
        assert response.status_code == 200
        body = client.get("/metrics").text
        assert "pulseboard_training_load_acute_7d_minutes" in body
        assert "pulseboard_training_load_chronic_28d_minutes 120.0" in body
        assert "pulseboard_training_load_acwr" in body

    def test_no_training_load_lines_when_db_empty(self, tmp_path):
        body = TestClient(create_app(str(tmp_path / "test.db"))).get("/metrics").text
        assert "pulseboard_training_load" not in body

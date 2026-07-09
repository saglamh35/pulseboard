from fastapi.testclient import TestClient

from pulseboard.app import create_app
from pulseboard.db import Database, MetricRecord
from pulseboard.score import compute_health_score


def seed(db: Database, *rows: tuple[str, str, float, str]) -> None:
    db.upsert_records(
        [MetricRecord(date, metric, value, "", aggregation, "test") for date, metric, value, aggregation in rows]
    )


class TestComputeHealthScore:
    def test_no_data_returns_none(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        assert compute_health_score(db) is None

    def test_steps_only_renormalizes_weights(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(db, ("2026-07-09", "steps", 4000, "sum"))
        assert compute_health_score(db) == 50.0

    def test_sleep_capped_at_target(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(db, ("2026-07-09", "sleep_hours", 10.0, "sum"))
        assert compute_health_score(db) == 100.0

    def test_single_day_full_metrics(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-09", "sleep_hours", 8.0, "sum"),
            ("2026-07-09", "steps", 9000, "sum"),
            ("2026-07-09", "resting_heart_rate", 58, "avg"),
            ("2026-07-09", "heart_rate_variability_sdnn", 50, "avg"),
        )
        # sleep 1.0*0.35 + steps 1.0*0.25 + rhr-at-baseline 1.0*0.20 + neutral hrv 0.5*0.20
        assert compute_health_score(db) == 90.0

    def test_resting_hr_above_baseline_lowers_score(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-08", "resting_heart_rate", 55, "avg"),
            ("2026-07-09", "resting_heart_rate", 70, "avg"),
        )
        # baseline (55+70)/2 = 62.5; 1 - 7.5/15 = 0.5
        assert compute_health_score(db) == 50.0

    def test_hrv_above_baseline_raises_score(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-08", "heart_rate_variability_sdnn", 40, "avg"),
            ("2026-07-09", "heart_rate_variability_sdnn", 60, "avg"),
        )
        # baseline 50; 0.5 + 10/50 = 0.7
        assert compute_health_score(db) == 70.0


class TestScoreInExporter:
    def test_metrics_endpoint_exposes_score_and_agg_labels(self, tmp_path):
        client = TestClient(create_app(str(tmp_path / "test.db")))
        client.post(
            "/ingest",
            json={
                "date": "2026-07-09",
                "metrics": [
                    {"name": "steps", "value": 8250},
                    {"name": "sleep_hours", "value": 7.4},
                    {"name": "heart_rate", "value": 51, "aggregation": "min"},
                    {"name": "heart_rate", "value": 74, "aggregation": "avg"},
                    {"name": "heart_rate", "value": 152, "aggregation": "max"},
                ],
            },
        )
        body = client.get("/metrics").text
        assert 'pulseboard_heart_rate_bpm{agg="min"} 51.0' in body
        assert 'pulseboard_heart_rate_bpm{agg="max"} 152.0' in body
        assert "pulseboard_sleep_hours 7.4" in body
        assert "pulseboard_health_score" in body

    def test_no_score_line_when_db_empty(self, tmp_path):
        client = TestClient(create_app(str(tmp_path / "test.db")))
        assert "pulseboard_health_score" not in client.get("/metrics").text

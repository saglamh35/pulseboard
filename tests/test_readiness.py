from fastapi.testclient import TestClient

from pulseboard.app import create_app
from pulseboard.db import Database, MetricRecord
from pulseboard.readiness import compute_readiness_score


def seed(db: Database, *rows: tuple[str, str, float, str]) -> None:
    db.upsert_records(
        [MetricRecord(date, metric, value, "", aggregation, "test") for date, metric, value, aggregation in rows]
    )


class TestComputeReadinessScore:
    def test_no_data_returns_none(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        assert compute_readiness_score(db) is None

    def test_sleep_only_renormalizes_weights(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(db, ("2026-07-09", "sleep_hours", 4.0, "sum"))
        assert compute_readiness_score(db) == 50.0

    def test_steps_do_not_count(self, tmp_path):
        # Readiness is recovery-only — an active day without recovery data has no score.
        db = Database(str(tmp_path / "test.db"))
        seed(db, ("2026-07-09", "steps", 20000, "sum"))
        assert compute_readiness_score(db) is None

    def test_all_components_at_known_values(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-09", "sleep_hours", 8.0, "sum"),
            ("2026-07-09", "resting_heart_rate", 58, "avg"),
            ("2026-07-09", "heart_rate_variability_sdnn", 50, "avg"),
        )
        # hrv at baseline 0.5*0.40 + rhr at baseline 1.0*0.35 + sleep 8h 1.0*0.25 = 0.80
        assert compute_readiness_score(db) == 80.0

    def test_hrv_below_baseline_lowers_readiness(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-08", "heart_rate_variability_sdnn", 60, "avg"),
            ("2026-07-09", "heart_rate_variability_sdnn", 40, "avg"),
        )
        # baseline 50; 0.5 - 10/50 = 0.3
        assert compute_readiness_score(db) == 30.0

    def test_resting_hr_above_baseline_lowers_readiness(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(
            db,
            ("2026-07-08", "resting_heart_rate", 55, "avg"),
            ("2026-07-09", "resting_heart_rate", 70, "avg"),
        )
        # baseline 62.5; 1 - 7.5/15 = 0.5
        assert compute_readiness_score(db) == 50.0


class TestReadinessInExporter:
    def test_metrics_endpoint_exposes_readiness(self, tmp_path):
        client = TestClient(create_app(str(tmp_path / "test.db")))
        client.post(
            "/ingest",
            json={
                "date": "2026-07-09",
                "metrics": [
                    {"name": "sleep_hours", "value": 8.0},
                    {"name": "resting_heart_rate", "value": 58, "aggregation": "avg"},
                ],
            },
        )
        body = client.get("/metrics").text
        assert "pulseboard_readiness_score 100.0" in body

    def test_no_readiness_line_when_db_empty(self, tmp_path):
        body = TestClient(create_app(str(tmp_path / "test.db"))).get("/metrics").text
        assert "pulseboard_readiness_score" not in body

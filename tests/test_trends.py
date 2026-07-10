from fastapi.testclient import TestClient

from pulseboard.app import create_app
from pulseboard.db import Database, MetricRecord
from pulseboard.trends import rising_days, rolling_average


def seed_days(db: Database, metric: str, aggregation: str, values: list[float], start_day: int = 1) -> None:
    db.upsert_records(
        [
            MetricRecord(f"2026-07-{start_day + i:02d}", metric, value, "", aggregation, "test")
            for i, value in enumerate(values)
        ]
    )


class TestRollingAverage:
    def test_averages_last_seven_days(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        seed_days(db, "steps", "sum", [1000] * 3 + [8000] * 7)  # 10 days; last 7 all 8000
        assert rolling_average(db, "steps", "sum") == 8000.0

    def test_short_history_uses_what_exists(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        seed_days(db, "steps", "sum", [4000, 6000])
        assert rolling_average(db, "steps", "sum") == 5000.0

    def test_no_history_is_none(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        assert rolling_average(db, "steps", "sum") is None


class TestRisingDays:
    def test_counts_consecutive_increases_from_latest(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        seed_days(db, "resting_heart_rate", "avg", [60, 55, 56, 58, 61])
        assert rising_days(db, "resting_heart_rate", "avg") == 3

    def test_flat_or_falling_latest_day_is_zero(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        seed_days(db, "resting_heart_rate", "avg", [55, 58, 58])
        assert rising_days(db, "resting_heart_rate", "avg") == 0

    def test_single_day_is_zero(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        seed_days(db, "resting_heart_rate", "avg", [55])
        assert rising_days(db, "resting_heart_rate", "avg") == 0


class TestWeeklyRollup:
    def test_groups_by_iso_week(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        # 2026-07-06 is a Monday; 5 days in one week + 2 in the next
        seed_days(db, "steps", "sum", [1000, 2000, 3000, 4000, 5000, 6000, 7000], start_day=8)
        rollup = db.weekly_rollup("steps", "sum")
        assert len(rollup) == 2
        first, second = rollup
        assert first["days"] + second["days"] == 7
        assert first["total"] + second["total"] == 28000
        assert first["week_start"] == "2026-07-08"

    def test_empty_metric_gives_empty_rollup(self, tmp_path):
        db = Database(str(tmp_path / "t.db"))
        assert db.weekly_rollup("steps", "sum") == []


class TestTrendGaugesInExporter:
    def test_metrics_endpoint_exposes_trend_gauges(self, tmp_path):
        client = TestClient(create_app(str(tmp_path / "t.db")))
        for day, (steps, rhr) in enumerate([(7000, 55), (8000, 56), (9000, 58)], start=1):
            client.post(
                "/ingest",
                json={
                    "date": f"2026-07-{day:02d}",
                    "metrics": [{"name": "steps", "value": steps}, {"name": "resting_heart_rate", "value": rhr}],
                },
            )
        body = client.get("/metrics").text
        assert "pulseboard_steps_7d_avg 8000.0" in body
        assert "pulseboard_resting_heart_rate_7d_avg_bpm 56.33" in body
        assert "pulseboard_resting_heart_rate_rising_days 2.0" in body

    def test_no_trend_gauges_on_empty_db(self, tmp_path):
        client = TestClient(create_app(str(tmp_path / "t.db")))
        body = client.get("/metrics").text
        assert "7d_avg" not in body
        assert "rising_days" not in body

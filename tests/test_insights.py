from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from pulseboard.app import create_app
from pulseboard.db import Database, MetricRecord
from pulseboard.insights import (
    MIN_SAMPLES,
    CorrelationPair,
    aligned_pairs,
    correlation,
    detect_anomalies,
    insights_summary,
    pearson,
    zscore_latest,
)

START = date(2026, 6, 1)


def seed(db: Database, metric: str, aggregation: str, values: list[float], offset_days: int = 0) -> None:
    """Store one value per consecutive day starting at START + offset."""
    records = [
        MetricRecord(
            date=(START + timedelta(days=offset_days + i)).isoformat(),
            metric=metric,
            value=value,
            unit="",
            aggregation=aggregation,
            source="canonical",
        )
        for i, value in enumerate(values)
    ]
    db.upsert_records(records)


SAME_DAY_PAIR = CorrelationPair("steps_vs_sleep", "steps", "sum", "sleep_hours", "sum", 0, "test")
LAGGED_PAIR = CorrelationPair("sleep_vs_hrv", "sleep_hours", "sum", "heart_rate_variability_sdnn", "avg", 1, "test")


class TestPearson:
    def test_perfect_positive(self):
        assert pearson([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert pearson([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_constant_series_is_none(self):
        assert pearson([1, 2, 3], [5, 5, 5]) is None

    def test_too_few_points_is_none(self):
        assert pearson([1], [2]) is None


class TestCorrelation:
    def test_positively_correlated_series(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        n = 20
        seed(db, "steps", "sum", [1000.0 * (i + 1) for i in range(n)])
        seed(db, "sleep_hours", "sum", [5.0 + 0.1 * i for i in range(n)])
        result = correlation(db, SAME_DAY_PAIR)
        assert result is not None
        r, samples = result
        assert samples == n
        assert r > 0.99

    def test_anticorrelated_series(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        n = 20
        seed(db, "steps", "sum", [1000.0 * (i + 1) for i in range(n)])
        seed(db, "sleep_hours", "sum", [9.0 - 0.1 * i for i in range(n)])
        result = correlation(db, SAME_DAY_PAIR)
        assert result is not None
        assert result[0] < -0.99

    def test_lag_pairs_day_with_next_day(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        # sleep on days 0..2; HRV only on days 1..3 (the mornings after)
        seed(db, "sleep_hours", "sum", [6.0, 7.0, 8.0])
        seed(db, "heart_rate_variability_sdnn", "avg", [30.0, 40.0, 50.0], offset_days=1)
        pairs = aligned_pairs(db, LAGGED_PAIR)
        assert pairs == [(6.0, 30.0), (7.0, 40.0), (8.0, 50.0)]

    def test_below_min_samples_is_none(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        n = MIN_SAMPLES - 1
        seed(db, "steps", "sum", [1000.0 * (i + 1) for i in range(n)])
        seed(db, "sleep_hours", "sum", [5.0 + 0.1 * i for i in range(n)])
        assert correlation(db, SAME_DAY_PAIR) is None

    def test_missing_days_are_dropped(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(db, "steps", "sum", [1000.0, 2000.0, 3000.0])
        seed(db, "sleep_hours", "sum", [6.0], offset_days=1)  # only day 1
        assert aligned_pairs(db, SAME_DAY_PAIR) == [(2000.0, 6.0)]


class TestZScore:
    def test_constant_baseline_is_none(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(db, "resting_heart_rate", "avg", [58.0] * 15)
        assert zscore_latest(db, "resting_heart_rate", "avg") is None

    def test_spike_has_large_zscore(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        baseline = [58.0, 59.0, 58.5, 57.5, 58.0, 59.5, 58.0, 57.0, 58.5, 59.0]
        seed(db, "resting_heart_rate", "avg", baseline + [75.0])
        z = zscore_latest(db, "resting_heart_rate", "avg")
        assert z is not None
        assert z > 3

    def test_too_little_history_is_none(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        seed(db, "resting_heart_rate", "avg", [58.0, 60.0, 59.0])
        assert zscore_latest(db, "resting_heart_rate", "avg") is None


class TestDetectAnomalies:
    def test_spike_is_reported(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        baseline = [58.0, 59.0, 58.5, 57.5, 58.0, 59.5, 58.0, 57.0, 58.5, 59.0]
        seed(db, "resting_heart_rate", "avg", baseline + [75.0])
        anomalies = detect_anomalies(db)
        assert len(anomalies) == 1
        anomaly = anomalies[0]
        assert anomaly.metric == "resting_heart_rate"
        assert anomaly.value == 75.0
        assert anomaly.zscore > 3
        assert anomaly.date == (START + timedelta(days=10)).isoformat()

    def test_normal_day_reports_nothing(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        baseline = [58.0, 59.0, 58.5, 57.5, 58.0, 59.5, 58.0, 57.0, 58.5, 59.0]
        seed(db, "resting_heart_rate", "avg", baseline + [58.5])
        assert detect_anomalies(db) == []


class TestInsightsEndpoint:
    def test_summary_shape_on_empty_db(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        summary = insights_summary(db)
        assert {c["pair"] for c in summary["correlations"]} == {
            "sleep_vs_next_day_hrv",
            "activity_vs_next_day_resting_hr",
            "workout_minutes_vs_next_day_hrv",
            "steps_vs_sleep_same_day",
        }
        assert all(c["r"] is None for c in summary["correlations"])
        assert summary["anomalies"] == []
        assert "not medical advice" in summary["disclaimer"]

    def test_endpoint_returns_summary(self, tmp_path):
        client = TestClient(create_app(str(tmp_path / "test.db")))
        response = client.get("/insights")
        assert response.status_code == 200
        body = response.json()
        assert "correlations" in body
        assert "anomalies" in body
        assert "disclaimer" in body

    def test_exporter_gauges_appear_with_enough_data(self, tmp_path):
        client = TestClient(create_app(str(tmp_path / "test.db")))
        db = client.app.state.db
        n = 20
        seed(db, "steps", "sum", [1000.0 + 137.0 * (i % 7) for i in range(n)])
        seed(db, "sleep_hours", "sum", [6.0 + 0.2 * (i % 5) for i in range(n)])
        metrics = client.get("/metrics").text
        assert 'pulseboard_correlation{pair="steps_vs_sleep_same_day"}' in metrics
        assert 'pulseboard_correlation_samples{pair="steps_vs_sleep_same_day"} 20.0' in metrics
        assert 'pulseboard_zscore{metric="steps"}' in metrics

from fastapi.testclient import TestClient

from pulseboard.app import create_app


def make_client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "test.db")))


class TestHealth:
    def test_health_ok(self, tmp_path):
        client = make_client(tmp_path)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestIngestCanonical:
    def test_stores_steps(self, tmp_path):
        client = make_client(tmp_path)
        response = client.post(
            "/ingest",
            json={"date": "2026-07-09", "metrics": [{"name": "steps", "value": 8250}]},
        )
        assert response.status_code == 200
        assert response.json() == {"stored": 1, "skipped": []}

    def test_unknown_metric_skipped_not_500(self, tmp_path):
        client = make_client(tmp_path)
        response = client.post(
            "/ingest",
            json={
                "date": "2026-07-09",
                "metrics": [{"name": "steps", "value": 100}, {"name": "definitely_not_a_metric", "value": 1}],
            },
        )
        assert response.status_code == 200
        assert response.json() == {"stored": 1, "skipped": ["definitely_not_a_metric"]}

    def test_reingest_updates_value(self, tmp_path):
        client = make_client(tmp_path)
        client.post("/ingest", json={"date": "2026-07-09", "metrics": [{"name": "steps", "value": 100}]})
        client.post("/ingest", json={"date": "2026-07-09", "metrics": [{"name": "steps", "value": 200}]})
        metrics = client.get("/metrics").text
        assert "pulseboard_steps 200.0" in metrics

    def test_invalid_shape_is_422(self, tmp_path):
        client = make_client(tmp_path)
        response = client.post("/ingest", json={"metrics": [{"name": "steps", "value": 1}]})
        assert response.status_code == 422

    def test_non_object_body_is_400(self, tmp_path):
        client = make_client(tmp_path)
        response = client.post("/ingest", json=[1, 2, 3])
        assert response.status_code == 400


class TestMetricsEndpoint:
    def test_exposes_latest_steps_gauge(self, tmp_path):
        client = make_client(tmp_path)
        client.post("/ingest", json={"date": "2026-07-08", "metrics": [{"name": "steps", "value": 5000}]})
        client.post("/ingest", json={"date": "2026-07-09", "metrics": [{"name": "steps", "value": 8250}]})
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "pulseboard_steps 8250.0" in response.text

    def test_empty_db_serves_no_gauges(self, tmp_path):
        client = make_client(tmp_path)
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "pulseboard_steps" not in response.text

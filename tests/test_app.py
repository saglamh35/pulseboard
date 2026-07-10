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
        assert response.json() == {"stored": 1, "skipped": [], "workouts": 0, "latest_date": "2026-07-09"}

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
        assert response.json() == {
            "stored": 1,
            "skipped": ["definitely_not_a_metric"],
            "workouts": 0,
            "latest_date": "2026-07-09",
        }

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

    def test_freshness_gauges_after_ingest(self, tmp_path):
        client = make_client(tmp_path)
        assert "pulseboard_last_ingest_timestamp_seconds" not in client.get("/metrics").text
        client.post("/ingest", json={"date": "2026-07-09", "metrics": [{"name": "steps", "value": 100}]})
        metrics = client.get("/metrics").text
        assert "pulseboard_last_ingest_timestamp_seconds" in metrics
        # 2026-07-09 00:00 UTC = 1783555200
        assert "pulseboard_latest_data_timestamp_seconds 1.7835552e+09" in metrics


class TestStatus:
    def test_empty_db(self, tmp_path):
        client = make_client(tmp_path)
        body = client.get("/status").json()
        assert body["rows"] == 0
        assert body["workouts"] == 0
        assert body["last_ingest_at"] is None
        assert body["latest_data_date"] is None
        assert body["freshness_seconds"] is None
        assert body["metrics_tracked"] > 20

    def test_after_ingest(self, tmp_path):
        client = make_client(tmp_path)
        client.post("/ingest", json={"date": "2026-07-09", "metrics": [{"name": "steps", "value": 100}]})
        body = client.get("/status").json()
        assert body["rows"] == 1
        assert body["latest_data_date"] == "2026-07-09"
        assert body["last_ingest_at"] is not None
        assert 0 <= body["freshness_seconds"] < 60


class TestIngestGuards:
    def test_oversized_body_is_413(self, tmp_path):
        client = make_client(tmp_path)
        huge = b'{"date": "2026-07-09", "metrics": [' + b" " * (10 * 1024 * 1024) + b"]}"
        response = client.post("/ingest", content=huge, headers={"content-type": "application/json"})
        assert response.status_code == 413

    def test_token_required_when_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PULSEBOARD_API_TOKEN", "sekrit")
        client = make_client(tmp_path)
        payload = {"date": "2026-07-09", "metrics": [{"name": "steps", "value": 1}]}
        assert client.post("/ingest", json=payload).status_code == 401
        wrong = client.post("/ingest", json=payload, headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        ok = client.post("/ingest", json=payload, headers={"Authorization": "Bearer sekrit"})
        assert ok.status_code == 200

    def test_no_token_configured_keeps_ingest_open(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PULSEBOARD_API_TOKEN", raising=False)
        client = make_client(tmp_path)
        payload = {"date": "2026-07-09", "metrics": [{"name": "steps", "value": 1}]}
        assert client.post("/ingest", json=payload).status_code == 200

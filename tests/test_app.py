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


class TestIngestWorkoutRollups:
    def _hae_payload(self, *durations: float) -> dict:
        return {
            "data": {
                "metrics": [],
                "workouts": [
                    {
                        "name": "Running",
                        "start": f"2026-07-09 {8 + i:02d}:00:00 +0000",
                        "duration": duration,
                        "activeEnergyBurned": {"qty": 100.0, "units": "kcal"},
                    }
                    for i, duration in enumerate(durations)
                ],
            }
        }

    def test_live_ingest_writes_daily_rollups(self, tmp_path):
        client = make_client(tmp_path)
        response = client.post("/ingest", json=self._hae_payload(30.0, 20.0))
        assert response.status_code == 200
        body = client.get("/metrics").text
        assert "pulseboard_workouts_count 2.0" in body
        assert "pulseboard_workouts_duration_min 50.0" in body
        assert "pulseboard_workouts_energy_kcal 200.0" in body

    def test_rollups_recomputed_across_posts(self, tmp_path):
        # A day's workouts can arrive over several POSTs; the rollup is
        # recomputed from the DB, not accumulated from payloads.
        client = make_client(tmp_path)
        client.post("/ingest", json=self._hae_payload(30.0, 20.0))
        client.post("/ingest", json=self._hae_payload(30.0, 20.0, 10.0))
        body = client.get("/metrics").text
        assert "pulseboard_workouts_count 3.0" in body
        assert "pulseboard_workouts_duration_min 60.0" in body


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


class TestWeeklyReportEndpoint:
    def test_markdown_default(self, tmp_path):
        client = make_client(tmp_path)
        response = client.get("/report/weekly")
        assert response.status_code == 200
        assert "PulseBoard weekly report" in response.text
        assert "not medical advice" in response.text

    def test_html_format(self, tmp_path):
        client = make_client(tmp_path)
        response = client.get("/report/weekly?format=html")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<table" in response.text

    def test_bad_format_is_422(self, tmp_path):
        client = make_client(tmp_path)
        assert client.get("/report/weekly?format=pdf").status_code == 422


class TestCoachEndpoints:
    def _seed(self, client):
        client.post(
            "/ingest",
            json={
                "date": "2026-07-09",
                "metrics": [{"name": "steps", "value": 8250}, {"name": "sleep_hours", "value": 7.4}],
            },
        )

    def test_coach_prompt_needs_no_provider(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PULSEBOARD_AI_PROVIDER", raising=False)
        client = make_client(tmp_path)
        self._seed(client)
        response = client.get("/coach/prompt")
        assert response.status_code == 200
        assert "not medical advice" in response.text
        assert "goals for next week" in response.text

    def test_coach_prompt_json_has_links(self, tmp_path):
        client = make_client(tmp_path)
        self._seed(client)
        body = client.get("/coach/prompt?format=json").json()
        assert body["links"]["claude"].startswith("https://claude.ai/new?q=")
        assert body["links"]["chatgpt"].startswith("https://chatgpt.com/?q=")
        assert "not medical advice" in body["prompt"]

    def test_weekly_report_makes_no_llm_call_by_default(self, tmp_path, monkeypatch):
        import pulseboard.coach as coach
        from tests.test_coach import FakeUrlopen

        faked = FakeUrlopen({"response": "hi"})
        monkeypatch.setattr(coach.urllib.request, "urlopen", faked)
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "ollama")
        client = make_client(tmp_path)
        self._seed(client)
        assert client.get("/report/weekly").status_code == 200
        assert faked.requests == []

    def test_weekly_report_coach_opt_in(self, tmp_path, monkeypatch):
        import pulseboard.coach as coach
        from tests.test_coach import FakeUrlopen

        monkeypatch.setattr(coach.urllib.request, "urlopen", FakeUrlopen({"response": "Solid week."}))
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "ollama")
        client = make_client(tmp_path)
        self._seed(client)
        text = client.get("/report/weekly?coach=1").text
        assert "## Coach (AI)" in text
        assert "Solid week." in text


class TestIngestGuards:
    def test_oversized_body_is_413(self, tmp_path):
        client = make_client(tmp_path)
        huge = b'{"date": "2026-07-09", "metrics": [' + b" " * (10 * 1024 * 1024) + b"]}"
        response = client.post("/ingest", content=huge, headers={"content-type": "application/json"})
        assert response.status_code == 413

    def test_oversized_chunked_body_is_413(self, tmp_path):
        # A generator body is sent chunked with no Content-Length, so only
        # the streaming cap can catch it.
        client = make_client(tmp_path)

        def chunks():
            yield b'{"date": "2026-07-09", "metrics": ['
            for _ in range(11):
                yield b" " * (1024 * 1024)
            yield b"]}"

        response = client.post("/ingest", content=chunks(), headers={"content-type": "application/json"})
        assert response.status_code == 413

    def test_nan_value_is_422(self, tmp_path):
        # json.loads accepts the NaN/Infinity literals; they must not reach
        # the DB (and from there the Prometheus gauges). Raw bytes because
        # httpx's json= refuses to serialize NaN.
        client = make_client(tmp_path)
        for literal in (b"NaN", b"Infinity", b"-Infinity"):
            body = b'{"date": "2026-07-09", "metrics": [{"name": "steps", "value": ' + literal + b"}]}"
            response = client.post("/ingest", content=body, headers={"content-type": "application/json"})
            assert response.status_code == 422, literal
        client_metrics = client.get("/metrics").text
        assert "pulseboard_steps " not in client_metrics

    def test_hae_nan_point_is_skipped(self, tmp_path):
        client = make_client(tmp_path)
        body = (
            b'{"data": {"metrics": [{"name": "step_count", "units": "count", "data": ['
            b'{"date": "2026-07-09 08:00:00 +0000", "qty": NaN},'
            b'{"date": "2026-07-09 09:00:00 +0000", "qty": 500}]}]}}'
        )
        response = client.post("/ingest", content=body, headers={"content-type": "application/json"})
        assert response.status_code == 200
        assert response.json()["stored"] == 1
        assert "pulseboard_steps 500.0" in client.get("/metrics").text

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

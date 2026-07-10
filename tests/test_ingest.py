import json
from pathlib import Path

from fastapi.testclient import TestClient

from pulseboard.app import create_app
from pulseboard.ingest.adapters.health_auto_export import is_hae_payload, normalize_hae
from pulseboard.ingest.canonical import CanonicalPayload, normalize

SAMPLES = Path(__file__).parent.parent / "samples"


def load_sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


class TestCanonicalNormalize:
    def test_full_sample_normalizes_without_skips(self):
        payload = CanonicalPayload.model_validate(load_sample("canonical_sample.json"))
        records, skipped = normalize(payload)
        assert skipped == []
        assert len(records) == 18
        assert all(r.date == "2026-07-09" for r in records)
        assert all(r.source == "canonical" for r in records)

    def test_default_aggregation_from_registry(self):
        payload = CanonicalPayload.model_validate(
            {"date": "2026-07-09", "metrics": [{"name": "vo2_max", "value": 41.2}]}
        )
        records, _ = normalize(payload)
        assert records[0].aggregation == "latest"
        assert records[0].unit == "mL/kg/min"

    def test_unsupported_aggregation_is_skipped(self):
        payload = CanonicalPayload.model_validate(
            {"date": "2026-07-09", "metrics": [{"name": "steps", "value": 1, "aggregation": "max"}]}
        )
        records, skipped = normalize(payload)
        assert records == []
        assert skipped == ["steps"]

    def test_heart_rate_min_avg_max_are_three_records(self):
        payload = CanonicalPayload.model_validate(
            {
                "date": "2026-07-09",
                "metrics": [
                    {"name": "heart_rate", "value": 51, "aggregation": "min"},
                    {"name": "heart_rate", "value": 74, "aggregation": "avg"},
                    {"name": "heart_rate", "value": 152, "aggregation": "max"},
                ],
            }
        )
        records, skipped = normalize(payload)
        assert skipped == []
        assert sorted(r.aggregation for r in records) == ["avg", "max", "min"]


class TestHAEAdapter:
    def test_detects_hae_shape(self):
        assert is_hae_payload(load_sample("hae_sample.json"))
        assert not is_hae_payload(load_sample("canonical_sample.json"))

    def test_full_sample_maps_known_metrics(self):
        records, skipped = normalize_hae(load_sample("hae_sample.json"))
        assert skipped == ["mindful_minutes"]
        by_key = {(r.metric, r.date, r.aggregation): r for r in records}
        assert by_key[("steps", "2026-07-08", "sum")].value == 11040
        assert by_key[("steps", "2026-07-09", "sum")].value == 8250
        assert by_key[("heart_rate", "2026-07-09", "min")].value == 51
        assert by_key[("heart_rate", "2026-07-09", "avg")].value == 74
        assert by_key[("heart_rate", "2026-07-09", "max")].value == 152
        assert by_key[("sleep_hours", "2026-07-09", "sum")].value == 7.4
        assert by_key[("body_mass", "2026-07-09", "latest")].value == 78.4
        assert all(r.source == "health_auto_export" for r in records)

    def test_lowercase_min_avg_max_keys(self):
        payload = {
            "data": {
                "metrics": [
                    {"name": "heart_rate", "data": [{"date": "2026-07-09 00:00:00 +0200", "min": 50, "max": 150}]}
                ]
            }
        }
        records, _ = normalize_hae(payload)
        assert sorted(r.aggregation for r in records) == ["max", "min"]

    def test_point_without_date_is_skipped(self):
        payload = {"data": {"metrics": [{"name": "step_count", "data": [{"qty": 100}]}]}}
        records, skipped = normalize_hae(payload)
        assert records == []
        assert skipped == []


class TestIngestSniffing:
    def make_client(self, tmp_path) -> TestClient:
        return TestClient(create_app(str(tmp_path / "test.db")))

    def test_canonical_sample_via_endpoint(self, tmp_path):
        client = self.make_client(tmp_path)
        response = client.post("/ingest", json=load_sample("canonical_sample.json"))
        assert response.status_code == 200
        assert response.json() == {"stored": 18, "skipped": []}

    def test_hae_sample_via_endpoint(self, tmp_path):
        client = self.make_client(tmp_path)
        response = client.post("/ingest", json=load_sample("hae_sample.json"))
        assert response.status_code == 200
        body = response.json()
        assert body["skipped"] == ["mindful_minutes"]
        assert body["stored"] == 9

    def test_hae_overlap_updates_canonical_row(self, tmp_path):
        client = self.make_client(tmp_path)
        client.post("/ingest", json={"date": "2026-07-09", "metrics": [{"name": "steps", "value": 1}]})
        client.post("/ingest", json=load_sample("hae_sample.json"))
        db = client.app.state.db
        rows = db.history("steps")
        assert [(r["date"], r["value"], r["source"]) for r in rows] == [
            ("2026-07-08", 11040.0, "health_auto_export"),
            ("2026-07-09", 8250.0, "health_auto_export"),
        ]

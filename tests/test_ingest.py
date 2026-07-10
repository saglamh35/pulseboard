import json
from pathlib import Path

from fastapi.testclient import TestClient

from pulseboard.app import create_app
from pulseboard.ingest.adapters.health_auto_export import extract_workouts, is_hae_payload, normalize_hae
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
        assert skipped == []
        by_key = {(r.metric, r.date, r.aggregation): r for r in records}
        assert by_key[("steps", "2026-07-08", "sum")].value == 11040
        assert by_key[("steps", "2026-07-09", "sum")].value == 8250
        assert by_key[("mindful_minutes", "2026-07-09", "sum")].value == 10
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

    def test_unknown_metric_reported_in_skipped(self):
        payload = {
            "data": {"metrics": [{"name": "definitely_not_a_metric", "data": [{"date": "2026-07-09", "qty": 1}]}]}
        }
        records, skipped = normalize_hae(payload)
        assert records == []
        assert skipped == ["definitely_not_a_metric"]

    def test_stringified_qty_is_accepted(self):
        point = {"date": "2026-07-09 10:00:00 +0200", "qty": "8250"}
        payload = {"data": {"metrics": [{"name": "step_count", "data": [point]}]}}
        records, _ = normalize_hae(payload)
        assert len(records) == 1
        assert records[0].value == 8250.0

    def test_blood_pressure_splits_into_two_metrics(self):
        payload = {
            "data": {
                "metrics": [
                    {
                        "name": "blood_pressure",
                        "units": "mmHg",
                        "data": [{"date": "2026-07-09 09:00:00 +0200", "systolic": 118, "diastolic": 76}],
                    }
                ]
            }
        }
        records, skipped = normalize_hae(payload)
        assert skipped == []
        by_metric = {r.metric: r for r in records}
        assert by_metric["blood_pressure_systolic"].value == 118
        assert by_metric["blood_pressure_diastolic"].value == 76
        assert all(r.aggregation == "avg" and r.unit == "mmHg" for r in records)


class TestIngestSniffing:
    def make_client(self, tmp_path) -> TestClient:
        return TestClient(create_app(str(tmp_path / "test.db")))

    def test_canonical_sample_via_endpoint(self, tmp_path):
        client = self.make_client(tmp_path)
        response = client.post("/ingest", json=load_sample("canonical_sample.json"))
        assert response.status_code == 200
        assert response.json() == {"stored": 18, "skipped": [], "workouts": 0, "latest_date": "2026-07-09"}

    def test_hae_sample_via_endpoint(self, tmp_path):
        client = self.make_client(tmp_path)
        response = client.post("/ingest", json=load_sample("hae_sample.json"))
        assert response.status_code == 200
        body = response.json()
        assert body["skipped"] == []
        assert body["stored"] == 14
        assert body["workouts"] == 1
        assert body["latest_date"] == "2026-07-09"

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


class TestHAEWorkoutsAndStages:
    def test_extract_workouts_from_sample(self):
        workouts = extract_workouts(load_sample("hae_sample.json"))
        assert len(workouts) == 1
        workout = workouts[0]
        assert workout.activity_type == "Running"
        assert workout.date == "2026-07-09"
        assert workout.duration_min == 31.5
        assert workout.energy_kcal == 342
        assert workout.distance_km == 5.2

    def test_sleep_stage_fields_become_stage_records(self):
        records, _ = normalize_hae(load_sample("hae_sample.json"))
        by_key = {(r.metric, r.aggregation): r.value for r in records}
        assert by_key[("sleep_core_hours", "sum")] == 4.1
        assert by_key[("sleep_deep_hours", "sum")] == 1.5
        assert by_key[("sleep_rem_hours", "sum")] == 1.8
        assert by_key[("sleep_awake_hours", "sum")] == 0.4

    def test_workout_without_start_is_skipped(self):
        payload = {"data": {"workouts": [{"name": "Running", "duration": 30}]}}
        assert extract_workouts(payload) == []

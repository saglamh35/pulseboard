from pulseboard.db import Database, MetricRecord


def make_record(**overrides) -> MetricRecord:
    defaults = dict(
        date="2026-07-01",
        metric="steps",
        value=8250.0,
        unit="count",
        aggregation="sum",
        source="canonical",
    )
    defaults.update(overrides)
    return MetricRecord(**defaults)


class TestUpsert:
    def test_insert_new_rows(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        stored = db.upsert_records([make_record(), make_record(date="2026-07-02", value=9100.0)])
        assert stored == 2
        assert db.count_rows() == 2

    def test_same_key_updates_in_place(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records([make_record(value=1000.0)])
        db.upsert_records([make_record(value=2000.0, source="export_xml")])
        assert db.count_rows() == 1
        row = db.history("steps")[0]
        assert row["value"] == 2000.0
        assert row["source"] == "export_xml"

    def test_different_aggregations_are_separate_rows(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records(
            [
                make_record(metric="heart_rate", aggregation="min", value=48.0),
                make_record(metric="heart_rate", aggregation="max", value=161.0),
            ]
        )
        assert db.count_rows() == 2

    def test_reingest_is_idempotent(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        records = [make_record(), make_record(date="2026-07-02")]
        db.upsert_records(records)
        db.upsert_records(records)
        assert db.count_rows() == 2


class TestQueries:
    def test_latest_values_picks_max_date_per_metric(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records(
            [
                make_record(date="2026-07-01", value=8000.0),
                make_record(date="2026-07-03", value=12000.0),
                make_record(date="2026-07-02", value=9000.0),
            ]
        )
        rows = db.latest_values()
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-07-03"
        assert rows[0]["value"] == 12000.0

    def test_history_is_date_ascending(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records(
            [
                make_record(date="2026-07-03"),
                make_record(date="2026-07-01"),
                make_record(date="2026-07-02"),
            ]
        )
        dates = [row["date"] for row in db.history("steps")]
        assert dates == ["2026-07-01", "2026-07-02", "2026-07-03"]

    def test_history_days_limit_keeps_most_recent(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.upsert_records([make_record(date=f"2026-07-0{i}") for i in range(1, 6)])
        dates = [row["date"] for row in db.history("steps", days=2)]
        assert dates == ["2026-07-04", "2026-07-05"]

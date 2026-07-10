from pulseboard.db import Database, MetricRecord
from pulseboard.doctor import check_database, main


def seed_steps(db_path: str, date: str) -> None:
    db = Database(db_path)
    db.upsert_records([MetricRecord(date, "steps", 1000.0, "count", "sum", "canonical")])
    db.close()


class TestCheckDatabase:
    def test_missing_file_fails_with_hint(self, tmp_path):
        results = check_database(str(tmp_path / "nope.db"))
        assert len(results) == 1
        assert not results[0].ok
        assert "does not exist" in results[0].detail
        assert results[0].hint

    def test_empty_db_flags_no_data(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        Database(db_path).close()
        results = check_database(db_path)
        by_name = {r.name: r for r in results}
        assert by_name["database file"].ok
        assert not by_name["stored data"].ok
        assert "SHORTCUT.md" in by_name["stored data"].hint

    def test_fresh_data_passes_all(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        seed_steps(db_path, "2026-07-09")
        results = check_database(db_path)
        assert all(r.ok for r in results)
        by_name = {r.name: r for r in results}
        assert by_name["newest data day"].detail == "2026-07-09"


class TestCli:
    def test_exit_zero_when_healthy(self, tmp_path, capsys):
        db_path = str(tmp_path / "test.db")
        seed_steps(db_path, "2026-07-09")
        assert main(["--db", db_path]) == 0
        assert "All checks passed" in capsys.readouterr().out

    def test_exit_one_with_hints_when_empty(self, tmp_path, capsys):
        db_path = str(tmp_path / "test.db")
        Database(db_path).close()
        assert main(["--db", db_path]) == 1
        out = capsys.readouterr().out
        assert "need attention" in out
        assert "→" in out

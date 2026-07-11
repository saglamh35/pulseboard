from pulseboard.db import Database, MetricRecord
from pulseboard.doctor import check_ai, check_database, main


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


class TestCheckAi:
    def test_unconfigured_is_ok(self, monkeypatch):
        monkeypatch.delenv("PULSEBOARD_AI_PROVIDER", raising=False)
        results = check_ai()
        assert len(results) == 1
        assert results[0].ok
        assert "not configured" in results[0].detail

    def test_unknown_provider_fails_with_hint(self, monkeypatch):
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "skynet")
        results = check_ai()
        assert not results[0].ok
        assert "ollama" in results[0].hint

    def test_cloud_provider_reports_key_presence_never_value(self, monkeypatch):
        secret = "sk-ant-super-secret-value"
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "anthropic")
        monkeypatch.setenv("PULSEBOARD_ANTHROPIC_API_KEY", secret)
        results = check_ai()
        assert results[0].ok
        for result in results:
            for field in (result.name, result.detail, result.hint):
                assert secret not in field

    def test_gemini_accepts_google_api_key_fallback(self, monkeypatch):
        # doctor must accept every fallback the coach dispatcher accepts
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "gemini")
        for var in ("PULSEBOARD_GEMINI_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "some-key")
        assert check_ai()[0].ok

    def test_cloud_provider_missing_key_fails(self, monkeypatch):
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "openai")
        monkeypatch.delenv("PULSEBOARD_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        results = check_ai()
        assert not results[0].ok
        assert "PULSEBOARD_OPENAI_API_KEY" in results[0].hint

    def test_ollama_unreachable_fails_with_pull_hint(self, monkeypatch):
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "ollama")
        monkeypatch.setenv("PULSEBOARD_OLLAMA_URL", "http://127.0.0.1:1")  # nothing listens here
        results = check_ai()
        assert not results[0].ok
        assert "ollama pull" in results[0].hint


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

import json
import urllib.error
from contextlib import contextmanager
from datetime import timedelta

import pytest

import pulseboard.coach as coach
from pulseboard.db import Database
from pulseboard.report import build_weekly_report
from tests.test_report import THIS_MONDAY, seed_two_weeks

KEY = "test-key-do-not-log"


class FakeUrlopen:
    """Records requests and returns a canned JSON body (per-URL by substring)."""

    def __init__(self, body: dict | None = None):
        self.requests = []
        self.body = body or {}

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        payload = json.dumps(self.body).encode("utf-8")

        class Response:
            def read(self):
                return payload

        @contextmanager
        def response():
            yield Response()

        return response()


def make_report(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    seed_two_weeks(db)
    return build_weekly_report(db, week_ending=THIS_MONDAY), db


def fake(monkeypatch, body: dict) -> FakeUrlopen:
    faked = FakeUrlopen(body)
    monkeypatch.setattr(coach.urllib.request, "urlopen", faked)
    return faked


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "PULSEBOARD_AI_PROVIDER",
        "PULSEBOARD_AI_MODEL",
        "PULSEBOARD_OLLAMA_URL",
        "PULSEBOARD_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
        "PULSEBOARD_OPENAI_API_KEY",
        "OPENAI_API_KEY",
        "PULSEBOARD_GEMINI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


class TestCoachPrompt:
    def test_contains_week_numbers_and_instructions(self, tmp_path):
        report, db = make_report(tmp_path)
        prompt = coach.coach_prompt(report, db)
        assert "84,000" in prompt  # this week's steps total
        assert "70,000" in prompt  # last week's
        assert "+20.0%" in prompt
        assert "met 7/7 days" in prompt  # steps goal
        assert "Sleep debt" in prompt
        assert "goals for next week" in prompt
        assert "not medical advice" in prompt

    def test_big_picture_context_needs_db(self, tmp_path):
        report, db = make_report(tmp_path)
        with_db = coach.coach_prompt(report, db)
        without_db = coach.coach_prompt(report)
        assert "Today's scores" in with_db
        assert "Today's scores" not in without_db

    def test_deterministic_and_compact(self, tmp_path):
        report, db = make_report(tmp_path)
        assert coach.coach_prompt(report, db) == coach.coach_prompt(report, db)
        assert len(coach.coach_prompt(report, db)) < 2500

    def test_workout_lines_capped(self, tmp_path):
        from pulseboard.db import WorkoutRecord

        db = Database(str(tmp_path / "test.db"))
        seed_two_weeks(db)
        db.upsert_workouts(
            [
                WorkoutRecord(
                    f"{(THIS_MONDAY + timedelta(days=i % 7)).isoformat()} {8 + i:02d}:00:00 +0000",
                    (THIS_MONDAY + timedelta(days=i % 7)).isoformat(),
                    "Running",
                    30.0,
                    250.0,
                    5.0,
                    "test",
                )
                for i in range(15)
            ]
        )
        report = build_weekly_report(db, week_ending=THIS_MONDAY)
        prompt = coach.coach_prompt(report, db)
        assert "...and 5 more" in prompt
        assert len(prompt) < 2500


class TestPromptLinks:
    def test_encoded_links(self):
        links = coach.prompt_links("hello coach & friend")
        assert links["claude"].startswith("https://claude.ai/new?q=hello")
        assert links["chatgpt"].startswith("https://chatgpt.com/?q=hello")
        assert "&" not in links["claude"].split("?q=")[1]  # percent-encoded

    def test_real_prompt_stays_under_url_limit(self, tmp_path):
        report, db = make_report(tmp_path)
        for link in coach.prompt_links(coach.coach_prompt(report, db)).values():
            assert len(link) < 8000


class TestProviderAdapters:
    def test_ollama_request_shape(self, monkeypatch):
        faked = fake(monkeypatch, {"response": " all good "})
        assert coach.ask_ollama("http://127.0.0.1:11434/", "gemma3:4b", "hi") == " all good "
        request = faked.requests[0]
        assert request.full_url == "http://127.0.0.1:11434/api/generate"
        assert request.get_method() == "POST"
        body = json.loads(request.data.decode())
        assert body == {"model": "gemma3:4b", "prompt": "hi", "stream": False}

    def test_anthropic_request_shape(self, monkeypatch):
        faked = fake(monkeypatch, {"content": [{"type": "text", "text": "nice week"}]})
        assert coach.ask_anthropic(KEY, "claude-haiku-4-5", "hi") == "nice week"
        request = faked.requests[0]
        assert request.full_url == "https://api.anthropic.com/v1/messages"
        assert request.get_header("X-api-key") == KEY
        assert request.get_header("Anthropic-version") == "2023-06-01"
        body = json.loads(request.data.decode())
        assert body["model"] == "claude-haiku-4-5"
        assert body["max_tokens"] == coach.MAX_TOKENS
        assert body["messages"] == [{"role": "user", "content": "hi"}]

    def test_openai_request_shape(self, monkeypatch):
        faked = fake(monkeypatch, {"choices": [{"message": {"content": "keep going"}}]})
        assert coach.ask_openai(KEY, "gpt-5-mini", "hi") == "keep going"
        request = faked.requests[0]
        assert request.full_url == "https://api.openai.com/v1/chat/completions"
        assert request.get_header("Authorization") == f"Bearer {KEY}"

    def test_gemini_key_in_header_never_in_url(self, monkeypatch):
        faked = fake(monkeypatch, {"candidates": [{"content": {"parts": [{"text": "well done"}]}}]})
        assert coach.ask_gemini(KEY, "gemini-2.5-flash", "hi") == "well done"
        request = faked.requests[0]
        assert "gemini-2.5-flash:generateContent" in request.full_url
        assert "key=" not in request.full_url
        assert KEY not in request.full_url
        assert request.get_header("X-goog-api-key") == KEY


class TestGenerateCoachSummary:
    def test_unconfigured_makes_zero_calls(self, tmp_path, monkeypatch):
        report, db = make_report(tmp_path)
        faked = fake(monkeypatch, {})
        assert coach.generate_coach_summary(report, db) is None
        assert faked.requests == []

    def test_ollama_success(self, tmp_path, monkeypatch):
        report, db = make_report(tmp_path)
        faked = fake(monkeypatch, {"response": "Solid week."})
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "ollama")
        summary = coach.generate_coach_summary(report, db)
        assert summary == coach.CoachSummary("Solid week.", "ollama", "gemma3:4b")
        assert len(faked.requests) == 1

    def test_model_override(self, tmp_path, monkeypatch):
        report, db = make_report(tmp_path)
        fake(monkeypatch, {"response": "ok"})
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "ollama")
        monkeypatch.setenv("PULSEBOARD_AI_MODEL", "llama3:8b")
        summary = coach.generate_coach_summary(report, db)
        assert summary is not None
        assert summary.model == "llama3:8b"

    def test_unknown_provider_warns(self, tmp_path, monkeypatch, caplog):
        report, db = make_report(tmp_path)
        faked = fake(monkeypatch, {})
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "skynet")
        with caplog.at_level("WARNING"):
            assert coach.generate_coach_summary(report, db) is None
        assert faked.requests == []
        assert "skynet" in caplog.text

    def test_cloud_without_key_makes_zero_calls(self, tmp_path, monkeypatch, caplog):
        report, db = make_report(tmp_path)
        faked = fake(monkeypatch, {})
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "anthropic")
        with caplog.at_level("WARNING"):
            assert coach.generate_coach_summary(report, db) is None
        assert faked.requests == []
        assert "PULSEBOARD_ANTHROPIC_API_KEY" in caplog.text

    def test_pulseboard_key_beats_standard_key(self, tmp_path, monkeypatch):
        report, db = make_report(tmp_path)
        faked = fake(monkeypatch, {"content": [{"type": "text", "text": "hi"}]})
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "standard-key")
        monkeypatch.setenv("PULSEBOARD_ANTHROPIC_API_KEY", "prefixed-key")
        assert coach.generate_coach_summary(report, db) is not None
        assert faked.requests[0].get_header("X-api-key") == "prefixed-key"

    def test_provider_failure_degrades_to_none_without_leaking(self, tmp_path, monkeypatch, caplog):
        report, db = make_report(tmp_path)

        def boom(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(coach.urllib.request, "urlopen", boom)
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "gemini")
        monkeypatch.setenv("PULSEBOARD_GEMINI_API_KEY", KEY)
        with caplog.at_level("WARNING"):
            assert coach.generate_coach_summary(report, db) is None
        assert "gemini" in caplog.text
        assert KEY not in caplog.text

    def test_empty_response_is_none(self, tmp_path, monkeypatch):
        report, db = make_report(tmp_path)
        fake(monkeypatch, {"response": "   "})
        monkeypatch.setenv("PULSEBOARD_AI_PROVIDER", "ollama")
        assert coach.generate_coach_summary(report, db) is None

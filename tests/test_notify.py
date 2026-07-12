import json
import traceback
import urllib.error
from contextlib import contextmanager

import pytest

import pulseboard.notify as notify


class FakeUrlopen:
    """Records urllib.request.Request objects instead of hitting the network."""

    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)

        @contextmanager
        def response():
            yield None

        return response()


@pytest.fixture
def fake_urlopen(monkeypatch):
    fake = FakeUrlopen()
    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    return fake


class TestSendNtfy:
    def test_posts_to_topic_with_headers(self, fake_urlopen):
        notify.send_ntfy("https://ntfy.example.com/", "pulse", "Weekly", "body text", token="tok")
        request = fake_urlopen.requests[0]
        assert request.full_url == "https://ntfy.example.com/pulse"
        assert request.get_method() == "POST"
        assert request.data == b"body text"
        assert request.get_header("Title") == "Weekly"
        assert request.get_header("Priority") == "default"
        assert request.get_header("Authorization") == "Bearer tok"

    def test_no_auth_header_without_token(self, fake_urlopen):
        notify.send_ntfy("https://ntfy.sh", "pulse", "t", "b")
        assert fake_urlopen.requests[0].get_header("Authorization") is None


class TestSendTelegram:
    def test_sends_message_payload(self, fake_urlopen):
        notify.send_telegram("BOT:TOKEN", "12345", "hello")
        request = fake_urlopen.requests[0]
        assert request.full_url == "https://api.telegram.org/botBOT:TOKEN/sendMessage"
        payload = json.loads(request.data.decode())
        assert payload == {"chat_id": "12345", "text": "hello"}

    def test_truncates_to_api_limit(self, fake_urlopen):
        notify.send_telegram("BOT", "1", "x" * 5000)
        payload = json.loads(fake_urlopen.requests[0].data.decode())
        assert len(payload["text"]) == notify.TELEGRAM_MAX_CHARS


class TestErrorSanitization:
    """urllib errors carry the full URL — which embeds the ntfy topic and the
    Telegram bot token — so failures must surface without it."""

    @pytest.fixture
    def failing_urlopen(self, monkeypatch):
        def raising(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)

        monkeypatch.setattr(notify.urllib.request, "urlopen", raising)

    @staticmethod
    def assert_sanitized(exc: BaseException, secret: str) -> None:
        assert secret not in str(exc)
        # The URL-bearing HTTPError must be kept out of the rendered chain.
        assert exc.__cause__ is None
        assert exc.__suppress_context__
        rendered = "".join(traceback.format_exception(type(exc), exc, None))
        assert secret not in rendered

    def test_telegram_error_hides_token(self, failing_urlopen):
        with pytest.raises(RuntimeError) as excinfo:
            notify.send_telegram("SECRET-TOKEN", "1", "hi")
        assert "404" in str(excinfo.value)
        self.assert_sanitized(excinfo.value, "SECRET-TOKEN")

    def test_ntfy_error_hides_topic(self, failing_urlopen):
        with pytest.raises(RuntimeError) as excinfo:
            notify.send_ntfy("https://ntfy.sh", "secret-topic", "t", "b")
        self.assert_sanitized(excinfo.value, "secret-topic")

    def test_url_error_is_sanitized_too(self, monkeypatch):
        def raising(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(notify.urllib.request, "urlopen", raising)
        with pytest.raises(RuntimeError, match="connection refused"):
            notify.send_telegram("SECRET-TOKEN", "1", "hi")


class TestNotifyAll:
    def test_no_channels_configured(self, fake_urlopen, monkeypatch):
        for var in (
            "PULSEBOARD_NTFY_URL",
            "PULSEBOARD_NTFY_TOPIC",
            "PULSEBOARD_TELEGRAM_BOT_TOKEN",
            "PULSEBOARD_TELEGRAM_CHAT_ID",
        ):
            monkeypatch.delenv(var, raising=False)
        assert notify.notify_all("t", "b") == []
        assert fake_urlopen.requests == []

    def test_ntfy_only(self, fake_urlopen, monkeypatch):
        monkeypatch.setenv("PULSEBOARD_NTFY_URL", "https://ntfy.sh")
        monkeypatch.setenv("PULSEBOARD_NTFY_TOPIC", "pulse")
        monkeypatch.delenv("PULSEBOARD_TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("PULSEBOARD_TELEGRAM_CHAT_ID", raising=False)
        assert notify.notify_all("t", "b") == ["ntfy"]
        assert len(fake_urlopen.requests) == 1

    def test_both_channels(self, fake_urlopen, monkeypatch):
        monkeypatch.setenv("PULSEBOARD_NTFY_URL", "https://ntfy.sh")
        monkeypatch.setenv("PULSEBOARD_NTFY_TOPIC", "pulse")
        monkeypatch.setenv("PULSEBOARD_TELEGRAM_BOT_TOKEN", "BOT")
        monkeypatch.setenv("PULSEBOARD_TELEGRAM_CHAT_ID", "1")
        assert notify.notify_all("t", "b") == ["ntfy", "telegram"]
        assert len(fake_urlopen.requests) == 2

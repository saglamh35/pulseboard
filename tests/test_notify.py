import json
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

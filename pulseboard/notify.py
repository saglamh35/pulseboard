"""Push notifications for reports and alerts: ntfy (first-class, self-hosted
friendly) and Telegram (optional).

stdlib urllib only — no new runtime dependency. Channels are configured via
environment variables; an unconfigured channel is skipped silently (logged):

- PULSEBOARD_NTFY_URL        e.g. https://ntfy.sh (server base URL)
- PULSEBOARD_NTFY_TOPIC      e.g. my-secret-pulseboard-topic
- PULSEBOARD_NTFY_TOKEN      optional bearer token for protected topics
- PULSEBOARD_TELEGRAM_BOT_TOKEN
- PULSEBOARD_TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

TELEGRAM_MAX_CHARS = 4096
_TIMEOUT_SECONDS = 15


def send_ntfy(
    server_url: str,
    topic: str,
    title: str,
    body: str,
    token: str | None = None,
    priority: str = "default",
) -> None:
    """POST a message to an ntfy topic (https://docs.ntfy.sh)."""
    url = f"{server_url.rstrip('/')}/{topic}"
    request = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
    request.add_header("Title", title)
    request.add_header("Priority", priority)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS):
        pass


def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    """Send a message via the Telegram Bot API (truncated to the API limit)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text[:TELEGRAM_MAX_CHARS]}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS):
        pass


def notify_all(title: str, body: str) -> list[str]:
    """Send to every configured channel; returns the channels used."""
    channels: list[str] = []

    ntfy_url = os.environ.get("PULSEBOARD_NTFY_URL")
    ntfy_topic = os.environ.get("PULSEBOARD_NTFY_TOPIC")
    if ntfy_url and ntfy_topic:
        send_ntfy(ntfy_url, ntfy_topic, title, body, token=os.environ.get("PULSEBOARD_NTFY_TOKEN"))
        channels.append("ntfy")
    else:
        logger.debug("ntfy not configured (PULSEBOARD_NTFY_URL/TOPIC unset)")

    bot_token = os.environ.get("PULSEBOARD_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("PULSEBOARD_TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        send_telegram(bot_token, chat_id, f"{title}\n\n{body}")
        channels.append("telegram")
    else:
        logger.debug("Telegram not configured (PULSEBOARD_TELEGRAM_BOT_TOKEN/CHAT_ID unset)")

    if not channels:
        logger.warning("notify requested but no channel is configured; see docs/REPORTS.md")
    return channels

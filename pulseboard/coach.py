"""AI weekly coach: an opt-in natural-language summary of the weekly report.

The coach looks at the big picture — this week vs last, goals and streaks,
sleep debt, training load, today's scores and the 90-day correlations — and
writes a short, motivating note with 2-3 gentle goals for next week. It is
informational only, never medical advice.

Providers are configured via environment variables; with no provider set the
feature is completely off (zero LLM calls). The default is a local Ollama, so
health data never leaves the machine unless a cloud provider is chosen:

- PULSEBOARD_AI_PROVIDER       ollama | anthropic | openai | gemini (empty = off)
- PULSEBOARD_AI_MODEL          model override (defaults per provider below)
- PULSEBOARD_OLLAMA_URL        Ollama base URL (default http://127.0.0.1:11434)
- PULSEBOARD_ANTHROPIC_API_KEY (falls back to ANTHROPIC_API_KEY)
- PULSEBOARD_OPENAI_API_KEY    (falls back to OPENAI_API_KEY)
- PULSEBOARD_GEMINI_API_KEY    (falls back to GEMINI_API_KEY, GOOGLE_API_KEY)

Security, because this repo is public: keys are read from the environment
only, sent only in request HEADERS (never in URLs, which land in logs and
exception messages), never logged, and never echoed by any endpoint.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulseboard.db import Database
    from pulseboard.report import WeeklyReport

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 120  # LLM calls are slow, especially a local model on CPU
MAX_TOKENS = 512
MAX_WORKOUT_LINES = 10
MAX_ANOMALY_LINES = 5
MAX_CORRELATION_LINES = 3
MIN_CORRELATION_R = 0.3

PROVIDERS = ("ollama", "anthropic", "openai", "gemini")
DEFAULT_MODELS = {
    "ollama": "gemma3:4b",
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5-mini",
    "gemini": "gemini-2.5-flash",
}
# Accepted key variables per cloud provider, in priority order. The single
# source of truth — the dispatcher and the doctor check both read this.
KEY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("PULSEBOARD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    "openai": ("PULSEBOARD_OPENAI_API_KEY", "OPENAI_API_KEY"),
    "gemini": ("PULSEBOARD_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
}

_INSTRUCTIONS = (
    "You are a friendly, motivating fitness coach reviewing the personal weekly "
    "health report above. Look at the big picture: compare this week with last "
    "week, connect related signals (sleep, HRV, resting heart rate, training "
    "load), and call out what went well before what slipped. Then suggest 2-3 "
    "concrete, gentle goals for next week. Write 5-8 sentences of plain text — "
    "no markdown, no lists. Be encouraging, never diagnose, and say so when the "
    "data is too sparse to conclude anything. This is informational only, not "
    "medical advice."
)


@dataclass(frozen=True)
class CoachSummary:
    text: str
    provider: str
    model: str


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:g}"


def coach_prompt(report: "WeeklyReport", db: "Database | None" = None) -> str:
    """The big-picture digest + instructions sent to the LLM.

    Also served verbatim at GET /coach/prompt and embedded in the HTML
    report's "ask an AI" links, so the no-API-key phone path sees exactly
    what a configured provider would. With db the digest gains training
    load, today's scores and 90-day correlations; without it (pure
    WeeklyReport) those sections are skipped.
    """
    lines = [f"Personal weekly health report, {report.week_start} to {report.week_end} (vs the previous week):"]

    for c in report.comparisons:
        if c.this_week is None:
            continue
        unit = f" {c.unit}" if c.unit else ""
        line = f"- {c.label}: {_fmt(c.this_week)}{unit} this week"
        if c.last_week is not None:
            line += f" vs {_fmt(c.last_week)}{unit} last week"
        if c.delta_pct is not None:
            line += f" ({c.delta_pct:+.1f}%)"
        lines.append(f"{line}, {c.days_with_data} days of data")

    if report.goals:
        lines.append("Daily goals this week:")
        for g in report.goals:
            streak = f" (current streak: {g.streak_days} days)" if g.streak_days else ""
            lines.append(f"- {g.label}: met {g.met_days}/{g.days_with_data} days{streak}")
    if report.sleep_debt_hours is not None:
        lines.append(f"Sleep debt over the last 14 nights: {report.sleep_debt_hours:g} h vs the nightly goal.")

    if report.workouts:
        lines.append("Workouts this week:")
        for w in report.workouts[:MAX_WORKOUT_LINES]:
            lines.append(f"- {w.date}: {w.activity_type}, {w.duration_min:g} min")
        if len(report.workouts) > MAX_WORKOUT_LINES:
            lines.append(f"- ...and {len(report.workouts) - MAX_WORKOUT_LINES} more")

    if db is not None:
        lines.extend(_context_lines(db))

    if report.anomalies:
        lines.append("Anomalies vs the personal 30-day baseline:")
        for a in report.anomalies[:MAX_ANOMALY_LINES]:
            lines.append(f"- {a.metric} on {a.date}: {a.value:g} (z-score {a.zscore:+.1f})")

    lines += ["", _INSTRUCTIONS]
    return "\n".join(lines)


def _context_lines(db: "Database") -> list[str]:
    """Longer-horizon context beyond the weekly window (needs the DB)."""
    from pulseboard.insights import CORRELATION_PAIRS, correlation
    from pulseboard.readiness import compute_readiness_score
    from pulseboard.score import compute_health_score
    from pulseboard.training_load import compute_training_load

    lines: list[str] = []

    load = compute_training_load(db)
    if load is not None:
        line = (
            f"Training load: {_fmt(load.acute_minutes)} workout min over the last 7 days, "
            f"{_fmt(load.chronic_minutes)} over the last 28"
        )
        if load.acwr is not None:
            line += f" (acute:chronic ratio {load.acwr:g}; above 1.5 means ramping fast)"
        lines.append(line + ".")

    scores = []
    health = compute_health_score(db)
    if health is not None:
        scores.append(f"health {health:g}/100")
    readiness = compute_readiness_score(db)
    if readiness is not None:
        scores.append(f"readiness {readiness:g}/100")
    if scores:
        lines.append(f"Today's scores: {', '.join(scores)}.")

    correlations = []
    for pair in CORRELATION_PAIRS:
        result = correlation(db, pair)
        if result is None:
            continue
        r, n = result
        if abs(r) >= MIN_CORRELATION_R:
            correlations.append(f"- {pair.description}: r={r:+.2f} over {n} days")
    if correlations:
        lines.append("Personal 90-day correlations (correlation is not causation):")
        lines.extend(correlations[:MAX_CORRELATION_LINES])

    return lines


def prompt_links(prompt: str) -> dict[str, str]:
    """Prefill links for chat apps that support a query parameter.

    Gemini has no reliable prefill URL, so it stays paste-only (docs).
    """
    encoded = urllib.parse.quote(prompt)
    return {
        "claude": f"https://claude.ai/new?q={encoded}",
        "chatgpt": f"https://chatgpt.com/?q={encoded}",
    }


def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        request.add_header(name, value)
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def ask_ollama(base_url: str, model: str, prompt: str) -> str:
    data = _post_json(
        f"{base_url.rstrip('/')}/api/generate",
        {"model": model, "prompt": prompt, "stream": False},
        {},
    )
    return str(data["response"])


def ask_anthropic(api_key: str, model: str, prompt: str) -> str:
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"model": model, "max_tokens": MAX_TOKENS, "messages": [{"role": "user", "content": prompt}]},
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    return "".join(block["text"] for block in data["content"] if block.get("type") == "text")


def ask_openai(api_key: str, model: str, prompt: str) -> str:
    data = _post_json(
        "https://api.openai.com/v1/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": prompt}]},
        {"Authorization": f"Bearer {api_key}"},
    )
    return str(data["choices"][0]["message"]["content"])


def ask_gemini(api_key: str, model: str, prompt: str) -> str:
    # The key travels in a header, never as a ?key= query parameter: URLs end
    # up in logs and HTTPError messages, headers don't.
    data = _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"contents": [{"parts": [{"text": prompt}]}]},
        {"x-goog-api-key": api_key},
    )
    return "".join(part["text"] for part in data["candidates"][0]["content"]["parts"])


def _api_key(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def generate_coach_summary(
    report: "WeeklyReport", db: "Database | None" = None, prompt: str | None = None
) -> CoachSummary | None:
    """Ask the configured provider for a summary; None when unconfigured or
    on any failure — the report must never fail because the LLM did.

    Pass `prompt` when the caller already built it (the HTML report reuses
    the same text for its ask-an-AI links) to avoid recomputing the digest.
    """
    provider = os.environ.get("PULSEBOARD_AI_PROVIDER", "").strip().lower()
    if not provider:
        logger.debug("AI coach not configured (PULSEBOARD_AI_PROVIDER unset)")
        return None
    if provider not in PROVIDERS:
        logger.warning("AI coach: unknown PULSEBOARD_AI_PROVIDER %r (expected one of %s)", provider, PROVIDERS)
        return None

    model = os.environ.get("PULSEBOARD_AI_MODEL") or DEFAULT_MODELS[provider]
    prompt = prompt if prompt is not None else coach_prompt(report, db)
    try:
        if provider == "ollama":
            base_url = os.environ.get("PULSEBOARD_OLLAMA_URL", "http://127.0.0.1:11434")
            text = ask_ollama(base_url, model, prompt)
        else:
            key_vars = KEY_ENV_VARS[provider]
            api_key = _api_key(*key_vars)
            if api_key is None:
                logger.warning("AI coach: %s selected but %s is not set", provider, key_vars[0])
                return None
            ask = {"anthropic": ask_anthropic, "openai": ask_openai, "gemini": ask_gemini}[provider]
            text = ask(api_key, model, prompt)
    except Exception as exc:  # any provider hiccup degrades to "no coach section"
        logger.warning("AI coach (%s/%s) failed: %s", provider, model, exc)
        return None

    text = text.strip()
    if not text:
        logger.warning("AI coach (%s/%s) returned an empty response", provider, model)
        return None
    return CoachSummary(text=text, provider=provider, model=model)

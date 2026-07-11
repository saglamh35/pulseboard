"""Setup wizard / health check CLI: is PulseBoard wired up end to end?

Usage: python -m pulseboard.doctor [--db PATH] [--url http://127.0.0.1:8000]

Checks the SQLite database (exists, has data, data is fresh) and — when
--url is given — probes the running API. Every failing check prints an
actionable next step, so this doubles as the guided setup path referenced
throughout the docs.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pulseboard.db import Database, resolve_db_path

STALE_AFTER_SECONDS = 2 * 24 * 3600
_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    hint: str = ""  # printed only when not ok


def check_database(db_path: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    if not Path(db_path).exists():
        results.append(
            CheckResult(
                "database file",
                False,
                f"{db_path} does not exist",
                "Start the API (docker compose up -d) or run a backfill; the file is created on first write. "
                "Set PULSEBOARD_DB_PATH or --db if your database lives elsewhere.",
            )
        )
        return results
    results.append(CheckResult("database file", True, db_path))

    db = Database(db_path)
    try:
        rows = db.count_rows()
        workouts = db.count_workouts()
        if rows == 0:
            results.append(
                CheckResult(
                    "stored data",
                    False,
                    "0 metric rows",
                    "Nothing has been ingested yet. POST a payload to /ingest (docs/INGEST.md), set up the "
                    "iPhone automation (docs/SHORTCUT.md), or backfill an export (python -m pulseboard.backfill).",
                )
            )
            return results
        results.append(CheckResult("stored data", True, f"{rows} metric rows, {workouts} workouts"))

        latest_date = db.latest_metric_date()
        results.append(CheckResult("newest data day", True, str(latest_date)))

        last_ingest = db.last_ingest_at()
        if last_ingest is None:
            return results
        ingested = datetime.fromisoformat(last_ingest)
        if ingested.tzinfo is None:
            ingested = ingested.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ingested).total_seconds()
        if age > STALE_AFTER_SECONDS:
            results.append(
                CheckResult(
                    "data freshness",
                    False,
                    f"last ingest {age / 86400.0:.1f} days ago",
                    "The phone automation looks stalled. Re-check the Shortcut personal automation or the "
                    "Health Auto Export REST automation (docs/SHORTCUT.md), then watch the Data freshness "
                    "panel in Grafana.",
                )
            )
        else:
            results.append(CheckResult("data freshness", True, f"last ingest {age / 3600.0:.1f} h ago"))
    finally:
        db.close()
    return results


def _probe(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
            return True, response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        return False, str(exc)


def check_api(base_url: str) -> list[CheckResult]:
    base = base_url.rstrip("/")
    results: list[CheckResult] = []

    ok, body = _probe(f"{base}/health")
    results.append(
        CheckResult(
            "API /health",
            ok,
            "reachable" if ok else body,
            "Is the API running? Try: docker compose up -d, or uvicorn --factory pulseboard.app:create_app",
        )
    )
    if not ok:
        return results

    ok, body = _probe(f"{base}/status")
    if ok:
        status = json.loads(body)
        results.append(
            CheckResult(
                "API /status",
                True,
                f"rows={status.get('rows')} latest_data_date={status.get('latest_data_date')}",
            )
        )
    else:
        results.append(CheckResult("API /status", False, body, "The API is up but /status failed — check its logs."))

    ok, body = _probe(f"{base}/metrics")
    has_gauges = ok and "pulseboard_" in body
    results.append(
        CheckResult(
            "Prometheus exporter",
            has_gauges,
            "pulseboard_* gauges present" if has_gauges else ("no pulseboard_* gauges yet" if ok else body),
            "Gauges appear after the first ingest. If Prometheus still shows nothing, check its target page "
            "at http://127.0.0.1:9090/targets.",
        )
    )
    return results


def check_ai() -> list[CheckResult]:
    """AI coach configuration (optional feature). Reports key PRESENCE only —
    never a key value; this repo's docs assume public hosting."""
    from pulseboard.coach import DEFAULT_MODELS, PROVIDERS

    provider = os.environ.get("PULSEBOARD_AI_PROVIDER", "").strip().lower()
    if not provider:
        return [CheckResult("AI coach", True, "not configured (optional; set PULSEBOARD_AI_PROVIDER)")]
    if provider not in PROVIDERS:
        return [
            CheckResult(
                "AI coach",
                False,
                f"unknown provider {provider!r}",
                f"PULSEBOARD_AI_PROVIDER must be one of: {', '.join(PROVIDERS)}. See docs/AI_COACH.md.",
            )
        ]

    model = os.environ.get("PULSEBOARD_AI_MODEL") or DEFAULT_MODELS[provider]
    if provider == "ollama":
        base = os.environ.get("PULSEBOARD_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        ok, detail = _probe(f"{base}/api/tags")
        return [
            CheckResult(
                "AI coach",
                ok,
                f"ollama reachable at {base}, model {model}" if ok else detail,
                "Start Ollama (ollama serve, or docker compose --profile ai up -d) and pull the model: "
                f"ollama pull {model}. See docs/AI_COACH.md.",
            )
        ]

    key_var = f"PULSEBOARD_{provider.upper()}_API_KEY"
    fallbacks = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}
    has_key = bool(os.environ.get(key_var) or os.environ.get(fallbacks[provider]))
    return [
        CheckResult(
            "AI coach",
            has_key,
            f"{provider} configured (API key set), model {model}" if has_key else f"{provider}: API key NOT set",
            f"Set {key_var} (or {fallbacks[provider]}) in the environment — never commit it; see docs/AI_COACH.md.",
        )
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pulseboard.doctor",
        description="Check that PulseBoard is wired up: database, data freshness, and (optionally) the API.",
    )
    parser.add_argument("--db", default=None, help="SQLite path (default: $PULSEBOARD_DB_PATH or data/pulseboard.db)")
    parser.add_argument("--url", default=None, help="Also probe a running API, e.g. http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    results = check_database(args.db or resolve_db_path())
    if args.url:
        results += check_api(args.url)
    results += check_ai()

    failures = 0
    for result in results:
        mark = "✓" if result.ok else "✗"
        print(f"{mark} {result.name}: {result.detail}")
        if not result.ok:
            failures += 1
            if result.hint:
                print(f"  → {result.hint}")

    if failures:
        print(f"\n{failures} check(s) need attention.")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

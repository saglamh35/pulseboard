"""FastAPI app: POST /ingest, GET /health, GET /status, and the Prometheus
exporter at /metrics.

Run with: uvicorn --factory pulseboard.app:create_app
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import ValidationError

import pulseboard
from pulseboard.coach import coach_prompt, prompt_links
from pulseboard.db import Database, MetricRecord
from pulseboard.exporter import build_metrics_app
from pulseboard.ingest.adapters.health_auto_export import extract_workouts, is_hae_payload, normalize_hae
from pulseboard.ingest.canonical import CanonicalPayload, normalize
from pulseboard.insights import insights_summary
from pulseboard.metrics import REGISTRY
from pulseboard.report import build_weekly_report, render_html, render_markdown, with_coach

logger = logging.getLogger(__name__)

# Personal daily payloads are a few KB; even a full HAE month is far below
# this. Anything bigger is a mistake (or someone POSTing an export.xml).
MAX_BODY_BYTES = 10 * 1024 * 1024


def _require_token(request: Request) -> None:
    """Opt-in shared-secret auth for /ingest: only enforced when
    PULSEBOARD_API_TOKEN is set (for Tailscale/WireGuard-exposed setups —
    the default localhost-only deployment needs nothing)."""
    token = os.environ.get("PULSEBOARD_API_TOKEN")
    if not token:
        return
    supplied = request.headers.get("authorization", "")
    if supplied != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Missing or invalid bearer token")


def _workout_rollup_records(db: Database, dates: list[str], source: str) -> list[MetricRecord]:
    """Daily workouts_* rollup rows recomputed from the workouts table.

    Recomputing from the DB (not the payload) keeps re-ingests idempotent —
    a day's workouts can arrive spread across several POSTs.
    """
    records: list[MetricRecord] = []
    for row in db.workout_rollups_for_dates(dates):
        for metric, value in (
            ("workouts_count", float(row["count"])),
            ("workouts_duration_min", float(row["duration_min"] or 0.0)),
            ("workouts_energy_kcal", float(row["energy_kcal"] or 0.0)),
        ):
            records.append(
                MetricRecord(
                    date=row["date"],
                    metric=metric,
                    value=round(value, 2),
                    unit=REGISTRY[metric].unit,
                    aggregation="sum",
                    source=source,
                )
            )
    return records


def create_app(db_path: str | None = None) -> FastAPI:
    db = Database(db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        db.close()

    app = FastAPI(title="PulseBoard", version=pulseboard.__version__, lifespan=lifespan)
    app.state.db = db
    app.mount("/metrics", build_metrics_app(db))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    def status() -> dict[str, object]:
        """Setup/debugging snapshot: is data arriving, and how fresh is it?"""
        last_ingest = db.last_ingest_at()
        freshness_seconds: float | None = None
        if last_ingest is not None:
            ingested = datetime.fromisoformat(last_ingest)
            if ingested.tzinfo is None:
                ingested = ingested.replace(tzinfo=timezone.utc)
            freshness_seconds = round((datetime.now(timezone.utc) - ingested).total_seconds(), 1)
        return {
            "rows": db.count_rows(),
            "workouts": db.count_workouts(),
            "last_ingest_at": last_ingest,
            "latest_data_date": db.latest_metric_date(),
            "freshness_seconds": freshness_seconds,
            "metrics_tracked": len(REGISTRY),
            "version": pulseboard.__version__,
        }

    @app.get("/insights")
    def insights() -> dict[str, object]:
        """Correlations and anomalies computed from stored history."""
        return insights_summary(db)

    @app.get("/report/weekly")
    def weekly_report(format: str = "md", coach: int = 0):
        """Current week-to-date report, built on the fly.

        ?coach=1 additionally asks the env-configured AI provider for a
        summary — opt-in per request because a local model can take a
        minute; the weekly CLI/cron path adds it automatically instead."""
        if format not in ("md", "html"):
            raise HTTPException(status_code=422, detail="format must be 'md' or 'html'")
        report = build_weekly_report(db)
        # One big-picture digest serves both the LLM call and the HTML links.
        ask_prompt = coach_prompt(report, db) if (coach or format == "html") else None
        if coach:
            report = with_coach(report, db, prompt=ask_prompt)
        if format == "html":
            return HTMLResponse(render_html(report, ask_prompt=ask_prompt))
        return PlainTextResponse(render_markdown(report), media_type="text/markdown")

    @app.get("/coach/prompt")
    def coach_prompt_endpoint(format: str = "text"):
        """The ready-to-paste big-picture prompt for any chat AI — the
        no-API-key phone path. Requires no provider config and makes no
        LLM call; data leaves the machine only if you paste or tap."""
        if format not in ("text", "json"):
            raise HTTPException(status_code=422, detail="format must be 'text' or 'json'")
        prompt = coach_prompt(build_weekly_report(db), db)
        if format == "json":
            return {"prompt": prompt, "links": prompt_links(prompt)}
        return PlainTextResponse(prompt)

    @app.post("/ingest")
    async def ingest(request: Request) -> dict[str, object]:
        _require_token(request)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail=f"Request body exceeds {MAX_BODY_BYTES} bytes")
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Request body is not valid JSON")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        # Shape sniffing: Health Auto Export wraps everything in a top-level
        # "data" object; the canonical shape has date + metrics at the top.
        workouts = []
        if is_hae_payload(payload):
            source = "health_auto_export"
            records, skipped = normalize_hae(payload)
            workouts = extract_workouts(payload)
        else:
            source = "canonical"
            try:
                canonical = CanonicalPayload.model_validate(payload)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            records, skipped = normalize(canonical)
        stored = db.upsert_records(records)
        workouts_stored = db.upsert_workouts(workouts) if workouts else 0
        if workouts:
            affected_dates = sorted({w.date for w in workouts})
            db.upsert_records(_workout_rollup_records(db, affected_dates, source))
        latest_date = max((r.date for r in records), default=None)
        logger.info(
            "ingest source=%s stored=%d skipped=%d workouts=%d latest_date=%s",
            source,
            stored,
            len(skipped),
            workouts_stored,
            latest_date,
        )
        return {"stored": stored, "skipped": skipped, "workouts": workouts_stored, "latest_date": latest_date}

    return app

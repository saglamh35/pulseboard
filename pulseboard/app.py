"""FastAPI app: POST /ingest, GET /health, and the Prometheus exporter at /metrics.

Run with: uvicorn --factory pulseboard.app:create_app
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from pulseboard.db import Database
from pulseboard.exporter import build_metrics_app
from pulseboard.ingest.adapters.health_auto_export import is_hae_payload, normalize_hae
from pulseboard.ingest.canonical import CanonicalPayload, normalize


def create_app(db_path: str | None = None) -> FastAPI:
    db = Database(db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        db.close()

    app = FastAPI(title="PulseBoard", version="0.1.0", lifespan=lifespan)
    app.state.db = db
    app.mount("/metrics", build_metrics_app(db))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/ingest")
    async def ingest(request: Request) -> dict[str, object]:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Request body is not valid JSON")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        # Shape sniffing: Health Auto Export wraps everything in a top-level
        # "data" object; the canonical shape has date + metrics at the top.
        if is_hae_payload(payload):
            records, skipped = normalize_hae(payload)
        else:
            try:
                canonical = CanonicalPayload.model_validate(payload)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            records, skipped = normalize(canonical)
        stored = db.upsert_records(records)
        return {"stored": stored, "skipped": skipped}

    return app

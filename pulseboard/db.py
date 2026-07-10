"""SQLite storage for daily health metrics.

One row per (date, metric, aggregation); re-ingesting the same day updates
the row in place via the UNIQUE + ON CONFLICT upsert, which is what makes
both the ingest endpoint and the backfill CLI idempotent.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = "data/pulseboard.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS health_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    aggregation TEXT NOT NULL DEFAULT 'sum',
    source TEXT NOT NULL DEFAULT 'unknown',
    ingested_at TEXT NOT NULL,
    UNIQUE(date, metric, aggregation)
);
CREATE INDEX IF NOT EXISTS idx_health_metrics_metric_date
    ON health_metrics(metric, date);
"""

_UPSERT_SQL = """
INSERT INTO health_metrics (date, metric, value, unit, aggregation, source, ingested_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(date, metric, aggregation) DO UPDATE SET
    value = excluded.value,
    unit = excluded.unit,
    source = excluded.source,
    ingested_at = excluded.ingested_at
"""


@dataclass(frozen=True)
class MetricRecord:
    """A single daily metric value ready to be stored."""

    date: str  # ISO date, e.g. "2026-07-09"
    metric: str  # canonical metric name from pulseboard.metrics
    value: float
    unit: str
    aggregation: str  # "sum" | "min" | "avg" | "max" | "latest"
    source: str  # "canonical" | "health_auto_export" | "export_xml"


def resolve_db_path() -> str:
    return os.environ.get("PULSEBOARD_DB_PATH", DEFAULT_DB_PATH)


class Database:
    """Thin SQLite wrapper; one connection shared across the app."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or resolve_db_path()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_records(self, records: list[MetricRecord]) -> int:
        """Insert or update records; returns the number of records written."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._conn.executemany(
            _UPSERT_SQL,
            [(r.date, r.metric, r.value, r.unit, r.aggregation, r.source, now) for r in records],
        )
        self._conn.commit()
        return len(records)

    def latest_values(self) -> list[sqlite3.Row]:
        """Most recent row per (metric, aggregation) — what the exporter exposes."""
        return self._conn.execute(
            """
            SELECT hm.date, hm.metric, hm.value, hm.unit, hm.aggregation, hm.source
            FROM health_metrics hm
            WHERE hm.date = (
                SELECT MAX(date) FROM health_metrics
                WHERE metric = hm.metric AND aggregation = hm.aggregation
            )
            ORDER BY hm.metric, hm.aggregation
            """
        ).fetchall()

    def history(self, metric: str, aggregation: str | None = None, days: int | None = None) -> list[sqlite3.Row]:
        """Rows for one metric ordered by date ascending; optionally the last N days."""
        sql = "SELECT date, value, unit, aggregation, source FROM health_metrics WHERE metric = ?"
        params: list[object] = [metric]
        if aggregation is not None:
            sql += " AND aggregation = ?"
            params.append(aggregation)
        sql += " ORDER BY date DESC"
        if days is not None:
            sql += " LIMIT ?"
            params.append(days)
        rows = self._conn.execute(sql, params).fetchall()
        return list(reversed(rows))

    def count_rows(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0])

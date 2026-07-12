"""SQLite storage for daily health metrics.

One row per (date, metric, aggregation); re-ingesting the same day updates
the row in place via the UNIQUE + ON CONFLICT upsert, which is what makes
both the ingest endpoint and the backfill CLI idempotent.
"""

from __future__ import annotations

import os
import sqlite3
import threading
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
CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start TEXT NOT NULL,
    date TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    duration_min REAL NOT NULL DEFAULT 0,
    energy_kcal REAL NOT NULL DEFAULT 0,
    distance_km REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'unknown',
    ingested_at TEXT NOT NULL,
    UNIQUE(start, activity_type)
);
CREATE INDEX IF NOT EXISTS idx_workouts_date ON workouts(date);
"""

_WORKOUT_UPSERT_SQL = """
INSERT INTO workouts (start, date, activity_type, duration_min, energy_kcal, distance_km, source, ingested_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(start, activity_type) DO UPDATE SET
    duration_min = excluded.duration_min,
    energy_kcal = excluded.energy_kcal,
    distance_km = excluded.distance_km,
    source = excluded.source,
    ingested_at = excluded.ingested_at
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


@dataclass(frozen=True)
class WorkoutRecord:
    """One workout session (per-workout drilldown, not the daily rollup)."""

    start: str  # full timestamp, e.g. "2026-07-02 18:00:00 +0200"
    date: str  # ISO date the workout started
    activity_type: str  # e.g. "Running" (HKWorkoutActivityType prefix stripped)
    duration_min: float
    energy_kcal: float
    distance_km: float
    source: str


def resolve_db_path() -> str:
    return os.environ.get("PULSEBOARD_DB_PATH", DEFAULT_DB_PATH)


class Database:
    """Thin SQLite wrapper; one connection shared across the app.

    The connection is used from several threads at once (the async /ingest
    handler on the event loop, sync endpoints and the /metrics collector in
    Starlette's threadpool), so every access is serialized through a lock —
    a single sqlite3 connection is not safe for concurrent statements.
    Nested helpers (`series` → `history`) must take the lock only once; keep
    the lock on the innermost `self._conn`-touching method.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path or resolve_db_path()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL lets external readers of the same file (e.g. the Grafana SQLite
        # datasource) coexist with our writes. Adds -wal/-shm sidecar files.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert_records(self, records: list[MetricRecord]) -> int:
        """Insert or update records; returns the number of records written."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.executemany(
                _UPSERT_SQL,
                [(r.date, r.metric, r.value, r.unit, r.aggregation, r.source, now) for r in records],
            )
            self._conn.commit()
        return len(records)

    def latest_values(self) -> list[sqlite3.Row]:
        """Most recent row per (metric, aggregation) — what the exporter exposes."""
        with self._lock:
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

    def history(
        self,
        metric: str,
        aggregation: str | None = None,
        days: int | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[sqlite3.Row]:
        """Rows for one metric ordered by date ascending; optionally the last
        N stored days and/or an inclusive [start, end] date window."""
        sql = "SELECT date, value, unit, aggregation, source FROM health_metrics WHERE metric = ?"
        params: list[object] = [metric]
        if aggregation is not None:
            sql += " AND aggregation = ?"
            params.append(aggregation)
        if start is not None:
            sql += " AND date >= ?"
            params.append(start)
        if end is not None:
            sql += " AND date <= ?"
            params.append(end)
        sql += " ORDER BY date DESC"
        if days is not None:
            sql += " LIMIT ?"
            params.append(days)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return list(reversed(rows))

    def last_ingest_at(self) -> str | None:
        """Most recent ingested_at across metrics and workouts — "is the
        phone still posting", regardless of which dates the data was for."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT MAX(ingested_at) FROM (
                    SELECT ingested_at FROM health_metrics
                    UNION ALL
                    SELECT ingested_at FROM workouts
                )
                """
            ).fetchone()
        return row[0] if row and row[0] is not None else None

    def latest_metric_date(self) -> str | None:
        """Newest date we have data FOR — a backfill of old days bumps
        last_ingest_at but not this, so staleness alerts key off this one."""
        with self._lock:
            row = self._conn.execute("SELECT MAX(date) FROM health_metrics").fetchone()
        return row[0] if row and row[0] is not None else None

    def series(self, metric: str, aggregation: str, days: int) -> dict[str, float]:
        """{date: value} for the last N stored days, oldest first."""
        return {row["date"]: float(row["value"]) for row in self.history(metric, aggregation, days=days)}

    def range_stats(self, metric: str, aggregation: str, start: str, end: str) -> sqlite3.Row:
        """SUM/AVG/COUNT over an inclusive date window (total/mean are NULL
        when no rows fall in the window)."""
        with self._lock:
            return self._conn.execute(
                """
                SELECT SUM(value) AS total, AVG(value) AS mean, COUNT(*) AS days
                FROM health_metrics
                WHERE metric = ? AND aggregation = ? AND date BETWEEN ? AND ?
                """,
                (metric, aggregation, start, end),
            ).fetchone()

    def workouts_between(self, start: str, end: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT start, date, activity_type, duration_min, energy_kcal, distance_km, source
                FROM workouts WHERE date BETWEEN ? AND ? ORDER BY start
                """,
                (start, end),
            ).fetchall()

    def upsert_workouts(self, workouts: list[WorkoutRecord]) -> int:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.executemany(
                _WORKOUT_UPSERT_SQL,
                [
                    (w.start, w.date, w.activity_type, w.duration_min, w.energy_kcal, w.distance_km, w.source, now)
                    for w in workouts
                ],
            )
            self._conn.commit()
        return len(workouts)

    def recent_workouts(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT start, date, activity_type, duration_min, energy_kcal, distance_km, source
                FROM workouts ORDER BY start DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def workout_rollups_for_dates(self, dates: list[str]) -> list[sqlite3.Row]:
        """Per-date COUNT/SUM(duration_min)/SUM(energy_kcal) from the
        workouts table — the source of truth for the daily rollup metrics."""
        if not dates:
            return []
        placeholders = ",".join("?" for _ in dates)
        with self._lock:
            return self._conn.execute(
                f"""
                SELECT date, COUNT(*) AS count, SUM(duration_min) AS duration_min, SUM(energy_kcal) AS energy_kcal
                FROM workouts WHERE date IN ({placeholders}) GROUP BY date
                """,
                dates,
            ).fetchall()

    def earliest_workout_date(self) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT MIN(date) FROM workouts").fetchone()
        return row[0] if row and row[0] is not None else None

    def count_workouts(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0])

    def weekly_rollup(self, metric: str, aggregation: str) -> list[sqlite3.Row]:
        """Per-ISO-week totals and means for one metric, oldest week first.

        `week_start` is the first stored date of that week — what the
        dashboard uses as the bar's time coordinate.
        """
        with self._lock:
            return self._conn.execute(
                """
                SELECT strftime('%Y-%W', date) AS week,
                       MIN(date) AS week_start,
                       SUM(value) AS total,
                       AVG(value) AS mean,
                       COUNT(*) AS days
                FROM health_metrics
                WHERE metric = ? AND aggregation = ?
                GROUP BY week
                ORDER BY week_start
                """,
                (metric, aggregation),
            ).fetchall()

    def count_rows(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0])

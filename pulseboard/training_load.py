"""Training load: acute:chronic workload ratio (ACWR) from workout history.

Load proxy is workout duration in minutes, summed over calendar-day windows
(a day without workouts contributes 0). ACWR = (7-day daily average) /
(28-day daily average); values above ~1.5 are the classic "ramping too fast"
heuristic — contested in the literature, and informational only, not medical
or training advice. Documented in docs/TRAINING_LOAD.md.

Computed from the per-workout `workouts` table (not the daily rollup
metrics), so it works for every ingest path and all stored history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pulseboard.db import Database

ACUTE_DAYS = 7
CHRONIC_DAYS = 28
# Below this much workout history the chronic window is mostly empty and the
# ratio is meaningless noise, so acwr stays None.
MIN_CHRONIC_HISTORY_DAYS = 14
ACWR_HIGH = 1.5  # the alert rule pb-acwr-high mirrors this threshold


@dataclass(frozen=True)
class TrainingLoad:
    acute_minutes: float  # total workout minutes over the last ACUTE_DAYS
    chronic_minutes: float  # total workout minutes over the last CHRONIC_DAYS
    acwr: float | None  # None while there is too little history to be meaningful


def _minutes_between(db: "Database", start: date, end: date) -> float:
    rows = db.workouts_between(start.isoformat(), end.isoformat())
    return sum(float(row["duration_min"]) for row in rows)


def compute_training_load(db: "Database", anchor: date | None = None) -> TrainingLoad | None:
    """Load as of `anchor` (default: today, so a rest week decays acute load).

    None when no workouts are stored at all; acwr is None when the chronic
    window is empty or the history is younger than MIN_CHRONIC_HISTORY_DAYS.
    """
    earliest = db.earliest_workout_date()
    if earliest is None:
        return None
    anchor = anchor or date.today()

    acute = _minutes_between(db, anchor - timedelta(days=ACUTE_DAYS - 1), anchor)
    chronic = _minutes_between(db, anchor - timedelta(days=CHRONIC_DAYS - 1), anchor)

    acwr: float | None = None
    enough_history = date.fromisoformat(earliest) <= anchor - timedelta(days=MIN_CHRONIC_HISTORY_DAYS - 1)
    if chronic > 0 and enough_history:
        acwr = round((acute / ACUTE_DAYS) / (chronic / CHRONIC_DAYS), 2)

    return TrainingLoad(acute_minutes=round(acute, 1), chronic_minutes=round(chronic, 1), acwr=acwr)

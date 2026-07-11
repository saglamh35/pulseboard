"""Per-metric daily goals: streaks, weekly hit counts, and sleep debt.

Goals are declared on MetricDef entries in the registry (pulseboard.metrics);
this module derives the streak/report/debt views over stored history. All of
it is informational only, not medical advice.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from pulseboard.metrics import REGISTRY, MetricDef

if TYPE_CHECKING:
    from pulseboard.db import Database

GOAL_DEFS: tuple[MetricDef, ...] = tuple(d for d in REGISTRY.values() if d.goal is not None)

STREAK_WINDOW_DAYS = 365
SLEEP_DEBT_DAYS = 14


def streak_days(db: "Database", metric: str) -> int | None:
    """Consecutive calendar days (ending at the metric's own latest stored
    day) on which the goal was met.

    Anchoring on the metric's latest date means a not-yet-synced today does
    not zero the streak. A missing calendar day breaks the streak — unlike
    trends.rising_days, which tolerates gaps. None when nothing is stored.
    """
    definition = REGISTRY[metric]
    goal = definition.goal
    if goal is None:
        return None
    series = db.series(metric, definition.default_aggregation, days=STREAK_WINDOW_DAYS)
    if not series:
        return None
    day = date.fromisoformat(max(series))
    count = 0
    while True:
        value = series.get(day.isoformat())
        if value is None or not goal.met(value):
            break
        count += 1
        day -= timedelta(days=1)
    return count


def goals_met_in_window(db: "Database", metric: str, start: str, end: str) -> tuple[int, int]:
    """(days the goal was met, days with data) over an inclusive date window."""
    definition = REGISTRY[metric]
    goal = definition.goal
    if goal is None:
        return (0, 0)
    rows = db.history(metric, definition.default_aggregation, start=start, end=end)
    met = sum(1 for row in rows if goal.met(float(row["value"])))
    return (met, len(rows))


def sleep_debt_hours(db: "Database", days: int = SLEEP_DEBT_DAYS) -> float | None:
    """Cumulative sleep shortfall vs the registry sleep goal over the last
    `days` calendar nights, anchored at the latest stored sleep date.

    Pure debt: surplus nights don't repay it and unrecorded nights add
    nothing (a sync gap is not sleep deprivation). None without sleep data.
    """
    definition = REGISTRY["sleep_hours"]
    goal = definition.goal
    if goal is None:
        return None
    series = db.series("sleep_hours", definition.default_aggregation, days=days)
    if not series:
        return None
    anchor = date.fromisoformat(max(series))
    window_start = (anchor - timedelta(days=days - 1)).isoformat()
    debt = sum(max(goal.value - value, 0.0) for day, value in series.items() if day >= window_start)
    return round(debt, 1)

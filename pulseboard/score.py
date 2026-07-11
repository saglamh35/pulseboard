"""Composite 0-100 daily health score.

Informational only — a toy heuristic for the dashboard, not a medical
indicator. The formula is documented in docs/SCORE.md; keep both in sync.

Components (weight):
- sleep (0.35): last night's sleep_hours vs. an 8 h target, capped at 1.
- steps (0.25): today's steps vs. an 8000-step goal, capped at 1.
- resting heart rate (0.20): 1 at/below your 30-day baseline, falling
  linearly to 0 at baseline + 15 bpm.
- HRV trend (0.20): 0.5 when today's SDNN equals the 30-day baseline,
  1 at +50% or better, 0 at -50% or worse.

Missing metrics drop out and the remaining weights are renormalized, so a
day with only steps data is scored on steps alone. No data -> no score.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pulseboard.metrics import REGISTRY

if TYPE_CHECKING:
    from pulseboard.db import Database

# The 8 h sleep target is the score's *ideal* (partial credit up to 8 h) and
# is deliberately separate from the registry's >= 7 h daily sleep *goal*.
SLEEP_TARGET_HOURS = 8.0
_steps_goal = REGISTRY["steps"].goal
assert _steps_goal is not None
STEPS_GOAL = _steps_goal.value
RESTING_HR_TOLERANCE_BPM = 15.0
BASELINE_DAYS = 30

_WEIGHTS = {
    "sleep": 0.35,
    "steps": 0.25,
    "resting_heart_rate": 0.20,
    "hrv": 0.20,
}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _baseline(db: "Database", metric: str, aggregation: str) -> float | None:
    rows = db.history(metric, aggregation, days=BASELINE_DAYS)
    if not rows:
        return None
    values = [float(row["value"]) for row in rows]
    return sum(values) / len(values)


def compute_health_score(db: "Database") -> float | None:
    """Score the latest stored day; None when nothing relevant is stored."""
    latest = {(row["metric"], row["aggregation"]): float(row["value"]) for row in db.latest_values()}
    components: dict[str, float] = {}

    sleep = latest.get(("sleep_hours", "sum"))
    if sleep is not None:
        components["sleep"] = _clamp(sleep / SLEEP_TARGET_HOURS)

    steps = latest.get(("steps", "sum"))
    if steps is not None:
        components["steps"] = _clamp(steps / STEPS_GOAL)

    resting_hr = latest.get(("resting_heart_rate", "avg"))
    if resting_hr is not None:
        baseline = _baseline(db, "resting_heart_rate", "avg")
        if baseline is not None:
            components["resting_heart_rate"] = _clamp(1.0 - max(resting_hr - baseline, 0.0) / RESTING_HR_TOLERANCE_BPM)

    hrv = latest.get(("heart_rate_variability_sdnn", "avg"))
    if hrv is not None:
        baseline = _baseline(db, "heart_rate_variability_sdnn", "avg")
        if baseline is not None and baseline > 0:
            components["hrv"] = _clamp(0.5 + (hrv - baseline) / baseline)

    if not components:
        return None

    total_weight = sum(_WEIGHTS[name] for name in components)
    weighted = sum(_WEIGHTS[name] * value for name, value in components.items())
    return round(weighted / total_weight * 100.0, 1)

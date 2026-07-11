"""Morning readiness score: a 0-100 "how recovered am I?" composite.

Informational only — a toy heuristic for the dashboard, not a medical
indicator. The formula is documented in docs/SCORE.md; keep both in sync.

Where the health score (pulseboard.score) asks "how did the latest day go
overall" (steps included), readiness looks only at recovery inputs:

Components (weight):
- HRV trend (0.40): 0.5 when the latest SDNN equals the 30-day baseline,
  1 at +50% or better, 0 at -50% or worse.
- resting heart rate (0.35): 1 at/below your 30-day baseline, falling
  linearly to 0 at baseline + 15 bpm.
- sleep (0.25): last night's sleep_hours vs. an 8 h target, capped at 1.

Missing metrics drop out and the remaining weights are renormalized.
No data -> no score.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pulseboard.score import RESTING_HR_TOLERANCE_BPM, SLEEP_TARGET_HOURS, _baseline, _clamp

if TYPE_CHECKING:
    from pulseboard.db import Database

_WEIGHTS = {
    "hrv": 0.40,
    "resting_heart_rate": 0.35,
    "sleep": 0.25,
}


def compute_readiness_score(db: "Database") -> float | None:
    """Readiness for the latest stored day; None when nothing relevant is stored."""
    latest = {(row["metric"], row["aggregation"]): float(row["value"]) for row in db.latest_values()}
    components: dict[str, float] = {}

    hrv = latest.get(("heart_rate_variability_sdnn", "avg"))
    if hrv is not None:
        baseline = _baseline(db, "heart_rate_variability_sdnn", "avg")
        if baseline is not None and baseline > 0:
            components["hrv"] = _clamp(0.5 + (hrv - baseline) / baseline)

    resting_hr = latest.get(("resting_heart_rate", "avg"))
    if resting_hr is not None:
        baseline = _baseline(db, "resting_heart_rate", "avg")
        if baseline is not None:
            components["resting_heart_rate"] = _clamp(1.0 - max(resting_hr - baseline, 0.0) / RESTING_HR_TOLERANCE_BPM)

    sleep = latest.get(("sleep_hours", "sum"))
    if sleep is not None:
        components["sleep"] = _clamp(sleep / SLEEP_TARGET_HOURS)

    if not components:
        return None

    total_weight = sum(_WEIGHTS[name] for name in components)
    weighted = sum(_WEIGHTS[name] * value for name, value in components.items())
    return round(weighted / total_weight * 100.0, 1)

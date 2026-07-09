"""Canonical metric registry: the single source of truth for metric names,
units, aggregations, and Prometheus gauge names.

Every ingestion path (canonical POST, Health Auto Export adapter, export.xml
backfill) normalizes to these names before anything touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDef:
    name: str  # canonical name, used as the `metric` column in SQLite
    unit: str
    aggregations: tuple[str, ...]  # allowed aggregations; first one is the default
    prom_name: str  # Prometheus gauge name
    description: str

    @property
    def default_aggregation(self) -> str:
        return self.aggregations[0]


_DEFS: tuple[MetricDef, ...] = (
    # Activity (daily cumulative sums)
    MetricDef("steps", "count", ("sum",), "pulseboard_steps", "Daily step count"),
    MetricDef(
        "distance_walking_running",
        "km",
        ("sum",),
        "pulseboard_distance_walking_running_km",
        "Daily walking + running distance",
    ),
    MetricDef("flights_climbed", "count", ("sum",), "pulseboard_flights_climbed", "Daily flights of stairs climbed"),
    MetricDef("active_energy", "kcal", ("sum",), "pulseboard_active_energy_kcal", "Daily active energy burned"),
    MetricDef("basal_energy", "kcal", ("sum",), "pulseboard_basal_energy_kcal", "Daily basal (resting) energy burned"),
    MetricDef(
        "apple_exercise_time", "min", ("sum",), "pulseboard_apple_exercise_time_min", "Daily Apple exercise minutes"
    ),
    MetricDef("apple_stand_hours", "count", ("sum",), "pulseboard_apple_stand_hours", "Daily Apple stand hours"),
    # Heart (min/avg/max within the day, or one daily value)
    MetricDef("heart_rate", "bpm", ("avg", "min", "max"), "pulseboard_heart_rate_bpm", "Daily heart rate"),
    MetricDef("resting_heart_rate", "bpm", ("avg",), "pulseboard_resting_heart_rate_bpm", "Daily resting heart rate"),
    MetricDef(
        "walking_heart_rate", "bpm", ("avg",), "pulseboard_walking_heart_rate_bpm", "Daily walking heart rate average"
    ),
    MetricDef(
        "heart_rate_variability_sdnn",
        "ms",
        ("avg",),
        "pulseboard_hrv_sdnn_ms",
        "Daily heart rate variability (SDNN)",
    ),
    # Respiratory / fitness
    MetricDef(
        "blood_oxygen_saturation",
        "percent",
        ("avg", "min", "max"),
        "pulseboard_blood_oxygen_saturation_percent",
        "Daily blood oxygen saturation",
    ),
    MetricDef("respiratory_rate", "breaths/min", ("avg",), "pulseboard_respiratory_rate", "Daily respiratory rate"),
    MetricDef("vo2_max", "mL/kg/min", ("latest",), "pulseboard_vo2_max", "Most recent VO2 max estimate"),
    # Sleep
    MetricDef("sleep_hours", "h", ("sum",), "pulseboard_sleep_hours", "Hours asleep for the night ending that day"),
    # Body
    MetricDef("body_mass", "kg", ("latest",), "pulseboard_body_mass_kg", "Most recent body mass"),
    # Workouts (daily rollups)
    MetricDef("workouts_count", "count", ("sum",), "pulseboard_workouts_count", "Number of workouts that day"),
    MetricDef(
        "workouts_duration_min", "min", ("sum",), "pulseboard_workouts_duration_min", "Total workout duration that day"
    ),
    MetricDef(
        "workouts_energy_kcal", "kcal", ("sum",), "pulseboard_workouts_energy_kcal", "Total workout energy that day"
    ),
)

REGISTRY: dict[str, MetricDef] = {d.name: d for d in _DEFS}

# Apple HealthKit record type -> canonical name (used by the backfill CLI).
# HKCategoryTypeIdentifierSleepAnalysis is handled separately in the backfill
# (interval durations, not point values), so it is deliberately not listed.
HEALTHKIT_TO_CANONICAL: dict[str, str] = {
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "distance_walking_running",
    "HKQuantityTypeIdentifierFlightsClimbed": "flights_climbed",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active_energy",
    "HKQuantityTypeIdentifierBasalEnergyBurned": "basal_energy",
    "HKQuantityTypeIdentifierAppleExerciseTime": "apple_exercise_time",
    "HKCategoryTypeIdentifierAppleStandHour": "apple_stand_hours",
    "HKQuantityTypeIdentifierHeartRate": "heart_rate",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": "walking_heart_rate",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "heart_rate_variability_sdnn",
    "HKQuantityTypeIdentifierOxygenSaturation": "blood_oxygen_saturation",
    "HKQuantityTypeIdentifierRespiratoryRate": "respiratory_rate",
    "HKQuantityTypeIdentifierVO2Max": "vo2_max",
    "HKQuantityTypeIdentifierBodyMass": "body_mass",
}

# Health Auto Export metric name -> canonical name (used by the HAE adapter).
HAE_TO_CANONICAL: dict[str, str] = {
    "step_count": "steps",
    "walking_running_distance": "distance_walking_running",
    "flights_climbed": "flights_climbed",
    "active_energy": "active_energy",
    "basal_energy_burned": "basal_energy",
    "apple_exercise_time": "apple_exercise_time",
    "apple_stand_hour": "apple_stand_hours",
    "heart_rate": "heart_rate",
    "resting_heart_rate": "resting_heart_rate",
    "walking_heart_rate_average": "walking_heart_rate",
    "heart_rate_variability": "heart_rate_variability_sdnn",
    "blood_oxygen_saturation": "blood_oxygen_saturation",
    "respiratory_rate": "respiratory_rate",
    "vo2_max": "vo2_max",
    "sleep_analysis": "sleep_hours",
    "weight_body_mass": "body_mass",
}


def get_metric(name: str) -> MetricDef | None:
    return REGISTRY.get(name)

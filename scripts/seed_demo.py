"""Seed a running PulseBoard stack with ~120 days of synthetic demo data.

For screenshots and local demos only — this is deterministic fake data, NOT
real health data (the repo's privacy stance is that real exports never get
committed). Point it at a live API (docker compose up -d) and it POSTs a
realistic history: slow fitness trends, a rough sleep patch, a weekly workout
rhythm, and lag-1 sleep->HRV correlation, tuned so every dashboard row —
including goals, streaks, sleep debt and training-load ACWR — renders with
meaningful values.

Usage:
    docker compose up -d --build
    python scripts/seed_demo.py                 # defaults to http://127.0.0.1:8000
    PULSEBOARD_URL=http://host:8000 python scripts/seed_demo.py

See docs/REGENERATE_SCREENSHOTS.md for the full screenshot workflow.
"""

from __future__ import annotations

import json
import os
import random
import urllib.request
from datetime import date, timedelta

BASE = os.environ.get("PULSEBOARD_URL", "http://127.0.0.1:8000").rstrip("/")
DAYS = 120
TODAY = date.today()
rng = random.Random(42)

# Weekly workout rhythm: weekday -> (activity, base minutes).
SCHEDULE = {
    0: ("Running", 42),
    1: ("Traditional Strength Training", 45),
    3: ("Running", 38),
    5: ("Cycling", 65),
    6: ("Yoga", 30),
}
KCAL_PER_MIN = {"Running": 11, "Cycling": 8.5, "Yoga": 3.5, "Traditional Strength Training": 6.5}


def post(payload: dict) -> dict:
    request = urllib.request.Request(f"{BASE}/ingest", data=json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def build_workouts() -> tuple[list[dict], dict[str, float]]:
    """HAE-shaped workouts (exercises the live rollup path); the recent week is
    loaded a touch heavier so the acute:chronic ratio lands around 1.1-1.3."""
    workouts: list[dict] = []
    minutes_by_day: dict[str, float] = {}
    for offset in range(DAYS, -1, -1):
        day = TODAY - timedelta(days=offset)
        plan = SCHEDULE.get(day.weekday())
        if plan is None or rng.random() < 0.12:  # skip ~12% of planned sessions
            continue
        activity, base = plan
        minutes = round(base * rng.uniform(0.85, 1.25) * (1.15 if offset <= 6 else 1.0), 1)
        distance = (
            round(minutes / 6.0, 2)
            if activity == "Running"
            else (round(minutes * 0.42, 2) if activity == "Cycling" else 0.0)
        )
        minutes_by_day[day.isoformat()] = minutes_by_day.get(day.isoformat(), 0.0) + minutes
        workouts.append(
            {
                "name": activity,
                "start": f"{day.isoformat()} 18:00:00 +0000",
                "duration": minutes,
                "activeEnergyBurned": {"qty": round(minutes * KCAL_PER_MIN[activity]), "units": "kcal"},
                "distance": {"qty": distance, "units": "km"},
            }
        )
    return workouts, minutes_by_day


def daily_metrics(offset: int, minutes_by_day: dict[str, float], sleep_prev: float, active_prev: float) -> list[dict]:
    day = TODAY - timedelta(days=offset)
    trend = (DAYS - offset) / DAYS  # 0..1 slow progress
    weekend = day.weekday() >= 5
    rough = 25 >= offset >= 15  # a visible rough sleep patch
    workout_min = minutes_by_day.get(day.isoformat(), 0.0)

    sleep = max(4.8, min(9.0, rng.gauss(6.1 if rough else 7.35, 0.45)))
    deep = round(sleep * rng.uniform(0.16, 0.2), 2)
    rem = round(sleep * rng.uniform(0.2, 0.24), 2)
    awake = round(rng.uniform(0.2, 0.5), 2)
    core = round(sleep - deep - rem, 2)

    steps = max(3500, round(rng.gauss(10200 if weekend else 9200, 1600) - (1800 if rough else 0)))
    if offset <= 9:
        steps = max(steps, 8300)  # a visible current streak on the goal

    exercise_min = round(workout_min * 0.85 + max(0.0, steps - 6000) / 250)
    active = round(320 + steps * 0.028 + workout_min * 7.5 + rng.gauss(0, 40))
    rhr = 57.5 - 2.5 * trend + (1.8 if rough else 0) + (active_prev - 500) / 400 + rng.gauss(0, 0.9)
    hrv = 44 + 6 * trend + (sleep_prev - 7.3) * 4.5 + rng.gauss(0, 3.5)

    return [
        {"name": "steps", "value": steps},
        {"name": "distance_walking_running", "value": round(steps * 0.00074, 2)},
        {"name": "flights_climbed", "value": rng.randint(4, 18)},
        {"name": "active_energy", "value": active},
        {"name": "basal_energy", "value": round(rng.gauss(1650, 40))},
        {"name": "apple_exercise_time", "value": exercise_min},
        {"name": "apple_stand_hours", "value": rng.randint(9, 14)},
        {"name": "sleep_hours", "value": round(sleep, 2)},
        {"name": "sleep_core_hours", "value": core},
        {"name": "sleep_deep_hours", "value": deep},
        {"name": "sleep_rem_hours", "value": rem},
        {"name": "sleep_awake_hours", "value": awake},
        {"name": "resting_heart_rate", "value": round(rhr, 1), "aggregation": "avg"},
        {"name": "heart_rate_variability_sdnn", "value": round(hrv, 1), "aggregation": "avg"},
        {"name": "heart_rate", "value": rng.randint(46, 52), "aggregation": "min"},
        {"name": "heart_rate", "value": round(rng.gauss(68, 3)), "aggregation": "avg"},
        {"name": "heart_rate", "value": rng.randint(120, 168), "aggregation": "max"},
        {"name": "walking_heart_rate", "value": round(rng.gauss(92, 4)), "aggregation": "avg"},
        {"name": "respiratory_rate", "value": round(rng.gauss(14.2, 0.5), 1), "aggregation": "avg"},
        {"name": "blood_oxygen_saturation", "value": round(rng.uniform(95.5, 98.5), 1), "aggregation": "avg"},
        {"name": "vo2_max", "value": round(41.5 + 3.2 * trend + rng.gauss(0, 0.3), 1), "aggregation": "latest"},
        {"name": "body_mass", "value": round(77.5 - 2.4 * trend + rng.gauss(0, 0.25), 1), "aggregation": "latest"},
        {
            "name": "body_fat_percentage",
            "value": round(21.5 - 1.8 * trend + rng.gauss(0, 0.2), 1),
            "aggregation": "latest",
        },
        {"name": "wrist_temperature", "value": round(rng.gauss(35.8, 0.15), 2), "aggregation": "avg"},
        {"name": "time_in_daylight", "value": rng.randint(25, 110)},
        {"name": "walking_speed", "value": round(rng.gauss(4.9, 0.2), 2), "aggregation": "avg"},
        {"name": "cardio_recovery", "value": round(rng.gauss(31 + 4 * trend, 2)), "aggregation": "avg"},
        {"name": "mindful_minutes", "value": rng.choice([0, 0, 5, 10, 12])},
    ]


def main() -> None:
    workouts, minutes_by_day = build_workouts()
    sleep_prev, active_prev = 7.4, 500.0
    rows = 0
    for offset in range(DAYS, -1, -1):
        day = TODAY - timedelta(days=offset)
        metrics = daily_metrics(offset, minutes_by_day, sleep_prev, active_prev)
        rows += post({"date": day.isoformat(), "metrics": metrics})["stored"]
        sleep_prev = next(m["value"] for m in metrics if m["name"] == "sleep_hours")
        active_prev = next(m["value"] for m in metrics if m["name"] == "active_energy")

    stored = post({"data": {"metrics": [], "workouts": workouts}})
    print(f"Seeded {rows} metric rows and {stored['workouts']} workouts.")
    with urllib.request.urlopen(f"{BASE}/status", timeout=10) as response:
        print("Status:", response.read().decode("utf-8"))


if __name__ == "__main__":
    main()

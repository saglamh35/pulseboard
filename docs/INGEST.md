# Ingestion

`POST /ingest` accepts two payload shapes on the same endpoint. The shape is
sniffed from the body: a top-level `"data"` object means Health Auto Export,
anything else is validated as the canonical shape.

Both paths normalize onto the canonical metric registry
(`pulseboard/metrics.py`) and upsert one row per `(date, metric, aggregation)`
— re-posting the same day simply updates the values. The response is always
`{"stored": <n>, "skipped": [<names>], "workouts": <n>, "latest_date": "..."}`;
unknown metric names are skipped and reported, never an error. Bodies over
10 MB are rejected with `413` (that's an `export.xml` — use the backfill CLI).

If the API is exposed beyond localhost, set `PULSEBOARD_API_TOKEN` on the
server and send `Authorization: Bearer <token>` with every POST — see
[SHORTCUT.md](SHORTCUT.md). Unset (the default), `/ingest` stays open.

`GET /status` reports what's stored and how fresh it is
(`rows`, `workouts`, `last_ingest_at`, `latest_data_date`,
`freshness_seconds`, …) — the first place to look when data doesn't show up,
alongside `python -m pulseboard.doctor`.

## Canonical shape

The simplest way to push data (e.g. from an Apple Shortcut, see
[SHORTCUT.md](SHORTCUT.md)):

```json
{
  "date": "2026-07-09",
  "metrics": [
    { "name": "steps", "value": 8250 },
    { "name": "sleep_hours", "value": 7.4 },
    { "name": "heart_rate", "value": 51, "aggregation": "min" },
    { "name": "heart_rate", "value": 74, "aggregation": "avg" },
    { "name": "heart_rate", "value": 152, "aggregation": "max" }
  ]
}
```

- `date` — ISO date the values belong to.
- `name` — canonical metric name (see the table below).
- `value` — number.
- `aggregation` — optional; defaults to the metric's default (its first
  allowed aggregation). An aggregation the metric doesn't support skips
  that entry.
- `unit` — optional; defaults to the canonical unit. Values are stored
  as-is, no conversion happens on this path.

## Health Auto Export shape

The [Health Auto Export](https://www.healthyapps.dev/) iOS app can POST its
"REST API" JSON export directly to `/ingest`:

```json
{
  "data": {
    "metrics": [
      {
        "name": "step_count",
        "units": "count",
        "data": [{ "date": "2026-07-09 23:59:59 +0200", "qty": 8250 }]
      },
      {
        "name": "heart_rate",
        "units": "count/min",
        "data": [{ "date": "2026-07-09 23:59:59 +0200", "Min": 51, "Avg": 74, "Max": 152 }]
      }
    ]
  }
}
```

Mapping rules (`pulseboard/ingest/adapters/health_auto_export.py`):

- HAE metric names are mapped via `HAE_TO_CANONICAL`; unmapped names are
  skipped and listed in the response.
- Points carry either a single `qty` (stored under the metric's default
  aggregation) or `Min`/`Avg`/`Max` fields (either capitalization), stored
  as separate aggregation rows.
- `sleep_analysis` points use `asleep` (falling back to `totalSleep`/`qty`)
  hours and are stored as `sleep_hours`; per-stage fields (`core`, `deep`,
  `rem`, `awake`) become `sleep_core_hours` / `sleep_deep_hours` /
  `sleep_rem_hours` / `sleep_awake_hours`.
- `blood_pressure` points carry `systolic`/`diastolic` fields instead of
  `qty` and are split into `blood_pressure_systolic` /
  `blood_pressure_diastolic`.
- Stringified quantities (`"qty": "8250"`) are accepted — some HAE/Shortcut
  versions stringify numbers.
- `data.workouts[]` entries (`name`, `start`, `duration`,
  `activeEnergyBurned`, `distance` — plain numbers or `{"qty": ...}`) are
  stored one row per session in the `workouts` table for the dashboard's
  drilldown, keyed on `(start, activity_type)` so re-posts update in place.
- Each point's `date` field determines the row date, so one payload can
  cover several days.

## Canonical metric names

| Name | Unit | Aggregations |
| --- | --- | --- |
| `steps` | count | sum |
| `distance_walking_running` | km | sum |
| `flights_climbed` | count | sum |
| `active_energy` | kcal | sum |
| `basal_energy` | kcal | sum |
| `apple_exercise_time` | min | sum |
| `apple_stand_hours` | count | sum |
| `heart_rate` | bpm | avg, min, max |
| `resting_heart_rate` | bpm | avg |
| `walking_heart_rate` | bpm | avg |
| `heart_rate_variability_sdnn` | ms | avg |
| `blood_oxygen_saturation` | percent | avg, min, max |
| `respiratory_rate` | breaths/min | avg |
| `vo2_max` | mL/kg/min | latest |
| `sleep_hours` | h | sum |
| `sleep_core_hours` | h | sum |
| `sleep_deep_hours` | h | sum |
| `sleep_rem_hours` | h | sum |
| `sleep_awake_hours` | h | sum |
| `mindful_minutes` | min | sum |
| `time_in_daylight` | min | sum |
| `walking_speed` | km/h | avg |
| `cardio_recovery` | bpm | avg |
| `wrist_temperature` | degC | avg |
| `blood_pressure_systolic` | mmHg | avg |
| `blood_pressure_diastolic` | mmHg | avg |
| `body_mass` | kg | latest |
| `body_fat_percentage` | percent | latest |
| `workouts_count` | count | sum |
| `workouts_duration_min` | min | sum |
| `workouts_energy_kcal` | kcal | sum |

## Backfilling history

For months/years of history, export from the Health app (Profile → Export
All Health Data), unzip the archive, and stream the XML into SQLite:

```bash
python -m pulseboard.backfill /path/to/export.xml
```

The parser streams the file (constant memory), aggregates per day, converts
units where Apple's exports differ (miles → km, kJ → kcal, SpO2 fraction →
percent), sums per-night asleep intervals into `sleep_hours` (stage intervals also
feed the per-stage metrics; awake time never counts toward the total), rolls
workouts up into daily count/duration/energy **and** stores each workout
session in the `workouts` table, and upserts with `source=export_xml`.
Re-running it is idempotent, and live `/ingest` posts for the same dates
simply overwrite the backfilled values.

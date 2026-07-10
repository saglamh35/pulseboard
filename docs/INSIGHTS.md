# Insights: correlations & anomalies

`pulseboard/insights.py` derives two kinds of signals from the SQLite
history. Both are **informational only, not medical advice**, and
**correlation is not causation** — a strong r between sleep and next-day HRV
says the two move together in *your* data, nothing more.

Everything is stdlib-only (hand-rolled Pearson, `statistics.stdev`) and
computed on demand: per Prometheus scrape for the gauges, per request for
`GET /insights`. At personal-data scale (a few rows per day) this costs
nothing and keeps the app stateless.

## Correlations

For each registered pair (`CORRELATION_PAIRS`), daily series are aligned —
optionally with a lag, so "sleep on day *d*" is paired with "HRV on day
*d+1*" — and a Pearson r is computed over the last **90 days**. Pairs with
fewer than **14** overlapping days are reported as "not enough data" rather
than a shaky number.

| Pair | A | B | Lag |
| --- | --- | --- | --- |
| `sleep_vs_next_day_hrv` | sleep_hours | HRV (SDNN) | 1 day |
| `activity_vs_next_day_resting_hr` | active_energy | resting heart rate | 1 day |
| `workout_minutes_vs_next_day_hrv` | workouts_duration_min | HRV (SDNN) | 1 day |
| `steps_vs_sleep_same_day` | steps | sleep_hours | same day |

Exposed as `pulseboard_correlation{pair="..."}` (r in [-1, 1]) with
`pulseboard_correlation_samples{pair="..."}` alongside, and charted on the
dashboard's **Insights** row — including two scatter plots that query SQLite
directly so you can eyeball the relationship behind the number.

## Anomalies (z-scores)

For each watchlist metric (resting HR, HRV, sleep hours, steps, respiratory
rate), the latest day's value is compared with the mean and standard
deviation of the preceding **30 days** (the latest day excluded from its own
baseline):

```
z = (latest − baseline mean) / baseline stdev
```

No z-score is emitted with fewer than 7 baseline days or a zero-variance
baseline. |z| ≥ 2 is "unusual for you", |z| ≥ 3 "very unusual". Exposed as
`pulseboard_zscore{metric="..."}`; `detect_anomalies()` (used by
`GET /insights` and the weekly report) lists metrics past the 2.0 threshold.

Two provisioned alerts ride on these gauges: resting HR z > 2.5 and
HRV z < −2.5 ([ALERTING.md](ALERTING.md)). The method is deliberately a
transparent rolling-baseline z-score rather than anything fancier — it's
explainable, testable, and matches the project's "read it in one sitting"
goal.

## API

`GET /insights` returns the full summary as JSON: every correlation (r,
sample count, lag, description), current anomalies, the windows used, and
the disclaimer.

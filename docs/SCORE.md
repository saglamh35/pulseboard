# Health score

`pulseboard_health_score` is a composite **0–100** number computed from the
latest stored day. It exists to give the dashboard a single "how am I doing"
gauge and to demonstrate deriving a metric from several stored series.

> **This is not medical advice.** The score is an arbitrary, documented
> heuristic over consumer-device data. It has no clinical meaning; don't use
> it to make health decisions.

## Formula

Each available component is scored 0–1, weighted, and the weighted mean is
scaled to 0–100. Components without data drop out and the remaining weights
are renormalized (a day with only steps data is scored on steps alone). With
no relevant data at all, no score is exported.

| Component | Weight | 0–1 score |
| --- | --- | --- |
| Sleep | 0.35 | `min(sleep_hours / 8, 1)` |
| Steps | 0.25 | `min(steps / 8000, 1)` |
| Resting heart rate | 0.20 | `1` at/below your 30-day baseline, falling linearly to `0` at baseline + 15 bpm |
| HRV trend | 0.20 | `0.5 + (today_sdnn − baseline) / baseline`, clamped to 0–1 (neutral 0.5 at baseline, 1 at +50 %, 0 at −50 %) |

Baselines are the mean of the last 30 stored days (including today) of
`resting_heart_rate` / `heart_rate_variability_sdnn`. With only one day of
history the baseline equals today's value, so resting HR scores 1 and HRV
scores a neutral 0.5.

Constants (`SLEEP_TARGET_HOURS`, `STEPS_GOAL`, `RESTING_HR_TOLERANCE_BPM`,
`BASELINE_DAYS`) and the implementation live in `pulseboard/score.py`; keep
this document in sync when tuning them.

## Example

Sleep 7.4 h, 8 250 steps, resting HR at baseline, HRV at baseline:

```
sleep  = 7.4 / 8      = 0.925 × 0.35
steps  = min(8250/8000, 1) = 1.0 × 0.25
rhr    = 1.0               × 0.20
hrv    = 0.5 (neutral)     × 0.20
score  = (0.324 + 0.25 + 0.20 + 0.10) / 1.0 × 100 ≈ 87.4
```

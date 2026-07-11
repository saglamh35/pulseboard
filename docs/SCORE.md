# Health score & readiness score

`pulseboard_health_score` is a composite **0–100** number computed from the
latest stored day. It exists to give the dashboard a single "how am I doing"
gauge and to demonstrate deriving a metric from several stored series.
Its recovery-focused sibling, the [readiness score](#readiness-score), is
documented below.

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
this document in sync when tuning them. The steps goal is sourced from the
metric registry's daily goals ([GOALS.md](GOALS.md)). Note the deliberate
distinction between the score's 8 h sleep *ideal* (partial credit up to 8 h)
and the registry's ≥ 7 h daily sleep *goal* used for streaks and sleep debt.

## Example

Sleep 7.4 h, 8 250 steps, resting HR at baseline, HRV at baseline:

```
sleep  = 7.4 / 8      = 0.925 × 0.35
steps  = min(8250/8000, 1) = 1.0 × 0.25
rhr    = 1.0               × 0.20
hrv    = 0.5 (neutral)     × 0.20
score  = (0.324 + 0.25 + 0.20 + 0.10) / 1.0 × 100 ≈ 87.4
```

## Readiness score

`pulseboard_readiness_score` answers the morning question "how recovered am
I?" — same 0–100 scale, same renormalized-weights mechanics, but only
recovery inputs (no steps: an active day says nothing about how well you
bounced back overnight). Implementation: `pulseboard/readiness.py`.

| Component | Weight | 0–1 score |
| --- | --- | --- |
| HRV trend | 0.40 | `0.5 + (today_sdnn − baseline) / baseline`, clamped to 0–1 |
| Resting heart rate | 0.35 | `1` at/below your 30-day baseline, falling linearly to `0` at baseline + 15 bpm |
| Sleep | 0.25 | `min(sleep_hours / 8, 1)` |

The HRV and resting-HR components are the exact formulas the health score
uses (shared 30-day baselines); only the weights differ — readiness leans on
the two physiological recovery signals. The matching alert `pb-readiness-low`
fires below 40 ([ALERTING.md](ALERTING.md)).

Same disclaimer as the health score: **informational only, not medical
advice.**

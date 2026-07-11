# Training load (ACWR)

The acute:chronic workload ratio compares what you did in the last 7 days
against your 4-week norm. Ramping volume much faster than your body is used
to is a classic overtraining/injury-risk signal; ~1.5 is the traditional
"too fast" line.

> **Informational only, not medical or training advice.** The ACWR heuristic
> is genuinely contested in the sports-science literature — treat it as a
> conversation starter with yourself, not a verdict.

## Definition

Load proxy is **workout duration in minutes**, summed per calendar day from
the per-workout `workouts` table (a day without workouts counts as 0 — it's
a rest day, not missing data). Implementation:
`pulseboard/training_load.py`.

```
acute   = total workout minutes over the last 7 days   (ending today)
chronic = total workout minutes over the last 28 days  (ending today)
ACWR    = (acute / 7) / (chronic / 28)
```

Duration (not energy) is the classic proxy and is present on every workout
record regardless of source. Windows are anchored to *today*, so a rest week
correctly decays the acute load even when no new data arrives.

## Gauges & guards

| Gauge | Meaning |
| --- | --- |
| `pulseboard_training_load_acute_7d_minutes` | 7-day total workout minutes |
| `pulseboard_training_load_chronic_28d_minutes` | 28-day total workout minutes |
| `pulseboard_training_load_acwr` | the ratio |

The ratio is withheld (no gauge) until there are at least **14 days** of
workout history and a non-zero chronic load — a fresh install with two
workouts would otherwise show absurd ratios. The acute/chronic totals are
always exported once any workout exists.

The alert `pb-acwr-high` fires above **1.5** with `severity=warning`
([ALERTING.md](ALERTING.md)); the constant lives in
`pulseboard/training_load.py` (`ACWR_HIGH`) and the YAML mirrors it.

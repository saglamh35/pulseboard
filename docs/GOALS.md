# Daily goals, streaks & sleep debt

Per-metric daily goals are declared directly in the metric registry
(`pulseboard/metrics.py`): a `MetricDef` can carry a
`Goal(value, direction)`, where direction is `"at_least"` or `"at_most"`.
Everything else — streak gauges, weekly-report lines, sleep debt — derives
from those declarations (`pulseboard/goals.py`), so adding a goal to a new
metric is a one-line registry change.

> **Informational only, not medical advice.** Goals are motivation
> scaffolding over consumer-device data, nothing more.

## Current goals

| Metric | Goal |
| --- | --- |
| `steps` | ≥ 8000 |
| `sleep_hours` | ≥ 7 h |
| `apple_exercise_time` | ≥ 30 min |

The ≥ 7 h sleep goal is deliberately different from the health/readiness
score's 8 h sleep *ideal* — the goal is a daily minimum commitment, the
ideal is what full credit looks like ([SCORE.md](SCORE.md)).

## Streaks

`pulseboard_goal_streak_days{metric=...}` counts consecutive calendar days
the goal was met, ending at that metric's **own latest stored day** (so a
not-yet-synced today doesn't zero your streak). A missing calendar day
breaks the streak — a streak means consecutive days, full stop. The
companion gauge `pulseboard_goal_target{metric=...}` exports each goal value
so dashboards can draw target lines without hardcoding.

The weekly report gets a **Goals** section with one line per goal, e.g.
`Steps ≥ 8000: met 5/7 days (streak: 12 days)` ([REPORTS.md](REPORTS.md)).

## Sleep debt

`pulseboard_sleep_debt_hours` is the cumulative shortfall vs the sleep goal
over the last 14 nights (anchored at the latest stored sleep date):

```
debt = Σ max(7.0 − sleep_hours, 0) over the last 14 nights
```

Pure debt, by design: a 9 h night doesn't repay a 5 h night (oversleeping
once does not undo deprivation), and unrecorded nights contribute nothing (a
sync gap is not sleep deprivation). The alert `pb-sleep-debt-high` fires
above 7 h — roughly one full lost night in two weeks — complementing the
existing 7-day-average sleep alert ([ALERTING.md](ALERTING.md)).

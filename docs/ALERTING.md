# Alerting

Grafana alert rules are provisioned as code from
`grafana/provisioning/alerting/pulseboard_alerts.yml` — they appear under
**Alerting → Alert rules → PulseBoard** on startup, no click-ops.

All rules query **Prometheus trend gauges** that the exporter derives from
SQLite history (`pulseboard/trends.py`), so the alert expressions stay
trivial threshold checks — the same pattern as alerting on recording rules
in production.

| Rule | Fires when | Backing gauge |
| --- | --- | --- |
| Resting heart rate rising 3+ days | consecutive strict daily increases ≥ 3 | `pulseboard_resting_heart_rate_rising_days` |
| 7-day average sleep below 6.5 h | rolling mean < 6.5 | `pulseboard_sleep_hours_7d_avg` |
| 7-day average steps below 5000 | rolling mean < 5000 | `pulseboard_steps_7d_avg` |
| No new health data for 2 days | `time() - latest data timestamp > 172800` | `pulseboard_latest_data_timestamp_seconds` |
| Resting HR unusually high vs baseline | z-score > 2.5 | `pulseboard_zscore{metric="resting_heart_rate"}` |
| HRV unusually low vs baseline | z-score < −2.5 | `pulseboard_zscore{metric="heart_rate_variability_sdnn"}` |
| Health score below 50 | score < 50 | `pulseboard_health_score` |

The staleness rule carries `severity=warning` (it means the pipeline broke,
not your body); everything else is `severity=info`. Anomaly z-scores are
explained in [INSIGHTS.md](INSIGHTS.md).

Notes:

- Rolling means use the **last 7 stored days** (fewer while history is
  short); the rising-days counter resets on any flat or falling day.
- `noDataState` is `OK` on purpose: a missing metric (e.g. no sleep data
  yet) should not page you.
- **No contact point is configured by default** — alerts show in the
  Grafana UI and that's it. Two opt-in delivery paths ship with the repo:
  an env-driven ntfy webhook contact point (uncomment
  `grafana/provisioning/alerting/contactpoints.yml` and set
  `PULSEBOARD_NTFY_URL`), or the report CLI's cron-friendly
  `--check-freshness --notify` ([REPORTS.md](REPORTS.md)). Secrets stay out
  of git either way.
- Thresholds (8 h sleep target vs 6.5 h alert floor, 5000-step floor,
  score 50) are starting points — edit the YAML and restart Grafana.
- Same framing as everything here: **informational signals, not medical
  advice.**

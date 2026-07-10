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
| Health score below 50 | score < 50 | `pulseboard_health_score` |

Notes:

- Rolling means use the **last 7 stored days** (fewer while history is
  short); the rising-days counter resets on any flat or falling day.
- `noDataState` is `OK` on purpose: a missing metric (e.g. no sleep data
  yet) should not page you.
- **No contact point is configured** — alerts show in the Grafana UI and
  that's it. To get notified, add your own contact point (email, Telegram,
  webhook, …) under Alerting → Contact points and route the `severity=info`
  label to it. Deliberately not shipped: delivery targets are personal
  config, not code.
- Thresholds (8 h sleep target vs 6.5 h alert floor, 5000-step floor,
  score 50) are starting points — edit the YAML and restart Grafana.
- Same framing as everything here: **informational signals, not medical
  advice.**

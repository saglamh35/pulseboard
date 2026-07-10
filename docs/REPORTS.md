# Weekly reports & notifications

`python -m pulseboard.report` builds a Monday–Sunday summary of the current
week against the previous one — totals for activity metrics (steps,
distance, energy, exercise minutes, workouts), nightly/daily means for
sleep, resting HR and HRV — plus the week's workouts and any current
anomalies ([INSIGHTS.md](INSIGHTS.md)). Informational only, not medical
advice.

## CLI

```bash
python -m pulseboard.report                      # markdown to stdout
python -m pulseboard.report --format html --out /data/reports/
python -m pulseboard.report --week-ending 2026-07-06   # any date in the target week
python -m pulseboard.report --notify             # also push a short summary
python -m pulseboard.report --check-freshness    # exit 1 if no data for 2+ days
python -m pulseboard.report --check-freshness --notify  # ...and push a warning
```

`--out` takes a file or a directory (directory → `pulseboard-week-<monday>.md`).
`GET /report/weekly?format=md|html` serves the same report on the fly from
the running API.

## Notifications (ntfy / Telegram)

`pulseboard/notify.py` pushes over plain HTTP — no extra dependencies.
Configure channels via environment variables; unset channels are skipped:

| Variable | Meaning |
| --- | --- |
| `PULSEBOARD_NTFY_URL` | ntfy server base URL, e.g. `https://ntfy.sh` or your own |
| `PULSEBOARD_NTFY_TOPIC` | topic name (treat it as a secret on public servers) |
| `PULSEBOARD_NTFY_TOKEN` | optional bearer token for protected topics |
| `PULSEBOARD_TELEGRAM_BOT_TOKEN` | bot token from @BotFather |
| `PULSEBOARD_TELEGRAM_CHAT_ID` | chat to message (your user id or a group) |

The push is a *short* summary — the three biggest week-over-week moves and
the anomaly count — not the whole report.

Grafana can also push its alert rules (e.g. data staleness) to ntfy; see the
commented provisioning in `grafana/provisioning/alerting/contactpoints.yml`.

## Scheduling

The report is a one-shot CLI on purpose (same pattern as
`python -m pulseboard.backfill`): scheduling belongs to the platform, so a
restart never silently skips a week.

**Host cron** (primary):

```cron
0 8 * * 1  cd /path/to/pulseboard && PULSEBOARD_NTFY_URL=... PULSEBOARD_NTFY_TOPIC=... python -m pulseboard.report --notify --out data/reports/
```

**systemd timer**: a `pulseboard-report.service` running the same command
plus a `pulseboard-report.timer` with `OnCalendar=Mon 08:00`.

**Docker Compose**: an optional sidecar under the `reports` profile sleeps
until Monday 08:00 in a loop:

```bash
PULSEBOARD_NTFY_URL=https://ntfy.sh PULSEBOARD_NTFY_TOPIC=my-topic \
  docker compose --profile reports up -d
```

**Kubernetes**: enable the CronJob in the Helm chart —
`--set report.enabled=true` (schedule defaults to `0 8 * * 1`). Put the
notification variables in a Secret and point `report.notifySecret` at it.

## Freshness watchdog without Grafana

If you skip Grafana alerting, `--check-freshness --notify` gives the same
"data stopped arriving" push from cron:

```cron
0 9 * * *  cd /path/to/pulseboard && PULSEBOARD_NTFY_URL=... PULSEBOARD_NTFY_TOPIC=... python -m pulseboard.report --check-freshness --notify
```

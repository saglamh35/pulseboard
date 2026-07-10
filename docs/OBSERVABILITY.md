# The exporter pattern (and why there are two stores)

PulseBoard deliberately mirrors how production services expose metrics to
Prometheus, because rehearsing that pattern is the point of the project.

## The exporter

`GET /metrics` is served by a custom `prometheus_client` **Collector**
(`pulseboard/exporter.py`) registered in its own `CollectorRegistry` (no
default process/python gauges — only PulseBoard's). On every scrape the
collector queries SQLite for the most recent row of each
`(metric, aggregation)` pair and emits gauges:

- one plain gauge per single-aggregation metric, e.g. `pulseboard_steps`,
  `pulseboard_sleep_hours`, `pulseboard_resting_heart_rate_bpm`;
- one labelled gauge per multi-aggregation metric, e.g.
  `pulseboard_heart_rate_bpm{agg="min|avg|max"}`;
- the derived `pulseboard_health_score` (see [SCORE.md](SCORE.md));
- freshness timestamps and insight gauges:

| Gauge | Meaning |
| --- | --- |
| `pulseboard_last_ingest_timestamp_seconds` | unix time of the most recent successful ingest (any dates — backfills count) |
| `pulseboard_latest_data_timestamp_seconds` | unix time (midnight UTC) of the newest day we have data *for* — staleness alerts key off this one |
| `pulseboard_correlation{pair=...}` | Pearson r between paired daily series over 90 days ([INSIGHTS.md](INSIGHTS.md)) |
| `pulseboard_correlation_samples{pair=...}` | aligned day pairs behind the r |
| `pulseboard_zscore{metric=...}` | latest day vs its own 30-day baseline, in standard deviations |

Because the collector reads at scrape time, `/metrics` always reflects the
database with no refresh loop, no caching layer, and no state in the app.
That is the same shape as a typical infrastructure exporter: pull-based,
stateless, cheap queries against the system of record.

The exporter is mounted into the FastAPI app with
`prometheus_client.make_asgi_app()`, so one uvicorn process serves both the
ingest API and the scrape endpoint on port 8000.

## Why Prometheus AND SQLite?

Health data arrives as **daily values, often backfilled months late** —
which is exactly what Prometheus is bad at. Prometheus timestamps samples at
scrape time; you cannot tell it "steps for 2024-03-17 were 9 412". Pushing
history through the scrape path would attach today's timestamp to last
year's data.

So the two stores split the job honestly:

- **SQLite is the system of record.** Every daily value ever ingested or
  backfilled, keyed on `(date, metric, aggregation)`, queryable months back.
  Grafana's *History* row charts it directly via the SQLite datasource.
- **Prometheus only sees "now".** Each scrape samples the latest day's
  values, giving the *Live* row, alerting-style semantics, and the exact
  operational pattern used at work — target discovery, scrape intervals,
  PromQL over current state.

This is the standard trade-off you also see in production: Prometheus for
operational "what is the state right now / recently", a real database for
the historical record. PulseBoard just makes the split explicit and small
enough to read in one sitting.

## Scrape topology

```
Prometheus (pulseboard-prometheus) --scrape 60s--> pulseboard:8000/metrics
Grafana    (pulseboard-grafana)    --query-------> Prometheus  (Live row)
                                   --query-------> /data/pulseboard.db (ro, History row)
```

Everything binds to `127.0.0.1` on the host; the containers talk over the
compose network. The SQLite file is mounted read-only into Grafana so a
dashboard bug can never corrupt the record.

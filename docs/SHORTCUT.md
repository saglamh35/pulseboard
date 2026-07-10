# Getting iPhone data into PulseBoard

Two ways to keep PulseBoard fresh from an iPhone / Apple Watch, ranked.
Either way, re-sending the same day is always safe: rows are upserted per
`(date, metric, aggregation)`, so values update instead of duplicating.

## Path A — Health Auto Export (recommended)

[Health Auto Export](https://www.healthyapps.dev/) reads HealthKit and can
POST its JSON export to PulseBoard on a schedule — the most hands-off setup,
and it covers far more metrics than a hand-built Shortcut.

1. In the app, create a new **Automation** of type **REST API**.
2. **URL**: `http://<host-running-pulseboard>:8000/ingest` (see the network
   note below), method **POST**.
3. **Export format**: JSON. **Aggregation**: daily ("Days").
4. **Metrics**: enable what you want tracked. PulseBoard currently maps:
   steps, walking/running distance, flights climbed, active/basal energy,
   exercise time, stand hours, heart rate (min/avg/max), resting & walking
   heart rate, HRV, blood oxygen, respiratory rate, VO2 max, sleep analysis
   (incl. stages), body mass, body fat percentage, mindful minutes, time in
   daylight, walking speed, wrist temperature, cardio recovery, blood
   pressure, and workouts. Unmapped metrics are skipped and listed in the
   response — never an error.
5. **Schedule**: hourly (or "when new data is available") keeps the *Data
   freshness* panel green; the payload is small and re-posts are idempotent,
   so frequent syncs cost nothing.
6. Run the automation once manually and check the response: `stored > 0`
   and `latest_date` should be today.

## Path B — plain Apple Shortcut (no third-party app)

The canonical `/ingest` shape is designed so a plain Shortcut can push
today's numbers.

1. **Find Health Samples** — one "Find Health Samples" action per metric
   (e.g. Steps, "is today", group by day, sum). Store each result in a
   variable.
2. **Dictionary** — build the payload:
   - `date`: "Current Date" formatted as `yyyy-MM-dd` (add a "Format Date"
     action with a custom format).
   - `metrics`: a list of dictionaries, one per metric, each with `name`
     (canonical name from [INGEST.md](INGEST.md)) and `value` (the variable
     from step 1).
3. **Get Contents of URL**:
   - URL: `http://<host-running-pulseboard>:8000/ingest`
   - Method: `POST`
   - Request Body: `JSON`, the dictionary from step 2.

Example payload the Shortcut should end up sending:

```json
{
  "date": "2026-07-09",
  "metrics": [
    { "name": "steps", "value": 8250 },
    { "name": "resting_heart_rate", "value": 58 },
    { "name": "sleep_hours", "value": 7.4 }
  ]
}
```

### Run it automatically

In the Shortcuts app: **Automation → + → Time of Day**, pick a time (or
several automations spread across the day — hourly triggers must be created
one by one), select **Run Immediately** so it doesn't wait for confirmation,
and attach the Shortcut. Because posts are idempotent, running it every few
hours simply refreshes today's row.

## Verify the pipeline

After setting either path up, confirm data actually flows:

```bash
curl -s http://127.0.0.1:8000/status
# latest_data_date should be today; freshness_seconds should be small

python -m pulseboard.doctor --url http://127.0.0.1:8000
```

In Grafana, the **Data freshness** stat on the Live row turns yellow after
one day without new data and red after two — and the provisioned
*No new health data for 2 days* alert fires ([ALERTING.md](ALERTING.md)).

## Network & auth

- PulseBoard binds to `127.0.0.1` on its host by design. To post from your
  phone, be on the same private network and deliberately choose how to
  expose port 8000 — a WireGuard/Tailscale address is the sane option.
  Don't port-forward it to the internet.
- If the API is reachable beyond localhost, set a shared secret:
  start the server with `PULSEBOARD_API_TOKEN=<random-string>` and add an
  `Authorization: Bearer <random-string>` header in the HAE automation or
  the Shortcut's "Get Contents of URL" headers. Requests without the header
  get `401`; when the variable is unset, `/ingest` stays open (localhost
  posture unchanged).

## A 200 response

```json
{ "stored": 3, "skipped": [], "workouts": 0, "latest_date": "2026-07-09" }
```

means the values are in SQLite and will appear on the next Prometheus
scrape. `skipped` lists metric names PulseBoard doesn't know — harmless,
but check for typos if something you expected is missing.

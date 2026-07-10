# Posting from an Apple Shortcut

The canonical `/ingest` shape is designed so a plain Apple Shortcut can push
today's numbers without any third-party app.

## Build the Shortcut

1. **Find Health Samples** — add one "Find Health Samples" action per metric
   you want (e.g. Steps, "is today", group by day, sum). Store each result
   in a variable.
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
4. Run it manually, or add it to a personal automation (e.g. every evening).

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

A `200` response with `{"stored": 3, "skipped": []}` means the values are in
SQLite and will appear on the next Prometheus scrape.

## Notes

- PulseBoard binds to `127.0.0.1` on its host by design. To post from your
  phone, you need to be on the same private network and deliberately choose
  how to expose port 8000 (e.g. a WireGuard/Tailscale address). Don't
  port-forward it to the internet — there is no authentication in the MVP.
- Re-running the Shortcut on the same day is safe: rows are upserted per
  `(date, metric, aggregation)`, so values are updated, not duplicated.
- Prefer an app? [Health Auto Export](https://www.healthyapps.dev/) can POST
  its JSON export to the same endpoint unchanged — see
  [INGEST.md](INGEST.md).

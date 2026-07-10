# Roadmap ideas

Directions PulseBoard could grow in, roughly ordered by how naturally they
build on what exists today. None of these are commitments — the project's
guardrails (localhost-first privacy, registry-driven metrics, dashboards as
code, stdlib-lean, "not medical advice") apply to all of them.

## Near-term, builds directly on existing modules

- **Readiness score** — a morning "how recovered am I?" 0–100 composite from
  HRV vs baseline, resting HR vs baseline, and last night's sleep — the same
  renormalized-weights pattern as `score.py`, exposed as a gauge and a
  dashboard stat with a matching alert.
- **Goals & streaks** — per-metric daily goals (steps ≥ 8000, sleep ≥ 7 h)
  declared in the metric registry; `pulseboard_goal_streak_days{metric=}`
  gauges, weekly-report "goals hit 5/7" lines, and a dashboard row.
- **Sleep debt** — cumulative shortfall vs target over the last 14 days,
  as a gauge + trend panel; pairs naturally with the existing sleep alerts.
- **Training load (ACWR)** — acute (7 d) vs chronic (28 d) workout-load
  ratio from the existing workout rollups; warn above ~1.5 ("ramping too
  fast"), a classic overtraining signal.
- **AI weekly narrative** — pipe the weekly report's numbers through the
  Claude API to generate a short natural-language coach summary appended to
  the markdown/HTML report (opt-in, API key via env, raw data never leaves
  the machine otherwise).

## Medium — new surfaces

- **Annotations** — a tiny `POST /annotate` (sick day, travel, alcohol,
  caffeine late, new mattress…) stored in SQLite and overlaid on Grafana
  panels as annotation queries; also lets insights exclude flagged days
  from baselines.
- **Email delivery** — SMTP as a third notify channel; the HTML report
  renderer already exists.
- **Data export & backup** — `GET /export?format=csv` and a
  `python -m pulseboard.export` CLI (CSV/Parquet), plus a documented
  litestream/backup recipe for the SQLite file.
- **Wall-display kiosk mode** — a documented Grafana kiosk playlist +
  compose profile for a spare tablet/monitor.

## Bigger bets

- **More sources** — adapters for Strava (webhook), Withings scales, and
  Garmin/Google Fit exports; the canonical registry means each adapter is a
  self-contained mapping module like `health_auto_export.py`.
- **Multi-user** — per-user API tokens and a `user` column threaded through
  the schema/exporter labels; useful for households, doubles the complexity.
- **Lightweight PWA companion** — a single-page mobile view (today's stats +
  freshness + report) served by FastAPI itself, for people who don't want
  Grafana open on a phone.

Informational only, not medical advice — that framing stays, whatever ships.

# Roadmap ideas

Directions PulseBoard could grow in, roughly ordered by how naturally they
build on what exists today. None of these are commitments — the project's
guardrails (localhost-first privacy, registry-driven metrics, dashboards as
code, stdlib-lean, "not medical advice") apply to all of them.

## Near-term, builds directly on existing modules

- ~~**Readiness score**~~ — **shipped**: `pulseboard_readiness_score` gauge,
  dashboard gauge and `pb-readiness-low` alert ([SCORE.md](SCORE.md)).
- ~~**Goals & streaks**~~ — **shipped**: registry-declared goals,
  `pulseboard_goal_streak_days{metric=}` / `pulseboard_goal_target{metric=}`
  gauges, weekly-report "met N/7 days" lines and a dashboard panel
  ([GOALS.md](GOALS.md)).
- ~~**Sleep debt**~~ — **shipped**: `pulseboard_sleep_debt_hours` gauge,
  report line, dashboard stat and `pb-sleep-debt-high` alert
  ([GOALS.md](GOALS.md)).
- ~~**Training load (ACWR)**~~ — **shipped**: acute/chronic/ACWR gauges from
  the per-workout table, dashboard stat and `pb-acwr-high` alert
  ([TRAINING_LOAD.md](TRAINING_LOAD.md)).
- **AI weekly narrative** — pipe the weekly report's numbers through the
  Claude API to generate a short natural-language coach summary appended to
  the markdown/HTML report (opt-in, API key via env, raw data never leaves
  the machine otherwise).
- **Persist derived daily scores** — store health/readiness per day so the
  weekly report can compare them week-over-week (today they are computed
  live from latest values only).

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

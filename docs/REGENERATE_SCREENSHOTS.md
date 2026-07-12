# Regenerating the dashboard screenshots

The images in `docs/img/` are captured from a live, seeded Grafana stack.
Regenerate them whenever the dashboard layout changes (e.g. a new row).
Everything below runs locally against synthetic demo data — no real health
data is involved.

## 1. Bring up the stack

```bash
docker compose up -d --build
```

Wait for Grafana to finish installing the `frser-sqlite-datasource` plugin
(a few seconds on first run): `docker compose logs -f grafana` until it is
ready, then Ctrl-C.

## 2. Seed ~120 days of demo data

```bash
python scripts/seed_demo.py
```

This POSTs a deterministic history (slow fitness trends, a rough sleep
patch, a weekly workout rhythm, a sleep→next-day-HRV correlation) tuned so
every panel renders with meaningful values — goal streaks, sleep debt, an
acute:chronic ratio around 1.1–1.3, correlations, and anomalies. It prints a
`/status` summary when done.

## 3. Let Prometheus scrape

The **History** and **Trends** rows read SQLite directly and fill instantly.
The **Today**, **Insights** and **Recovery & goals** rows read Prometheus
gauges, so wait for one or two scrapes (~60–120 s) before capturing them.

## 4. Open the dashboard and capture each row

Open <http://127.0.0.1:3000/d/pulseboard/pulseboard> (login `admin` / `admin`;
skip the change-password prompt). Use a wide browser window (≈1600 px) at 2×
device pixel ratio so the captures match the existing density.

Capture each row region and save with the **existing filenames** so the
README references keep working:

| Dashboard row | File |
| --- | --- |
| **Today** (hero) | `docs/img/dashboard.png` |
| **Trends** | `docs/img/trends.png` |
| **Insights** | `docs/img/insights.png` |
| **Recovery & goals** (new) | `docs/img/recovery.png` |

Tip: collapse the other rows (click the row title) so only the target row is
expanded, then screenshot that region — it keeps each capture tight and
consistent.

## 5. Wire the new image into the README

`recovery.png` is new, so add it under the Recovery & goals paragraph in the
README's **Dashboard** section (this line is intentionally not committed
until the image exists, to avoid a broken link on the public repo):

```markdown
![Recovery & goals — readiness, sleep debt, ACWR, goal streaks](docs/img/recovery.png)
```

## 6. Commit and tear down

```bash
docker compose down -v            # -v drops the demo data volume
git add docs/img/*.png README.md
git commit -m "Refresh dashboard screenshots (adds Recovery & goals row)"
```

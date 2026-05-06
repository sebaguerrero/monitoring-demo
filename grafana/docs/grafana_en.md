# Grafana — file reference

Grafana is the **dashboard layer**. It reads metrics from Prometheus and renders them as panels in a single dashboard called *ML System Dashboard*. Grafana 10.0.3 (the version pinned in `docker-compose.yml`) supports **provisioning** — declarative configuration loaded from disk at startup. The two files under `provisioning/` tell Grafana to auto-load a Prometheus datasource and a dashboard from local JSON, so the demo never requires clicking through Grafana's first-run wizard.

This folder contains three files:

- `provisioning/datasources/datasource.yml` — registers Prometheus as the default datasource.
- `provisioning/dashboards/dashboard.yml` — tells Grafana where to find dashboard JSON files.
- `dashboards/ml_dashboard.json` — the dashboard itself: 24 panels in three sections (6 stat tiles + 6 DevOps timeseries + 12 ML-metric panels).

## `provisioning/datasources/datasource.yml`

### What it is
A Grafana provisioning manifest for datasources. Grafana scans `/etc/grafana/provisioning/datasources/` at startup and creates whatever is declared there before the UI accepts a request.

### Role in the stack
- Mounted into the Grafana container at `/etc/grafana/provisioning/datasources/datasource.yml`.
- Without it, Grafana boots with no datasource and every panel shows "No data source selected." A presenter would have to add Prometheus by hand on every fresh demo.

### Walkthrough

- `apiVersion: 1` — required marker on every Grafana provisioning file.
- `datasources[0].name: Prometheus` — the human-readable name shown in panel queries.
- `type: prometheus` — Grafana's built-in Prometheus plugin.
- `url: http://prometheus:9090` — Docker DNS resolves `prometheus` to the Prometheus container on `monitor_net`. Port 9090 is Prometheus's default.
- `access: proxy` — Grafana proxies queries through its server rather than asking the user's browser to hit Prometheus directly. This is what allows the "Prometheus" hostname to resolve (the browser would have no way to reach it).
- `isDefault: true` — every panel that doesn't specify a datasource picks this one. The dashboard JSON relies on this.

## `provisioning/dashboards/dashboard.yml`

### What it is
A Grafana provisioning manifest for dashboards. Tells Grafana to scan a folder for dashboard JSON files and import each one.

### Role in the stack
- Mounted into the Grafana container at `/etc/grafana/provisioning/dashboards/dashboard.yml`.
- Drives the auto-load of `ml_dashboard.json`. Without it the dashboard exists as a file but Grafana never sees it.

### Walkthrough

- `apiVersion: 1` — provisioning manifest marker.
- `providers[0].name: 'Dashboards'` — internal provider name. Free-form.
- `orgId: 1` — Grafana's default organization.
- `folder: ''` — empty string puts the dashboard in the *General* (root) folder of the Grafana UI.
- `type: file` — load from local files, not from a remote source.
- `disableDeletion: false` — a user can delete the dashboard from the UI (it would be re-provisioned on next restart anyway).
- `updateIntervalSeconds: 10` — Grafana re-scans the path every 10 seconds. Editing `ml_dashboard.json` while the stack is running picks up the change without a restart, which is convenient when iterating on panels.
- `options.path: /var/lib/grafana/dashboards` — where Grafana looks. The Compose file mounts `./grafana/dashboards/` into that path.

## `dashboards/ml_dashboard.json`

### What it is
The full dashboard definition. Grafana 10 dashboard JSON is verbose; what matters here is the structure (3 sections, 24 panels) and the PromQL behind each panel — both of which are documented below. Editing the file directly is supported but the easier workflow is: edit in Grafana UI → "Save JSON to file" → paste back over this file → commit.

### Role in the stack
The single source of truth for what users see. Every metric `model_api` exports either appears here directly or is referenced through one of the alert rules (which power the top section).

### Dashboard structure

The dashboard is split into **three rows** (collapsible section headers in Grafana). Each row groups thematically related panels.

#### Row 0 — Alert Status Overview (6 stat tiles)

Five of the tiles mirror the rules in [`prometheus/rules.yml`](../../prometheus/docs/prometheus_en.md); the sixth (`Process CPU`) is an informational health stat without a corresponding alert (it's useful for noticing the API host is overloaded, even when no rule is firing). The tiles use Grafana's *threshold colouring* feature: green when the value is below the threshold, red when above. This gives the dashboard a very fast visual read — all green means "everything is fine," any red tile names exactly which condition tripped.

| Tile | PromQL | Threshold |
|---|---|---|
| **House Price Predictor** | `up{job="house_price_predictor"}` | red below 1 (target down), green at 1 |
| **Predict Latency** | `sum(rate(api_request_duration_seconds_sum{endpoint="/predict"}[15s])) / sum(rate(api_request_duration_seconds_count{endpoint="/predict"}[15s]))` | red above 0.35 s |
| **Predict Error Rate** | `sum(rate(api_requests_total{endpoint="/predict",http_status="500"}[15s])) / sum(rate(api_requests_total{endpoint="/predict"}[15s]))` | red above 8 % |
| **Process CPU** | `avg_over_time(model_api_process_cpu_percent[15s])` | informational (no threshold, fixed green color) |
| **Missing Features** | `sum(rate(ml_missing_feature_total[15s]))` | red above 0.5/s |
| **Avg Prediction** | `sum(rate(ml_prediction_value_sum[15s])) / sum(rate(ml_prediction_value_count[15s]))` | red above `$600K` (USD-formatted) |

All rate-based panels in the dashboard use `[15s]` — alert stat tiles, their paired timeseries, the prediction histogram, and the ML input panels (Square Meters, Bedrooms, Neighborhood Mix). The `[15s]` window matches the 30 s anomaly cadence: a 15 s window fits cleanly inside one phase, so during anomaly the panels visibly shift to the right (high-priced predictions, larger square_meters, `industrial` neighborhood) and snap back during the normal phase. A longer window like `[1m]` would always mix both phases and hide the drift effect. Raw gauges with no window (Process Memory, Disk Utilization, Prediction Median/Min/Max/Standard Deviation) are scalar values sampled every 5 s. Process CPU is the in-between case: the `model_api_process_cpu_percent` gauge is sampled every 5 s, but the panels display it through `avg_over_time(...[15s])` to smooth out sampling noise — it has a 15 s window even though it's not `rate()`.

Important: alert tiles flip red as soon as the PromQL crosses the threshold, *without* the `for: 5s` delay that the actual alert rules wait for. So the dashboard turns red a few seconds before the alert reaches `Firing` state.

#### Row 1 — DevOps Metrics (6 panels)

The operations side of the article (Section 7).

| Panel | Type | What it shows |
|---|---|---|
| **Predict Request Rate** | timeseries | `sum(rate(api_requests_total{endpoint="/predict"}[15s]))` — predictions per second over time |
| **Predict Latency** | timeseries | `[15s]` mean latency filtered by `endpoint="/predict"`, with a 0.35 s threshold line. Same expression as the Row 0 stat tile; shown here as a historical series so the normal/anomaly square wave is visible |
| **Predict Error Rate** | timeseries | `[15s]` percent of 500 responses filtered by `endpoint="/predict"`, with 8 % threshold. Same expression as the Row 0 stat tile; shown here as a historical series |
| **Process CPU** | timeseries | `avg_over_time(model_api_process_cpu_percent[15s])` — operational visibility, no alert attached |
| **Process Memory** | timeseries | `model_api_process_resident_memory_bytes` |
| **Disk Utilization** | timeseries | `model_api_process_disk_utilization_percent` |

#### Row 2 — ML Metrics (12 panels)

The data-science side of the article (Section 6). These panels read both the *rolling-statistic gauges* that `model_api` computes (mean, median, min, max, stddev) and the underlying *histograms* (for bucket distributions). The **Model Identity** tile appears at the top of this section.

| Panel | Type | What it shows |
|---|---|---|
| **Model Identity** | stat | `ml_model_info == 1` — shows the current `version` on the tile (the metric also carries the `trained_at` label, but the panel only exposes it via `legendFormat: "Version {{version}}"`); paired with a native Grafana annotation that draws a turquoise vertical line on all timeseries panels whenever `bump_version` is called |
| **Prediction Mean** | timeseries | `sum(rate(ml_prediction_value_sum[15s])) / sum(rate(ml_prediction_value_count[15s]))` — same expression as the Avg Prediction stat tile, with 600 k threshold |
| **Prediction Median** | timeseries | `ml_prediction_median_recent` |
| **Prediction Min/Max** | timeseries | both `ml_prediction_min_recent` and `ml_prediction_max_recent` overlaid |
| **Prediction Standard Deviation** | timeseries | `ml_prediction_stddev_recent` — variance widens during anomaly windows |
| **Prediction Histogram Buckets** | bargauge | 11 `[15s]`-rate expressions, one per bucket from 100 k to 1.5 M+. Note: `prometheus_client` emits `le` ≥ 1 000 000 in scientific notation (`1e+06`, `1.5e+06`) — the queries match that format, not decimal |
| **Square Meters Mean** | timeseries | `sum(rate(ml_input_square_meters_sum[15s])) / sum(rate(ml_input_square_meters_count[15s]))` — same `[15s]` cadence as the rest of the dashboard |
| **Square Meters Histogram Buckets** | bargauge | 9 `[15s]`-rate expressions, one per bucket from 50 to 650+ m² |
| **Bedrooms Mean** | timeseries | `sum(rate(ml_input_bedrooms_sum[15s])) / sum(rate(ml_input_bedrooms_count[15s]))` |
| **Bedrooms Histogram Buckets** | bargauge | 9 `[15s]`-rate expressions, one per bucket from 0 to 8+ bedrooms |
| **Neighborhood Mix** | timeseries | `sum(rate(ml_input_neighborhood_total[15s])) by (neighborhood)` — one line per category, makes the anomaly-window shift toward `industrial` visually obvious |
| **Missing Features** | timeseries | `sum(rate(ml_missing_feature_total[15s])) by (feature)` with 0.5/s threshold — `[15s]` to match the alert rule; currently always reports `bedrooms` |

### Top-level dashboard fields

Apart from `panels`, the JSON contains:

- `uid` — `ml-system`. The dashboard's stable identifier, used in URLs (`/d/ml-system`) referenced throughout the rest of the docs.
- `title` — *ML System Dashboard*. What appears in the Grafana sidebar.
- `refresh` — the dashboard's auto-refresh interval. The demo uses 5 s.
- `time` — default time window when the dashboard opens (last 15 minutes).
- `schemaVersion` — Grafana's internal version tag for the JSON shape. Grafana will migrate older versions on load; bumping it keeps the file in sync with newer Grafana releases.
- `version` — the dashboard's edit version, used by Grafana's UI history.
- `annotations` — defines the `Model Deployments` annotation with a turquoise `iconColor` that filters by tag `deploy`. It is the source of the vertical lines drawn on every timeseries when `bump_version` posts to Grafana's annotations API.

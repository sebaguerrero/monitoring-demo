# Kibana — file reference

Kibana is the **log explorer UI** in the demo: a web frontend on port 5601 that reads from Elasticsearch and lets you filter, count, and inspect individual prediction events. Kibana itself runs from the official Elastic image — there is no Kibana configuration to maintain. The only file in this folder is a one-shot bootstrap script that runs the first time the stack comes up and configures Kibana so that opening it goes straight to a usable Discover view.

## `init/import.sh`

### What it is
A POSIX shell script that the `kibana-init` container runs to provision a **data view** in Kibana via Kibana's saved-objects HTTP API. A data view is Kibana's name for what older Elastic versions called an index pattern: it tells Kibana which Elasticsearch indices a given UI experience covers, and which field is the time axis.

### Role in the stack
Kibana 8 boots into a first-run wizard that asks the user to "Create your first data view." Without `import.sh`, every fresh demo would start with a presenter clicking through that wizard before anything was visible. The script automates that step against the running Kibana container so that as soon as `kibana-init` exits cleanly:

- A data view named `model-api-logs` exists.
- It is bound to the index pattern `model-api-logs-*` (matches all daily indices Logstash creates when writing to Elasticsearch).
- `@timestamp` is the time field, which is what makes Discover show events on a time axis.
- That data view is the **default**, so opening Discover loads it without selection.

The script is idempotent: re-runs detect an existing data view and exit successfully.

### Walkthrough

**Header.**

- `#!/bin/sh` — the script must work under any POSIX shell (the `curlimages/curl:8.5.0` image used by `kibana-init` only ships `sh`, not `bash`).
- `set -eu` — exit immediately on any failed command (`-e`) or unbound variable (`-u`). The script is expected to either succeed end-to-end or fail loudly.

**Constants.**

- `KIBANA_URL="http://kibana:5601"` — the in-network address. `kibana` resolves via Docker DNS within the `monitor_net` network.
- `DATA_VIEW_ID="model-api-logs"` — the saved-object ID. Stable so we can reference the exact ID when setting the default below.

**Step 1 — wait for Kibana.** Kibana is heavier than Elasticsearch and takes 30–90 s to become responsive even after Elasticsearch reports healthy. The wait loop polls `GET /api/status` (Kibana's readiness endpoint) every 5 s for up to 60 attempts (5 min ceiling). `curl -fs` makes the call silent and exits non-zero on HTTP errors so the `until` predicate flips only when Kibana actually serves a 2xx. The ceiling exists so a misconfigured stack fails the script instead of hanging the lab session forever.

**Step 2 — create the data view.** A `POST` to `/api/data_views/data_view` with the standard Kibana saved-objects payload:

- `id` — the stable identifier (`model-api-logs`).
- `title` — the index pattern (`model-api-logs-*`), which is what Kibana expands at query time.
- `name` — the human-readable label shown in the data-view dropdown.
- `timeFieldName: "@timestamp"` — required for time-axis behaviour in Discover; `@timestamp` is the field Logstash sets per event (via its `date` filter, reading the timestamp from the API's log).
- `override: true` — replaces an existing data view with the same ID, so re-runs of the script land on a clean state.
- `kbn-xsrf: true` header — Kibana requires this on any state-changing API call as a CSRF guard. The value is opaque; `true` works.

The response code is captured in `HTTP_CODE`. The script handles three branches:
- `200` / `201`: created. Log success.
- `409`: data view already exists *and* `override` couldn't replace it (rare; usually `override: true` returns 200). Log and continue — this is not a failure for the demo.
- anything else: dump the body to stderr and exit non-zero. The Compose stack will mark `kibana-init` failed in `docker compose ps`.

**Step 3 — make it the default.** A second `POST` to `/api/data_views/default` with `{"data_view_id": "...", "force": true}`. `force` lets it overwrite any pre-existing default. The whole call is suffixed with `|| true`, meaning a failure here does *not* abort the script — having a data view but no default is still usable; the user just has to pick it from the dropdown the first time.

## Auto-provisioned dashboard: `ML Drift Investigation`

Beyond the data view, `kibana-init` also auto-provisions a **dashboard** with 2 panels covering drift types that need per-event joins over the logs. Local URL: [http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard](http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard) — in the poster public deployment, behind Caddy: [https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard](https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard).

> If the URL above 404s, the dashboard ID may have changed. To verify the actual ID, run:
> ```bash
> curl -s -u elastic:changeme \
>   "http://localhost:5601/api/saved_objects/_find?type=dashboard" \
>   -H "kbn-xsrf: true" | jq '.saved_objects[] | {id, attributes: {title: .attributes.title}}'
> ```
> The expected `id` is `ml-derived-fields-dashboard` (matches the file `05-dashboard-ml-derived-fields-dashboard.json` in `kibana/init/saved_objects/`). The ID stays stable across regenerations of the per-object JSON files from the NDJSON, so external links don't break.

**The 2 panels:**

| # | Panel | Type | What it covers | What it shows |
|---|---|---|---|---|
| 1 | Predictions with missing features | XY histogram | **Data skew "feature unavailable"** | Distribution of `prediction` filtered by `missing_features:*`. Only populates during anomaly windows when `bedrooms=None` ~35% of traffic and the model imputes with the training-set median (`bedrooms=3`). Predictions are technically valid (HTTP 200) but statistically weak — they accumulate uncertainty from the imputed value plus the model's own error. |
| 2 | Top-20 extreme predictions with feature context | Datatable | **Prediction tail / distribution monitoring** (article sección 6 "basic stats: max + full distribution") | Table of the 20 highest predictions in the time range with full per-event feature context: `request_id`, `features.square_meters`, `features.bedrooms`, `features.neighborhood`, `latency_ms`, `model_version`, `@timestamp`. Drill-down for post-alert investigation: when `PredictionDriftDetected` fires, this shows exactly which requests caused extreme predictions and with which inputs. |

### How they are provisioned

Unlike data views (created via the `/api/data_views/` API), Kibana dashboards are more complex objects. The implementation chosen:

1. **Source definition**: `kibana/init/dashboards/ml-derived-fields.ndjson` — an NDJSON file with the 3 saved objects (2 Lens panels + 1 dashboard) in Kibana's bulk-export format. Serves as a readable, portable reference.

2. **Per-object files**: `kibana/init/saved_objects/NN-<type>-<id>.json` — the NDJSON is split into 3 individual files (`00-lens-prediction-with-missing`, `01-lens-top-prediction-outliers`, `05-dashboard-ml-derived-fields-dashboard`) with the `{"attributes": {...}, "references": [...]}` body the POST endpoint expects. The numeric prefix (`00-`, `01-`, `05-`) forces creation order: panels first, dashboard last (so references resolve). Gaps in the numbering (`02`–`04`) are leftovers from earlier panels that were retired; the import script just iterates the existing files in lexical order.

3. **Import in `import.sh`**: the script iterates over the JSON files and runs `POST /api/saved_objects/<type>/<id>?overwrite=true` for each. The direct POST (instead of `/api/saved_objects/_import`) **avoids Kibana's automatic migrations** which expect legacy schema fields (`currentIndexPatternId` instead of `indexPatternId`, etc.) — the current 8.x format passes through POST as-is but would fail `_import`.

### How to extend the dashboard

To add a new panel:

1. Build the panel in the Kibana UI manually (Visualize Library → Lens).
2. Export it: **Stack Management → Saved Objects → select → Export → Include related objects**.
3. Move the JSON to `kibana/init/saved_objects/0X-lens-<name>.json` with the format `{"attributes": {...}, "references": [...]}` (use a numeric prefix that sorts before `05-dashboard-...json`).
4. Edit `05-dashboard-ml-derived-fields-dashboard.json` to add the new panel: a `panelsJSON` entry with `panelRefName: "panel_pN"` and a `gridData` (x/y/w/h) that doesn't overlap, plus a matching reference.
5. Re-run `docker compose up kibana-init`.

The source NDJSON (`kibana/init/dashboards/ml-derived-fields.ndjson`) serves as a readable reference of Kibana's bulk export; the per-object files are maintained by hand, no auto-generation.

## Useful Discover queries

Once Kibana is up and documents are flowing, the following filters are the most useful for showing the pipeline in action:

| Query | What it shows |
|---|---|
| `anomaly_window: true` | Only events during synthetic anomaly windows — predictions with suspicious inputs. |
| `http_status: 500` | Only failed predictions. |
| `event_type: prediction_failed` | Equivalent to `http_status: 500`. |
| `internal: false` | Only real `POST /predict` calls (excludes synthetic background traffic). |
| **`missing_features: *`** | **Predictions where some feature came missing in the request** — the model imputed with the training-set median (e.g. `bedrooms=3`). Drives Panel 1 of the dashboard. |
| **`prediction > 1500000`** | **Extreme predictions** — the right tail of the distribution. Combine with the table panel for full feature context. |

# Model API — file reference

The Model API is the **service being monitored** in the demo. It is a synthetic FastAPI application that:

- exposes `POST /predict` to return a fake house-price prediction,
- exposes `POST /admin/bump_version` to simulate deploying a new model version (drives the Grafana deploy-marker annotation),
- exports Prometheus metrics on `GET /metrics`,
- writes one plain-text free-form log line per prediction to stdout (consumed by Filebeat → Logstash → Elasticsearch → Kibana),
- generates its own background traffic so the dashboards have data without external clients,
- periodically enters an *anomaly window* during which inputs, latency, error rate, prediction range, missing-feature rate, and CPU usage all shift visibly so every alert in the demo gets triggered on a regular cadence.

The folder contains three files:

- `app.py` — the entire application.
- `Dockerfile` — image build recipe.
- `requirements.txt` — Python dependencies.

## `app.py`

### What it is
A single-file FastAPI application that combines four concerns: an HTTP API, a Prometheus metrics exporter, a plain-text logger, and three background threads that generate traffic, sample process resource usage, and bump the model version periodically.

### Role in the stack
- Built into a container by `docker-compose.yml` (`build: ./model_api`).
- Listens on port 8000. Prometheus scrapes `model_api:8000/metrics` every 5 s. Caddy reverse-proxies external HTTPS traffic (poster deployment only) to Grafana / Prometheus / PanicDuty / Kibana — *not* to the API itself, which stays internal.
- Stdout **plain text** is captured by the Docker daemon, read by Filebeat, sent to Logstash (which parses it via `grok`, normalizes types, and restructures fields into a nested shape), and finally indexed in Elasticsearch.

### Walkthrough

The file is organised top-to-bottom in this order: imports → plain-text logging setup → Prometheus metric definitions → environment-driven config → global app state → helper functions (anomaly detection, feature synthesis, normalization, prediction) → background-thread workers → FastAPI app + lifespan + routes.

#### Imports

Standard library: `logging`, `os`, `random`, `shutil`, `statistics`, `sys`, `threading`, `time`, `uuid`, `collections.deque`, `contextlib.asynccontextmanager`, `datetime`. Third-party: `psutil` (for process metrics), `fastapi`, `prometheus_client`.

#### Plain-text logging

`PlainTextFormatter(logging.Formatter)` overrides `format()` to emit one **free-form string** per log record with an ISO-like timestamp, level, logger name, and all `extra_fields` formatted as `key=value` pairs. It has two branches based on `event_type`:

- **`event_type == "prediction"`** (success) → ends with `... missing=<csv> prediction=<num> summary="..."`.
- **`event_type == "prediction_failed"`** (failure) → ends with `... msg="..."`.

The format was designed to be parseable by two alternative `grok` patterns in Logstash. Strings with spaces (such as `summary` and `msg`) are quoted with `"..."` and internal double quotes are escaped to `'`.

`_configure_logging()` replaces the root logger's handlers with a single `StreamHandler(sys.stdout)` using `PlainTextFormatter`. INFO level. The module-level `logger = _configure_logging()` runs at import time so every subsequent call goes through the formatter, including FastAPI's own logs.

This is the *only* logging the application does. There is no separate file output, no rotation, no syslog. Docker captures stdout; Filebeat reads from there. Standard 12-factor pattern.

#### Prometheus metric definitions

Each metric mirrors a panel or alert in the dashboard / rules.

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `api_requests_total` | Counter | `endpoint`, `http_status` | request count, broken down by status; powers the error-rate alert |
| `api_request_duration_seconds` | Histogram (buckets 0.01–4.0 s) | `endpoint` | latency distribution; `_sum/_count` gives the mean used by the latency alert and panel |
| `ml_prediction_value` | Histogram (buckets 100k–1.5M) | none | predicted prices; histogram lets the dashboard compute P95 via `histogram_quantile()` and the alert use the rolling mean via `_sum/_count` |
| `ml_input_square_meters` | Histogram (buckets 50–650) | none | input distribution for the `square_meters` feature |
| `ml_input_bedrooms` | Histogram (buckets 0–8) | none | input distribution for `bedrooms` |
| `ml_input_neighborhood_total` | Counter | `neighborhood` | one counter per category; powers the *Neighborhood Mix* panel |
| `ml_missing_feature_total` | Counter | `feature` | increments whenever a feature is missing on an incoming request |
| `model_api_process_cpu_percent` | Gauge | none | live CPU%, sampled every second |
| `model_api_process_resident_memory_bytes` | Gauge | none | RSS in bytes |
| `model_api_process_disk_utilization_percent` | Gauge | none | filesystem fill % at `/` |
| `ml_prediction_mean_recent` | Gauge | none | mean over the rolling window |
| `ml_prediction_median_recent` | Gauge | none | median over the rolling window |
| `ml_prediction_min_recent` | Gauge | none | min over the rolling window |
| `ml_prediction_max_recent` | Gauge | none | max over the rolling window |
| `ml_prediction_stddev_recent` | Gauge | none | population stddev over the rolling window |
| `ml_model_info` | Gauge | `version`, `trained_at` | exposes the active model's metadata as labels on a constant-1 gauge (the previous version's gauge is set to 0). Re-published on every `POST /admin/bump_version` |
| `model_deployments_total` | Counter | none | number of `bump_version` calls since startup. Drives the dashboard's deploy-marker annotation via a direct POST to Grafana's `/api/annotations` endpoint (see `_post_grafana_annotation()` in `app.py`) |

Histograms expose `_sum`, `_count`, and per-bucket `_bucket{le="..."}` automatically — the dashboard uses all three.

#### Environment-driven config

| Env var | Default | Meaning |
|---|---|---|
| `MODEL_VERSION` | `v1.1.0-demo` | reported in `/health`, `/predict` responses, and the `ml_model_info` metric |
| `DEMO_BASE_RPS` | 8 | synthetic traffic rate, floored at 1 RPS |
| `DEMO_ANOMALY_INTERVAL_SECONDS` | 30 | normal-mode duration before each anomaly window, floored at 10 s |
| `DEMO_ANOMALY_DURATION_SECONDS` | 30 | length of each anomaly window, floored at 5 s |
| `DEMO_ANOMALY_FAILURE_RATE` | 0.2 (in code) / 0.7 (in compose) | probability that a request fails *during* an anomaly window; clamped to [0, 0.95] |
| `DEMO_PREDICTION_STATS_WINDOW_SECONDS` | 300 | rolling window length for the `_recent` gauges, floored at 30 s |

The clamps are belt-and-suspenders — they prevent obvious foot-guns like setting an anomaly duration shorter than a single scrape.

#### Global state

- `app_state` holds `start_time` (used by the anomaly-window math) and `stop_event` (a `threading.Event` the background workers watch for clean shutdown).
- `prediction_history: deque` of `(timestamp, value)` tuples; the rolling window for the `_recent` gauges.
- `prediction_history_lock: threading.Lock` — required because the deque is mutated from the background traffic thread *and* the request-handling thread; FastAPI's default is sync routes which run on a thread pool.
- `NEIGHBORHOODS` and `NEIGHBORHOOD_MULTIPLIERS` define the four categories and their per-neighbourhood price multipliers (suburb 1.0×, downtown 1.25×, rural 0.82×, industrial 1.55×). The multipliers are why the *Neighborhood Mix* shift toward `industrial` during anomaly windows pushes predictions up.

#### `is_anomaly_window()`

The trick that makes the demo lively without any external coordination. Computes how long the process has been running, modulo `(interval + duration)`. Returns true when the position within the cycle is past `interval`. With defaults: 60-second cycle, 30 s normal + 30 s anomaly — picked so the PanicDuty banner visibly flips between green and red on a regular cadence. Pure function of monotonic time — the cycle never drifts and survives daylight-savings shifts.

#### `build_synthetic_features(anomaly_active)`

Produces the synthetic input dict for the background traffic generator. Different distributions per mode:

- **Normal**: `square_meters ∈ [80, 260]`, `bedrooms` always present (`randint(1, 5)`), neighborhood from `("suburb", "suburb", "downtown", "rural")` — 50 % suburb, 25 % downtown, 25 % rural.
- **Anomaly**: `square_meters ∈ [320, 580]`, `bedrooms` ~65 % present (1–7) and 35 % `None`, neighborhood from `("industrial", "industrial", "industrial", "downtown")` — 75 % industrial, 25 % downtown.

The neighborhood weighting is done by populating a tuple with repeats and `random.choice()`-ing it. Cheap and readable.

#### `normalize_features(payload, anomaly_active, internal)`

The same pipeline whether the request is internal-traffic or a real `POST /predict`. Steps:

1. If internal or no payload, generate synthetic features. Otherwise read whichever of `square_meters`/`bedrooms`/`neighborhood` are present.
2. For each of those three features, if missing/empty, increment `ml_missing_feature_total{feature=...}` and remember the name in a `missing` list.
3. Observe `ml_input_square_meters` / `ml_input_bedrooms` histograms when those values are present.
4. Validate `neighborhood` against `NEIGHBORHOODS`. If unknown or missing, impute with the **training-set mode** (`NEIGHBORHOOD_TRAIN_MODE = "suburb"`). Increment `ml_input_neighborhood_total{neighborhood=...}` always.
5. Impute missing values with the **training-set median / mode** (constants `SQM_TRAIN_MEDIAN = 170.0`, `BEDROOMS_TRAIN_MEDIAN = 3`, `NEIGHBORHOOD_TRAIN_MODE = "suburb"`). The median/mode imputation keeps the substituted value **inside the range the model saw during training** — so the model isn't asked to extrapolate over a fabricated point. The values are computed offline from the normal-traffic generator in `build_synthetic_features`. In practice only the `bedrooms` fallback fires (it is the only feature the synthetic generator ever omits, ~35% during anomaly); the other two are defensive code for external `POST /predict` requests. These constants feed `perform_prediction`; the histograms have already been observed (or skipped) above with the *real* values, so the missing-feature counter and the input histograms accurately reflect what came in.
6. Build a separate `presented_features` dict that mirrors what came in: real values where present, `None` where missing. This is the dict the log line uses, so `sqm`/`br`/`nbhd` in the log always agree with the `missing` list — the imputed values live only inside the `normalized` dict that feeds the prediction.
7. Returns `(normalized_features, presented_features, missing_list)`.

#### `perform_prediction(features, anomaly_active)`

Where the prediction is "computed" and where the demo's pathological behaviour during anomaly windows lives:

1. `time.sleep(15–50 ms)` — baseline latency.
2. **If anomaly**: extra 450–850 ms sleep + `random.random() < ANOMALY_FAILURE_RATE` chance to raise `RuntimeError`. **If normal**: nothing extra.
3. Compute the price: `square_meters * 1800 + bedrooms * 12000`, multiplied by the neighborhood multiplier, plus uniform noise in [-15k, 15k].
4. **If anomaly**: add another 180k–420k uniform offset.
5. Floor at 50k (so the histogram lower bound never gets crossed).

The anomaly offset is what triggers `PredictionDriftDetected`. The error injection is what triggers `ElevatedApiErrorRate`. The anomaly latency sleep triggers `HighApiLatency`. One function, three alerts.

#### `record_prediction_statistics(prediction)`

Append `(now, prediction)` to `prediction_history`, evict old entries past the rolling window, then recompute the five `_recent` gauges (`fmean`, `median`, `min`, `max`, `pstdev`). Lock-protected. `pstdev` returns 0 for `len(values) <= 1` (the stdlib raises otherwise).

#### `describe_prediction(features, prediction, latency_ms, missing)`

Builds the human-readable `summary` string emitted on successful predictions. Returns `"prediction within expected ranges"` when nothing is suspicious, otherwise `"anomalous prediction: <issues>"` where issues are heuristic flags on the *observable* values (not the synthetic `anomaly_window` flag): `latency_ms > 250` (vs. 15–50 ms in normal mode), any `missing_features`, `square_meters > 300` (vs. normal range 80–260), `prediction > 1_300_000` (vs. normal ~110k–830k). The thresholds straddle the gap between normal and anomaly distributions so a reader of the log can recognise unusual values without consulting the synthetic flag.

#### `describe_failure(features, latency_ms, missing, exc, anomaly_active)`

Builds the human-readable `error_message` string emitted on failed predictions. Plain-English output of the form:

```
Prediction failed during an anomaly window. Anomalous signals: latency was 546ms (typical 15-50ms); square_meters was 450, unusually large (typical 80-260); neighborhood was 'industrial', unusual (typical 'suburb' or 'rural'). Missing on request: bedrooms. Cause: Synthetic anomaly triggered while scoring the model.
```

Structure: a lead clause (`Prediction failed during an anomaly window` or `…during normal operation`); when features couldn't be normalized, a single sentence saying so; otherwise an `Anomalous signals:` clause listing whichever of the same three observable thresholds as `describe_prediction` tripped (`latency_ms > 250`, `square_meters > 300`, `neighborhood == "industrial"`) plus a `Missing on request:` clause if any features were missing; finally `Cause: <exc>` with the underlying exception's text. The exception class name (`RuntimeError`, `KeyError`, …) is intentionally omitted — the cause text from the exception itself is more informative for a human reader than the class name. `bedrooms` doesn't get its own anomalous-signal entry because the normal and anomaly bedroom ranges overlap heavily — a value of 2 or 6 isn't reliably anomalous on its own.

#### `execute_prediction(payload, internal)`

The full request lifecycle, used by both internal traffic and `POST /predict`:

1. Generate a `request_id` (UUID hex).
2. Snapshot anomaly state.
3. **Try**: normalize → predict → observe `ml_prediction_value` → recompute statistics → increment `api_requests_total{http_status="200"}` → call `describe_prediction()` → log a `prediction` event in plain text with full context (including the `summary`) → return `(200, body)`.
4. **Except**: increment `api_requests_total{http_status="500"}` → call `describe_failure()` to build the prose `error_message` → log a `prediction_failed` event in plain text → return `(500, body)`. No traceback is captured; the prose `Cause: …` clause is the only failure-text representation.
5. **Finally**: observe `api_request_duration_seconds` regardless of success.

The payload that gets logged (via `extra_fields`) is what makes Kibana useful after Logstash parses it. Every event carries `event_type`, `request_id`, `endpoint`, `http_status`, `latency_ms`, `model_version`, `anomaly_window`, `internal`, `features`. Success-only fields: `summary`, `prediction`, `missing_features`. Failure-only fields: just `error_message`. The `internal: false` flag separates real `POST /predict` calls from background traffic — the `internal: false` filter in Kibana shows what real users would have seen.

**Important**: although the `logger.info(extra={"extra_fields": {...}})` calls are identical to the old JSON version, the `PlainTextFormatter` now serializes those dicts as a `key=value` string instead of as JSON. Logstash takes the reverse path with `grok` to reconstruct the structure. The final field names in Elasticsearch are the same as when we emitted JSON directly.

The `event_type` field name is intentional: ECS reserves the `event` namespace, and Filebeat would otherwise overwrite our value with its own metadata. The `summary` / `error_message` split (rather than a single shared `message`) keeps the field names self-describing and lets the index template map them differently — `summary` is short, `error_message` allows up to 4096 chars (see [`elasticsearch/model-api-logs-template.json`](../../elasticsearch/model-api-logs-template.json)).

#### Background workers (three threads)

- `generate_traffic()` calls `execute_prediction(internal=True)` at 1 / `BASE_RPS` cadence until `stop_event` is set.
- `sample_resources()` updates the three process gauges every second using `psutil.Process(os.getpid())`. The first `process.cpu_percent(interval=None)` call before the loop is a deliberate priming call — `psutil` returns 0.0 the first time, then a real percentage on subsequent calls.
- `auto_bump_version()` runs the same inline block every 900 s (15 min) that the `POST /admin/bump_version` handler uses: increments `bump_count`, computes new metadata via `_make_version_meta()`, calls `_set_model_info()`, increments `MODEL_DEPLOYMENTS`, and posts the annotation to Grafana via `_post_grafana_annotation()`. The deploy-marker annotation appears in the dashboard without manual intervention.

#### Lifespan

FastAPI 0.100+'s replacement for `@app.on_event("startup")` / `"shutdown"`. The `@asynccontextmanager` body runs *before* yield (startup) and *after* yield (shutdown).

- **Startup**: reset `start_time`, clear `stop_event`, set the initial model metadata via `_set_model_info(_make_version_meta(0))` (which yields `version="1.0.0-demo"` from the formula below), spawn the three background threads as daemons.
- **Shutdown**: set `stop_event`, `join(timeout=2)` each thread to allow the loops to exit cleanly.

#### FastAPI app & routes

- `GET /metrics` — calls `generate_latest()` and returns the bytes with `text/plain; version=0.0.4; charset=utf-8` (the standard Prometheus content type, exposed by `prometheus_client.CONTENT_TYPE_LATEST`).
- `GET /health` — JSON object with `status`, `model_version`, `anomaly_window`. Useful for liveness probes and demo narration ("look at how `anomaly_window` flips between true and false").
- `POST /predict` — accepts an optional JSON body (the synthetic generator works regardless), passes through `execute_prediction`, returns the response with the right status code.
- `POST /admin/bump_version` — increments an internal `bump_count` and recomputes the model version via `_make_version_meta(bump_count)`. The formula is `major = 1 + count // 10; minor = count % 10; patch = "0-demo"` — so the sequence is `1.0.0-demo → 1.1.0-demo → ... → 1.9.0-demo → 2.0.0-demo → ... → 2.9.0-demo → 3.0.0-demo → ...`, with `trained_at` cycling through ten dates from `_TRAINED_DATES`. Each call sets the previous gauge labels to 0 and the new ones to 1 (the `Gauge` pattern), then increments `model_deployments_total`. The counter exists exclusively to drive the Grafana deploy-marker annotation. Demo narration: "I'm going to deploy a new version right now — watch the vertical line appear on every chart."

## `Dockerfile`

### What it is
The image build recipe. Almost identical to `panic_duty/Dockerfile`.

### Role in the stack
Built when `docker compose up --build` runs. Compose reads `build: ./model_api` from `docker-compose.yml`.

### Walkthrough

- `FROM python:3.10-slim` — same base as `panic_duty`. Slim variant is ~80 MB compressed.
- `WORKDIR /app` — operative directory for subsequent steps and runtime.
- `COPY requirements.txt .` then `RUN pip install --no-cache-dir -r requirements.txt` — separate layer for deps so editing `app.py` doesn't reinstall them. `--no-cache-dir` keeps wheel cache out of the image.
- `COPY . .` — the rest of the build context (`app.py`, `requirements.txt`, anything else in `model_api/`).
- `EXPOSE 8000` — declarative metadata. Compose still maps the port.
- `CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]` — Uvicorn ASGI server. `--no-access-log` suppresses the per-request log line; only the plain-text events emitted by the application reach stdout, which keeps the Logstash pipeline clean.

## `requirements.txt`

### What it is
Four-package list, no version pins.

### Role in the stack
Read by `pip install` during the Docker build.

### Walkthrough

- **`fastapi`** — web framework. Provides `FastAPI`, `Body`, `Response`, `JSONResponse`. Async-by-default but the routes here are sync handlers running on the thread pool.
- **`uvicorn`** — ASGI server. Started by the Dockerfile's `CMD`. Bare install (no `[standard]` extras) is sufficient for plain HTTP.
- **`prometheus-client`** — official Python client for Prometheus. Provides `Counter`, `Gauge`, `Histogram`, `Info`, `generate_latest`, `CONTENT_TYPE_LATEST`. Holds metric values in a process-local registry.
- **`psutil`** — cross-platform process and system utilities. Used for `process.cpu_percent()` and `process.memory_info().rss`.

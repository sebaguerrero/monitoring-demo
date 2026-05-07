import json as _json
import logging
import os
import random
import shutil
import statistics
import sys
import threading
import time
import urllib.request
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import psutil
from fastapi import Body, FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


class PlainTextFormatter(logging.Formatter):
    """Free-form text formatter — la API loguea como un servicio "legacy" que
    Logstash debe parsear con grok. Decisión pedagógica: invierte la práctica
    moderna (JSON estructurado) para mostrar el rol clásico de Logstash."""

    def format(self, record: logging.LogRecord) -> str:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        level = record.levelname
        logger_name = record.name

        extra = getattr(record, "extra_fields", None) or {}
        event_type = extra.get("event_type", "")
        request_id = extra.get("request_id", "-")
        endpoint = extra.get("endpoint", "-")
        http_status = extra.get("http_status", "-")
        latency_ms = extra.get("latency_ms", 0)
        model_version = extra.get("model_version", "-")
        anomaly_window = "true" if extra.get("anomaly_window") else "false"
        internal = "true" if extra.get("internal") else "false"

        features = extra.get("features") or {}
        sqm = features.get("square_meters") if features.get("square_meters") is not None else "null"
        br = features.get("bedrooms") if features.get("bedrooms") is not None else "null"
        nbhd = features.get("neighborhood") if features.get("neighborhood") is not None else "null"

        common = (
            f"{ts} {level} [{logger_name}] "
            f"req={request_id} endpoint={endpoint} status={http_status} "
            f"latency={latency_ms}ms model={model_version} "
            f"anomaly={anomaly_window} internal={internal} "
            f"sqm={sqm} br={br} nbhd={nbhd}"
        )

        if event_type == "prediction":
            missing = extra.get("missing_features") or []
            missing_str = ",".join(missing) if missing else "none"
            prediction = extra.get("prediction", 0)
            summary = (extra.get("summary") or "").replace('"', "'")
            return (
                f"{common} missing={missing_str} prediction={prediction} "
                f'summary="{summary}"'
            )

        if event_type == "prediction_failed":
            error_message = (extra.get("error_message") or "").replace('"', "'")
            return f'{common} msg="{error_message}"'

        return f"{ts} {level} [{logger_name}] {record.getMessage()}"


def _configure_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(PlainTextFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return logging.getLogger("model_api")


logger = _configure_logging()

REQUEST_COUNT = Counter("api_requests_total", "Total number of requests", ["endpoint", "http_status"])
REQUEST_LATENCY = Histogram(
    "api_request_duration_seconds",
    "Request latency in seconds",
    ["endpoint"],
    buckets=(0.01, 0.03, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0),
)

PREDICTION_VALUE = Histogram(
    "ml_prediction_value",
    "Predicted house prices",
    buckets=(100000, 150000, 200000, 250000, 300000, 400000, 500000, 650000, 800000, 1000000, 1500000),
)
INPUT_SQUARE_METERS = Histogram(
    "ml_input_square_meters",
    "Observed square-meter house sizes sent to the model",
    buckets=(50, 100, 150, 200, 250, 300, 400, 500, 650),
)
INPUT_BEDROOMS = Histogram(
    "ml_input_bedrooms",
    "Observed bedroom counts sent to the model",
    buckets=(0, 1, 2, 3, 4, 5, 6, 7, 8),
)
INPUT_NEIGHBORHOOD = Counter(
    "ml_input_neighborhood_total",
    "Observed neighborhood categories sent to the model",
    ["neighborhood"],
)
MISSING_FEATURE = Counter(
    "ml_missing_feature_total",
    "Count of missing required model inputs",
    ["feature"],
)
PROCESS_CPU_PERCENT = Gauge("model_api_process_cpu_percent", "CPU percent used by the API process")
PROCESS_RESIDENT_MEMORY_BYTES = Gauge(
    "model_api_process_resident_memory_bytes",
    "Resident memory used by the API process in bytes",
)
PROCESS_DISK_UTILIZATION_PERCENT = Gauge(
    "model_api_process_disk_utilization_percent",
    "Filesystem utilization percent for the API container filesystem",
)
PREDICTION_MEAN_RECENT = Gauge(
    "ml_prediction_mean_recent",
    "Recent mean prediction value over the rolling demo window",
)
PREDICTION_MEDIAN_RECENT = Gauge(
    "ml_prediction_median_recent",
    "Recent median prediction value over the rolling demo window",
)
PREDICTION_MIN_RECENT = Gauge(
    "ml_prediction_min_recent",
    "Recent minimum prediction value over the rolling demo window",
)
PREDICTION_MAX_RECENT = Gauge(
    "ml_prediction_max_recent",
    "Recent maximum prediction value over the rolling demo window",
)
PREDICTION_STDDEV_RECENT = Gauge(
    "ml_prediction_stddev_recent",
    "Recent standard deviation of prediction values over the rolling demo window",
)
MODEL_INFO = Gauge("ml_model_info", "Deployed model metadata (1=active, 0=retired)", ["version", "trained_at"])
MODEL_DEPLOYMENTS = Counter(
    "model_deployments_total",
    "Count of model version bumps since service start (used to drive Grafana annotations)",
)

model_deploy_lock = threading.Lock()

_TRAINED_DATES = [
    "2024-09-15", "2024-10-01", "2024-10-20", "2024-11-02",
    "2024-11-18", "2024-12-05", "2024-12-22", "2025-01-08",
    "2025-01-20", "2025-02-03",
]


def _make_version_meta(bump_count: int) -> dict:
    major = 1 + bump_count // 10
    minor = bump_count % 10
    trained_at = _TRAINED_DATES[bump_count % len(_TRAINED_DATES)]
    return {"version": f"{major}.{minor}.0-demo", "trained_at": trained_at}


def _set_model_info(meta: dict) -> None:
    current = app_state.get("current_model_meta")
    if current:
        MODEL_INFO.labels(**current).set(0)
    MODEL_INFO.labels(**meta).set(1)
    app_state["current_model_meta"] = meta


_GRAFANA_URL = os.getenv("GRAFANA_URL", "http://grafana:3000")
_GRAFANA_PASSWORD = os.getenv("GF_ADMIN_PASSWORD", "admin")


def _post_grafana_annotation(version: str) -> None:
    now_ms = int(time.time() * 1000)
    payload = _json.dumps({
        "time": now_ms,
        "tags": ["deploy"],
        "text": f"Model deploy: {version}",
    }).encode()
    req = urllib.request.Request(
        f"{_GRAFANA_URL}/api/annotations",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {__import__('base64').b64encode(f'admin:{_GRAFANA_PASSWORD}'.encode()).decode()}",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass  # Grafana may not be ready yet; annotation is best-effort


MODEL_VERSION = os.getenv("MODEL_VERSION", "v1.1.0-demo")
BASE_RPS = max(float(os.getenv("DEMO_BASE_RPS", "8")), 1.0)
ANOMALY_INTERVAL_SECONDS = max(float(os.getenv("DEMO_ANOMALY_INTERVAL_SECONDS", "30")), 10.0)
ANOMALY_DURATION_SECONDS = max(float(os.getenv("DEMO_ANOMALY_DURATION_SECONDS", "30")), 5.0)
ANOMALY_FAILURE_RATE = min(max(float(os.getenv("DEMO_ANOMALY_FAILURE_RATE", "0.2")), 0.0), 0.95)
PREDICTION_STATS_WINDOW_SECONDS = max(float(os.getenv("DEMO_PREDICTION_STATS_WINDOW_SECONDS", "300")), 30.0)

app_state = {
    "start_time": time.monotonic(),
    "stop_event": threading.Event(),
}
prediction_history = deque()
prediction_history_lock = threading.Lock()

NEIGHBORHOODS = ("suburb", "downtown", "rural", "industrial")
NEIGHBORHOOD_MULTIPLIERS = {
    "suburb": 1.0,
    "downtown": 1.25,
    "rural": 0.82,
    "industrial": 1.55,
}

# Imputation values for missing features at prediction time.
# Strategy: use the training-set median (numeric) / mode (categorical) instead
# of a hardcoded default — the imputed value stays inside the training-set
# range, so the model isn't asked to extrapolate over a fabricated point.
# The "training set" here is the normal-traffic generator in build_synthetic_features:
#   square_meters: randint(80, 260)                       → median = 170
#   bedrooms:      randint(1, 5)                          → median = 3
#   neighborhood:  ("suburb","suburb","downtown","rural") → mode   = "suburb"
# In practice only `bedrooms` is ever missing from synthetic traffic; the other
# two constants exist as defensive code for external POST /predict requests.
SQM_TRAIN_MEDIAN = 170.0
BEDROOMS_TRAIN_MEDIAN = 3
NEIGHBORHOOD_TRAIN_MODE = "suburb"


def is_anomaly_window() -> bool:
    elapsed = time.monotonic() - app_state["start_time"]
    cycle_length = ANOMALY_INTERVAL_SECONDS + ANOMALY_DURATION_SECONDS
    return (elapsed % cycle_length) >= ANOMALY_INTERVAL_SECONDS


def build_synthetic_features(anomaly_active: bool) -> dict:
    if anomaly_active:
        neighborhoods = ("industrial", "industrial", "industrial", "downtown")
        square_meters = random.randint(320, 580)
        bedrooms = None if random.random() < 0.35 else random.randint(1, 7)
    else:
        neighborhoods = ("suburb", "suburb", "downtown", "rural")
        square_meters = random.randint(80, 260)
        bedrooms = random.randint(1, 5)

    return {
        "square_meters": square_meters,
        "bedrooms": bedrooms,
        "neighborhood": random.choice(neighborhoods),
    }


def normalize_features(payload: dict | None, anomaly_active: bool, *, internal: bool) -> tuple[dict, dict, list[str]]:
    if internal or payload is None:
        features = build_synthetic_features(anomaly_active)
    else:
        features = {
            "square_meters": payload.get("square_meters"),
            "bedrooms": payload.get("bedrooms"),
            "neighborhood": payload.get("neighborhood"),
        }

    missing: list[str] = []
    for feature_name in ("square_meters", "bedrooms", "neighborhood"):
        if features.get(feature_name) in (None, ""):
            MISSING_FEATURE.labels(feature=feature_name).inc()
            missing.append(feature_name)

    if features.get("square_meters") not in (None, ""):
        INPUT_SQUARE_METERS.observe(float(features["square_meters"]))
    if features.get("bedrooms") not in (None, ""):
        INPUT_BEDROOMS.observe(float(features["bedrooms"]))

    neighborhood = features.get("neighborhood")
    if neighborhood not in NEIGHBORHOODS:
        neighborhood = NEIGHBORHOOD_TRAIN_MODE
    INPUT_NEIGHBORHOOD.labels(neighborhood=neighborhood).inc()

    square_meters = float(features["square_meters"]) if features.get("square_meters") not in (None, "") else SQM_TRAIN_MEDIAN
    bedrooms = int(features["bedrooms"]) if features.get("bedrooms") not in (None, "") else BEDROOMS_TRAIN_MEDIAN

    normalized = {
        "square_meters": square_meters,
        "bedrooms": bedrooms,
        "neighborhood": neighborhood,
    }
    presented = {
        "square_meters": features.get("square_meters") if features.get("square_meters") not in (None, "") else None,
        "bedrooms": features.get("bedrooms") if features.get("bedrooms") not in (None, "") else None,
        "neighborhood": features.get("neighborhood") if features.get("neighborhood") in NEIGHBORHOODS else None,
    }
    return normalized, presented, missing


def perform_prediction(features: dict, anomaly_active: bool) -> float:
    time.sleep(random.uniform(0.015, 0.05))

    if anomaly_active:
        time.sleep(random.uniform(0.45, 0.85))
        if random.random() < ANOMALY_FAILURE_RATE:
            raise RuntimeError("Synthetic anomaly triggered while scoring the model")

    base_price = features["square_meters"] * 1800
    bedroom_adjustment = features["bedrooms"] * 12000
    multiplier = NEIGHBORHOOD_MULTIPLIERS[features["neighborhood"]]
    noise = random.uniform(-15000, 15000)

    prediction = (base_price + bedroom_adjustment) * multiplier + noise
    if anomaly_active:
        prediction += random.uniform(180000, 420000)

    return max(prediction, 50000.0)


def record_prediction_statistics(prediction: float) -> None:
    now = time.monotonic()
    with prediction_history_lock:
        prediction_history.append((now, prediction))
        cutoff = now - PREDICTION_STATS_WINDOW_SECONDS
        while prediction_history and prediction_history[0][0] < cutoff:
            prediction_history.popleft()

        values = [value for _, value in prediction_history]

    if not values:
        return

    PREDICTION_MEAN_RECENT.set(statistics.fmean(values))
    PREDICTION_MEDIAN_RECENT.set(statistics.median(values))
    PREDICTION_MIN_RECENT.set(min(values))
    PREDICTION_MAX_RECENT.set(max(values))
    PREDICTION_STDDEV_RECENT.set(statistics.pstdev(values) if len(values) > 1 else 0.0)


def describe_prediction(features: dict, prediction: float, latency_ms: float, missing: list[str]) -> str:
    issues: list[str] = []
    if latency_ms > 250:
        issues.append(f"high latency {latency_ms:.0f}ms")
    if missing:
        issues.append(f"missing features: {', '.join(missing)}")
    sqm = features.get("square_meters")
    if isinstance(sqm, (int, float)) and sqm > 300:
        issues.append(f"large square_meters {sqm:.0f}")
    if prediction > 1_300_000:
        issues.append(f"elevated prediction ${prediction:,.0f}")
    if not issues:
        return "prediction within expected ranges"
    return "anomalous prediction: " + "; ".join(issues)


def describe_failure(features: dict | None, latency_ms: float, missing: list[str], exc: Exception, anomaly_active: bool) -> str:
    context = "during an anomaly window" if anomaly_active else "during normal operation"
    parts = [f"Prediction failed {context}"]

    if features is None:
        parts.append("The request payload could not be normalized into the expected (square_meters, bedrooms, neighborhood) features")
    else:
        flags: list[str] = []
        if latency_ms > 250:
            flags.append(f"latency was {latency_ms:.0f}ms (typical 15-50ms)")
        sqm = features.get("square_meters")
        if isinstance(sqm, (int, float)) and sqm > 300:
            flags.append(f"square_meters was {sqm:.0f}, unusually large (typical 80-260)")
        neighborhood = features.get("neighborhood")
        if neighborhood == "industrial":
            flags.append("neighborhood was 'industrial', unusual (typical 'suburb' or 'rural')")
        if flags:
            parts.append("Anomalous signals: " + "; ".join(flags))
        if missing:
            parts.append(f"Missing on request: {', '.join(missing)}")

    parts.append(f"Cause: {exc}")
    return ". ".join(parts) + "."


def execute_prediction(payload: dict | None = None, *, internal: bool = False) -> tuple[int, dict]:
    endpoint = "/predict"
    start_time = time.time()
    anomaly_active = is_anomaly_window()
    request_id = uuid.uuid4().hex
    features: dict | None = None
    presented_features: dict | None = None
    missing: list[str] = []

    try:
        features, presented_features, missing = normalize_features(payload, anomaly_active, internal=internal)
        prediction = perform_prediction(features, anomaly_active)
        PREDICTION_VALUE.observe(prediction)
        record_prediction_statistics(prediction)
        REQUEST_COUNT.labels(endpoint=endpoint, http_status="200").inc()
        latency_ms = (time.time() - start_time) * 1000.0
        summary = describe_prediction(features, prediction, latency_ms, missing)
        logger.info(
            summary,
            extra={"extra_fields": {
                "event_type": "prediction",
                "summary": summary,
                "request_id": request_id,
                "endpoint": endpoint,
                "http_status": 200,
                "latency_ms": round(latency_ms, 2),
                "model_version": MODEL_VERSION,
                "anomaly_window": anomaly_active,
                "internal": internal,
                "features": presented_features,
                "missing_features": missing,
                "prediction": round(prediction, 2),
            }},
        )
        return 200, {
            "prediction": round(prediction, 2),
            "model_version": MODEL_VERSION,
            "anomaly_window": anomaly_active,
            "features": features,
        }
    except Exception as exc:
        REQUEST_COUNT.labels(endpoint=endpoint, http_status="500").inc()
        latency_ms = (time.time() - start_time) * 1000.0
        error_message = describe_failure(features, latency_ms, missing, exc, anomaly_active)
        logger.error(
            error_message,
            extra={"extra_fields": {
                "event_type": "prediction_failed",
                "request_id": request_id,
                "endpoint": endpoint,
                "http_status": 500,
                "latency_ms": round(latency_ms, 2),
                "model_version": MODEL_VERSION,
                "anomaly_window": anomaly_active,
                "internal": internal,
                "features": presented_features,
                "error_message": error_message,
            }},
        )
        return 500, {
            "error": str(exc),
            "model_version": MODEL_VERSION,
            "anomaly_window": anomaly_active,
        }
    finally:
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(time.time() - start_time)


def generate_traffic() -> None:
    sleep_interval = 1.0 / BASE_RPS
    stop_event = app_state["stop_event"]
    while not stop_event.is_set():
        execute_prediction(internal=True)
        time.sleep(sleep_interval)


def sample_resources() -> None:
    stop_event = app_state["stop_event"]
    process = psutil.Process(os.getpid())
    process.cpu_percent(interval=None)

    while not stop_event.is_set():
        PROCESS_CPU_PERCENT.set(process.cpu_percent(interval=None))
        PROCESS_RESIDENT_MEMORY_BYTES.set(process.memory_info().rss)
        disk = shutil.disk_usage("/")
        PROCESS_DISK_UTILIZATION_PERCENT.set((disk.used / disk.total) * 100 if disk.total else 0.0)
        time.sleep(1.0)


VERSION_BUMP_INTERVAL_SECONDS = 900


def auto_bump_version() -> None:
    stop_event = app_state["stop_event"]
    while not stop_event.wait(timeout=VERSION_BUMP_INTERVAL_SECONDS):
        with model_deploy_lock:
            bump_count = app_state.get("bump_count", 0) + 1
            app_state["bump_count"] = bump_count
            meta = _make_version_meta(bump_count)
            _set_model_info(meta)
            MODEL_DEPLOYMENTS.inc()
        _post_grafana_annotation(meta["version"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_state["start_time"] = time.monotonic()
    app_state["stop_event"].clear()
    app_state["bump_count"] = 0
    _set_model_info(_make_version_meta(0))

    traffic_thread = threading.Thread(target=generate_traffic, daemon=True)
    resource_thread = threading.Thread(target=sample_resources, daemon=True)
    version_thread = threading.Thread(target=auto_bump_version, daemon=True)
    traffic_thread.start()
    resource_thread.start()
    version_thread.start()

    yield

    app_state["stop_event"].set()
    traffic_thread.join(timeout=2)
    resource_thread.join(timeout=2)
    version_thread.join(timeout=2)


app = FastAPI(title="ML Prediction API", lifespan=lifespan)


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "anomaly_window": is_anomaly_window(),
    }


@app.post("/predict")
def predict(payload: dict | None = Body(default=None)):
    status_code, body = execute_prediction(payload=payload, internal=False)
    if status_code == 200:
        return body
    return JSONResponse(status_code=status_code, content=body)


@app.post("/admin/bump_version")
def bump_version():
    with model_deploy_lock:
        bump_count = app_state.get("bump_count", 0) + 1
        app_state["bump_count"] = bump_count
        meta = _make_version_meta(bump_count)
        _set_model_info(meta)
        MODEL_DEPLOYMENTS.inc()
    _post_grafana_annotation(meta["version"])
    return {"deployed": meta}

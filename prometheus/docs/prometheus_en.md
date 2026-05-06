# Prometheus — file reference

Prometheus is the **metrics engine** of the demo. It scrapes the time series exposed by `model_api`, evaluates alert rules against them, and forwards firing alerts to Alertmanager. This folder holds three YAML files: one for the Prometheus server itself, one for the alert rules it evaluates, and one for Alertmanager (which is technically a separate binary but lives alongside Prometheus by convention).

## `prometheus.yml`

### What it is
The Prometheus server's main configuration. Tells Prometheus what to scrape, how often, where to load alert rules from, and where to send alerts.

### Role in the stack
- Mounted into the Prometheus container at `/etc/prometheus/prometheus.yml` by `docker-compose.yml`.
- Read once at startup; changes require a Prometheus reload (`docker compose restart prometheus`).
- Drives the entire metrics pipeline: without an entry here, a target is invisible to Prometheus and to Grafana.

### Walkthrough

**`global`** — defaults that apply to every scrape and rule unless overridden per-job.

- `scrape_interval: 5s` — Prometheus pulls `/metrics` from each target every 5 seconds. Far more aggressive than production defaults (15–60 s) so the demo's anomaly windows show up on the dashboard within a few scrapes instead of a minute. The `model_api` background traffic is sized to make this rate sustainable.
- `evaluation_interval: 5s` — alert rules are re-evaluated at the same cadence as scraping. Combined with each rule's `for: 5s` clause, this means alerts can transition from `Pending` to `Firing` within ~10 s of the underlying threshold being breached.

**`rule_files`** — paths to alert rule definitions.

- `"/etc/prometheus/rules.yml"` — the file documented below. Mounted from `./prometheus/rules.yml` on the host.

**`alerting`** — where Prometheus pushes firing alerts.

- `alertmanagers[0].static_configs[0].targets: ['alertmanager:9093']` — service name on the `monitor_net` network plus Alertmanager's default port. If the Alertmanager container is not running, Prometheus still evaluates rules and shows them at `/alerts`, but logs warnings about being unable to push.

**`scrape_configs`** — what to scrape.

- `job_name: 'house_price_predictor'` — the only scrape job. The job name becomes the `job` label on every metric Prometheus stores.
- `static_configs[0].targets: ['model_api:8000']` — single in-network target. `model_api` resolves to the FastAPI container.
- `static_configs[0].labels.group: 'ml_models'` — extra label attached to every metric from this target. Useful for grouping in dashboards or rule expressions when more services are added later.

## `rules.yml`

### What it is
Five Prometheus alert rules grouped under `ml_monitoring_alerts`. Each rule names a condition, gives it a severity, and attaches human-readable annotations that Alertmanager (and PanicDuty) display when the alert fires.

### Role in the stack
- Loaded by Prometheus at startup via the `rule_files:` directive in `prometheus.yml`.
- Re-evaluated every `evaluation_interval` (5 s in the demo).
- A rule whose expression returns a non-empty result for at least its `for:` window transitions from `Pending` to `Firing` and is pushed to Alertmanager.

The rules cover three pillars of monitoring (data-science / ops / availability), which is exactly the split the demo is trying to teach.

### Walkthrough — every rule

All five rules share `for: 5s`. That means the threshold has to be breached for one evaluation tick (5 s) before firing — minimum delay so the PanicDuty page flips red within seconds of the anomaly window starting. Combined with the short `[15s]` lookback windows below, this is what makes a 30 s green / 30 s red cadence visibly alternate; the trade-off is that a single noisy scrape can briefly flip an alert.

**1. `PredictionDriftDetected`** *(severity: critical)*

```promql
sum(rate(ml_prediction_value_sum[15s])) / sum(rate(ml_prediction_value_count[15s])) > 600000
```

Computes the rolling mean prediction over the last 15 seconds and fires if it exceeds **$600 000**. Mechanic: `ml_prediction_value` is a histogram; `_sum / _count` over `rate(…)` is the standard PromQL idiom for "average value per observation in the lookback window." A 15 s window means the average tracks the live anomaly signal closely instead of being smeared across the whole anomaly cycle. During an anomaly window, `model_api` adds a 180k–420k offset to predictions, easily clearing this threshold.

**2. `HighApiLatency`** *(severity: warning)*

```promql
sum(rate(api_request_duration_seconds_sum[15s])) / sum(rate(api_request_duration_seconds_count[15s])) > 0.35
```

Same `_sum / _count` pattern applied to the request-duration histogram. Threshold **350 ms**. Normal latency is 15–50 ms; anomaly windows inject 450–850 ms extra, so the average crosses 350 ms quickly.

**3. `ElevatedApiErrorRate`** *(severity: critical)*

```promql
sum(rate(api_requests_total{http_status="500"}[15s])) / sum(rate(api_requests_total[15s])) > 0.08
```

Ratio of 500 responses to total responses. Threshold **8 %**. During anomaly windows, `model_api` raises exceptions at the configured `DEMO_ANOMALY_FAILURE_RATE` (code default `0.2`, overridden to `0.7` in `docker-compose.yml`), which puts the rolling ratio well above 8 %.

**4. `MissingFeatureSpike`** *(severity: warning)*

```promql
sum(rate(ml_missing_feature_total[15s])) > 0.5
```

Per-second rate of missing-feature increments (currently `bedrooms`). Threshold **0.5 increments/second** ≈ 30/min. Normal traffic doesn't drop `bedrooms` at all; anomaly windows drop it ~35 % of the time (with `BASE_RPS=8`, that's ~2.8 missing/s during anomaly), so the rate clears the threshold easily.

**5. `ModelApiTargetDown`** *(severity: critical)*

```promql
up{job="house_price_predictor"} == 0
```

The classic "is the target alive?" check. `up` is set to 0 by Prometheus when a scrape fails. Demonstrates the operational availability angle of the article's monitoring framework.

## `alertmanager.yml`

### What it is
Alertmanager's routing config. One receiver, one route, no grouping subtlety.

### Role in the stack
- Loaded by Alertmanager at startup. Mounted from `./prometheus/alertmanager.yml`.
- Receives every firing alert Prometheus pushes.
- Forwards them as webhook calls to the PanicDuty mock incident UI.

### Walkthrough

- `route.group_by: ['alertname']` — Alertmanager batches together notifications with the same alertname. With five independent alerts and short demo windows, this mostly means "send each one separately" but it prevents thundering bursts if multiple instances of the same alert fire simultaneously.
- `route.group_wait: 5s` / `group_interval: 10s` / `repeat_interval: 1m` — explicitly tightened from Alertmanager's defaults (30 s / 5 min / 4 h). At default values, a 30 s anomaly window would end before Alertmanager finished waiting to send the first webhook. With these values, the firing webhook reaches PanicDuty within ~5 s of the alert firing, follow-ups every 10 s, and resolved webhooks aren't held up.
- `route.receiver: 'panic_duty_webhook'` — every alert goes to this single receiver. There is no per-severity routing; all alerts (warning and critical) hit the same UI.
- `receivers[0].name: 'panic_duty_webhook'` — matches the route's `receiver:` value.
- `webhook_configs[0].url: 'http://panic_duty:8080/webhook'` — the HTTP endpoint on the PanicDuty container. PanicDuty exposes this route in `panic_duty/app.py` to receive Alertmanager's standard webhook payload.
- `webhook_configs[0].send_resolved: true` — Alertmanager also sends a resolved notification when the alert clears, so PanicDuty can mark the incident closed instead of leaving it sticky.

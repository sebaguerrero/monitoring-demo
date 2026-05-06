# ML Monitoring in Production: Local Demo

## Index
- [Introduction](#introduction)
- [Services](#services)
- [What Docker is and why it is used](#what-docker-is-and-why-it-is-used)
- [What Docker Compose is and what it does in the demo](#what-docker-compose-is-and-what-it-does-in-the-demo)
- [How to install Docker](#how-to-install-docker)
- [Docker permissions](#docker-permissions)
- [Quick start](#quick-start)
- [Demo modes](#demo-modes)
- [What to expect after startup](#what-to-expect-after-startup)
- [Folder structure](#folder-structure)
- [What each file does](#what-each-file-does)
- [Public deployment for the class poster](#public-deployment-for-the-class-poster)
- [Endpoints](#endpoints)
- [What to look for during a demo](#what-to-look-for-during-a-demo)

## Introduction

A local demo of monitoring an ML system in production, inspired by [Monitoring Machine Learning Models in Production](https://christophergs.com/machine%20learning/2020/03/14/how-to-monitor-machine-learning-models/) by Christopher GS. It simulates a real-estate price prediction API that alternates between normal mode and anomaly windows every 30 seconds, which fires alerts and exposes how the monitoring stack (Prometheus, Grafana, Alertmanager, ELK) reacts to typical ML problems: input drift, prediction drift, missing features, latency, HTTP errors.


## Services

The demo combines 11 services. For a thorough explanation of each one (what it does, how it connects to the others, what metrics/logs it produces), see [`docs/descripcion_demo_en.md`](docs/descripcion_demo_en.md):

| Service | Role | Detail doc |
|---|---|---|
| `model_api` | Prediction API + synthetic traffic generator + emits metrics and logs | [§4](docs/descripcion_demo_en.md#4-the-prediction-api-model_api) · [model_api_en.md](model_api/docs/model_api_en.md) |
| `prometheus` | Metric collection and alert evaluation | [§5](docs/descripcion_demo_en.md#5-prometheus-the-metric-collector) · [prometheus_en.md](prometheus/docs/prometheus_en.md) |
| `grafana` | Visual metric dashboard | [§6](docs/descripcion_demo_en.md#6-grafana-the-ml-dashboard) · [grafana_en.md](grafana/docs/grafana_en.md) |
| `alertmanager` | Alert grouping and routing | [§5](docs/descripcion_demo_en.md#5-prometheus-the-metric-collector) · [prometheus_en.md](prometheus/docs/prometheus_en.md#alertmanageryml) |
| `panic_duty` | Alert webhook receiver + mock PagerDuty-style UI | [§7](docs/descripcion_demo_en.md#7-panicduty-the-alert-receiver) · [panic_duty_en.md](panic_duty/docs/panic_duty_en.md) |
| `filebeat` | Log shipper (reads `model_api` stdout via Docker, forwards to Logstash) | [§8](docs/descripcion_demo_en.md#8-the-log-pipeline-filebeat--logstash--elasticsearch) · [filebeat_en.md](filebeat/docs/filebeat_en.md) |
| `logstash` | Intermediate processor (parses plain text to JSON via `grok`, normalizes types) | [§8](docs/descripcion_demo_en.md#8-the-log-pipeline-filebeat--logstash--elasticsearch) · [logstash_en.md](logstash/docs/logstash_en.md) |
| `elasticsearch` | Log storage (daily `model-api-logs-*` indices) | [§8](docs/descripcion_demo_en.md#8-the-log-pipeline-filebeat--logstash--elasticsearch) · [elasticsearch_en.md](elasticsearch/docs/elasticsearch_en.md) |
| `kibana` | UI to explore logs (Discover + `ML Drift Investigation` dashboard) | [§9](docs/descripcion_demo_en.md#9-kibana-log-exploration-and-dashboard) · [kibana_en.md](kibana/docs/kibana_en.md) |
| `kibana-init` | One-shot bootstrap: creates the data view and provisions the Kibana dashboard | [§9](docs/descripcion_demo_en.md#9-kibana-log-exploration-and-dashboard) · [kibana_en.md](kibana/docs/kibana_en.md) |
| `caddy` *(optional, `poster` profile)* | Reverse proxy with automatic HTTPS to expose the demo on a public VM | [Public deployment](#public-deployment-for-the-class-poster) |

## What Docker is and why it is used in the demo
Docker is a tool for packaging applications together with their runtime environment. In practice, that means each part of the demo can run inside its own isolated container with the dependencies and configuration it needs.

**Why the demo uses it.** Without Docker you would need to install and configure several different tools locally (Prometheus, Grafana, Alertmanager, Elasticsearch, Kibana, Logstash, Filebeat), make sure their versions are compatible, expose ports manually, and wire the services together yourself. For a class demo, that creates unnecessary setup friction.

What Docker provides:

- **Reproducibility** — the same `docker compose up` produces the same stack on any machine.
- **Isolation** — each service has its dependencies inside its container; they don't step on each other or on what you have installed on the host.
- **One-command startup** — a single command brings up all 10 services.
- **Clear multi-service architecture** — each service = one container, easy to reason about.
- **Fewer "it works on my machine" problems** — the container pins the exact version of each tool.

## What Docker Compose is and what role it plays in the demo
Docker Compose is the tool used to **define and run multiple Docker services together** from a single configuration file (`docker-compose.yml`). If Docker lets you run one container at a time, Compose lets you describe 10 containers and orchestrate them as a unit.

**What it brings up in the demo.** The full 10-service stack by default, organized into three logical groups:

- **Core**: `model_api`, `prometheus`, `grafana`
- **Alerts**: `alertmanager`, `panic_duty`
- **Logs**: `elasticsearch`, `kibana`, `filebeat`, `logstash`, `kibana-init`

A plain `docker compose up` brings up everything. If you want a lighter footprint, you can name a subset of services on the command line — see [Demo modes](#demo-modes) below.

**What Compose handles, specifically:**

- **Builds the custom Python services** (`model_api`, `panic_duty`) from their respective `Dockerfile`s.
- **Runs Prometheus, Grafana, Alertmanager, Elasticsearch, Kibana, Logstash, and Filebeat** from official images pinned to known versions. Logstash, in particular, runs from the official image with `pipeline/logstash.conf` and `config/logstash.yml` mounted as volumes — pipeline changes apply with a container `restart`, no rebuild needed.
- **Connects all services to the same network** (`monitor_net`) so they can reach each other by service name (e.g. `prometheus` resolves to the Prometheus container's IP).
- **Exposes ports** to your machine (`8000` for the API, `9090` for Prometheus, `3000` for Grafana, `5601` for Kibana, etc.).
- **Mounts configuration files** from the repo into each container:
  - Prometheus rules,
  - Alertmanager routing config,
  - Grafana provisioning and dashboard files,
  - Filebeat autodiscover config,
  - Logstash pipeline (`pipeline/logstash.conf`),
  - Kibana data-view + dashboard bootstrap script.
- **Injects environment variables** into `model_api` (demo knobs: `MODEL_VERSION`, `DEMO_BASE_RPS`, etc.).
- **Controls startup order** with `depends_on` and healthchecks (e.g. Logstash waits for Elasticsearch to be healthy before starting; Filebeat waits for Logstash to be healthy).

That means the entire architecture can be started and stopped as one unit instead of as several manually configured processes — and you can run a smaller subset by naming individual services on `docker compose up`.

## How to install Docker
To run the demo, you need Docker installed on your machine.

The simplest path is to follow Docker's official installation instructions for your operating system:

- Docker installation guide: https://docs.docker.com/get-docker/

After installing Docker, verify it is available:

```bash
docker --version
docker-compose --version
```

If your installation uses the newer Compose plugin, this may work instead:

```bash
docker compose version
```

If Docker is installed but commands fail because of permissions, see the **Docker Permissions** section below.

## Docker Permissions
If Docker commands fail with a permission error, your user probably cannot access the Docker socket directly.

Common options:

- run commands with `sudo`,
- or add your user to the `docker` group and start a new shell/session.

This is a local machine setup issue, not a problem in the demo itself.

## Quick start
From this directory, the recommended way to bring up the stack is:

```bash
make fresh
```

This is the **"always works"** option — equivalent to:

```bash
docker compose down -v --rmi local --remove-orphans
docker compose up -d --build
```

That is: tear everything down, drop volumes (old Elasticsearch data), drop local images (`model_api`, `panic_duty`) to force a rebuild, drop orphans, and bring all 10 services back up in the background with freshly rebuilt images. Guarantees a clean stack with no residual state from previous runs.

For the older Compose plugin syntax, `docker-compose` instead of `docker compose` also works.

### When NOT to use `make fresh` (faster alternatives)

`make fresh` is safe but tears everything down and rebuilds — takes ~1-2 min. If you know the current state of the stack, faster alternatives exist:

| Situation | Command | What it preserves |
|---|---|---|
| **Starting fresh** (recommended default) | `make fresh` | nothing |
| Re-start without touching anything | `docker compose restart` | images, volumes, ES data |
| Changed code but want to keep old ES indices | `docker compose up -d --build` | volumes (data), recreates containers |
| Just bring it up (never brought it down) | `docker compose up -d` | everything — uses cached images |

### Relevant `docker compose up` flags

`--build` and `-d` are two **independent** flags that control different things:

**`--build`** — forces Compose to **rebuild the images** before starting the containers.

- **Without `--build`** → Compose uses the cached image (if one exists). If you changed code in `model_api/` or `panic_duty/`, or edited any `Dockerfile`, those changes **will not show up**.
- **With `--build`** → Runs `docker build` on every service that has `build:` in `docker-compose.yml` (in the demo: `model_api` and `panic_duty`), then brings up the containers with the freshly built image.

When to use it alone: after editing app code, a `Dockerfile`, or `requirements.txt` of `model_api`/`panic_duty`, but you want to preserve volumes. If you want a rebuild **and** a data reset, `make fresh` is simpler. For changes in mounted-volume files (Logstash pipeline at `logstash/pipeline/logstash.conf`, Prometheus rules, Grafana dashboards), `docker compose restart <service>` is enough — no `--build` required.

**`-d` (detached)** — controls **where logs run**, not whether anything is rebuilt.

- **Without `-d`** → the containers start and your terminal stays "stuck" streaming the merged logs of every service. If you hit `Ctrl+C`, the containers stop.
- **With `-d`** → starts the containers in the background and your terminal returns to the prompt. Containers keep running even if you close the terminal. To see logs later: `docker compose logs -f`.

| Command | Rebuilds images | Drops volumes | Frees the terminal |
|---|---|---|---|
| `docker compose up` | no | no | no |
| `docker compose up --build` | **yes** | no | no |
| `docker compose up -d` | no | no | **yes** |
| `docker compose up --build -d` | **yes** | no | **yes** |
| **`make fresh`** | **yes** | **yes** | **yes** |

### Access

Then open:

- Grafana — ML System Dashboard: [http://localhost:3000/d/ml-system](http://localhost:3000/d/ml-system) (or [http://localhost:3000](http://localhost:3000) for the home page)
- Prometheus: [http://localhost:9090](http://localhost:9090)
- API health: [http://localhost:8000/health](http://localhost:8000/health)
- PanicDuty (alert UI): [http://localhost:8080](http://localhost:8080)
- Kibana — Discover (logs): [http://localhost:5601/app/discover](http://localhost:5601/app/discover)
- **Kibana — ML Drift Investigation dashboard**: [http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard](http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard)
- Logstash monitoring API: [http://localhost:9600/_node/stats](http://localhost:9600/_node/stats)

**In the public poster deployment** (`make poster-up`, see [Public deployment for the class poster](#public-deployment-for-the-class-poster) and the [AWS](docs/aws_en.md)-specific guide), all 6 tools listed in the `Caddyfile` sit behind HTTPS subdomains with auto-issued Let's Encrypt certs. URLs point to the current deployment's EIP (`3-226-31-220` with dashes, which sslip.io resolves to `3.226.31.220`); if you redeploy with a different IP, regenerate them by replacing the dashed IP in each hostname:

- Grafana — ML System Dashboard: [https://grafana.3-226-31-220.sslip.io/d/ml-system](https://grafana.3-226-31-220.sslip.io/d/ml-system) (or [https://grafana.3-226-31-220.sslip.io](https://grafana.3-226-31-220.sslip.io) for the home)
- Prometheus: [https://prometheus.3-226-31-220.sslip.io](https://prometheus.3-226-31-220.sslip.io)
- Alertmanager: [https://alertmanager.3-226-31-220.sslip.io](https://alertmanager.3-226-31-220.sslip.io)
- API health: [https://api.3-226-31-220.sslip.io/health](https://api.3-226-31-220.sslip.io/health)
- API Swagger UI: [https://api.3-226-31-220.sslip.io/docs](https://api.3-226-31-220.sslip.io/docs)
- PanicDuty (alert UI): [https://panicduty.3-226-31-220.sslip.io](https://panicduty.3-226-31-220.sslip.io)
- Kibana — Discover (logs): [https://kibana.3-226-31-220.sslip.io/app/discover](https://kibana.3-226-31-220.sslip.io/app/discover)
- **Kibana — ML Drift Investigation dashboard**: [https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard](https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard)
- Logstash monitoring API: only from inside the VM (`docker exec` or `curl localhost:9600` from the host) — not exposed externally

What to expect:

- within 1-2 minutes, Grafana should show steady traffic,
- soon after, anomaly windows should make some panels move more dramatically,
- alert rules visible at http://localhost:9090/alerts will go Pending/Firing during anomaly windows,
- in the Kibana dashboard, the **Predictions with missing features** panel should populate during anomaly windows when `bedrooms=None` triggers training-set median imputation (`bedrooms=3`), and the **Top-20 outliers** table should fill with extreme predictions and full feature context.

In Kibana **Discover**, the `model-api-logs-*` data view is already selected by default (kibana-init creates it). From there you can:

- watch the stream of prediction events one by one,
- filter by log fields: `event_type`, `http_status`, `anomaly_window`, `internal`, `features.neighborhood`, `missing_features`, `latency_ms`, `prediction`,
- adjust the time window (top right) to look at the latest predictions or a specific anomaly window.

For the **auto-provisioned dashboard** (`ML Drift Investigation`), open Kibana → hamburger menu → *Analytics → Dashboards* → select `ML Drift Investigation`. It has 2 panels: predictions with missing features (histogram) and top-20 extreme predictions with full feature context (datatable).

If your machine is short on RAM, see [Demo modes](#demo-modes) for the minimal 3-service experience.

## Demo modes

The commands below follow the **"down + rebuild + up"** pattern — they guarantee a clean stack with no residual state from previous runs. For faster alternatives (preserve data, skip rebuild, etc.) see [When NOT to use `make fresh`](#when-not-to-use-make-fresh-faster-alternatives) in Quick start.

| Mode | Command | Services | RAM | Where it runs |
|---|---|---|---|---|
| **Default local (full stack)** | `make fresh` | all 10 services | ~2.6 GB | local machine |
| Minimal local (low-RAM) | `docker compose down -v --remove-orphans && docker compose up -d --build model_api prometheus grafana` | core 3 services | ~600 MB | local machine |
| **Public (poster QR / class)** | `make poster-fresh` | all 10 + `caddy` = **11 services** | ~2.7 GB | public VM (AWS EC2, Oracle Cloud Always Free, Hetzner, etc.) |

### Default local (full stack)
The recommended way for development and single-screen presentations. Shows every part of the article's monitoring + observability story (metrics, alerts, logs).

### Minimal local (low-RAM)
Exists for low-memory machines that can't fit Elasticsearch + Kibana + Logstash (~1.9 GB combined). You keep Grafana and Prometheus, but lose alert routing, the PanicDuty UI, and the Kibana logs.

### Public (poster QR / class)
For exposing the demo to the internet from a public VM — typically for a poster session where QR codes link to the live tools over HTTPS. Adds a `caddy` service (gated behind Compose's `poster` profile) that terminates TLS with automatic Let's Encrypt certificates and reverse-proxies six tools (Grafana, Prometheus, Alertmanager, PanicDuty, the model API, and Kibana), each on its own sslip.io subdomain. **It does not start by default in local** — only when you pass `--profile poster` (or use the `make poster-up` shortcut).

For operational details — how to bring it up on AWS EC2, Makefile shortcuts, hardening (`GF_ANONYMOUS_ROLE=Viewer`, `GF_ADMIN_PASSWORD`), QR tips, stable IPs — see the [Public deployment for the class poster](#public-deployment-for-the-class-poster) section below.

## What to expect after startup

### Within the first minute

**In Grafana** ([http://localhost:3000/d/ml-system](http://localhost:3000/d/ml-system)):
- the request-rate panel should populate,
- latency should be visible,
- prediction and input metrics should start filling in,
- the `bedrooms` metric should begin updating with the other input metrics,
- CPU, memory, and disk metrics should be non-zero,
- rolling mean, median, min, max, and stddev should begin updating.

**In Kibana** ([http://localhost:5601/app/discover](http://localhost:5601/app/discover)):
- the first documents should appear in Discover within 30-60 seconds (the time it takes Logstash to process the first batch it receives from Filebeat),
- the `model-api-logs-*` data view is already selected by default (created by `kibana-init`),
- the structured fields from Logstash parsing (`event_type`, `http_status`, `latency_ms`, `prediction`, `features.*`, `missing_features`, etc.) should appear in the available-fields sidebar.

### Within a few minutes

**In Grafana / PanicDuty:**
- the anomaly window should begin,
- one or more alerts should fire,
- PanicDuty ([http://localhost:8080](http://localhost:8080)) should display active incidents,
- the Grafana dashboard should show that operational and ML-specific metrics are changing together.

**In the auto-provisioned Kibana dashboard** ([http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard](http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard)):
- the **Predictions with missing features** histogram should start empty (during normal traffic, no features are missing) and populate during anomalies with predictions concentrated around $1.4M-$1.8M (when `bedrooms=None` triggers training-set median imputation: `bedrooms=3`),
- the **Top-20 extreme predictions with feature context** datatable should always show 20 rows ordered by `prediction` desc, and during anomalies the rows are dominated by predictions $1.5M-$2.2M with `neighborhood=industrial|downtown` and `square_meters > 320` — full per-event feature context for post-alert drill-down.

## Folder structure
This is the relevant structure of `monitoring_demo/`:

```text
monitoring_demo/
├── README_EN.md
├── README_ES.md
├── docker-compose.yml
├── Makefile
├── Caddyfile
├── .env.poster.example
├── files_root/
│   ├── files_root_en.md
│   └── files_root_es.md
├── docs/
│   ├── aws_en.md
│   ├── aws_es.md
│   ├── descripcion_demo_en.md
│   └── descripcion_demo_es.md
├── presentacion/             # gitignored — presentation materials
│   ├── presentacion_completa.md
│   └── presentacion_corta.md
├── model_api/
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── prometheus/
│   ├── prometheus.yml
│   ├── rules.yml
│   └── alertmanager.yml
├── grafana/
│   ├── dashboards/
│   │   └── ml_dashboard.json
│   └── provisioning/
│       ├── dashboards/
│       │   └── dashboard.yml
│       └── datasources/
│           └── datasource.yml
├── panic_duty/
│   ├── app.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── templates/
│       └── index.html
├── filebeat/
│   └── filebeat.yml
├── logstash/
│   ├── config/
│   │   └── logstash.yml
│   └── pipeline/
│       └── logstash.conf
├── elasticsearch/
│   └── model-api-logs-template.json
└── kibana/
    └── init/
        ├── import.sh
        ├── dashboards/
        │   └── ml-derived-fields.ndjson
        └── saved_objects/
            ├── 00-lens-prediction-with-missing.json
            ├── 01-lens-top-prediction-outliers.json
            └── 05-dashboard-ml-derived-fields-dashboard.json
```

## What each file does

Quick summaries below; per-folder `README_EN.md` files contain thorough walkthroughs.

### Root files
> See [files_root_en.md](files_root/files_root_en.md) for the full reference of root-level files.

- `README_EN.md`
  - English documentation for the demo.
- `README_ES.md`
  - Spanish documentation for the demo.
- `docker-compose.yml`
  - Defines all 10 services across the three logical groups (core, alerts, logs). Also configures ports, networks, mounted files, environment variables, and the optional `poster` profile that enables the Caddy reverse proxy for public deployments.
- `Caddyfile`
  - Caddy reverse-proxy configuration used by the public-deployment workflow. Reads six hostnames from environment variables (Grafana, Prometheus, Alertmanager, PanicDuty, model API, Kibana) and routes each one to the matching service. Only loaded when `--profile poster` is active.
- `.env.poster.example`
  - Template for the public-deployment `.env.poster` file (which is gitignored). See [Public deployment for the class poster](#public-deployment-for-the-class-poster).
- `Makefile`
  - Defines short Make targets (`poster-up`, `poster-down`, `poster-logs`, `poster-status`) that wrap the longer `docker compose --env-file .env.poster --profile poster ...` invocations. See the Makefile shortcuts subsection in [Public deployment for the class poster](#public-deployment-for-the-class-poster).

### `model_api/`
> See [model_api_en.md](model_api/docs/model_api_en.md) for the full reference.

- `model_api/app.py`
  - Main FastAPI application.
  - Serves predictions.
  - Exposes Prometheus metrics.
  - Emits one plain-text free-form log line per prediction to stdout (consumed by Filebeat → Logstash → Elasticsearch in the default stack). Pedagogical decision: the API behaves like a "legacy" service so Logstash has a visible role.
  - Generates synthetic traffic and anomaly windows.
  - Computes prediction statistics and resource metrics.
- `model_api/Dockerfile`
  - Builds the container image for the API service.
- `model_api/requirements.txt`
  - Python dependencies for the API service.

### `prometheus/`
> See [prometheus_en.md](prometheus/docs/prometheus_en.md) for the full reference.

- `prometheus/prometheus.yml`
  - Main Prometheus configuration.
  - Defines the scrape target (`model_api`) and Alertmanager connection.
- `prometheus/rules.yml`
  - Alert rules evaluated by Prometheus.
- `prometheus/alertmanager.yml`
  - Alertmanager routing configuration.
  - Sends alerts to PanicDuty through a webhook.

### `grafana/`
> See [grafana_en.md](grafana/docs/grafana_en.md) for the full reference.

- `grafana/dashboards/ml_dashboard.json`
  - The dashboard definition shown in Grafana.
  - Organizes panels into DevOps metrics and ML metrics.
- `grafana/provisioning/dashboards/dashboard.yml`
  - Tells Grafana where to load dashboard JSON files from.
- `grafana/provisioning/datasources/datasource.yml`
  - Preconfigures Prometheus as the default Grafana data source.

### `panic_duty/` (alerts services)
> See [panic_duty_en.md](panic_duty/docs/panic_duty_en.md) for the full reference.

- `panic_duty/app.py`
  - FastAPI app that receives alerts from Alertmanager and serves the PanicDuty UI.
- `panic_duty/Dockerfile`
  - Builds the PanicDuty container image.
- `panic_duty/requirements.txt`
  - Python dependencies for PanicDuty.
- `panic_duty/templates/index.html`
  - HTML template for the PanicDuty web interface.

### `filebeat/` (logs services)
> See [filebeat_en.md](filebeat/docs/filebeat_en.md) for the full reference.

- `filebeat/filebeat.yml`
  - Filebeat configuration. Uses Docker autodiscover to read plain-text log lines from the `model_api` container and forward them to Logstash at `logstash:5044` (no longer directly to Elasticsearch — Logstash handles the intermediate processing).

### `logstash/` (logs services)
> See [logstash_en.md](logstash/docs/logstash_en.md) for the full reference.

- `logstash/config/logstash.yml`
  - Minimal Logstash runtime config (port 9600 for the monitoring API, X-Pack monitoring disabled). Mounted as a read-only volume into the official `docker.elastic.co/logstash/logstash:8.17.0` image at `/usr/share/logstash/config/logstash.yml`.
- `logstash/pipeline/logstash.conf`
  - The pipeline itself. Defines `beats` input (receives from Filebeat on TCP 5044), filter with `grok` (parses plain text to JSON), `mutate`/`date` filters (type and timestamp normalization), and Elasticsearch output. Mounted as a read-only volume at `/usr/share/logstash/pipeline/logstash.conf`.

### `elasticsearch/` (logs services)
> See [elasticsearch_en.md](elasticsearch/docs/elasticsearch_en.md) for the full reference.

- `elasticsearch/model-api-logs-template.json`
  - Index template applied to every `model-api-logs-*` index. Maps strings as `keyword` (single field, no `.keyword` twin) with a lowercase normalizer for case-insensitive matching. `error_message` and `summary` are mapped explicitly as `keyword` without the normalizer so error and summary text keep their original casing; their longer `ignore_above` lets them carry full descriptive sentences. The numeric and array fields (`prediction`, `latency_ms`, `features.*`, `missing_features`) are mapped explicitly as well. The template is mounted into the Logstash container and registered automatically on Logstash startup via `manage_template => true` — there is no longer a separate `es-init` sidecar.

### `kibana/` (logs services)
> See [kibana_en.md](kibana/docs/kibana_en.md) for the full reference.

- `kibana/init/import.sh`
  - One-shot bootstrap script run by the `kibana-init` container. Waits for Kibana to be healthy, creates the `model-api-logs-*` data view and sets it as default, and provisions the 3 saved objects (2 Lens panels + 1 dashboard) from `kibana/init/saved_objects/`. After this runs once, opening Kibana goes straight to a usable Discover view and to the `ML Drift Investigation` dashboard.
- `kibana/init/dashboards/ml-derived-fields.ndjson`
  - Source definition of the dashboard as NDJSON (Kibana's bulk-export format). 3 saved objects: 2 Lens panels + 1 dashboard. This is the "readable" version — the script doesn't use it directly, but it's there to understand the structure.
- `kibana/init/saved_objects/*.json`
  - The 3 saved objects split into individual files in the `{"attributes": {...}, "references": [...]}` format the Kibana POST endpoint expects. The numeric prefix (`00-`, `01-`, `05-`) forces creation order: panels first, dashboard last. `kibana-init` iterates over these files and POSTs each one to `/api/saved_objects/<type>/<id>` (direct POST avoids automatic migrations that would break the 8.x format).

## Public deployment for the class poster
This section is for the specific case of standing up a publicly reachable copy of the demo for a poster session — QR codes on a poster, each opening one of the six live tools in a phone browser.

The repo includes a `Caddyfile` and a `caddy` service (gated behind a `poster` Compose profile) that reverse-proxies six tools (Grafana, Prometheus, Alertmanager, PanicDuty, the model API, Kibana) on a single VM behind HTTPS. Local development is unaffected because the Caddy service only starts when you pass `--profile poster`.

### Prerequisites
- A public Linux VM with Docker and Compose installed (any free tier or cheap VPS — AWS EC2, Oracle Cloud Always Free, Hetzner, etc.).
- A public IP reachable on ports 80 and 443 (for Caddy + Let's Encrypt).
- Optionally, ports 3000, 9090, 8080, 5601 open as well — they enable a fallback path if sslip.io's wildcard DNS is unreachable on poster day (you can switch the QR codes to plain `http://<ip>:<port>` URLs without re-printing).

### Steps

1. SSH to the VM, clone the repo, `cd monitoring_demo`.
2. Copy the env template and fill in the VM's public IP in dashed form (sslip.io resolves `<hostname>.3-226-31-220.sslip.io` to the IP):
   ```bash
   cp .env.poster.example .env.poster
   $EDITOR .env.poster
   ```
3. Bring up the full stack plus Caddy with a clean start (recommended on poster day):
   ```bash
   make poster-fresh
   ```
   This target runs `down --rmi local --remove-orphans` + `up -d --build` with `--env-file .env.poster --profile poster`. It recreates every container with a fresh, rebuilt image (so Elasticsearch/Kibana/Prometheus container state is wiped — they don't have named volumes) but **preserves the `caddy_data` named volume**, so Let's Encrypt certs survive the rebuild and don't need to be reissued.
4. On the first `poster-fresh` after deploying to a new IP, Caddy auto-issues Let's Encrypt certs on first request to each subdomain (~30 s wait on first hit). After that, certs are cached in `caddy_data` and reused across subsequent `poster-fresh` runs.
5. Verify each `https://<tool>.<ip>.sslip.io` URL from a phone on cellular before printing QRs.

### Makefile shortcuts
The repo includes a `Makefile` with five targets that wrap the Compose commands so you don't have to retype `--env-file` and `--profile poster` every time:

| Target | Equivalent Compose command |
|---|---|
| **`make poster-fresh`** (recommended) | `docker compose --env-file .env.poster --profile poster down --rmi local --remove-orphans` + `... up -d --build` |
| `make poster-up` | `docker compose --env-file .env.poster --profile poster up -d --build` |
| `make poster-down` | `docker compose --env-file .env.poster --profile poster down` |
| `make poster-logs` | `docker compose --env-file .env.poster --profile poster logs -f` |
| `make poster-status` | `docker compose --env-file .env.poster --profile poster ps` |

`make poster-fresh` is the public-deployment equivalent of `make fresh`, but **without `-v`** on the `down` step. The difference matters: on a public deployment Caddy holds Let's Encrypt certs in the `caddy_data` named volume, and re-issuing them on every restart would burn through Let's Encrypt's rate limit (5 duplicate certs per hostname per 7 days). By keeping the volume, certs survive arbitrarily many `poster-fresh` runs and only refresh ~30 days before expiry (Caddy's auto-renewal). The other services (Elasticsearch, Kibana, Prometheus) don't have named volumes so their container state is wiped on every recreate — that's intentional and matches how `make fresh` resets local state.

`make` is preinstalled on essentially every Linux/macOS machine, including the AWS EC2 Ubuntu image you'll SSH into. Recipes are intentionally one-liners — read the `Makefile` if you want to see exactly what they do.

### Hardening
- `.env.poster.example` already sets `GF_ANONYMOUS_ROLE=Viewer` (read-only). This downgrades the anonymous Grafana role from the local default `Admin`, so QR scanners cannot edit dashboards.
- Set `GF_ADMIN_PASSWORD` to something strong before bringing the stack up — the page is reachable from the public internet.
- `.env.poster` is gitignored — never commit it.

### QR generation tips
- If you do encode the sslip.io hostnames directly, regenerate the QRs every time the VM's public IP changes. On AWS specifically, allocate an Elastic IP to keep the address stable across instance stop/start.

## Endpoints
These are the application endpoints exposed by the services in the demo.

### `model_api`
- `POST /predict`
  - Main inference endpoint.
  - Accepts an optional JSON payload with fields such as `square_meters`, `bedrooms`, and `neighborhood`.
  - If no payload is provided (e.g., from the internal traffic generator), the service synthesizes its own inputs.
  - Returns a synthetic predicted house price.
  - Also records request, input, and prediction metrics.
- `GET /metrics`
  - Prometheus scrape endpoint.
  - Exposes all exported metrics in Prometheus text format.
- `GET /health`
  - Simple health endpoint.
  - Returns basic service status, model version, and whether the demo is currently in an anomaly window.

### `panic_duty` (alerts services)
- `GET /`
  - Main PanicDuty UI page.
  - Shows active incidents received from Alertmanager.
- `POST /webhook`
  - Webhook endpoint called by Alertmanager.
  - Receives firing and resolved alerts and updates the PanicDuty incident list.

### `logstash` (logs services)
- `GET /_node/stats` (port 9600) — Logstash node stats. The `pipelines.main.events.{in,filtered,out}` field tells you how many events flowed through the pipeline. Used by the Compose healthcheck; useful for quick checks:
  ```bash
  curl -s http://localhost:9600/_node/stats | jq '.pipelines.main.events'
  ```
- `GET /_node/pipelines/main` — detailed pipeline info (loaded configuration, per-filter metrics).
- TCP `:5044` — `beats` input (Filebeat connects here). Not HTTP, not accessible with curl.

### `elasticsearch` (logs services)
- `GET /_cluster/health` — cluster health (used by the Compose healthcheck).
- `GET /model-api-logs-*/_count` — count of indexed prediction events.
- `GET /model-api-logs-*/_search` — search/filter events directly via the Elasticsearch API. Mostly used by Kibana; useful for quick CLI checks.

### `kibana` (logs services)
- `GET /` — main Kibana UI; navigate to **Discover** to explore prediction logs.
- `GET /app/dashboards#/view/ml-derived-fields-dashboard` — auto-provisioned `ML Drift Investigation` dashboard (2 panels: predictions with missing features, top-20 outlier predictions).
- `GET /api/status` — Kibana readiness endpoint (used by `kibana-init`).
- `GET /api/data_views` — list of configured data views (includes `model-api-logs`).
- `GET /api/saved_objects/dashboard/ml-derived-fields-dashboard` — auto-provisioned dashboard definition via the saved-objects API.

## What to look for during a demo
If you are presenting this to other people, a simple script is:

1. Start the stack.
2. Open Grafana and explain the dashboard sections, starting from the **Alert Status Overview** row at the top (all tiles should be green).
3. Show that the service is already producing traffic.
4. Explain that normal software monitoring is not enough for ML.
5. Wait for the anomaly window.
6. Show latency, errors, input distributions, and prediction values changing together — and point out the corresponding overview tile(s) turning red and the red threshold band appearing on the time-series panels.
7. Open PanicDuty and show the corresponding alerts.
8. Open Kibana in **Discover** and show the other observability pillar — per-event logs. Filter by `anomaly_window: true` to see, request by request, which inputs reached the model during the window (`industrial` neighborhoods, larger `square_meters`, missing `bedrooms`) and what predictions came back. This makes the article's **Section 9** point concrete: metrics summarize, logs explain.

That makes the article's core argument visible:

- healthy infrastructure is not the same as healthy model behavior,
- metrics + logs together cover the two pillars of observability: aggregates (Grafana) and per-event inspection (Kibana).


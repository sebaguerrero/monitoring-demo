# Root-level files — reference

The four orchestration-level files at the root of `monitoring_demo/` aren't owned by any single service — they describe how the services come together, where Caddy routes traffic in the public deployment, how to invoke the deployment in one command, and what env vars to set when you do. This document explains each of them in detail.

| File | Used by | Purpose |
|---|---|---|
| `docker-compose.yml` | `docker compose` | service definitions, ports, volumes, networks, the `poster` profile |
| `Caddyfile` | the Caddy container (poster only) | reverse-proxy mappings from public hostnames to internal services |
| `Makefile` | `make` | shortcuts for the long `docker compose --env-file --profile` invocation |
| `.env.poster.example` | reader / copy template | shape and defaults for the `.env.poster` file you create on the VM |

For per-folder documentation see the `README_EN.md` inside each subfolder.

## `docker-compose.yml`

### What it is
The main Compose file. Defines all services that make up the demo, the network they share, the named volumes Caddy uses, and the optional `poster` profile that switches on the public-facing reverse proxy.

### Role in the stack
Everything starts from here. `docker compose up` builds the two custom images (`model_api`, `panic_duty`), pulls the rest, sets up the `monitor_net` bridge network, mounts every config file from this repo into the right container, and starts the lot in the right order.

### Walkthrough — services

The file declares eleven services. Ten come up by default; the eleventh (`caddy`) sits behind `profiles: ["poster"]` and only starts when you opt in.

**`model_api`** *(custom build, port 8000)*

- `build: ./model_api` — Compose runs `docker build` on that folder. See [`model_api_en.md`](../model_api/docs/model_api_en.md).
- `ports: "8000:8000"` — exposes the API on the host. Useful locally; on the public VM the port isn't strictly needed because Caddy reaches it via the internal network.
- `environment:` — six demo knobs (`MODEL_VERSION`, `DEMO_BASE_RPS`, `DEMO_ANOMALY_INTERVAL_SECONDS`, `DEMO_ANOMALY_DURATION_SECONDS`, `DEMO_ANOMALY_FAILURE_RATE`, `DEMO_PREDICTION_STATS_WINDOW_SECONDS`) plus `GF_ADMIN_PASSWORD` (used by `model_api` to authenticate against Grafana when posting annotations on `bump_version`). The model API reads these at startup; see its README for what each does.

**`prometheus`** *(prom/prometheus:v2.45.0, port 9090)*

- Pinned to v2.45.0 (LTS at the time the demo was written).
- Mounts `./prometheus/prometheus.yml` and `./prometheus/rules.yml` into `/etc/prometheus/`. See [`prometheus_en.md`](../prometheus/docs/prometheus_en.md).
- `command: ['--config.file=/etc/prometheus/prometheus.yml']` — explicit override even though it matches the default; protects against future image versions changing the default location.
- `depends_on: model_api` — Compose starts `model_api` first. This is start order only, not a healthcheck, so Prometheus may scrape and miss for the first few seconds while the API is still booting.

**`alertmanager`** *(prom/alertmanager:v0.25.0, port 9093)*

- Mounts `./prometheus/alertmanager.yml` into `/etc/alertmanager/`. See [`prometheus_en.md`](../prometheus/docs/prometheus_en.md#alertmanageryml).
- Same explicit `--config.file` override pattern as Prometheus.

**`panic_duty`** *(custom build, port 8080)*

- `build: ./panic_duty`. See [`panic_duty_en.md`](../panic_duty/docs/panic_duty_en.md).

**`grafana`** *(grafana/grafana:10.0.3, port 3000)*

- Mounts both `./grafana/provisioning` (datasource + dashboard auto-load) and `./grafana/dashboards` (the JSON itself). See [`grafana_en.md`](../grafana/docs/grafana_en.md).
- `GF_SECURITY_ADMIN_PASSWORD: ${GF_ADMIN_PASSWORD:-admin}` — defaults to `admin` locally, overridable via the env var for the public deployment.
- `GF_AUTH_ANONYMOUS_ENABLED: true` — anonymous read-only access is on so QR scanners don't see a login page.
- `GF_AUTH_ANONYMOUS_ORG_ROLE: ${GF_ANONYMOUS_ROLE:-Admin}` — defaults to `Admin` locally (handy for editing dashboards) and is overridden to `Viewer` in the public deployment so anonymous visitors cannot edit anything.
- `depends_on: prometheus` — start order only.

**`elasticsearch`** *(docker.elastic.co/elasticsearch/elasticsearch:8.17.0, port 9200)*

- `discovery.type=single-node` — short-circuits cluster formation; required for a one-node demo.
- `xpack.security.enabled=false` — disables auth/TLS so the demo doesn't need certs and credentials. **Demo grade only.**
- `ES_JAVA_OPTS=-Xms512m -Xmx512m` — caps the JVM heap to 512 MB. Production runs would size this much higher; this size helps the full stack fit comfortably in ~3 GB of Docker RAM (actual usage ~2.6 GB).
- `healthcheck:` polls `/_cluster/health` every 10 s with a 5 s timeout, up to 12 retries. Other services that depend on Elasticsearch wait for this healthcheck to go green.

**`kibana`** *(docker.elastic.co/kibana/kibana:8.17.0, port 5601)*

- `ELASTICSEARCH_HOSTS=http://elasticsearch:9200` — service-name DNS over the bridge network.
- `depends_on: elasticsearch` with `condition: service_healthy` — Compose waits for the ES healthcheck before starting Kibana, which avoids Kibana's noisy "ES not yet ready" log spam during cold starts.

**`logstash`** *(`docker.elastic.co/logstash/logstash:8.17.0`, ports 5044 + 9600)*

- `image: docker.elastic.co/logstash/logstash:8.17.0` — official image, no local build. See [`logstash_en.md`](../logstash/docs/logstash_en.md).
- `volumes:` mounts three files as read-only volumes onto the official image:
  - `./logstash/config/logstash.yml` → `/usr/share/logstash/config/logstash.yml` (minimal runtime config: port 9600 for the monitoring API, X-Pack monitoring disabled).
  - `./logstash/pipeline/logstash.conf` → `/usr/share/logstash/pipeline/logstash.conf` (the pipeline: `beats` input, `grok`/`mutate`/`date` filters, Elasticsearch output).
  - `./elasticsearch/model-api-logs-template.json` → `/usr/share/logstash/templates/model-api-logs.json` (the index template). Logstash uploads it to Elasticsearch at startup via `manage_template => true` in its `elasticsearch` output — that's how the field mappings get installed without an external sidecar. See [`elasticsearch_en.md`](../elasticsearch/docs/elasticsearch_en.md).
- `ports: 5044, 9600` — 5044 is the `beats` input port (where Filebeat connects); 9600 is Logstash's monitoring HTTP API (`/_node/stats` to check the pipeline is alive).
- `depends_on: elasticsearch` with `condition: service_healthy` — Logstash waits for Elasticsearch to be ready before initializing its pipeline (and before issuing the template-registration call).
- `healthcheck:` polls `localhost:9600/_node/stats` every 10 s. Other services that depend on Logstash (Filebeat) wait until it goes healthy.

**`filebeat`** *(docker.elastic.co/beats/filebeat:8.17.0, no port)*

- `user: root` — required to read `/var/lib/docker/containers` and the Docker socket.
- `command: ["filebeat", "-e", "--strict.perms=false"]` — `-e` logs to stderr, `--strict.perms=false` allows the read-only mounted `filebeat.yml` to have non-Filebeat-owned ownership.
- Three volume mounts: `filebeat.yml` (the config, see [`filebeat_en.md`](../filebeat/docs/filebeat_en.md)), `/var/lib/docker/containers` (read-only, where Docker stores per-container log files), and `/var/run/docker.sock` (read-only, used by Docker autodiscover to learn about running containers).
- `depends_on:` Logstash healthy + `model_api` started. The Logstash gate guarantees the processor is ready to receive (and has already registered the index template against Elasticsearch) before Filebeat starts pushing logs.

**`kibana-init`** *(curlimages/curl:8.5.0, no port, runs once)*

- Mounts `./kibana/init` into `/init` and runs `sh /init/import.sh`. See [`kibana_en.md`](../kibana/docs/kibana_en.md).
- Three responsibilities: (1) create the `model-api-logs-*` data view and set it as default, (2) iterate over the files in `/init/saved_objects/*.json` and POST each saved object (2 Lens panels + 1 dashboard) via `/api/saved_objects/<type>/<id>?overwrite=true`, (3) exit.
- The direct POST avoids the automatic migrations of the `/_import` endpoint, which expect legacy schema fields and would break the current 8.x objects.
- `restart: "no"` — when the script finishes, the container exits and stays exited. `docker compose ps` shows it in `Exit 0` state, which is correct.

**`caddy`** *(caddy:2-alpine, ports 80/443, profile `poster` only)*

- `profiles: ["poster"]` — the gate that keeps Caddy out of local development. See [`Caddyfile`](#caddyfile) below.
- `ports: 80, 443` — Let's Encrypt's HTTP-01 challenge needs port 80; HTTPS needs 443.
- Mounts `./Caddyfile` read-only plus two named volumes (`caddy_data`, `caddy_config`) so Let's Encrypt certs and Caddy state survive container restarts. Without the volumes, every restart would re-issue certs and risk hitting Let's Encrypt's rate limits.
- `environment:` — `${GRAFANA_HOST:-}`, `${PROMETHEUS_HOST:-}`, `${ALERTMANAGER_HOST:-}`, `${PANICDUTY_HOST:-}`, `${API_HOST:-}`, `${KIBANA_HOST:-}`. The empty defaults silence Compose warnings during local `docker compose config` even though the values aren't usable; the poster deployment passes a real `.env.poster`.
- `depends_on:` the six services Caddy proxies to.

### Walkthrough — networks and volumes

- `networks.monitor_net.driver: bridge` — a single user-defined Docker bridge network. All services attach to it. Service-name DNS works only inside such a network (the default bridge does not provide DNS, which is why we declare our own).
- `volumes.caddy_data` and `volumes.caddy_config` — empty named-volume declarations, used only by the `caddy` service. `caddy_data` holds the Let's Encrypt certs + ACME account; `caddy_config` holds Caddy's runtime config cache. Persist across container restarts. Wipeable with `docker compose down -v`, but in the public deployment that re-issues all certs and counts against Let's Encrypt's rate limit (5 duplicate certs per hostname per 7 days), so `make poster-fresh` deliberately omits `-v` to keep them.

## `Caddyfile`

### What it is
Caddy's site config. Used only when the `poster` profile is active. Defines six virtual hosts that proxy to the six internal services Caddy needs to expose publicly.

### Role in the stack
Caddy reads this file at startup, expands the `{$VAR}` placeholders against its container environment, and configures itself accordingly. With the six host vars set to real sslip.io hostnames, Caddy:

- Listens on port 443 for each of those six hostnames.
- Requests a Let's Encrypt certificate per hostname on first request.
- Reverse-proxies traffic to the matching internal service.
- Listens on port 80 for the ACME HTTP-01 challenge during cert issuance, then redirects all other 80 traffic to HTTPS.

### Walkthrough

The file is six near-identical site blocks. Each block uses Caddy's compact form: hostname (or any address) followed by directives in braces.

```
{$GRAFANA_HOST} {
    reverse_proxy grafana:3000
}
```

- `{$GRAFANA_HOST}` — placeholder substituted from the container env. With `GRAFANA_HOST=grafana.18-204-12-50.sslip.io` set in `.env.poster`, this expands to that hostname at startup.
- `reverse_proxy grafana:3000` — Caddy's built-in proxy directive. `grafana` is the Compose service name, resolved on the `monitor_net` Docker network. Port 3000 is Grafana's internal port.

The six blocks differ only in env var name and upstream:

| Hostname placeholder | Upstream service:port | What it serves |
|---|---|---|
| `{$GRAFANA_HOST}` | `grafana:3000` | the unified dashboard |
| `{$PROMETHEUS_HOST}` | `prometheus:9090` | raw metrics + alert rules UI |
| `{$ALERTMANAGER_HOST}` | `alertmanager:9093` | Alertmanager UI (active alerts grouped) |
| `{$PANICDUTY_HOST}` | `panic_duty:8080` | mock incident UI |
| `{$API_HOST}` | `model_api:8000` | Model API (Swagger UI at `/docs`, health at `/health`) |
| `{$KIBANA_HOST}` | `kibana:5601` | log search |

## `Makefile`

### What it is
A small Makefile that wraps the most-typed Compose invocations — both the `docker compose --env-file .env.poster --profile poster ...` poster-deployment forms and a `fresh` target for the local development cycle. `make` is preinstalled on virtually every Linux/macOS environment, including the AWS EC2 Ubuntu image used in the poster deployment, so this is zero-dependency.

### Role in the stack
Optional but strongly recommended for the poster day workflow. The full Compose command is fiddly to type correctly under stress; `make poster-up` is hard to mistype.

### Walkthrough

```makefile
.PHONY: poster-up poster-down poster-logs poster-status poster-fresh es-reset-logs fresh
```

Declares all targets as *phony* — Make won't look for files of those names on disk, it will always run the recipe.

```makefile
poster-up:
	docker compose --env-file .env.poster --profile poster up -d --build
```

- `up -d --build` — builds the custom images, then starts the full stack in detached mode. `-d` returns control to the shell instead of streaming logs.

```makefile
poster-down:
	docker compose --env-file .env.poster --profile poster down
```

- Stops and removes the containers. Without `-v`, named volumes (Caddy certs, Caddy config) are preserved, which avoids re-issuing Let's Encrypt certs on the next `poster-up`.

```makefile
poster-logs:
	docker compose --env-file .env.poster --profile poster logs -f
```

- Tails logs from every service, following new lines (`-f`). Useful for "is anything actually arriving at PanicDuty" sanity checks.

```makefile
poster-status:
	docker compose --env-file .env.poster --profile poster ps
```

- One-line status per service. `docker compose ps` already does most of the work; the wrapper just keeps the env file and profile flags consistent.

```makefile
es-reset-logs:
	-curl -fsS -X DELETE 'http://localhost:9200/model-api-logs-*'
	@echo
	docker compose restart logstash filebeat
```

- The on-demand wipe target for the log indices ([`elasticsearch_en.md`](../elasticsearch/docs/elasticsearch_en.md)). Before, this was a sidecar that ran on every `docker compose up`; now it is a deliberate user action.
- Deletes every `model-api-logs-*` index and restarts Logstash + Filebeat. Restarting Logstash forces it to re-register the index template against the (now empty) namespace; restarting Filebeat triggers a re-ship into the freshly created index. The leading `-` on the `curl` line lets Make tolerate the case where there are no indices to delete.

```makefile
fresh:
	docker compose down -v --rmi local --remove-orphans
	docker compose up -d --build
```

- The "wipe everything and start over" target for local development. `down -v --rmi local --remove-orphans` stops every container, drops every volume (named and anonymous — including ES's data volume so old indices vanish), removes the locally built images so the next build re-reads the source, and clears stray containers from previous compose configs. `up -d --build` then rebuilds and starts the default stack detached. Operates on the default services (no `--profile poster`); use `poster-down` followed by `poster-up` for the public deployment workflow.

## `.env.poster.example`

### What it is
The committed template for the **gitignored** `.env.poster` file that a deployer creates on their VM. Contains every variable the public-deployment workflow expects, with placeholder values and inline comments.

### Role in the stack
Never read directly by `docker compose`. The deployer runs `cp .env.poster.example .env.poster`, edits the values, and `docker compose --env-file .env.poster ...` reads the resulting file. Compose substitutes the values into `${...}` placeholders in `docker-compose.yml` before any container starts.

### Walkthrough

The file has eight variables, split into two groups by purpose.

**Hostnames (six).** These tell Caddy which sslip.io subdomains to listen on, and `docker-compose.yml` injects them into the Caddy container's environment as `GRAFANA_HOST` etc.

```
GRAFANA_HOST=grafana.YOUR-IP-WITH-DASHES.sslip.io
PROMETHEUS_HOST=prometheus.YOUR-IP-WITH-DASHES.sslip.io
ALERTMANAGER_HOST=alertmanager.YOUR-IP-WITH-DASHES.sslip.io
PANICDUTY_HOST=panicduty.YOUR-IP-WITH-DASHES.sslip.io
API_HOST=api.YOUR-IP-WITH-DASHES.sslip.io
KIBANA_HOST=kibana.YOUR-IP-WITH-DASHES.sslip.io
```

The placeholder string `YOUR-IP-WITH-DASHES` reminds the deployer to substitute the VM's public IP with dots replaced by dashes (sslip.io's wildcard DNS expects either form, but the dashed form is more URL-friendly). For an instance at `18.204.12.50`, the hostname becomes `grafana.18-204-12-50.sslip.io`.

**Grafana hardening (two).**

```
GF_ANONYMOUS_ROLE=Viewer
GF_ADMIN_PASSWORD=change-me-to-something-strong
```

- `GF_ANONYMOUS_ROLE=Viewer` flips the anonymous access role from the local default (`Admin`) to read-only. Any visitor who scans a QR code lands as an anonymous Viewer and cannot edit dashboards or change the configuration.
- `GF_ADMIN_PASSWORD` overrides the local default `admin`. The `change-me-to-something-strong` placeholder is intentionally hard to mistake for a real password.


# Filebeat — file reference

Filebeat is the **log shipper** in the demo: it tails the plain-text log lines that `model_api` writes to stdout and forwards them to **Logstash**, which parses them, normalizes types, and ships them to Elasticsearch. The Filebeat container reads its configuration from `filebeat.yml` mounted into `/usr/share/filebeat/filebeat.yml`. There is exactly one file in this folder.

It's the standard production pattern: **Filebeat (lightweight shipper at the edge) → Logstash (heavy processor in the center) → Elasticsearch**.

## `filebeat.yml`

### What it is
The Filebeat agent's runtime configuration. It tells Filebeat which container logs to read, where to ship the events, and which noise fields to drop along the way.

### Role in the stack
- `model_api` writes one plain-text free-form line per prediction event to stdout (pedagogical decision — see [`logstash_en.md`](../../logstash/docs/logstash_en.md) for pipeline details).
- The Docker daemon captures stdout into per-container log files at `/var/lib/docker/containers/<id>/<id>-json.log`.
- Filebeat, running as a sibling container, mounts `/var/lib/docker/containers` read-only and the Docker socket read-only so it can discover containers by name.
- Filebeat reads the relevant log files and forwards each line (without parsing) to Logstash on TCP 5044.
- **Logstash** parses the plain text via `grok` and ships the resulting document to Elasticsearch.
- Kibana then reads from Elasticsearch via the `model-api-logs-*` data view that `kibana-init` provisions.

If this file is missing, malformed, or points at the wrong container name, no log events reach Logstash and Kibana's Discover view stays empty.

### Walkthrough

**`filebeat.autodiscover`** — Filebeat does not have a hardcoded path to watch. Instead, it asks Docker which containers are running and applies a *template* to any container that matches a condition. This is the right pattern in a Compose stack where container IDs are not known ahead of time.

- `providers[0].type: docker` — autodiscovery driven by Docker events.
- `hints.enabled: false` — we do not use Filebeat's per-container hint annotations; the matching is driven entirely by the template below.
- `templates[0].condition.contains.docker.container.name: "model_api"` — the template only applies to containers whose name contains the substring `model_api`. That single match is sufficient because the demo only ships logs from one service.
- `config[0].type: container` — Filebeat's container input handles Docker's JSON log file format natively (each line is `{"log": "...", "stream": "stdout", "time": "..."}`), unwrapping the inner payload before further parsing.
- `paths` — the Docker log file location pattern. `${data.container.id}` is filled in at runtime once a matching container is discovered.

**Note**: previously this input included `json.keys_under_root: true`, `json.add_error_key: true`, `json.overwrite_keys: true` to parse the JSON `model_api` used to emit. Those settings were **removed** because the API no longer emits JSON — it emits plain text. Logstash handles the parsing downstream.

**`processors`** — Filebeat applies these to every event before shipping.

- `add_docker_metadata: ~` — enriches each event with `container.id`, `container.name`, `container.image`. The processor stays enabled because Filebeat's autodiscover provider relies on its lookup, but the resulting `container.*` subtree is dropped immediately afterward (see below).
- `drop_fields: { fields: [...], ignore_missing: true }` — removes fields that Filebeat or the container input add by default but that add noise to the event. The list is split into two groups:
  - **Filebeat / input metadata**: `agent` describes Filebeat itself; `ecs` is the Elastic Common Schema version metadata; `input` describes which Filebeat input fired; `host` is the host machine info; `stream` is `stdout` vs `stderr`.
  - **Docker metadata**: `container` drops the entire `container.*` subtree (`id`, `name`, `image.name`, `labels`); `docker` removes the duplicate `docker.container.*` subtree; `log.offset` and `log.file.path` are Filebeat tail-bookkeeping fields that have no value in queries.
  - `ignore_missing: true` prevents an error if a field happens not to be present.

**`output.logstash`** — where to send events.

- `hosts: ["logstash:5044"]` — Docker DNS resolves `logstash` to the Logstash container on the `monitor_net` network. TCP 5044 is the standard `beats` input port on Logstash.

**Note**: previously there was an `output.elasticsearch` (with `index: "model-api-logs-..."`, `setup.ilm.enabled: false`, `setup.template.enabled: false`). Those settings were **removed** because the destination is now Logstash, not Elasticsearch directly. Logstash is now responsible for emitting the final `index` **and** for registering the index template (via `manage_template => true` in its `elasticsearch` output).

**`logging.level: info` / `logging.to_files: false`** — Filebeat's own diagnostic logs go to stdout at INFO level, which Compose then captures. That's why `docker logs filebeat` shows the agent's own activity.


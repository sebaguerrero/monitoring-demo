# Elasticsearch — file reference

Elasticsearch is the **log store** in the demo: it indexes the JSON documents **Logstash** ships (after parsing the plain text that comes from Filebeat) and serves them to Kibana for queries. The Elasticsearch container itself runs from the official Elastic image with no custom configuration. This folder owns one piece of setup applied against that container:

- an **index template** that fixes the mapping of string fields (kills `.keyword` multi-field noise, makes search case-insensitive, gives error fields enough room for full descriptive sentences). The template is registered automatically by **Logstash** (`manage_template => true` in its `elasticsearch` output) the first time its pipeline starts; the JSON file in this folder is mounted into the Logstash container and uploaded via `PUT /_index_template/model-api-logs`.

It replaces what would otherwise be Elasticsearch's default dynamic mapping — which produces noisy, duplicated, case-sensitive fields that are tedious to query.

## `model-api-logs-template.json`

### What it is
An Elasticsearch [index template](https://www.elastic.co/guide/en/elasticsearch/reference/current/index-templates.html) that matches `model-api-logs-*` and fixes how every field in those indices is mapped. The template is registered in Elasticsearch via `PUT /_index_template/model-api-logs` and applies to any newly created index that matches the pattern (existing indices keep whatever mapping they were created with).

### Role in the stack
Without this template, ES uses default dynamic mapping, which:

- maps every string as a `text` field with a `.keyword` multi-field — every string field shows up twice in Kibana's document view (`endpoint` and `endpoint.keyword`, `level` and `level.keyword`, etc.),
- is case-sensitive on the `.keyword` side, so `level : info` does not match `INFO`,
- doubles inverted-index storage on string fields.

The template replaces that default with rules tailored to the kind of data the API actually emits — short identifiers and enums plus a couple of free-text fields where word-inside search matters.

### Walkthrough

**`index_patterns`** — the template applies only to indices whose name matches `model-api-logs-*`. Logstash creates those at `model-api-logs-YYYY.MM.DD` once a day (via its `output.elasticsearch.index`).

**`settings.analysis.normalizer.lowercase_normalizer`** — defines a custom `keyword` normalizer that runs the built-in `lowercase` token filter at index time and at query time. This is what enables case-insensitive matching on `keyword` fields. A `keyword` field with this normalizer attached stores `INFO` as `info` in the inverted index and rewrites query terms the same way, so all of `level : INFO`, `level : info`, `level : Info` match the same documents.

**`mappings.properties`** — explicit per-field overrides. Explicit `properties` always win over the dynamic templates below. The template declares **8 overrides** split across strings, numerics, and nested:

Strings:

- `error_message` — `keyword` with `ignore_above: 4096`. No multi-field, no `.keyword` subfield, no normalizer. The error text is a plain-English sentence (e.g. `"Prediction failed during an anomaly window. Anomalous signals: latency was 546ms (typical 15-50ms); square_meters was 450, unusually large (typical 80-260); neighborhood was 'industrial', unusual (typical 'suburb' or 'rural'). Missing on request: bedrooms. Cause: Synthetic anomaly triggered while scoring the model."`); `keyword` keeps its casing intact and supports exact-match queries and aggregations. The 4096-char ceiling leaves room for the longest realistic message — multiple anomalous-signal clauses plus a missing-feature list plus the cause — without being truncated. Trade-off: word-inside search (`error_message : anomaly`) won't match — use a wildcard query (`error_message : *anomaly*`) instead.
- `summary` — `keyword` with `ignore_above: 1024`. Carries the human-readable description on successful predictions (`"prediction within expected ranges"` or `"anomalous prediction: high latency 898ms; …"`). Mapped explicitly so the dynamic template's lowercase normalizer doesn't strip casing. Aggregations on `summary` group identical-text outcomes together — e.g. counting how many predictions fell into "within expected ranges" vs. specific anomaly patterns.
- `missing_features` — `keyword` (no normalizer). It's an array of strings (`["bedrooms"]`, `[]`, etc.) that Logstash builds from splitting the log's `missing` field. No normalizer because feature names are fixed lowercase identifiers that don't need rewriting.

Numerics:

- `prediction` — `float`. The predicted price in USD.
- `latency_ms` — `float`. Request latency in milliseconds.

Nested `features.*`:

- `features.square_meters` — `float`. Square meters from the request.
- `features.bedrooms` — `integer`. Number of bedrooms from the request.
- `features.neighborhood` — `keyword` with `lowercase_normalizer`. Same behavior as the dynamic template, declared explicitly so it lives inside the nested `features.*` subtree.

**`mappings.dynamic_templates[0].strings_as_keyword`** — applies to every string field that does not have an explicit override above:

- `match_mapping_type: string` — the rule fires whenever ES would otherwise map a string. ES's date detection on `@timestamp` runs first, so the timestamp stays a `date` and is not affected.
- `mapping.type: keyword` — collapse the default `text` + `.keyword` multi-field into a single `keyword` mapping. The result in Kibana: one row per string field instead of two.
- `mapping.ignore_above: 1024` — keyword fields longer than 1024 characters stop being indexed (the value is still stored in `_source` and visible in the document view, just not searchable past that point). This caps storage and protects against a runaway log line filling the inverted index.
- `mapping.normalizer: lowercase_normalizer` — applies the lowercase normalizer defined in settings. Every string field that falls through to the dynamic template gets case-insensitive matching for free.

### What this means for queries

| Field | Mapping source | Mapping | Case-insensitive search? | Aggregation case |
|---|---|---|---|---|
| `error_message` | explicit override | `keyword` (ignore_above 4096) | no — exact match only | original case |
| `summary` | explicit override | `keyword` (ignore_above 1024) | no — exact match only | original case |
| `missing_features` | explicit override | `keyword` | no — exact match only | original case (values are already lowercase by construction) |
| `features.neighborhood` | explicit override | `keyword` + `lowercase_normalizer` | yes | lowercase |
| `prediction`, `latency_ms`, `features.square_meters` | explicit override | `float` | n/a | n/a |
| `features.bedrooms` | explicit override | `integer` | n/a | n/a |
| `level`, `endpoint`, `event_type`, `request_id`, `model_version`, `logger` | dynamic_template | `keyword` (ignore_above 1024) + `lowercase_normalizer` | yes | lowercase |
| `@timestamp` | dynamic (ES date detection) | `date` | n/a | n/a |
| `http_status` | dynamic (Logstash converts to int) | `long` | n/a | n/a |
| `anomaly_window`, `internal` | dynamic (Logstash converts to boolean) | `boolean` | n/a | n/a |

## How the template gets registered

The template is installed by **Logstash itself** at startup, not by a sidecar. The `output { elasticsearch { ... } }` block in [`logstash/pipeline/logstash.conf`](../../logstash/pipeline/logstash.conf) declares:

```ruby
manage_template => true
template => "/usr/share/logstash/templates/model-api-logs.json"
template_name => "model-api-logs"
template_overwrite => true
```

The JSON file in this folder is mounted into Logstash via a Docker volume (`./elasticsearch/model-api-logs-template.json:/usr/share/logstash/templates/model-api-logs.json:ro`). When the Logstash pipeline initializes its Elasticsearch output, it issues `PUT /_index_template/model-api-logs` **before accepting any events** from the filter stage. That call is idempotent — `template_overwrite => true` makes it replace any previous version of the same template.

In Compose terms:

- `logstash` declares `depends_on: elasticsearch (service_healthy)` so the pipeline doesn't initialize until ES is reachable.
- `filebeat` declares `depends_on: logstash (service_healthy)` so no log is shipped before Logstash is ready — by the time Filebeat starts pushing, Logstash has already registered the template.

### Resetting the index without re-deploying

The template only applies to **newly created** indices — mappings are immutable once an index exists. If you change the template (or just want a clean slate during the demo), use the Makefile targets at the root of `monitoring_demo/`:

- `make es-reset-logs` — `DELETE /model-api-logs-*` and restart Logstash + Filebeat. Logstash re-uploads the template at startup, the next document creates a fresh index with the current mapping.
- `make fresh` — full `docker compose down -v` followed by `up -d --build`. Volumes (including ES data) are deleted, so the cluster comes up empty and Logstash registers the template against an empty ES.

This replaces the previous behavior where a sidecar (`es-init`) deleted indices on every cold start. Now the wipe is **on demand**: each `docker compose up` is **not** automatically destructive, and indices accumulate across restarts until you explicitly reset.


# Logstash

Logstash sits as the **intermediate processor** between Filebeat and Elasticsearch. It receives plain-text free-form logs from the API and normalizes them to structured JSON before indexing.

## Logstash's 5 responsibilities and which we use here

| Responsibility | Use in the demo |
|---|---|
| **Ingestion** | Yes — `beats` input on TCP 5044 |
| **Parsing** | Yes — `grok` over the `message` field |
| **Transformation** | Yes — `mutate convert/rename/lowercase` to normalize types and field names |
| **Enrichment** | No — earlier iterations had derived fields here, removed because the same queries are achievable without precomputation |
| **Routing** | No — all events to `model-api-logs-*` (for pedagogical simplicity) |

## Pipeline (`pipeline/logstash.conf`)

The pipeline has a single parsing-and-normalization stage:

`grok` matches **two alternative patterns**:

- **Success** (`event_type=prediction`): includes `missing`, `prediction`, `summary`.
- **Error** (`event_type=prediction_failed`): includes `msg`.

Both patterns accept `null` as an alternative for `sqm`, `br`, and `nbhd` — the API emits `null` for any feature that was absent from the request, so the grok must not fail when those fields aren't numeric. **The handling is asymmetric**:

- For `sqm` and `br`, the alternation `(?:%{NUMBER:sqm:float}|null)` (and analogous for `br`) does NOT capture when the literal `null` matches. The field is simply absent from the resulting event.
- For `nbhd`, the pattern is `%{WORD:nbhd_raw}` (no alternation). `%{WORD}` matches `null` as a regular string, so `nbhd_raw="null"` IS captured, and a separate cleanup step (see below) is needed to drop it.

Without these accommodations, a log line like `... sqm=null br=3 nbhd=suburb missing=square_meters ...` would tag the event with `_grokparsefailure` and Logstash would drop it (the conditional `if "_grokparsefailure" in [tags] { drop {} }` removes any line that doesn't match either pattern).

Then it applies:
- `mutate convert` to convert types (string → boolean for `anomaly_window`, `internal`).
- `mutate rename` to restructure to nested: `sqm → features.square_meters`, etc.
- **Post-rename `null`-cleanup for nbhd**: `if [features][neighborhood] == "null" { remove_field => "[features][neighborhood]" }`. This is what makes nbhd symmetric with sqm/br — when neighborhood was missing, the field ends up absent from the event, just like the other two. Without this step, missing-neighborhood events would contain the literal string `"null"` in `[features][neighborhood]`, breaking downstream filtering.
- `missing_features`: if `missing_raw == "none"` adds an empty array (`add_field`); otherwise `split` by `","` and rename `missing_raw → missing_features`.
- `date filter` to parse the timestamp from the log and use it as `@timestamp` (not Filebeat's).

#### Examples

> The example inputs are illustrative of the grok pattern. The demo only generates traffic where `bedrooms` may be missing (during anomaly); `sqm` and `nbhd` are always present in synthetic traffic, but the grok still handles `null` for external clients that omit fields.

Successful request in normal mode (all features present):
```
... sqm=180 br=3 nbhd=suburb missing=none prediction=289456.78 summary="prediction within expected ranges"
```
After processing: `features.{square_meters: 180, bedrooms: 3, neighborhood: "suburb"}`, `missing_features: []`.

Successful request during anomaly with `bedrooms` missing:
```
... sqm=420 br=null nbhd=industrial missing=bedrooms prediction=1380540 summary="anomalous prediction: input drift: square_meters=420 (typical 80-260); neighborhood='industrial'; missing features: bedrooms"
```
After processing: `features.{square_meters: 420, neighborhood: "industrial"}` (no `bedrooms` key), `missing_features: ["bedrooms"]`.

Failed request during anomaly (70% of requests fail):
```
... sqm=510 br=2 nbhd=industrial msg="Prediction failed during an anomaly window. Anomalous signals: latency was 612ms (typical 15-50ms); square_meters was 510, unusually large (typical 80-260); neighborhood was 'industrial', unusual (typical 'suburb' or 'rural'). Cause: Synthetic anomaly triggered while scoring the model."
```
After processing: `features.{square_meters: 510, bedrooms: 2, neighborhood: "industrial"}`, `error_message: "..."`, `event_type: "prediction_failed"`.

## Quick verification

```bash
# Healthcheck: pipeline alive
curl -s http://localhost:9600/_node/stats | jq '.pipelines.main.events'

# Any grok parse failures? (events tagged with _grokparsefailure are dropped,
# so they never reach ES. Check pipeline counters or Logstash logs instead.)
curl -s http://localhost:9600/_node/stats \
  | jq '.pipelines.main.events | {in, out, dropped: (.in - .out)}'

# Or grep grok failure messages directly in Logstash's logs
docker logs logstash 2>&1 | grep -c "_grokparsefailure"

# Latency tail (≥500ms requests)
curl -s 'localhost:9200/model-api-logs-*/_search?q=latency_ms:>=500&size=0' \
  | jq '.hits.total.value'

# Predictions with input outside the training range (manual KQL example)
curl -s 'localhost:9200/model-api-logs-*/_search?q=features.square_meters:>300%20AND%20event_type:prediction&size=0' \
  | jq '.hits.total.value'
```

## How to extend the pipeline

- **Add derived fields**: edit `pipeline/logstash.conf` and add a `mutate { add_field => { ... } }` block before the cleanup stage. Remember to add the mapping in [`elasticsearch/model-api-logs-template.json`](../../elasticsearch/model-api-logs-template.json) if the type is not string or boolean. (Earlier iterations of the demo had derived fields here — they were removed because the same queries are achievable without precomputation.)
- **Add external lookups**: use the `translate` filter (maps value → value from a YAML/CSV) or `jdbc_streaming` (queries a DB).
- **Conditional routing**: change the `output` block to `if [event_type] == "prediction_failed" { ... } else { ... }` with different `index`.
- **Persistent queue** (resilience against ES outages): add `queue.type: persisted` in `config/logstash.yml` + Docker volume.

## Troubleshooting

| Symptom | Likely cause | How to verify |
|---|---|---|
| Events tagged `_grokparsefailure` | Log doesn't match either pattern | Look at the raw `message`; adjust grok pattern |
| Missing `features.neighborhood` field | Grok pattern didn't extract `nbhd_raw` correctly | `curl localhost:9200/model-api-logs-*/_search?size=1` and inspect the document |
| Pipeline receives nothing | Filebeat doesn't point to Logstash | Check `filebeat/filebeat.yml` → `output.logstash.hosts` |
| Logstash won't start | Broken `.conf` syntax | `docker compose logs logstash` |

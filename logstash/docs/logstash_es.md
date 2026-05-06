# Logstash

Logstash actúa como **procesador intermedio** entre Filebeat y Elasticsearch. Recibe los logs en texto plano free-form que emite la API y los normaliza a JSON estructurado antes de indexarlos.

## Las 5 responsabilidades de Logstash y cuáles usamos acá

| Responsabilidad | Uso en la demo |
|---|---|
| **Ingesta** | Sí — input `beats` en TCP 5044 |
| **Parsing** | Sí — `grok` sobre el campo `message` |
| **Transformación** | Sí — `mutate convert/rename/lowercase` para normalizar tipos y nombres de campo |
| **Enriquecimiento** | No — iteraciones anteriores tenían campos derivados acá, se sacaron porque las mismas queries se logran sin precómputo |
| **Routing** | No — todos los eventos a `model-api-logs-*` (por simplicidad pedagógica) |

## Pipeline (`pipeline/logstash.conf`)

El pipeline tiene un único stage de parsing y normalización:

`grok` matchea **dos patterns** alternativos:

- **Éxito** (`event_type=prediction`): incluye `missing`, `prediction`, `summary`.
- **Error** (`event_type=prediction_failed`): incluye `msg`.

Ambos patterns aceptan `null` como alternativa para `sqm`, `br` y `nbhd` — la API emite `null` para cualquier feature ausente del request, así que el grok no puede fallar cuando esos campos no son numéricos. **El manejo es asimétrico**:

- Para `sqm` y `br`, la alternación `(?:%{NUMBER:sqm:float}|null)` (y análoga para `br`) NO captura el campo cuando matchea el literal `null`. El campo simplemente queda ausente del evento resultante.
- Para `nbhd`, el pattern es `%{WORD:nbhd_raw}` (sin alternación). `%{WORD}` matchea `null` como un string normal, así que `nbhd_raw="null"` SÍ se captura, y un cleanup separado (ver abajo) se encarga de dropearlo.

Sin estas acomodaciones, una línea como `... sqm=null br=3 nbhd=suburb missing=square_meters ...` taggearía el evento con `_grokparsefailure` y Logstash lo dropearía (el conditional `if "_grokparsefailure" in [tags] { drop {} }` elimina cualquier línea que no matchee ningún pattern).

Después aplica:
- `mutate convert` para convertir tipos (string → boolean para `anomaly_window`, `internal`).
- `mutate rename` para reestructurar a nested: `sqm → features.square_meters`, etc.
- **Cleanup post-rename de `null` para nbhd**: `if [features][neighborhood] == "null" { remove_field => "[features][neighborhood]" }`. Esto es lo que hace simétrico nbhd con sqm/br — cuando neighborhood faltó, el campo termina ausente del evento, igual que los otros dos. Sin este paso, los eventos con neighborhood faltante contendrían el string literal `"null"` en `[features][neighborhood]`, rompiendo el filtrado downstream.
- `missing_features`: si `missing_raw == "none"` agrega un array vacío (`add_field`); si no, hace `split` por `","` y renombra `missing_raw → missing_features`.
- `date filter` para parsear el timestamp del log y usarlo como `@timestamp` (no el de Filebeat).

#### Ejemplos

> Los inputs de los ejemplos son ilustrativos del grok pattern. La demo solo genera tráfico donde `bedrooms` puede faltar (durante anomalía); `sqm` y `nbhd` siempre están presentes en el tráfico sintético, pero el grok igual maneja `null` para clientes externos que omitan campos.

Request exitoso en modo normal (todas las features presentes):
```
... sqm=180 br=3 nbhd=suburb missing=none prediction=289456.78 summary="prediction within expected ranges"
```
Después del procesamiento: `features.{square_meters: 180, bedrooms: 3, neighborhood: "suburb"}`, `missing_features: []`.

Request exitoso durante anomalía con `bedrooms` faltante:
```
... sqm=420 br=null nbhd=industrial missing=bedrooms prediction=1380540 summary="anomalous prediction: input drift: square_meters=420 (typical 80-260); neighborhood='industrial'; missing features: bedrooms"
```
Después del procesamiento: `features.{square_meters: 420, neighborhood: "industrial"}` (sin la key `bedrooms`), `missing_features: ["bedrooms"]`.

Request fallido durante anomalía (70% de las requests fallan):
```
... sqm=510 br=2 nbhd=industrial msg="Prediction failed during an anomaly window. Anomalous signals: latency was 612ms (typical 15-50ms); square_meters was 510, unusually large (typical 80-260); neighborhood was 'industrial', unusual (typical 'suburb' or 'rural'). Cause: Synthetic anomaly triggered while scoring the model."
```
Después del procesamiento: `features.{square_meters: 510, bedrooms: 2, neighborhood: "industrial"}`, `error_message: "..."`, `event_type: "prediction_failed"`.

## Verificación rápida

```bash
# Healthcheck: pipeline activo
curl -s http://localhost:9600/_node/stats | jq '.pipelines.main.events'

# ¿Hubo grok parse failures? (los eventos con _grokparsefailure se dropean,
# así que NO aparecen en ES. Hay que revisar contadores del pipeline o logs.)
curl -s http://localhost:9600/_node/stats \
  | jq '.pipelines.main.events | {in, out, dropped: (.in - .out)}'

# O buscar mensajes de grok fail directamente en los logs de Logstash
docker logs logstash 2>&1 | grep -c "_grokparsefailure"

# Cola de latencia (≥500ms)
curl -s 'localhost:9200/model-api-logs-*/_search?q=latency_ms:>=500&size=0' \
  | jq '.hits.total.value'

# Predicciones con input fuera del rango de training (ejemplo KQL manual)
curl -s 'localhost:9200/model-api-logs-*/_search?q=features.square_meters:>300%20AND%20event_type:prediction&size=0' \
  | jq '.hits.total.value'
```

## Cómo extender el pipeline

- **Agregar campos derivados**: editar `pipeline/logstash.conf` y agregar un bloque `mutate { add_field => { ... } }` antes del stage de limpieza. Recordar agregar el mapping en [`elasticsearch/model-api-logs-template.json`](../../elasticsearch/model-api-logs-template.json) si el tipo no es string ni boolean. (Iteraciones anteriores de la demo tenían campos derivados acá — se sacaron porque las mismas queries se logran sin precómputo.)
- **Agregar lookups externos**: usar el filtro `translate` (mapea valor → valor desde un YAML/CSV) o `jdbc_streaming` (consulta una DB).
- **Routing condicional**: cambiar el bloque `output` por `if [event_type] == "prediction_failed" { ... } else { ... }` con `index` distintos.
- **Persistent queue** (resiliencia ante caídas de ES): agregar `queue.type: persisted` en `config/logstash.yml` + volumen Docker.

## Troubleshooting

| Síntoma | Causa probable | Cómo verificar |
|---|---|---|
| Eventos con tag `_grokparsefailure` | El log no matchea ninguno de los 2 patterns | Ver el `message` raw del evento; ajustar grok pattern |
| Falta el campo `features.neighborhood` | Pattern grok no extrajo `nbhd_raw` correctamente | `curl localhost:9200/model-api-logs-*/_search?size=1` e inspeccionar el documento |
| Pipeline no recibe nada | Filebeat no apunta a Logstash | Verificar `filebeat/filebeat.yml` → `output.logstash.hosts` |
| Logstash no arranca | Sintaxis del `.conf` rota | `docker compose logs logstash` |

# Elasticsearch — referencia de archivos

Elasticsearch es el **almacén de logs** de la demo: indexa los documentos JSON que shipa **Logstash** (después de parsear el texto plano que viene de Filebeat) y los sirve a Kibana para consultas. El contenedor Elasticsearch corre desde la imagen oficial de Elastic sin configuración custom. Esta carpeta contiene una pieza de setup aplicada contra ese contenedor:

- un **template de índice** que fija el mapping de los campos string (mata el ruido del multi-field `.keyword`, hace búsquedas case-insensitive, da a los campos de error suficiente espacio para frases descriptivas completas). El template lo registra automáticamente **Logstash** (`manage_template => true` en su output `elasticsearch`) la primera vez que arranca el pipeline; el archivo JSON de esta carpeta se monta dentro del contenedor de Logstash y se sube vía `PUT /_index_template/model-api-logs`.

Reemplaza lo que sería el mapping dinámico default de Elasticsearch — que produce campos ruidosos, duplicados y case-sensitive que son tediosos de consultar.

## `model-api-logs-template.json`

### Qué es
Un [template de índice](https://www.elastic.co/guide/en/elasticsearch/reference/current/index-templates.html) de Elasticsearch que matchea `model-api-logs-*` y fija cómo se mapea cada campo en esos índices. El template se registra en Elasticsearch vía `PUT /_index_template/model-api-logs` y se aplica a cualquier índice nuevo creado que matchee el patrón (los índices existentes mantienen el mapping con el que fueron creados).

### Rol en el stack
Sin este template, ES usa mapping dinámico default, que:

- mapea cada string como un campo `text` con un multi-field `.keyword` — cada campo string aparece dos veces en la vista de documentos de Kibana (`endpoint` y `endpoint.keyword`, `level` y `level.keyword`, etc.),
- es case-sensitive del lado `.keyword`, así que `level : info` no matchea `INFO`,
- duplica el almacenamiento del inverted index sobre los campos string.

El template reemplaza ese default con reglas hechas a medida del tipo de datos que la API realmente emite — identificadores cortos y enums más un par de campos de texto libre donde la búsqueda por palabra-adentro importa.

### Recorrido

**`index_patterns`** — el template aplica solo a índices cuyo nombre matchee `model-api-logs-*`. Logstash los crea como `model-api-logs-YYYY.MM.DD` una vez por día (a través de su `output.elasticsearch.index`).

**`settings.analysis.normalizer.lowercase_normalizer`** — define un normalizer custom para `keyword` que corre el token filter integrado `lowercase` al indexar y al consultar. Esto es lo que habilita matching case-insensitive en campos `keyword`. Un campo `keyword` con este normalizer adjunto guarda `INFO` como `info` en el inverted index y reescribe los términos de query igual, así que `level : INFO`, `level : info`, `level : Info` matchean los mismos documentos.

**`mappings.properties`** — overrides explícitos por campo. Las `properties` explícitas siempre ganan contra los dynamic templates de abajo. El template declara **8 overrides** divididos en strings, numéricos y nested:

Strings:

- `error_message` — `keyword` con `ignore_above: 4096`. Sin multi-field, sin subfield `.keyword`, sin normalizer. El texto del error es una frase en lenguaje natural (ej. `"Prediction failed during an anomaly window. Anomalous signals: latency was 546ms (typical 15-50ms); square_meters was 450, unusually large (typical 80-260); neighborhood was 'industrial', unusual (typical 'suburb' or 'rural'). Missing on request: bedrooms. Cause: Synthetic anomaly triggered while scoring the model."`); `keyword` mantiene la caja intacta y soporta queries de match exacto y agregaciones. El techo de 4096 caracteres deja espacio para el mensaje más largo realista — múltiples cláusulas de anomalous-signal más una lista de missing más la causa — sin truncar. Trade-off: la búsqueda por palabra-adentro (`error_message : anomaly`) no matchea — usar wildcard (`error_message : *anomaly*`).
- `summary` — `keyword` con `ignore_above: 1024`. Lleva la descripción legible en predicciones exitosas (`"prediction within expected ranges"` o `"anomalous prediction: high latency 898ms; …"`). Mapeado explícitamente para que el normalizer en lowercase del dynamic template no le quite la caja. Las agregaciones sobre `summary` agrupan resultados de texto idéntico — ej. contar cuántas predicciones cayeron en "within expected ranges" vs. patrones específicos de anomalía.
- `missing_features` — `keyword` (sin normalizer). Es un array de strings (`["bedrooms"]`, `[]`, etc.) que Logstash construye al splitear el campo `missing` del log. Sin normalizer porque los nombres de feature son identificadores fijos en lowercase y no necesitan reescritura.

Numéricos:

- `prediction` — `float`. El precio predicho en USD.
- `latency_ms` — `float`. Latencia del request en milisegundos.

Nested `features.*`:

- `features.square_meters` — `float`. Superficie en m² del request.
- `features.bedrooms` — `integer`. Cantidad de habitaciones del request.
- `features.neighborhood` — `keyword` con `lowercase_normalizer`. Mismo comportamiento que el dynamic template, declarado explícitamente para que quede dentro del subárbol nested `features.*`.

**`mappings.dynamic_templates[0].strings_as_keyword`** — aplica a cada campo string que no tenga un override explícito arriba:

- `match_mapping_type: string` — la regla se dispara cuando ES de otro modo mapearía un string. La detección de fechas de ES sobre `@timestamp` corre primero, así que el timestamp queda como `date` y no se ve afectado.
- `mapping.type: keyword` — colapsa el multi-field default `text` + `.keyword` a un solo mapping `keyword`. El resultado en Kibana: una fila por campo string en lugar de dos.
- `mapping.ignore_above: 1024` — campos keyword más largos que 1024 caracteres dejan de indexarse (el valor sigue almacenado en `_source` y visible en la vista de documentos, simplemente no es buscable más allá de ese punto). Esto pone un techo al storage y protege contra una línea de log descontrolada llenando el inverted index.
- `mapping.normalizer: lowercase_normalizer` — aplica el normalizer en lowercase definido en settings. Cada campo string que cae al dynamic template obtiene matching case-insensitive gratis.

### Qué significa esto para las queries

| Campo | Origen del mapping | Mapping | ¿Búsqueda case-insensitive? | Caja de agregación |
|---|---|---|---|---|
| `error_message` | override explícito | `keyword` (ignore_above 4096) | no — solo match exacto | caja original |
| `summary` | override explícito | `keyword` (ignore_above 1024) | no — solo match exacto | caja original |
| `missing_features` | override explícito | `keyword` | no — solo match exacto | caja original (los valores ya son lowercase por construcción) |
| `features.neighborhood` | override explícito | `keyword` + `lowercase_normalizer` | sí | lowercase |
| `prediction`, `latency_ms`, `features.square_meters` | override explícito | `float` | n/a | n/a |
| `features.bedrooms` | override explícito | `integer` | n/a | n/a |
| `level`, `endpoint`, `event_type`, `request_id`, `model_version`, `logger` | dynamic_template | `keyword` (ignore_above 1024) + `lowercase_normalizer` | sí | lowercase |
| `@timestamp` | dynamic (date detection de ES) | `date` | n/a | n/a |
| `http_status` | dynamic (Logstash convierte a int) | `long` | n/a | n/a |
| `anomaly_window`, `internal` | dynamic (Logstash convierte a boolean) | `boolean` | n/a | n/a |

## Cómo se registra el template

El template lo instala **Logstash mismo** al arrancar, no un sidecar. El bloque `output { elasticsearch { ... } }` en [`logstash/pipeline/logstash.conf`](../../logstash/pipeline/logstash.conf) declara:

```ruby
manage_template => true
template => "/usr/share/logstash/templates/model-api-logs.json"
template_name => "model-api-logs"
template_overwrite => true
```

El JSON de esta carpeta se monta dentro de Logstash mediante un volumen de Docker (`./elasticsearch/model-api-logs-template.json:/usr/share/logstash/templates/model-api-logs.json:ro`). Cuando el pipeline de Logstash inicializa su output de Elasticsearch, ejecuta `PUT /_index_template/model-api-logs` **antes de aceptar cualquier evento** del stage de filtros. Esa llamada es idempotente — `template_overwrite => true` hace que reemplace cualquier versión previa del mismo template.

En términos de Compose:

- `logstash` declara `depends_on: elasticsearch (service_healthy)` así el pipeline no inicializa hasta que ES esté alcanzable.
- `filebeat` declara `depends_on: logstash (service_healthy)` así ningún log se shipea antes de que Logstash esté listo — para cuando Filebeat empieza a empujar, Logstash ya registró el template.

### Resetear el índice sin re-deployar

El template solo aplica a índices **recién creados** — los mappings son inmutables una vez que un índice existe. Si cambiás el template (o simplemente querés un estado limpio durante la demo), usá los targets del Makefile en la raíz de `monitoring_demo/`:

- `make es-reset-logs` — `DELETE /model-api-logs-*` y reinicia Logstash + Filebeat. Logstash re-sube el template al arrancar, y el próximo documento crea un índice fresco con el mapping actual.
- `make fresh` — `docker compose down -v` completo seguido de `up -d --build`. Los volúmenes (incluyendo los datos de ES) se borran, así que el cluster levanta vacío y Logstash registra el template contra un ES vacío.

Esto reemplaza el comportamiento previo donde un sidecar (`es-init`) borraba índices en cada arranque en frío. Ahora el wipe es **on demand**: cada `docker compose up` **no** es automáticamente destructivo, y los índices se acumulan entre reinicios hasta que vos resetees explícitamente.


# Filebeat — referencia de archivos

Filebeat es el **log shipper** de la demo: lee las líneas de texto plano que `model_api` escribe a stdout y las reenvía a **Logstash**, que las parsea, normaliza tipos y las envía a Elasticsearch. El contenedor de Filebeat lee su configuración desde `filebeat.yml` montado en `/usr/share/filebeat/filebeat.yml`. Hay exactamente un archivo en esta carpeta.

Es el patrón estándar en producción: **Filebeat (shipper liviano en el borde) → Logstash (procesador pesado en el centro) → Elasticsearch**.

## `filebeat.yml`

### Qué es
La configuración de runtime del agente Filebeat. Le indica qué logs de contenedores leer, dónde enviarlos y qué campos descartar como ruido.

### Rol en el stack
- `model_api` escribe una línea de texto plano free-form por evento de predicción a stdout (decisión pedagógica — ver [`logstash_es.md`](../../logstash/docs/logstash_es.md) para detalles del pipeline).
- El daemon de Docker captura stdout en archivos por contenedor en `/var/lib/docker/containers/<id>/<id>-json.log`.
- Filebeat, corriendo como contenedor hermano, monta `/var/lib/docker/containers` en solo lectura y el socket de Docker en solo lectura para descubrir contenedores por nombre.
- Filebeat lee los archivos de log relevantes y reenvía cada línea (sin parsear) a Logstash en TCP 5044.
- **Logstash** parsea el texto plano con `grok` y manda el documento resultante a Elasticsearch.
- Kibana lee desde Elasticsearch vía el data view `model-api-logs-*` que provisiona `kibana-init`.

Si este archivo falta, está mal formado o apunta al nombre de contenedor equivocado, ningún evento de log llega a Logstash y la vista Discover de Kibana queda vacía.

### Recorrido

**`filebeat.autodiscover`** — Filebeat no tiene un path hardcodeado para mirar. En cambio, le pregunta a Docker qué contenedores están corriendo y aplica un *template* a cualquier contenedor que cumpla una condición. Es el patrón correcto para un stack Compose donde los IDs de contenedor no se conocen de antemano.

- `providers[0].type: docker` — autodescubrimiento manejado por eventos de Docker.
- `hints.enabled: false` — no usamos las anotaciones de hints por contenedor; el matching está totalmente manejado por el template de abajo.
- `templates[0].condition.contains.docker.container.name: "model_api"` — el template solo aplica a contenedores cuyo nombre contiene `model_api`. Ese único match alcanza porque la demo solo shipa logs desde un servicio.
- `config[0].type: container` — el input `container` de Filebeat maneja nativamente el formato JSON de los archivos de log de Docker (cada línea es `{"log": "...", "stream": "stdout", "time": "..."}`), desempaquetando el payload interno antes de seguir.
- `paths` — el patrón de ubicación del archivo de log de Docker. `${data.container.id}` se completa en runtime cuando un contenedor matcheante es descubierto.

**Nota**: Antes este input incluía `json.keys_under_root: true`, `json.add_error_key: true`, `json.overwrite_keys: true` para parsear el JSON que `model_api` emitía. Esos settings fueron **removidos** porque la API ya no emite JSON — emite texto plano. Logstash hace el parsing río abajo.

**`processors`** — Filebeat aplica estos a cada evento antes de shipearlo.

- `add_docker_metadata: ~` — enriquece cada evento con `container.id`, `container.name`, `container.image`. El processor sigue habilitado porque el provider de autodescubrimiento de Filebeat depende de su lookup, pero el subárbol `container.*` resultante se descarta inmediatamente después (ver abajo).
- `drop_fields: { fields: [...], ignore_missing: true }` — elimina campos que Filebeat o el input `container` agregan por defecto pero que suman ruido al evento. La lista se divide en dos grupos:
  - **Metadata de Filebeat / input**: `agent` describe a Filebeat; `ecs` es la metadata de versión del Elastic Common Schema; `input` describe qué input de Filebeat se disparó; `host` es info de la máquina host; `stream` es `stdout` vs `stderr`.
  - **Metadata de Docker**: `container` elimina el subárbol `container.*` entero (`id`, `name`, `image.name`, `labels`); `docker` elimina el subárbol duplicado `docker.container.*`; `log.offset` y `log.file.path` son campos de bookkeeping del tail de Filebeat que no aportan valor en consultas.
  - `ignore_missing: true` previene un error si un campo no está presente.

**`output.logstash`** — adónde mandar los eventos.

- `hosts: ["logstash:5044"]` — el DNS de Docker resuelve `logstash` al contenedor de Logstash en la red `monitor_net`. TCP 5044 es el puerto estándar del input `beats` de Logstash.

**Nota**: Antes existía un `output.elasticsearch` (con `index: "model-api-logs-..."`, `setup.ilm.enabled: false`, `setup.template.enabled: false`). Esos settings fueron **removidos** porque ahora el destino es Logstash, no Elasticsearch directo. Logstash es el responsable de generar el `index` final **y** de registrar el template de índice (vía `manage_template => true` en su output `elasticsearch`).

**`logging.level: info` / `logging.to_files: false`** — los logs de diagnóstico de Filebeat van a stdout en nivel INFO, que Compose captura. Por eso `docker logs filebeat` muestra la actividad del agente.


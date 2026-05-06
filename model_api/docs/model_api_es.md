# Model API — referencia de archivos

El Model API es el **servicio que se monitorea** en la demo. Es una aplicación FastAPI sintética que:

- expone `POST /predict` para devolver una predicción falsa de precio de casa,
- expone `POST /admin/bump_version` para simular el deploy de una nueva versión del modelo (alimenta la annotation de deploy en Grafana),
- exporta métricas Prometheus en `GET /metrics`,
- escribe una línea de texto plano free-form por predicción a stdout (consumida por Filebeat → Logstash → Elasticsearch → Kibana),
- genera su propio tráfico de fondo para que los dashboards tengan datos sin necesidad de clientes externos,
- entra periódicamente en una *ventana de anomalía* durante la cual inputs, latencia, error rate, rango de predicción, tasa de features faltantes y uso de CPU se desplazan visiblemente para que cada alerta de la demo se dispare en cadencia regular.

La carpeta contiene tres archivos:

- `app.py` — la aplicación entera.
- `Dockerfile` — receta de build de la imagen.
- `requirements.txt` — dependencias Python.

## `app.py`

### Qué es
Una única aplicación FastAPI que combina cuatro responsabilidades: una API HTTP, un exportador de métricas Prometheus, un logger en texto plano y tres hilos de fondo que generan tráfico, muestrean uso de recursos del proceso y bumpean la versión del modelo periódicamente.

### Rol en el stack
- Buildeada en un contenedor por `docker-compose.yml` (`build: ./model_api`).
- Escucha en el puerto 8000. Prometheus scrapea `model_api:8000/metrics` cada 5 s. Caddy proxea HTTPS externo (solo en deployment del póster) hacia Grafana / Prometheus / PanicDuty / Kibana — *no* hacia la propia API, que queda interna.
- El **texto plano** de stdout es capturado por el daemon Docker, leído por Filebeat, mandado a Logstash (que lo parsea con `grok`, normaliza tipos y reestructura los campos a nested), y finalmente indexado en Elasticsearch.

### Recorrido

El archivo está organizado top-to-bottom en este orden: imports → setup de logging texto plano → definiciones de métricas Prometheus → config por env vars → estado global de app → funciones helper (detección de anomalía, síntesis de features, normalización, predicción) → workers de hilos de fondo → app FastAPI + lifespan + rutas.

#### Imports

Standard library: `logging`, `os`, `random`, `shutil`, `statistics`, `sys`, `threading`, `time`, `uuid`, `collections.deque`, `contextlib.asynccontextmanager`, `datetime`. Third-party: `psutil` (para métricas de proceso), `fastapi`, `prometheus_client`.

#### Logging texto plano

`PlainTextFormatter(logging.Formatter)` sobreescribe `format()` para emitir una **cadena free-form** por log record con timestamp ISO-like, nivel, logger name, y todos los `extra_fields` formateados como pares `key=value`. Tiene dos branches según el `event_type`:

- **`event_type == "prediction"`** (éxito) → `... missing=<csv> prediction=<num> summary="..."` al final.
- **`event_type == "prediction_failed"`** (error) → `... msg="..."` al final.

El formato fue diseñado para ser parseable por dos patterns alternativos de `grok` en Logstash. Las strings con espacios (como `summary` y `msg`) se quotean con `"..."` y las comillas dobles internas se escapan a `'`.

`_configure_logging()` reemplaza los handlers del root logger con un único `StreamHandler(sys.stdout)` usando `PlainTextFormatter`. Nivel INFO. La línea a nivel módulo `logger = _configure_logging()` corre al import time, así que cada llamada subsecuente pasa por el formatter, incluyendo los propios logs de FastAPI.

Este es el *único* logging que hace la aplicación. No hay output a archivo separado, ni rotación, ni syslog. Docker captura stdout; Filebeat lee desde ahí. Patrón 12-factor estándar.

#### Definiciones de métricas Prometheus

Cada métrica refleja un panel o alerta del dashboard / reglas.

| Métrica | Tipo | Labels | Propósito |
|---|---|---|---|
| `api_requests_total` | Counter | `endpoint`, `http_status` | conteo de requests, desglosado por status; potencia la alerta de error rate |
| `api_request_duration_seconds` | Histogram (buckets 0.01–4.0 s) | `endpoint` | distribución de latencia; `_sum/_count` da la media usada por la alerta y panel de latencia |
| `ml_prediction_value` | Histogram (buckets 100k–1.5M) | ninguno | precios predichos; el histograma deja al dashboard computar P95 vía `histogram_quantile()` y a la alerta usar la media móvil vía `_sum/_count` |
| `ml_input_square_meters` | Histogram (buckets 50–650) | ninguno | distribución de input para la feature `square_meters` |
| `ml_input_bedrooms` | Histogram (buckets 0–8) | ninguno | distribución de input para `bedrooms` |
| `ml_input_neighborhood_total` | Counter | `neighborhood` | un counter por categoría; potencia el panel *Neighborhood Mix* |
| `ml_missing_feature_total` | Counter | `feature` | incrementa cuando una feature está faltando en un request entrante |
| `model_api_process_cpu_percent` | Gauge | ninguno | CPU% en vivo, muestreado cada segundo |
| `model_api_process_resident_memory_bytes` | Gauge | ninguno | RSS en bytes |
| `model_api_process_disk_utilization_percent` | Gauge | ninguno | porcentaje de llenado del filesystem en `/` |
| `ml_prediction_mean_recent` | Gauge | ninguno | media sobre la ventana móvil |
| `ml_prediction_median_recent` | Gauge | ninguno | mediana sobre la ventana móvil |
| `ml_prediction_min_recent` | Gauge | ninguno | mínimo sobre la ventana móvil |
| `ml_prediction_max_recent` | Gauge | ninguno | máximo sobre la ventana móvil |
| `ml_prediction_stddev_recent` | Gauge | ninguno | stddev poblacional sobre la ventana móvil |
| `ml_model_info` | Gauge | `version`, `trained_at` | expone la metadata del modelo activo como labels en un gauge constante-1 (el gauge de la versión anterior se setea a 0). Re-publicado en cada `POST /admin/bump_version` |
| `model_deployments_total` | Counter | ninguno | número de llamadas a `bump_version` desde el startup. Alimenta la annotation de marca de deploy del dashboard vía un POST directo al endpoint `/api/annotations` de Grafana (ver `_post_grafana_annotation()` en `app.py`) |

Los histogramas exponen `_sum`, `_count` y `_bucket{le="..."}` por bucket automáticamente — el dashboard usa los tres.

#### Config por env vars

| Env var | Default | Significado |
|---|---|---|
| `MODEL_VERSION` | `v1.1.0-demo` | reportada en `/health`, respuestas de `/predict` y la métrica `ml_model_info` |
| `DEMO_BASE_RPS` | 8 | tasa de tráfico sintético, mínimo 1 RPS |
| `DEMO_ANOMALY_INTERVAL_SECONDS` | 30 | duración del modo normal antes de cada ventana de anomalía, mínimo 10 s |
| `DEMO_ANOMALY_DURATION_SECONDS` | 30 | duración de cada ventana de anomalía, mínimo 5 s |
| `DEMO_ANOMALY_FAILURE_RATE` | 0.2 (en código) / 0.7 (en compose) | probabilidad de que un request falle *durante* una ventana de anomalía; clamped a [0, 0.95] |
| `DEMO_PREDICTION_STATS_WINDOW_SECONDS` | 300 | duración de la ventana móvil para los gauges `_recent`, mínimo 30 s |

Los clamps son cinturón y tirantes — previenen foot-guns obvios como configurar una duración de anomalía más corta que un único scrape.

#### Estado global

- `app_state` mantiene `start_time` (usado por la matemática de la ventana de anomalía) y `stop_event` (un `threading.Event` que los workers de fondo miran para shutdown limpio).
- `prediction_history: deque` de tuplas `(timestamp, value)`; la ventana móvil para los gauges `_recent`.
- `prediction_history_lock: threading.Lock` — requerido porque la deque es mutada desde el hilo de tráfico de fondo *y* el hilo de manejo de requests; el default de FastAPI es rutas sync que corren en un thread pool.
- `NEIGHBORHOODS` y `NEIGHBORHOOD_MULTIPLIERS` definen las cuatro categorías y sus multiplicadores de precio por barrio (suburb 1.0×, downtown 1.25×, rural 0.82×, industrial 1.55×). Los multiplicadores son por qué el shift de *Neighborhood Mix* hacia `industrial` durante anomalía empuja las predicciones hacia arriba.

#### `is_anomaly_window()`

El truco que hace la demo ágil sin coordinación externa. Computa cuánto tiempo lleva corriendo el proceso, módulo `(interval + duration)`. Devuelve true cuando la posición dentro del ciclo está pasada `interval`. Con defaults: ciclo de 60 segundos, 30 s normal + 30 s anomalía — elegido para que el banner de PanicDuty alterne visiblemente entre verde y rojo con una cadencia regular. Función pura del tiempo monotónico — el ciclo nunca driftea y sobrevive cambios de horario de verano.

#### `build_synthetic_features(anomaly_active)`

Produce el dict de input sintético para el generador de tráfico de fondo. Distribuciones distintas por modo:

- **Normal**: `square_meters ∈ [80, 260]`, `bedrooms` siempre presente (`randint(1, 5)`), barrio desde `("suburb", "suburb", "downtown", "rural")` — 50 % suburb, 25 % downtown, 25 % rural.
- **Anomalía**: `square_meters ∈ [320, 580]`, `bedrooms` ~65 % presente (1–7) y 35 % `None`, barrio desde `("industrial", "industrial", "industrial", "downtown")` — 75 % industrial, 25 % downtown.

El peso del barrio se hace poblando una tupla con repeticiones y haciéndole `random.choice()`. Barato y legible.

#### `normalize_features(payload, anomaly_active, internal)`

El mismo pipeline ya sea que el request venga del tráfico interno o sea un `POST /predict` real. Pasos:

1. Si es interno o no hay payload, generar features sintéticas. Si no, leer las que estén presentes de `square_meters`/`bedrooms`/`neighborhood`.
2. Para cada una de esas tres features, si está faltando/vacía, incrementar `ml_missing_feature_total{feature=...}` y recordar el nombre en una lista `missing`.
3. Observar histogramas `ml_input_square_meters` / `ml_input_bedrooms` cuando esos valores están presentes.
4. Validar `neighborhood` contra `NEIGHBORHOODS`. Si es desconocido o falta, imputar con la **moda del training set** (`NEIGHBORHOOD_TRAIN_MODE = "suburb"`). Siempre incrementar `ml_input_neighborhood_total{neighborhood=...}`.
5. Imputar valores faltantes con la **mediana / moda del training set** (constantes `SQM_TRAIN_MEDIAN = 170.0`, `BEDROOMS_TRAIN_MEDIAN = 3`, `NEIGHBORHOOD_TRAIN_MODE = "suburb"`). La imputación con mediana/moda mantiene el valor sustituido **dentro del rango que el modelo vio en training** — así el modelo no tiene que extrapolar sobre un punto fabricado. Los valores se calculan offline desde el generador de tráfico normal en `build_synthetic_features`. En la práctica solo el fallback de `bedrooms` se dispara (es la única feature que el generador sintético omite, ~35% durante anomalía); los otros dos son código defensivo para requests externos al `POST /predict`. Estas constantes alimentan `perform_prediction`; los histogramas ya fueron observados (o saltados) arriba con los valores *reales*, así que el counter de feature faltante y los histogramas de input reflejan correctamente lo que vino.
6. Construir un dict separado `presented_features` que refleja lo que vino: valores reales donde están presentes, `None` donde faltan. Es el dict que usa la línea de log, así que `sqm`/`br`/`nbhd` siempre coinciden con la lista `missing` — los valores imputados viven sólo dentro del `normalized` que alimenta la predicción.
7. Devuelve `(normalized_features, presented_features, missing_list)`.

#### `perform_prediction(features, anomaly_active)`

Donde la predicción se "computa" y donde vive el comportamiento patológico de la demo durante ventanas de anomalía:

1. `time.sleep(15–50 ms)` — latencia base.
2. **Si anomalía**: sleep extra de 450–850 ms + chance `random.random() < ANOMALY_FAILURE_RATE` de lanzar `RuntimeError`. **Si normal**: nada extra.
3. Computar el precio: `square_meters * 1800 + bedrooms * 12000`, multiplicado por el multiplicador del barrio, más ruido uniforme en [-15k, 15k].
4. **Si anomalía**: agregar otro offset uniforme de 180k–420k.
5. Floor en 50k (para que el bound inferior del histograma nunca se cruce).

El offset de anomalía es lo que dispara `PredictionDriftDetected`. La inyección de error dispara `ElevatedApiErrorRate`. El sleep de latencia de la rama de anomalía dispara `HighApiLatency`. Una función, tres alertas.

#### `record_prediction_statistics(prediction)`

Append `(now, prediction)` a `prediction_history`, evict entradas viejas pasada la ventana móvil, después recomputa los cinco gauges `_recent` (`fmean`, `median`, `min`, `max`, `pstdev`). Protegido por lock. `pstdev` devuelve 0 para `len(values) <= 1` (la stdlib raisearía si no).

#### `describe_prediction(features, prediction, latency_ms, missing)`

Construye el string `summary` legible que se emite en predicciones exitosas. Devuelve `"prediction within expected ranges"` cuando nada parece sospechoso, si no `"anomalous prediction: <issues>"` donde los issues son flags heurísticos sobre los valores *observables* (no el flag sintético `anomaly_window`): `latency_ms > 250` (vs. 15–50 ms en modo normal), cualquier `missing_features`, `square_meters > 300` (vs. rango normal 80–260), `prediction > 1_300_000` (vs. ~110k–830k normal). Los thresholds caen en el gap entre las distribuciones normal y de anomalía para que un lector del log reconozca valores raros sin tener que mirar el flag sintético.

#### `describe_failure(features, latency_ms, missing, exc, anomaly_active)`

Construye el string `error_message` legible que se emite en predicciones fallidas. Output en lenguaje natural de la forma:

```
Prediction failed during an anomaly window. Anomalous signals: latency was 546ms (typical 15-50ms); square_meters was 450, unusually large (typical 80-260); neighborhood was 'industrial', unusual (typical 'suburb' or 'rural'). Missing on request: bedrooms. Cause: Synthetic anomaly triggered while scoring the model.
```

Estructura: una cláusula líder (`Prediction failed during an anomaly window` o `…during normal operation`); cuando los features no se pudieron normalizar, una sola oración diciéndolo; si no, una cláusula `Anomalous signals:` listando cuáles de los mismos tres thresholds observables que `describe_prediction` se dispararon (`latency_ms > 250`, `square_meters > 300`, `neighborhood == "industrial"`) más una cláusula `Missing on request:` si faltaron features; finalmente `Cause: <exc>` con el texto de la excepción subyacente. El nombre de la clase de excepción (`RuntimeError`, `KeyError`, …) se omite intencionalmente — el texto de la excepción en sí es más informativo para un lector humano que el nombre de la clase. `bedrooms` no tiene su propia entrada de signal anómalo porque los rangos normal y de anomalía de bedrooms se superponen mucho — un valor de 2 o 6 no es confiablemente anómalo por sí solo.

#### `execute_prediction(payload, internal)`

El ciclo completo de un request, usado por tráfico interno y `POST /predict`:

1. Generar un `request_id` (UUID hex).
2. Snapshot del estado de anomalía.
3. **Try**: normalize → predict → observar `ml_prediction_value` → recomputar estadísticos → incrementar `api_requests_total{http_status="200"}` → llamar a `describe_prediction()` → loguear un evento `prediction` en texto plano con contexto completo (incluyendo el `summary`) → devolver `(200, body)`.
4. **Except**: incrementar `api_requests_total{http_status="500"}` → llamar `describe_failure()` para armar el `error_message` en prosa → loguear un evento `prediction_failed` en texto plano → devolver `(500, body)`. No se captura traceback; la cláusula `Cause: …` en prosa es la única representación de texto de la falla.
5. **Finally**: observar `api_request_duration_seconds` independientemente del éxito.

El payload que se loguea (vía `extra_fields`) es lo que hace útil a Kibana después de que Logstash lo parsee. Cada evento lleva `event_type`, `request_id`, `endpoint`, `http_status`, `latency_ms`, `model_version`, `anomaly_window`, `internal`, `features`. Campos solo en éxito: `summary`, `prediction`, `missing_features`. Campos solo en falla: solo `error_message`. El flag `internal: false` separa los `POST /predict` reales del tráfico de fondo — el filtro `internal: false` en Kibana muestra lo que vieron los usuarios reales.

**Importante**: aunque las llamadas `logger.info(extra={"extra_fields": {...}})` siguen siendo idénticas a la versión vieja con JSON, el `PlainTextFormatter` ahora serializa esos dicts a una cadena estilo `key=value` en lugar de a JSON. Logstash hace el camino inverso con `grok` para reconstruir la estructura. Los nombres de campos finales en Elasticsearch son los mismos que cuando emitíamos JSON directo.

El nombre del campo `event_type` es intencional: ECS reserva el namespace `event`. La separación `summary` / `error_message` (en lugar de un único `message` compartido) mantiene los nombres de campo auto-descriptivos y permite que el index template los mapee distinto — `summary` es corto, `error_message` admite hasta 4096 caracteres (ver [`elasticsearch/model-api-logs-template.json`](../../elasticsearch/model-api-logs-template.json)).

#### Workers de fondo (tres hilos)

- `generate_traffic()` llama a `execute_prediction(internal=True)` a ritmo 1 / `BASE_RPS` hasta que `stop_event` se setea.
- `sample_resources()` actualiza los tres gauges de proceso cada segundo usando `psutil.Process(os.getpid())`. La primera llamada `process.cpu_percent(interval=None)` antes del loop es un priming deliberado — `psutil` devuelve 0.0 la primera vez, después porcentajes reales en llamadas siguientes.
- `auto_bump_version()` ejecuta cada 900 s (15 min) el mismo bloque inline que el handler `POST /admin/bump_version`: incrementa `bump_count`, computa la nueva metadata vía `_make_version_meta()`, llama `_set_model_info()`, incrementa `MODEL_DEPLOYMENTS` y postea la annotation a Grafana vía `_post_grafana_annotation()`. La annotation de marca de deploy aparece en el dashboard sin intervención manual.

#### Lifespan

El reemplazo de FastAPI 0.100+ para `@app.on_event("startup")` / `"shutdown"`. El cuerpo `@asynccontextmanager` corre *antes* del yield (startup) y *después* del yield (shutdown).

- **Startup**: reset de `start_time`, clear de `stop_event`, set inicial de la metadata del modelo vía `_set_model_info(_make_version_meta(0))` (que produce `version="1.0.0-demo"` desde la fórmula de abajo), spawn de los tres hilos de fondo como daemons.
- **Shutdown**: set de `stop_event`, `join(timeout=2)` cada hilo para permitir que los loops salgan limpiamente.

#### App FastAPI y rutas

- `GET /metrics` — llama a `generate_latest()` y devuelve los bytes con `text/plain; version=0.0.4; charset=utf-8` (el content type estándar de Prometheus, expuesto por `prometheus_client.CONTENT_TYPE_LATEST`).
- `GET /health` — objeto JSON con `status`, `model_version`, `anomaly_window`. Útil para liveness probes y para narrar la demo ("miren cómo `anomaly_window` flipea entre true y false").
- `POST /predict` — acepta un body JSON opcional (el generador sintético funciona igual), pasa por `execute_prediction`, devuelve la respuesta con el status code correcto.
- `POST /admin/bump_version` — incrementa un `bump_count` interno y recomputa la versión del modelo vía `_make_version_meta(bump_count)`. La fórmula es `major = 1 + count // 10; minor = count % 10; patch = "0-demo"` — entonces la sucesión es `1.0.0-demo → 1.1.0-demo → ... → 1.9.0-demo → 2.0.0-demo → ... → 2.9.0-demo → 3.0.0-demo → ...`, con `trained_at` ciclando por diez fechas de `_TRAINED_DATES`. Cada llamada setea los labels viejos del gauge a 0 y los nuevos a 1 (el patrón de `Gauge`), después incrementa `model_deployments_total`. El counter existe exclusivamente para alimentar la annotation de deploy en Grafana. Narración para la demo: "voy a desplegar una versión nueva ahora — miren la línea vertical aparecer en todos los gráficos".

## `Dockerfile`

### Qué es
La receta de build de la imagen. Casi idéntica a `panic_duty/Dockerfile`.

### Rol en el stack
Buildeada cuando corre `docker compose up --build`. Compose lee `build: ./model_api` desde `docker-compose.yml`.

### Recorrido

- `FROM python:3.10-slim` — misma base que `panic_duty`. La variante slim pesa ~80 MB comprimida.
- `WORKDIR /app` — directorio operativo para los pasos siguientes y el runtime.
- `COPY requirements.txt .` luego `RUN pip install --no-cache-dir -r requirements.txt` — capa separada para deps así editar `app.py` no las reinstala. `--no-cache-dir` mantiene el cache de wheels fuera de la imagen.
- `COPY . .` — el resto del contexto de build (`app.py`, `requirements.txt`, lo que sea que esté en `model_api/`).
- `EXPOSE 8000` — metadata declarativa. Compose igual mapea el puerto.
- `CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]` — servidor ASGI Uvicorn. `--no-access-log` suprime la línea de log por request; solo los eventos en texto plano emitidos por la app llegan a stdout, lo que mantiene la pipeline de Logstash limpia.

## `requirements.txt`

### Qué es
Lista de cuatro paquetes, sin pins de versión.

### Rol en el stack
Leído por `pip install` durante el build de Docker.

### Recorrido

- **`fastapi`** — framework web. Provee `FastAPI`, `Body`, `Response`, `JSONResponse`. Async por default pero las rutas acá son handlers sync que corren en el thread pool.
- **`uvicorn`** — servidor ASGI. Arrancado por el `CMD` del Dockerfile. Instalación pelada (sin extras `[standard]`) es suficiente para HTTP plano.
- **`prometheus-client`** — cliente Python oficial de Prometheus. Provee `Counter`, `Gauge`, `Histogram`, `Info`, `generate_latest`, `CONTENT_TYPE_LATEST`. Mantiene los valores de métricas en un registry local al proceso.
- **`psutil`** — utilidades multiplataforma de proceso y sistema. Usado para `process.cpu_percent()` y `process.memory_info().rss`.

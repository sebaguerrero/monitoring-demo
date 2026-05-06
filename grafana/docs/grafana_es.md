# Grafana — referencia de archivos

Grafana es la **capa de dashboards**. Lee métricas de Prometheus y las renderiza como paneles en un único dashboard llamado *ML System Dashboard*. Grafana 10.0.3 (la versión pinned en `docker-compose.yml`) soporta **provisioning** — configuración declarativa cargada desde disco al arrancar. Los dos archivos bajo `provisioning/` le dicen a Grafana que auto-cargue un datasource Prometheus y un dashboard desde JSON local, así que la demo nunca requiere clickear el wizard de primer uso de Grafana.

Esta carpeta tiene tres archivos:

- `provisioning/datasources/datasource.yml` — registra Prometheus como datasource default.
- `provisioning/dashboards/dashboard.yml` — le dice a Grafana dónde buscar archivos JSON de dashboards.
- `dashboards/ml_dashboard.json` — el dashboard en sí: 24 paneles en tres secciones (6 baldosas stat + 6 timeseries DevOps + 12 paneles de métricas ML).

## `provisioning/datasources/datasource.yml`

### Qué es
Un manifiesto de provisioning de datasources de Grafana. Grafana scanea `/etc/grafana/provisioning/datasources/` al arrancar y crea lo que esté declarado ahí antes de aceptar requests en la UI.

### Rol en el stack
- Montado en el contenedor de Grafana en `/etc/grafana/provisioning/datasources/datasource.yml`.
- Sin él, Grafana arranca sin datasource y cada panel muestra "No data source selected." Un presentador tendría que agregar Prometheus a mano en cada demo nueva.

### Recorrido

- `apiVersion: 1` — marker requerido en cada archivo de provisioning de Grafana.
- `datasources[0].name: Prometheus` — el nombre legible que aparece en las queries de paneles.
- `type: prometheus` — el plugin Prometheus integrado de Grafana.
- `url: http://prometheus:9090` — el DNS de Docker resuelve `prometheus` al contenedor de Prometheus en `monitor_net`. El puerto 9090 es el default de Prometheus.
- `access: proxy` — Grafana proxea las queries por su servidor en lugar de pedirle al navegador del usuario que vaya directo a Prometheus. Esto es lo que permite que el hostname "prometheus" resuelva (el navegador no tendría forma de alcanzarlo).
- `isDefault: true` — todo panel que no especifique un datasource toma este. El JSON del dashboard depende de esto.

## `provisioning/dashboards/dashboard.yml`

### Qué es
Un manifiesto de provisioning de dashboards de Grafana. Le dice a Grafana que scanee una carpeta buscando archivos JSON de dashboards e importe cada uno.

### Rol en el stack
- Montado en el contenedor de Grafana en `/etc/grafana/provisioning/dashboards/dashboard.yml`.
- Maneja la auto-carga de `ml_dashboard.json`. Sin él, el dashboard existe como archivo pero Grafana nunca lo ve.

### Recorrido

- `apiVersion: 1` — marker de manifiesto de provisioning.
- `providers[0].name: 'Dashboards'` — nombre interno del provider. Libre.
- `orgId: 1` — la organización default de Grafana.
- `folder: ''` — string vacío pone el dashboard en la carpeta *General* (raíz) de la UI de Grafana.
- `type: file` — cargar desde archivos locales, no desde una fuente remota.
- `disableDeletion: false` — un usuario puede borrar el dashboard desde la UI (igual lo re-provisionaría en el próximo restart).
- `updateIntervalSeconds: 10` — Grafana re-scanea el path cada 10 segundos. Editar `ml_dashboard.json` con el stack corriendo aplica el cambio sin restart, lo que es conveniente al iterar paneles.
- `options.path: /var/lib/grafana/dashboards` — adónde Grafana mira. El archivo Compose monta `./grafana/dashboards/` en ese path.

## `dashboards/ml_dashboard.json`

### Qué es
La definición completa del dashboard. El JSON de dashboard de Grafana 10 es verboso; lo que importa acá es la estructura (3 secciones, 24 paneles) y el PromQL detrás de cada panel — ambos documentados abajo. Editar el archivo directamente está soportado pero el flujo más fácil es: editar en la UI de Grafana → "Save JSON to file" → pegar de vuelta sobre este archivo → commit.

### Rol en el stack
La fuente única de verdad de lo que ven los usuarios. Cada métrica que `model_api` exporta o aparece acá directamente o se referencia a través de una de las reglas de alerta (que potencian la sección superior).

### Estructura del dashboard

El dashboard se divide en **tres rows** (encabezados de sección colapsables en Grafana). Cada row agrupa paneles relacionados temáticamente.

#### Row 0 — Alert Status Overview (6 baldosas stat)

Cinco de las baldosas reflejan las reglas en [`prometheus/rules.yml`](../../prometheus/docs/prometheus_es.md); la sexta (`Process CPU`) es una stat informativa de salud sin alerta correspondiente (útil para notar si el host del API está saturado, incluso cuando ninguna regla está disparando). Las baldosas usan la feature de *coloreado por umbral* de Grafana: verde cuando el valor está debajo del umbral, rojo cuando está arriba. Esto da una lectura visual muy rápida — todas verdes significa "todo bien", cualquier baldosa roja nombra exactamente qué condición se disparó.

| Baldosa | PromQL | Umbral |
|---|---|---|
| **House Price Predictor** | `up{job="house_price_predictor"}` | rojo si <1 (target caído), verde en 1 |
| **Predict Latency** | `sum(rate(api_request_duration_seconds_sum{endpoint="/predict"}[15s])) / sum(rate(api_request_duration_seconds_count{endpoint="/predict"}[15s]))` | rojo arriba de 0.35 s |
| **Predict Error Rate** | `sum(rate(api_requests_total{endpoint="/predict",http_status="500"}[15s])) / sum(rate(api_requests_total{endpoint="/predict"}[15s]))` | rojo arriba de 8 % |
| **Process CPU** | `avg_over_time(model_api_process_cpu_percent[15s])` | informativo (sin umbral, color fixed green) |
| **Missing Features** | `sum(rate(ml_missing_feature_total[15s]))` | rojo arriba de 0.5/s |
| **Avg Prediction** | `sum(rate(ml_prediction_value_sum[15s])) / sum(rate(ml_prediction_value_count[15s]))` | rojo arriba de `$600K` (formateado USD) |

Todos los paneles basados en rate del dashboard usan `[15s]` — los stat tiles de alerta, sus timeseries apareados, el histograma de predicciones, y los paneles de inputs ML (Square Meters, Bedrooms, Neighborhood Mix). La ventana de `[15s]` matchea la cadencia de anomalía de 30 s: una ventana de 15 s entra limpia dentro de una sola fase, así que durante anomalía los paneles se desplazan visiblemente hacia la derecha (predicciones de precio alto, square_meters más grandes, neighborhood `industrial`) y vuelven al lugar durante la fase normal. Una ventana más larga como `[1m]` siempre mezclaría ambas fases y ocultaría el efecto del drift. Los gauges crudos sin ventana (Process Memory, Disk Utilization, Prediction Median/Min/Max/Standard Deviation) son valores escalares sampleados cada 5 s. Process CPU es el caso intermedio: el gauge `model_api_process_cpu_percent` se sampea cada 5 s, pero los paneles lo muestran a través de `avg_over_time(...[15s])` para suavizar el ruido del sampling — tiene ventana de 15 s aunque no sea `rate()`.

Importante: las baldosas de alerta se ponen rojas apenas el PromQL cruza el umbral, *sin* el delay `for: 5s` que esperan las reglas reales. Así que el dashboard se pone rojo unos segundos antes de que la alerta llegue a `Firing`.

#### Row 1 — DevOps Metrics (6 paneles)

El lado de operaciones del artículo (Sección 7).

| Panel | Tipo | Qué muestra |
|---|---|---|
| **Predict Request Rate** | timeseries | `sum(rate(api_requests_total{endpoint="/predict"}[15s]))` — predicciones por segundo en el tiempo |
| **Predict Latency** | timeseries | latencia media `[15s]` filtrada por `endpoint="/predict"`, con línea de umbral en 0.35 s. Misma expresión que la baldosa stat de Row 0; acá se ve como serie histórica para ver la onda cuadrada normal/anomalía |
| **Predict Error Rate** | timeseries | porcentaje `[15s]` de respuestas 500 filtrado por `endpoint="/predict"`, con umbral 8 %. Misma expresión que la baldosa stat de Row 0; acá se ve como serie histórica |
| **Process CPU** | timeseries | `avg_over_time(model_api_process_cpu_percent[15s])` — visibilidad operativa, sin alerta asociada |
| **Process Memory** | timeseries | `model_api_process_resident_memory_bytes` |
| **Disk Utilization** | timeseries | `model_api_process_disk_utilization_percent` |

#### Row 2 — ML Metrics (12 paneles)

El lado de data science del artículo (Sección 6). Estos paneles leen tanto los *gauges de estadísticos rolling* que `model_api` computa (mean, median, min, max, stddev) como los *histogramas* subyacentes (para distribución por buckets). La baldosa **Model Identity** aparece al tope de esta sección.

| Panel | Tipo | Qué muestra |
|---|---|---|
| **Model Identity** | stat | `ml_model_info == 1` — muestra `version` en la baldosa (la métrica también lleva el label `trained_at`, pero el panel solo lo expone vía `legendFormat: "Version {{version}}"`); apareada con una annotation nativa de Grafana que dibuja línea vertical turquesa en todos los timeseries cuando se llama `bump_version` |
| **Prediction Mean** | timeseries | `sum(rate(ml_prediction_value_sum[15s])) / sum(rate(ml_prediction_value_count[15s]))` — misma expresión que la baldosa Avg Prediction, con umbral 600 k |
| **Prediction Median** | timeseries | `ml_prediction_median_recent` |
| **Prediction Min/Max** | timeseries | `ml_prediction_min_recent` y `ml_prediction_max_recent` superpuestos |
| **Prediction Standard Deviation** | timeseries | `ml_prediction_stddev_recent` — la varianza se ensancha durante ventanas de anomalía |
| **Prediction Histogram Buckets** | bargauge | 11 expresiones rate `[15s]`, una por bucket de 100 k a 1.5 M+. Nota: `prometheus_client` emite los `le` ≥ 1 000 000 en notación científica (`1e+06`, `1.5e+06`) — las queries matchean ese formato, no decimal |
| **Square Meters Mean** | timeseries | `sum(rate(ml_input_square_meters_sum[15s])) / sum(rate(ml_input_square_meters_count[15s]))` — misma cadencia `[15s]` que el resto del dashboard |
| **Square Meters Histogram Buckets** | bargauge | 9 expresiones rate `[15s]`, una por bucket de 50 a 650+ m² |
| **Bedrooms Mean** | timeseries | `sum(rate(ml_input_bedrooms_sum[15s])) / sum(rate(ml_input_bedrooms_count[15s]))` |
| **Bedrooms Histogram Buckets** | bargauge | 9 expresiones rate `[15s]`, una por bucket de 0 a 8+ dormitorios |
| **Neighborhood Mix** | timeseries | `sum(rate(ml_input_neighborhood_total[15s])) by (neighborhood)` — una línea por categoría, hace visualmente obvio el shift hacia `industrial` durante anomalía |
| **Missing Features** | timeseries | `sum(rate(ml_missing_feature_total[15s])) by (feature)` con umbral 0.5/s — `[15s]` para coincidir con la regla de alerta; actualmente siempre reporta `bedrooms` |

### Campos top-level del dashboard

Aparte de `panels`, el JSON contiene:

- `uid` — `ml-system`. El identificador estable del dashboard, usado en URLs (`/d/ml-system`) que aparecen en el resto de la doc.
- `title` — *ML System Dashboard*. Lo que aparece en la sidebar de Grafana.
- `refresh` — el intervalo de auto-refresh del dashboard. La demo usa 5 s.
- `time` — ventana temporal default cuando se abre el dashboard (últimos 15 minutos).
- `schemaVersion` — el tag interno de versión de Grafana para la forma del JSON. Grafana migrará versiones más viejas al cargar; subirlo mantiene el archivo en sync con releases más nuevas de Grafana.
- `version` — la versión de edición del dashboard, usada por la historia de UI de Grafana.
- `annotations` — define la annotation `Model Deployments` con `iconColor` turquesa que filtra por tag `deploy`. Es la fuente de las líneas verticales que dibujan los timeseries cuando `bump_version` postea contra la API de annotations de Grafana.

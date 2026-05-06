# Kibana — referencia de archivos

Kibana es la **UI de exploración de logs** de la demo: un frontend web en el puerto 5601 que lee desde Elasticsearch y permite filtrar, contar e inspeccionar eventos individuales de predicción. Kibana corre desde la imagen oficial de Elastic — no hay configuración de Kibana que mantener. El único archivo de esta carpeta es un script de bootstrap one-shot que corre la primera vez que arranca el stack y configura Kibana de forma que abrirlo lleve directamente a una vista Discover usable.

## `init/import.sh`

### Qué es
Un script POSIX shell que el contenedor `kibana-init` corre para provisionar un **data view** en Kibana vía la API HTTP de saved-objects de Kibana. Un data view es como Kibana llama a lo que las versiones más viejas llamaban index pattern: le dice a Kibana qué índices de Elasticsearch cubre una experiencia de UI dada, y qué campo es el eje temporal.

### Rol en el stack
Kibana 8 arranca con un wizard de primer uso que pide al usuario "Crea tu primer data view". Sin `import.sh`, cada demo nueva empezaría con un presentador clickeando ese wizard antes de que se vea algo. El script automatiza ese paso contra el contenedor de Kibana corriendo, así que apenas `kibana-init` termina con éxito:

- Existe un data view llamado `model-api-logs`.
- Está bindeado al patrón `model-api-logs-*` (matchea todos los índices diarios que crea Logstash al escribir a Elasticsearch).
- `@timestamp` es el campo temporal, que es lo que hace que Discover muestre eventos sobre un eje temporal.
- Ese data view es el **default**, así que abrir Discover lo carga sin tener que seleccionarlo.

El script es idempotente: en re-runs detecta un data view existente y sale con éxito.

### Recorrido

**Header.**

- `#!/bin/sh` — el script tiene que funcionar bajo cualquier shell POSIX (la imagen `curlimages/curl:8.5.0` que usa `kibana-init` solo trae `sh`, no `bash`).
- `set -eu` — sale inmediato ante cualquier comando fallido (`-e`) o variable no definida (`-u`). Se espera que el script termine bien o falle ruidosamente.

**Constantes.**

- `KIBANA_URL="http://kibana:5601"` — la dirección dentro de la red. `kibana` resuelve vía el DNS de Docker dentro de `monitor_net`.
- `DATA_VIEW_ID="model-api-logs"` — el ID del saved-object. Estable para poder referenciarlo al setear el default abajo.

**Paso 1 — esperar a Kibana.** Kibana es más pesado que Elasticsearch y tarda 30–90 s en estar responsive incluso después de que Elasticsearch reporta healthy. El loop de espera hace polling a `GET /api/status` (el endpoint de readiness de Kibana) cada 5 s hasta 60 intentos (techo de 5 minutos). `curl -fs` hace el llamado silencioso y sale con código no-cero ante errores HTTP, así que el predicado de `until` solo se invierte cuando Kibana realmente sirve un 2xx. El techo existe para que un stack mal configurado falle el script en lugar de colgar la sesión del lab para siempre.

**Paso 2 — crear el data view.** Un `POST` a `/api/data_views/data_view` con el payload estándar de saved-objects de Kibana:

- `id` — el identificador estable (`model-api-logs`).
- `title` — el patrón de índice (`model-api-logs-*`), que es lo que Kibana expande al consultar.
- `name` — la etiqueta legible que se muestra en el dropdown de data views.
- `timeFieldName: "@timestamp"` — requerido para el comportamiento de eje temporal en Discover; `@timestamp` es el campo que Logstash setea por evento (vía su filtro `date`, leyendo el timestamp del log de la API).
- `override: true` — reemplaza un data view existente con el mismo ID, así re-runs del script terminan en estado limpio.
- Header `kbn-xsrf: true` — Kibana lo requiere en cualquier API call que cambie estado, como guardia contra CSRF. El valor es opaco; `true` funciona.

El código de respuesta queda en `HTTP_CODE`. El script maneja tres ramas:
- `200` / `201`: creado. Loguea éxito.
- `409`: el data view ya existe *y* `override` no pudo reemplazarlo (raro; usualmente `override: true` devuelve 200). Loguea y continúa — no es una falla para la demo.
- cualquier otra cosa: vuelca el body a stderr y sale con código no-cero. El stack Compose mostrará a `kibana-init` como fallido en `docker compose ps`.

**Paso 3 — hacerlo el default.** Un segundo `POST` a `/api/data_views/default` con `{"data_view_id": "...", "force": true}`. `force` permite sobrescribir cualquier default preexistente. Toda la llamada termina con `|| true`, lo que significa que una falla acá *no* aborta el script — tener un data view sin default igual es usable; el usuario solo tiene que elegirlo del dropdown la primera vez.

## Dashboard auto-provisionado: `ML Drift Investigation`

Además del data view, `kibana-init` aprovisiona automáticamente un **dashboard** con 2 paneles que cubren tipos de drift que requieren joins per-evento sobre los logs. URL local: [http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard](http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard) — en el deployment público del póster, detrás de Caddy: [https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard](https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard).

> Si la URL de arriba da 404, el ID del dashboard puede haber cambiado. Para verificar el ID actual, correr:
> ```bash
> curl -s -u elastic:changeme \
>   "http://localhost:5601/api/saved_objects/_find?type=dashboard" \
>   -H "kbn-xsrf: true" | jq '.saved_objects[] | {id, attributes: {title: .attributes.title}}'
> ```
> El `id` esperado es `ml-derived-fields-dashboard` (coincide con el archivo `05-dashboard-ml-derived-fields-dashboard.json` en `kibana/init/saved_objects/`). El ID se mantiene estable entre regeneraciones de los archivos JSON desde el NDJSON, así los links externos no se rompen.

**Los 2 paneles:**

| # | Panel | Tipo | Qué cubre | Qué muestra |
|---|---|---|---|---|
| 1 | Predicciones con missing features | Histograma XY | **Data skew "feature deja de estar disponible"** | Distribución de `prediction` filtrada por `missing_features:*`. Solo se puebla durante anomalías cuando `bedrooms=None` ~35% del tráfico y el modelo imputa con la mediana del training set (`bedrooms=3`). Las predicciones son técnicamente válidas (HTTP 200) pero estadísticamente débiles porque acumulan la incertidumbre del valor imputado más la del modelo. |
| 2 | Top-20 predicciones extremas con feature context | Datatable | **Cola de predicción / monitoreo de distribución** (artículo sección 6 "estadísticas básicas: max + distribución completa") | Tabla de las 20 predicciones más altas del rango temporal con full feature context per-evento: `request_id`, `features.square_meters`, `features.bedrooms`, `features.neighborhood`, `latency_ms`, `model_version`, `@timestamp`. Drill-down post-alerta: cuando dispara `PredictionDriftDetected`, este panel muestra exactamente qué requests causaron las predicciones extremas y con qué inputs. |

### Cómo se aprovisionan

A diferencia de los data views (que se crean vía la API `/api/data_views/`), los dashboards de Kibana son objetos más complejos. La implementación elegida:

1. **Definición fuente**: `kibana/init/dashboards/ml-derived-fields.ndjson` — un archivo NDJSON con los 3 saved objects (2 panels Lens + 1 dashboard) en el formato del bulk-export de Kibana. Sirve como referencia legible y portable.

2. **Archivos por objeto**: `kibana/init/saved_objects/NN-<type>-<id>.json` — el NDJSON está descompuesto en 3 archivos individuales (`00-lens-prediction-with-missing`, `01-lens-top-prediction-outliers`, `05-dashboard-ml-derived-fields-dashboard`) con el cuerpo `{"attributes": {...}, "references": [...]}` que el endpoint POST espera. El prefijo numérico (`00-`, `01-`, `05-`) fuerza el orden de creación: panels primero, dashboard al final (para que las references resuelvan). Los huecos en la numeración (`02`–`04`) son sobras de paneles anteriores que se retiraron; el script de import simplemente itera los archivos existentes en orden léxico.

3. **Import en `import.sh`**: el script itera sobre los archivos JSON y hace `POST /api/saved_objects/<type>/<id>?overwrite=true` por cada uno. El POST directo (en lugar de `/api/saved_objects/_import`) **evita las migraciones automáticas** de Kibana que esperan campos legacy del schema viejo (`currentIndexPatternId` en lugar de `indexPatternId`, etc.) — el formato 8.x actual pasa el POST sin modificación pero rompería el `_import`.

### Cómo extender el dashboard

Para agregar un panel nuevo:

1. Crear el panel en Kibana UI manualmente (Visualize Library → Lens).
2. Exportarlo: **Stack Management → Saved Objects → seleccionar → Export → Include related objects**.
3. Mover el JSON a `kibana/init/saved_objects/0X-lens-<nombre>.json` con el formato `{"attributes": {...}, "references": [...]}` (usar un número que ordene antes de `05-dashboard-...json`).
4. Editar `05-dashboard-ml-derived-fields-dashboard.json` para agregar el nuevo panel: una entrada en `panelsJSON` con `panelRefName: "panel_pN"` y `gridData` (x/y/w/h) que no choque, más un reference correspondiente.
5. Re-correr `docker compose up kibana-init`.

El NDJSON fuente (`kibana/init/dashboards/ml-derived-fields.ndjson`) sirve como referencia legible del export bulk de Kibana; los archivos por objeto se mantienen a mano, sin auto-generación.

## Queries útiles para Discover

Una vez que Kibana arranca y aparecen documentos, los siguientes filtros son los más útiles para mostrar la pipeline en acción:

| Query | Qué muestra |
|---|---|
| `anomaly_window: true` | Solo eventos durante ventanas de anomalía sintéticas — predicciones con inputs sospechosos. |
| `http_status: 500` | Solo predicciones que fallaron. |
| `event_type: prediction_failed` | Equivalente a `http_status: 500`. |
| `internal: false` | Solo llamadas reales `POST /predict` (excluye el tráfico sintético interno). |
| **`missing_features: *`** | **Predicciones donde alguna feature llegó faltante en el request** — el modelo imputó con la mediana del training set (ej. `bedrooms=3`). Alimenta el Panel 1 del dashboard. |
| **`prediction > 1500000`** | **Predicciones extremas** — la cola derecha de la distribución. Combinar con la tabla del Panel 2 para tener full feature context. |

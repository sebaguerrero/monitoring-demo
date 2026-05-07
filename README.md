# Monitorización de ML en Producción: Demo

Es una demo de monitorización de un sistema de ML en producción, inspirada en el artículo [Monitoring Machine Learning Models in Production](https://christophergs.com/machine%20learning/2020/03/14/how-to-monitor-machine-learning-models/) de Christopher GS. Simula una API de predicción de precios inmobiliarios que alterna entre modo normal y ventanas de anomalía cada 30 segundos, lo que dispara alertas y deja ver el comportamiento del stack de monitoreo (Prometheus, Grafana, ELK) frente a problemas típicos de ML: input drift, prediction drift, features faltantes, latencia, errores HTTP.

## Tabla de contenidos
- [Servicios](#servicios)
- [Qué es Docker y por qué se usa](#qué-es-docker-y-por-qué-se-usa)
- [Qué es Docker Compose y qué hace en la demo](#qué-es-docker-compose-y-qué-hace-en-la-demo)
- [Cómo instalar Docker](#cómo-instalar-docker)
- [Permisos de Docker](#permisos-de-docker)
- [Inicio rápido](#inicio-rápido)
- [Modos de la demo](#modos-de-la-demo)
- [Endpoints](#endpoints)
- [Estructura de carpetas](#estructura-de-carpetas)
- [Qué hace cada archivo](#qué-hace-cada-archivo)
- [Despliegue público](#despliegue-público)
- [Demo en funcionamiento](#demo-en-funcionamiento)

## Servicios

La demo combina 11 servicios. Para una explicación a fondo de cada uno (qué hace, cómo se conecta con el resto, qué métricas/logs produce), ver [`docs/descripcion_demo_es.md`](docs/descripcion_demo_es.md):

| Servicio | Rol | Doc detallada |
|---|---|---|
| `model_api` | API de predicción + generador de tráfico sintético + emisión de métricas y logs | [descripcion_demo # La API de predicción](docs/descripcion_demo_es.md#4-la-api-de-predicción-model_api)<br><br>[model_api_es.md](model_api/docs/model_api_es.md) |
| `prometheus` | Recolección de métricas y evaluación de alertas | [descripcion_demo # Prometheus: el colector de métricas](docs/descripcion_demo_es.md#5-prometheus-el-colector-de-métricas)<br><br>[prometheus_es.md](prometheus/docs/prometheus_es.md) |
| `grafana` | Dashboard visual de métricas | [descripcion_demo # Grafana: el dashboard de ML](docs/descripcion_demo_es.md#6-grafana-el-dashboard-de-ml)<br><br>[grafana_es.md](grafana/docs/grafana_es.md) |
| `alertmanager` | Agrupación y enrutamiento de alertas | [descripcion_demo # Prometheus: el colector de métricas](docs/descripcion_demo_es.md#5-prometheus-el-colector-de-métricas)<br><br>[prometheus_es.md](prometheus/docs/prometheus_es.md#alertmanageryml) |
| `panic_duty` | Receptor de webhooks de alertas + UI mock estilo PagerDuty | [descripcion_demo # PanicDuty: el receptor de alertas](docs/descripcion_demo_es.md#7-panicduty-el-receptor-de-alertas)<br><br>[panic_duty_es.md](panic_duty/docs/panic_duty_es.md) |
| `filebeat` | Log shipper (lee stdout de `model_api` vía Docker, reenvía a Logstash) | [descripcion_demo # El pipeline de logs](docs/descripcion_demo_es.md#8-el-pipeline-de-logs-filebeat--logstash--elasticsearch)<br><br>[filebeat_es.md](filebeat/docs/filebeat_es.md) |
| `logstash` | Procesador intermedio (parsea texto plano a JSON con `grok`, normaliza tipos) | [descripcion_demo # El pipeline de logs](docs/descripcion_demo_es.md#8-el-pipeline-de-logs-filebeat--logstash--elasticsearch)<br><br>[logstash_es.md](logstash/docs/logstash_es.md) |
| `elasticsearch` | Almacenamiento de logs (índices diarios `model-api-logs-*`) | [descripcion_demo # El pipeline de logs](docs/descripcion_demo_es.md#8-el-pipeline-de-logs-filebeat--logstash--elasticsearch)<br><br>[elasticsearch_es.md](elasticsearch/docs/elasticsearch_es.md) |
| `kibana` | UI para explorar logs (Discover + dashboard `ML Drift Investigation`) | [descripcion_demo # Kibana: exploración de logs y dashboard](docs/descripcion_demo_es.md#9-kibana-exploración-de-logs-y-dashboard)<br><br>[kibana_es.md](kibana/docs/kibana_es.md) |
| `kibana-init` | Bootstrap one-shot: crea data view y aprovisiona el dashboard de Kibana | [descripcion_demo # Kibana: exploración de logs y dashboard](docs/descripcion_demo_es.md#9-kibana-exploración-de-logs-y-dashboard)<br><br>[kibana_es.md](kibana/docs/kibana_es.md) |
| `caddy` *(opcional, profile `poster`)* | Reverse proxy con HTTPS automático para exponer la demo en una VM pública | [Despliegue público](#despliegue-público) |

## Qué es Docker y por qué se usa en la demo
Docker es una herramienta para empaquetar aplicaciones junto con su entorno de ejecución. En la práctica, eso significa que cada parte de la demo puede correr dentro de su propio contenedor aislado, con las dependencias y la configuración que necesita.

**Por qué la demo lo usa.** Sin Docker habría que instalar y configurar varias herramientas distintas en la máquina local (Prometheus, Grafana, Alertmanager, Elasticsearch, Kibana, Logstash, Filebeat), asegurarse de que las versiones sean compatibles, exponer puertos manualmente y conectar los servicios entre sí. Para una demo de clase, eso agrega una fricción innecesaria.

Lo que Docker aporta:

- **Reproducibilidad** — el mismo `docker compose up` produce el mismo stack en cualquier máquina.
- **Aislamiento** — cada servicio tiene sus dependencias dentro de su contenedor; no se pisan entre sí ni con lo que tenés instalado en el host.
- **Arranque con un solo comando** — un solo comando levanta los 10 servicios.
- **Arquitectura multi-servicio clara** — cada servicio = un contenedor, fácil de razonar.
- **Menos problemas de "en mi máquina funciona"** — el contenedor encapsula la versión exacta de cada herramienta.

## Qué es Docker Compose y qué rol tiene en la demo
Docker Compose es la herramienta que se usa para **definir y ejecutar varios servicios Docker juntos** desde un solo archivo de configuración (`docker-compose.yml`). Si Docker te deja correr un contenedor a la vez, Compose te deja describir 10 contenedores y orquestarlos como una unidad.

**Qué levanta en la demo.** El stack completo de 10 servicios por defecto, organizado en tres grupos lógicos:

- **Núcleo**: `model_api`, `prometheus`, `grafana`
- **Alertas**: `alertmanager`, `panic_duty`
- **Logs**: `elasticsearch`, `kibana`, `filebeat`, `logstash`, `kibana-init`
- **Despliegue público** *(opcional, profile `poster`)*: `caddy` — ver [Despliegue público](#despliegue-público)

Un `docker compose up` simple levanta todo. Si querés un footprint más liviano, podés nombrar un subconjunto de servicios en la línea de comandos — ver [Modos de la demo](#modos-de-la-demo) más abajo.

**De qué se encarga Compose, concretamente:**

- **Construir los servicios Python propios** (`model_api`, `panic_duty`) desde sus respectivos `Dockerfile`.
- **Ejecutar Prometheus, Grafana, Alertmanager, Elasticsearch, Kibana, Logstash y Filebeat** desde imágenes oficiales pinneadas a versiones conocidas. Logstash, en particular, corre desde la imagen oficial con `pipeline/logstash.conf` y `config/logstash.yml` montados como volúmenes — los cambios al pipeline se aplican con un `restart` del contenedor, sin rebuild.
- **Conectar todos los servicios a la misma red** (`monitor_net`) para que se vean por nombre de servicio (ej. `prometheus` resuelve a la IP del contenedor de Prometheus).
- **Exponer puertos** a la máquina local (`8000` para la API, `9090` para Prometheus, `3000` para Grafana, `5601` para Kibana, etc.).
- **Montar archivos de configuración** desde el repo a cada contenedor:
  - reglas de Prometheus,
  - configuración de enrutamiento de Alertmanager,
  - aprovisionamiento y dashboards de Grafana,
  - configuración de autodescubrimiento de Filebeat,
  - pipeline de Logstash (`pipeline/logstash.conf`),
  - script de bootstrap del data view + dashboard de Kibana.
- **Inyectar variables de entorno** en `model_api` (perillas de la demo: `MODEL_VERSION`, `DEMO_BASE_RPS`, etc.).
- **Controlar el orden de arranque** con `depends_on` y healthchecks (ej. Logstash espera a que Elasticsearch esté healthy antes de arrancar; Filebeat espera a que Logstash esté healthy).

Eso permite arrancar y detener toda la arquitectura como una sola unidad — y podés correr un subconjunto más chico nombrando servicios individuales en `docker compose up`.

## Cómo instalar Docker
Para ejecutar la demo, necesita tener Docker instalado en su máquina.

La forma más simple es seguir la documentación oficial de Docker para su sistema operativo:

- Guía de instalación de Docker: https://docs.docker.com/get-docker/

Después de instalar Docker, puede verificarlo así:

```bash
docker --version
docker-compose --version
```

Si su instalación usa el plugin nuevo de Compose, también puede funcionar:

```bash
docker compose version
```

Si Docker está instalado pero los comandos fallan por permisos, vea la sección **Permisos de Docker** siguiente.

## Permisos de Docker
Si los comandos de Docker fallan con un error de permisos, probablemente su usuario no puede acceder directamente al socket de Docker.

Opciones habituales:

- ejecutar los comandos con `sudo`,
- o agregar su usuario al grupo `docker` y abrir una nueva shell/sesión.

Esto es un problema de configuración local de la máquina, no un problema propio de la demo.

## Inicio rápido
Desde este directorio, la forma recomendada de levantar el stack es:

```bash
make fresh
```

Es la opción **"siempre funciona"** — equivale a:

```bash
docker compose down -v --rmi local --remove-orphans
docker compose up -d --build
```

Es decir: baja todo, borra los volúmenes (datos viejos de Elasticsearch), borra las imágenes locales (`model_api`, `panic_duty`) para forzar el rebuild, borra huérfanos, y vuelve a arrancar los 10 servicios en background reconstruyendo las imágenes desde cero. Garantiza un stack limpio sin estado residual de corridas anteriores.

Para la sintaxis vieja, `docker-compose` en lugar de `docker compose` también funciona.

### Cuándo NO usar `make fresh` (alternativas más rápidas)

`make fresh` es seguro pero borra todo y rebuilda — tarda ~1-2 min. Si ya conocés el estado del stack, hay alternativas más rápidas:

| Situación | Comando | Qué preserva |
|---|---|---|
| **Empezar de cero** (recomendado siempre) | `make fresh` | nada |
| Re-arrancar sin tocar nada | `docker compose restart` | imágenes, volúmenes, datos de ES |
| Cambié código pero quiero conservar índices viejos de ES | `docker compose up -d --build` | volúmenes (datos), borra contenedores y los recrea |
| Solo levantarlo (nunca lo bajé) | `docker compose up -d` | todo — usa imágenes cacheadas |

### Flags relevantes de `docker compose up`

`--build` y `-d` son dos flags **independientes** que controlan cosas distintas:

**`--build`** — fuerza a Compose a **reconstruir las imágenes** antes de levantar los contenedores.

- **Sin `--build`** → Compose usa la imagen que ya está cacheada (si existe). Si tocaste código en `model_api/` o `panic_duty/`, o cambiaste cualquier `Dockerfile`, esos cambios **no se reflejan**.
- **Con `--build`** → Corre `docker build` en cada servicio que tiene `build:` en el `docker-compose.yml` (en la demo: `model_api` y `panic_duty`) y después levanta los contenedores con la imagen recién construida.

Cuándo usarlo solo: después de modificar código de la app, el `Dockerfile` o `requirements.txt` de `model_api`/`panic_duty`, pero sin querer borrar volúmenes. Si querés rebuild **y** reset de datos, `make fresh` es más simple. Para cambios en archivos montados como volumen (pipeline de Logstash en `logstash/pipeline/logstash.conf`, reglas de Prometheus, dashboards de Grafana) alcanza con `docker compose restart <servicio>` — no requiere `--build`.

**`-d` (detached)** — controla **dónde corren los logs**, no si se reconstruye nada.

- **Sin `-d`** → los contenedores arrancan y la terminal queda "pegada" mostrando los logs de todos los servicios mezclados. Si hacés `Ctrl+C`, los contenedores se paran.
- **Con `-d`** → arranca los contenedores en background y la terminal vuelve al prompt. Los contenedores siguen corriendo aunque cierres la terminal. Para ver logs después: `docker compose logs -f`.

| Comando | Reconstruye imágenes | Borra volúmenes | Suelta la terminal |
|---|---|---|---|
| `docker compose up` | no | no | no |
| `docker compose up --build` | **sí** | no | no |
| `docker compose up -d` | no | no | **sí** |
| `docker compose up --build -d` | **sí** | no | **sí** |
| **`make fresh`** | **sí** | **sí** | **sí** |

### Acceso

| Servicio | URL | Qué podés ver |
|---|---|---|
| **Grafana** (home) | http://localhost:3000 | Página principal de Grafana |
| **Grafana — ML System Dashboard** | http://localhost:3000/d/ml-system | Dashboard de métricas de ML en tiempo real (link directo) |
| **Prometheus** (home) | http://localhost:9090 | Métricas crudas, reglas de alertas, targets |
| **Prometheus Alerts** | http://localhost:9090/alerts | Estado de cada alerta (inactive/pending/firing) |
| **Prometheus Targets** | http://localhost:9090/targets | Estado del scrape (UP/DOWN) hacia la API |
| **Alertmanager** | http://localhost:9093 | Alertas activas agrupadas |
| **PanicDuty** | http://localhost:8080 | UI con alertas disparadas en este momento |
| **API (Swagger UI)** | http://localhost:8000/docs | Documentación interactiva de la API (predict, health, metrics) |
| **Kibana** (home) | http://localhost:5601 | Página principal de Kibana |
| **Kibana — Discover (logs)** | http://localhost:5601/app/discover | Listado de logs parseados con el data view `model-api-logs` (link directo) |
| **Kibana — ML Drift Investigation Dashboard** | http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard | Dashboard con 2 paneles Lens (predicciones con missing features, top-20 outlier predictions) |

**En el deployment público del póster** (`make poster-up`, ver [Despliegue público](#despliegue-público) y la guía específica de [AWS](docs/aws_es.md)) las 6 herramientas listadas en el `Caddyfile` quedan detrás de subdominios HTTPS con cert de Let's Encrypt automático. Las URLs apuntan a la EIP del deployment actual (`3-226-31-220` con guiones, que sslip.io resuelve a `3.226.31.220`); si re-desplegás con otra IP, regenerá las URLs reemplazando ese tramo en cada hostname:

| Servicio | URL pública |
|---|---|
| **Grafana** (home) | [https://grafana.3-226-31-220.sslip.io](https://grafana.3-226-31-220.sslip.io) |
| **Grafana — ML System Dashboard** | [https://grafana.3-226-31-220.sslip.io/d/ml-system](https://grafana.3-226-31-220.sslip.io/d/ml-system) |
| **Prometheus** (home) | [https://prometheus.3-226-31-220.sslip.io](https://prometheus.3-226-31-220.sslip.io) |
| **Prometheus Alerts** | [https://prometheus.3-226-31-220.sslip.io/alerts](https://prometheus.3-226-31-220.sslip.io/alerts) |
| **Prometheus Targets** | [https://prometheus.3-226-31-220.sslip.io/targets](https://prometheus.3-226-31-220.sslip.io/targets) |
| **Alertmanager** | [https://alertmanager.3-226-31-220.sslip.io](https://alertmanager.3-226-31-220.sslip.io) |
| **PanicDuty** | [https://panicduty.3-226-31-220.sslip.io](https://panicduty.3-226-31-220.sslip.io) |
| **API (Swagger UI)** | [https://api.3-226-31-220.sslip.io/docs](https://api.3-226-31-220.sslip.io/docs) |
| **Kibana** (home) | [https://kibana.3-226-31-220.sslip.io](https://kibana.3-226-31-220.sslip.io) |
| **Kibana — Discover (logs)** | [https://kibana.3-226-31-220.sslip.io/app/discover](https://kibana.3-226-31-220.sslip.io/app/discover) |
| **Kibana — ML Drift Investigation Dashboard** | [https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard](https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard) |

## Modos de la demo

Los comandos de abajo siguen el patrón **"down + rebuild + up"** — garantizan un stack limpio sin estado residual de corridas anteriores. Si necesitás alternativas más rápidas (preservar datos, no rebuildar, etc.) ver [Cuándo NO usar `make fresh`](#cuándo-no-usar-make-fresh-alternativas-más-rápidas) en Inicio rápido.

| Modo | Comando | Servicios | RAM | Dónde corre |
|---|---|---|---|---|
| **Default local (stack completo)** | `make fresh` | los 10 servicios | ~2.6 GB | máquina local |
| Mínimo local (poca RAM) | `docker compose down -v --remove-orphans && docker compose up -d --build model_api prometheus grafana` | núcleo de 3 servicios | ~600 MB | máquina local |
| **Público (póster QR / clase)** | `make poster-fresh` | los 10 + `caddy` = **11 servicios** | ~2.7 GB | VM pública (AWS EC2, Oracle Cloud Always Free, Hetzner, etc.) |

### Default local (stack completo)
Es la forma recomendada para desarrollo y para presentaciones en una sola pantalla. Muestra todas las partes de la historia de monitoreo + observabilidad del artículo (métricas, alertas, logs).

### Mínimo local (poca RAM)
Existe para máquinas con poca memoria que no pueden alojar Elasticsearch + Kibana + Logstash (~1.9 GB juntos). Te quedás con Grafana y Prometheus, pero perdés el enrutamiento de alertas, la UI de PanicDuty y los logs en Kibana.

### Público (póster QR / clase)
Agrega un servicio `caddy` (gateado detrás del profile `poster` de Compose) que termina TLS con certificados Let's Encrypt automáticos y hace reverse-proxy de seis herramientas (Grafana, Prometheus, Alertmanager, PanicDuty, la API del modelo y Kibana), cada una en su propio subdominio sslip.io. **No arranca por defecto en local** — solo cuando pasás `--profile poster` (o usás el atajo `make poster-up`).

Para los detalles operacionales — cómo levantarlo en AWS EC2, atajos del Makefile, hardening (`GF_ANONYMOUS_ROLE=Viewer`, `GF_ADMIN_PASSWORD`), tips de QR, IPs estables — ver la sección [Despliegue público](#despliegue-público) más abajo.

## Endpoints
Estos son los endpoints de aplicación expuestos por los servicios de la demo.

### `model_api`
- `POST /predict`
  - Endpoint principal de inferencia.
  - Acepta un JSON opcional con campos como `square_meters`, `bedrooms` y `neighborhood`.
  - Si no se le pasa payload (por ejemplo, desde el generador interno de tráfico), el servicio crea inputs sintéticos por sí mismo.
  - Devuelve un precio inmobiliario sintético predicho.
  - También registra métricas de request, inputs y predicciones.
- `GET /metrics`
  - Endpoint de scraping para Prometheus.
  - Expone todas las métricas en formato texto de Prometheus.
- `GET /health`
  - Endpoint simple de salud.
  - Devuelve el estado básico del servicio, la versión del modelo y si la demo está actualmente en una ventana de anomalía.

### `panic_duty` (servicios de alertas)
- `GET /`
  - Página principal de la UI de PanicDuty.
  - Muestra los incidentes activos recibidos desde Alertmanager.
- `POST /webhook`
  - Webhook llamado por Alertmanager.
  - Recibe alertas firing y resolved y actualiza la lista de incidentes de PanicDuty.

### `logstash` (servicios de logs)
- `GET /_node/stats` (puerto 9600) — estadísticas del nodo Logstash. El campo `pipelines.main.events.{in,filtered,out}` te dice cuántos eventos pasaron por el pipeline. Lo usa el healthcheck de Compose; útil para checks rápidos:
  ```bash
  curl -s http://localhost:9600/_node/stats | jq '.pipelines.main.events'
  ```
- `GET /_node/pipelines/main` — info detallada del pipeline (configuración cargada, métricas por filter).
- TCP `:5044` — input `beats` (Filebeat se conecta acá). No es HTTP, no se accede con curl.

### `elasticsearch` (servicios de logs)
- `GET /_cluster/health` — salud del cluster (lo usa el healthcheck de Compose).
- `GET /model-api-logs-*/_count` — conteo de eventos indexados.
- `GET /model-api-logs-*/_search` — búsqueda/filtrado de eventos directamente vía la API de Elasticsearch. Lo usa Kibana; útil para checks rápidos por CLI.

### `kibana` (servicios de logs)
- `GET /` — UI principal de Kibana; navegar a **Discover** para explorar logs de predicción.
- `GET /app/dashboards#/view/ml-derived-fields-dashboard` — dashboard auto-provisionado `ML Drift Investigation` (2 paneles: predicciones con missing features, top-20 outlier predictions).
- `GET /api/status` — endpoint de readiness de Kibana (lo usa `kibana-init`).
- `GET /api/data_views` — lista de data views configurados (incluye `model-api-logs`).
- `GET /api/saved_objects/dashboard/ml-derived-fields-dashboard` — definición del dashboard auto-provisionado vía la saved-objects API.

## Estructura de carpetas
Esta es la estructura relevante de `monitoring_demo/`:

```text
monitoring_demo/
├── README.md
├── docker-compose.yml
├── Makefile
├── Caddyfile
├── .env.poster.example
├── docs/
│   ├── README_EN.md
│   ├── aws_en.md
│   ├── aws_es.md
│   ├── descripcion_demo_en.md
│   ├── descripcion_demo_es.md
│   ├── files_root_en.md
│   └── files_root_es.md
├── model_api/
│   ├── app.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── docs/
│       ├── model_api_en.md
│       └── model_api_es.md
├── prometheus/
│   ├── prometheus.yml
│   ├── rules.yml
│   ├── alertmanager.yml
│   └── docs/
│       ├── prometheus_en.md
│       └── prometheus_es.md
├── grafana/
│   ├── dashboards/
│   │   └── ml_dashboard.json
│   ├── provisioning/
│   │   ├── dashboards/
│   │   │   └── dashboard.yml
│   │   └── datasources/
│   │       └── datasource.yml
│   └── docs/
│       ├── grafana_en.md
│       └── grafana_es.md
├── panic_duty/
│   ├── app.py
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── templates/
│   │   └── index.html
│   └── docs/
│       ├── panic_duty_en.md
│       └── panic_duty_es.md
├── filebeat/
│   ├── filebeat.yml
│   └── docs/
│       ├── filebeat_en.md
│       └── filebeat_es.md
├── logstash/
│   ├── config/
│   │   └── logstash.yml
│   ├── pipeline/
│   │   └── logstash.conf
│   └── docs/
│       ├── logstash_en.md
│       └── logstash_es.md
├── elasticsearch/
│   ├── model-api-logs-template.json
│   └── docs/
│       ├── elasticsearch_en.md
│       └── elasticsearch_es.md
└── kibana/
    ├── init/
    │   ├── import.sh
    │   ├── dashboards/
    │   │   └── ml-derived-fields.ndjson
    │   └── saved_objects/
    │       ├── 00-lens-prediction-with-missing.json
    │       ├── 01-lens-top-prediction-outliers.json
    │       └── 05-dashboard-ml-derived-fields-dashboard.json
    └── docs/
        ├── kibana_en.md
        └── kibana_es.md
```

## Qué hace cada archivo

Resúmenes breves abajo; los archivos `README_ES.md` por carpeta contienen recorridos detallados.

### Archivos raíz
> Ver [files_root_es.md](docs/files_root_es.md) para la referencia completa de los archivos a nivel raíz.

- `README.md`
  - Documentación principal de la demo (en español). La versión en inglés está en `docs/README_EN.md`.
- `docker-compose.yml`
  - Define 11 servicios: 10 que arrancan por defecto distribuidos en tres grupos lógicos (núcleo, alertas, logs) más `caddy` gateado detrás del profile opcional `poster`. También configura puertos, redes, archivos montados, variables de entorno y el reverse proxy Caddy para despliegues públicos.
- `Caddyfile`
  - Configuración del reverse proxy Caddy usado por el flujo de despliegue público. Lee seis hostnames de variables de entorno (Grafana, Prometheus, Alertmanager, PanicDuty, API del modelo, Kibana) y rutea cada uno al servicio que corresponda. Solo se carga cuando el profile `poster` está activo.
- `.env.poster.example`
  - Plantilla del archivo `.env.poster` para despliegue público (que está en gitignore). Ver [Despliegue público](#despliegue-público).
- `Makefile`
  - Define targets cortos de Make (`poster-up`, `poster-down`, `poster-logs`, `poster-status`) que envuelven las invocaciones más largas de `docker compose --env-file .env.poster --profile poster ...`. Ver la subsección de atajos del Makefile en [Despliegue público](#despliegue-público).

### `model_api/`
> Ver [model_api_es.md](model_api/docs/model_api_es.md) para la referencia completa.

- `model_api/app.py`
  - Aplicación principal en FastAPI.
  - Sirve predicciones.
  - Expone métricas Prometheus.
  - Emite una línea en texto plano free-form por predicción a stdout (consumida por Filebeat → Logstash → Elasticsearch en el stack default). Decisión pedagógica: la API se comporta como un servicio "legacy" para que Logstash tenga un rol visible.
  - Genera tráfico sintético y ventanas de anomalía.
  - Calcula estadísticas de predicción y métricas de recursos.
- `model_api/Dockerfile`
  - Construye la imagen del contenedor del API.
- `model_api/requirements.txt`
  - Dependencias Python del API.

### `prometheus/`
> Ver [prometheus_es.md](prometheus/docs/prometheus_es.md) para la referencia completa.

- `prometheus/prometheus.yml`
  - Configuración principal de Prometheus.
  - Define el target de scraping (`model_api`) y la conexión con Alertmanager.
- `prometheus/rules.yml`
  - Reglas de alerta evaluadas por Prometheus.
- `prometheus/alertmanager.yml`
  - Configuración de enrutamiento de Alertmanager.
  - Envía alertas a PanicDuty vía webhook.

### `grafana/`
> Ver [grafana_es.md](grafana/docs/grafana_es.md) para la referencia completa.

- `grafana/dashboards/ml_dashboard.json`
  - Definición del dashboard que muestra Grafana.
  - Organiza paneles en métricas DevOps y métricas de ML.
- `grafana/provisioning/dashboards/dashboard.yml`
  - Le indica a Grafana de dónde cargar los archivos JSON de dashboards.
- `grafana/provisioning/datasources/datasource.yml`
  - Preconfigura Prometheus como fuente de datos por defecto en Grafana.

### `panic_duty/` (servicios de alertas)
> Ver [panic_duty_es.md](panic_duty/docs/panic_duty_es.md) para la referencia completa.

- `panic_duty/app.py`
  - App FastAPI que recibe alertas desde Alertmanager y sirve la UI de PanicDuty.
- `panic_duty/Dockerfile`
  - Construye la imagen del contenedor de PanicDuty.
- `panic_duty/requirements.txt`
  - Dependencias Python de PanicDuty.
- `panic_duty/templates/index.html`
  - Plantilla HTML de la interfaz web de PanicDuty.

### `filebeat/` (servicios de logs)
> Ver [filebeat_es.md](filebeat/docs/filebeat_es.md) para la referencia completa.

- `filebeat/filebeat.yml`
  - Configuración de Filebeat. Usa autodescubrimiento de Docker para leer las líneas de texto plano del contenedor `model_api` y enviarlas a Logstash en `logstash:5044` (no directo a Elasticsearch como antes — Logstash hace el procesamiento intermedio).

### `logstash/` (servicios de logs)
> Ver [logstash_es.md](logstash/docs/logstash_es.md) para la referencia completa.

- `logstash/config/logstash.yml`
  - Config mínima del runtime de Logstash (puerto 9600 para la API de monitoreo, X-Pack monitoring deshabilitado). Se monta como volumen read-only sobre la imagen oficial `docker.elastic.co/logstash/logstash:8.17.0` en `/usr/share/logstash/config/logstash.yml`.
- `logstash/pipeline/logstash.conf`
  - El pipeline propiamente dicho. Define input `beats` (recibe de Filebeat en TCP 5044), filtro con `grok` (parsea texto plano a JSON), filtros `mutate`/`date` (normalización de tipos y timestamps), y output a Elasticsearch. Se monta como volumen read-only en `/usr/share/logstash/pipeline/logstash.conf`.

### `elasticsearch/` (servicios de logs)
> Ver [elasticsearch_es.md](elasticsearch/docs/elasticsearch_es.md) para la referencia completa.

- `elasticsearch/model-api-logs-template.json`
  - Template de índice aplicado a cada índice `model-api-logs-*`. Mapea los strings como `keyword` (campo único, sin gemelo `.keyword`) con un normalizer en lowercase para matching case-insensitive. `error_message` y `summary` se mapean explícitamente como `keyword` sin el normalizer para que el texto de errores y resúmenes mantenga la caja original; su `ignore_above` más alto les permite cargar frases descriptivas completas. Los campos numéricos y de array (`prediction`, `latency_ms`, `features.*`, `missing_features`) también tienen mappings explícitos. El template se monta dentro del contenedor de Logstash y se registra automáticamente al arrancar Logstash vía `manage_template => true` — ya no hay un sidecar `es-init` separado.

### `kibana/` (servicios de logs)
> Ver [kibana_es.md](kibana/docs/kibana_es.md) para la referencia completa.

- `kibana/init/import.sh`
  - Script de bootstrap one-shot que ejecuta el contenedor `kibana-init`. Espera a que Kibana esté saludable, crea el data view `model-api-logs-*` y lo deja como default, y aprovisiona los 3 saved objects (2 panels Lens + 1 dashboard) desde `kibana/init/saved_objects/`. Después de correrse una vez, abrir Kibana lleva directo a un Discover usable y al dashboard `ML Drift Investigation`.
- `kibana/init/dashboards/ml-derived-fields.ndjson`
  - Definición fuente del dashboard como NDJSON (formato bulk-export de Kibana). 3 saved objects: 2 panels Lens + 1 dashboard. Es la versión "legible" — el script no la usa directamente, pero sirve para entender la estructura.
- `kibana/init/saved_objects/*.json`
  - Los 3 saved objects descompuestos en archivos individuales en el formato `{"attributes": {...}, "references": [...]}` que el endpoint POST de Kibana espera. El prefijo numérico (`00-`, `01-`, `05-`) fuerza el orden de creación: panels primero, dashboard al final. `kibana-init` itera sobre estos archivos y POSTea cada uno a `/api/saved_objects/<type>/<id>` (POST directo evita las migraciones automáticas que romperían el formato 8.x).

## Despliegue público
Esta sección es para el caso específico de levantar una copia de la demo accesible públicamente.

El repo incluye un `Caddyfile` y un servicio `caddy` (gateado detrás de un profile `poster` de Compose) que hace de reverse-proxy de seis herramientas (Grafana, Prometheus, Alertmanager, PanicDuty, la API del modelo, Kibana) en una sola VM con HTTPS. El desarrollo local no se ve afectado porque el servicio Caddy solo arranca cuando pasás `--profile poster`.

### Requisitos previos
- Una VM Linux pública con Docker y Compose instalados (cualquier free tier o VPS barato — AWS EC2, Oracle Cloud Always Free, Hetzner, etc.).
- IP pública alcanzable en los puertos 80 y 443 (para Caddy + Let's Encrypt).
- Opcionalmente, los puertos 3000, 9090, 8080, 5601 también abiertos — habilitan un camino de fallback si el DNS comodín de sslip.io no es alcanzable el día del póster (podés cambiar los QR a URLs `http://<ip>:<puerto>` planas sin reimprimir).

### Pasos

1. SSH a la VM, clonar el repo, `cd monitoring_demo`.
2. Copiar la plantilla de env y completar la IP pública de la VM en formato con guiones (sslip.io resuelve `<hostname>.3-226-31-220.sslip.io` a la IP):
   ```bash
   cp .env.poster.example .env.poster
   $EDITOR .env.poster
   ```
3. Levantar el stack completo más Caddy con un arranque limpio (recomendado el día del póster):
   ```bash
   make poster-fresh
   ```
   Ese target hace `down --rmi local --remove-orphans` + `up -d --build` con `--env-file .env.poster --profile poster`. Recrea todos los contenedores con imágenes rebuilteadas (así Elasticsearch/Kibana/Prometheus quedan vacíos — no tienen volúmenes nombrados) pero **preserva el volumen nombrado `caddy_data`**, por lo que los certs Let's Encrypt sobreviven al rebuild y no se re-emiten.
4. En el primer `poster-fresh` después de desplegar a una IP nueva, Caddy emite certs LE en el primer pedido a cada subdominio (~30 s en el primer hit). Después quedan cacheados en `caddy_data` y se reusan en los siguientes `poster-fresh`.
5. Verificar cada URL `https://<herramienta>.<ip>.sslip.io` desde un teléfono en datos móviles antes de imprimir los QR.

### Atajos del Makefile
El repo incluye un `Makefile` con cinco targets que envuelven los comandos Compose para no tener que retipear `--env-file` y `--profile poster` cada vez:

| Target | Comando Compose equivalente |
|---|---|
| **`make poster-fresh`** (recomendado) | `docker compose --env-file .env.poster --profile poster down --rmi local --remove-orphans` + `... up -d --build` |
| `make poster-up` | `docker compose --env-file .env.poster --profile poster up -d --build` |
| `make poster-down` | `docker compose --env-file .env.poster --profile poster down` |
| `make poster-logs` | `docker compose --env-file .env.poster --profile poster logs -f` |
| `make poster-status` | `docker compose --env-file .env.poster --profile poster ps` |

`make poster-fresh` es el equivalente público de `make fresh`, **sin el `-v`** en el `down`. La diferencia importa: en deploy público Caddy guarda los certs Let's Encrypt en el volumen nombrado `caddy_data`, y re-emitirlos en cada restart agotaría el rate limit de Let's Encrypt (5 certs duplicados por hostname cada 7 días). Al preservar el volumen, los certs sobreviven a cualquier cantidad de `poster-fresh` y solo se renuevan ~30 días antes de expirar (auto-renewal de Caddy). Los demás servicios (Elasticsearch, Kibana, Prometheus) no tienen volúmenes nombrados, así que su estado se borra al recrear los contenedores — eso es intencional y refleja lo que hace `make fresh` para resetear estado local.

`make` viene preinstalado en prácticamente toda máquina Linux/macOS, incluida la imagen Ubuntu de AWS EC2 a la que vas a conectarte por SSH. Las recetas son intencionalmente de una línea — leer el `Makefile` si querés ver exactamente qué hacen.

## Demo en funcionamiento

Apenas el stack está arriba y empieza a generar tráfico, lo que se ve es esto:

1. **Grafana** (local: [http://localhost:3000/d/ml-system](http://localhost:3000/d/ml-system) — AWS: [https://grafana.3-226-31-220.sslip.io/d/ml-system](https://grafana.3-226-31-220.sslip.io/d/ml-system)) muestra las secciones del dashboard. La fila **Alert Status Overview** arriba del todo arranca con todos los tiles en verde.
2. El servicio ya está generando tráfico: el panel de request rate, la latencia, las métricas de predicción y de inputs (incluida `bedrooms` y las series de CPU/memoria/disco) se pueblan en los primeros segundos. Las estadísticas rolling (media, mediana, min, max, stddev) empiezan a moverse.
3. Pasa la primera ventana de anomalía (cada 30 segundos): latencia, errores, distribuciones de inputs y valores de predicción cambian al mismo tiempo — los tiles correspondientes del Alert Status Overview se ponen rojos.
4. **PanicDuty** (local: [http://localhost:8080](http://localhost:8080) — AWS: [https://panicduty.3-226-31-220.sslip.io](https://panicduty.3-226-31-220.sslip.io)) muestra las alertas firing en vivo (y resolved cuando termina la ventana).
5. **Kibana Discover** (local: [http://localhost:5601/app/discover](http://localhost:5601/app/discover) — AWS: [https://kibana.3-226-31-220.sslip.io/app/discover](https://kibana.3-226-31-220.sslip.io/app/discover)) muestra el otro pilar de observabilidad — los logs por evento. El data view `model-api-logs-*` ya está seleccionado por default (lo crea `kibana-init`). Filtrando por `anomaly_window: true` se ve, request por request, qué inputs llegaron al modelo durante la ventana (barrios `industrial`, `square_meters` más grandes, `bedrooms` faltantes) y qué predicciones salieron.6. El **dashboard auto-provisionado de Kibana** `ML Drift Investigation` (local: [http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard](http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard) — AWS: [https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard](https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard)) tiene 2 paneles que se pueblan durante las anomalías: el histograma **Predicciones con missing features** (vacío en tráfico normal, predicciones concentradas en $1.4M-$1.8M durante anomalías cuando `bedrooms=None` dispara la imputación con mediana del training set: `bedrooms=3`), y la tabla **Top-20 predicciones extremas con feature context** (siempre 20 filas ordenadas por `prediction` desc, dominadas durante anomalías por predicciones $1.5M-$2.2M con `neighborhood=industrial|downtown` y `square_meters > 320`).
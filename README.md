# Monitorización de ML en Producción: Demo Local

## Índice
- [Introducción](#introducción)
- [Servicios](#servicios)
- [Qué es Docker y por qué se usa](#qué-es-docker-y-por-qué-se-usa)
- [Qué es Docker Compose y qué hace en la demo](#qué-es-docker-compose-y-qué-hace-en-la-demo)
- [Cómo instalar Docker](#cómo-instalar-docker)
- [Permisos de Docker](#permisos-de-docker)
- [Inicio rápido](#inicio-rápido)
- [Modos de la demo](#modos-de-la-demo)
- [Qué esperar después del arranque](#qué-esperar-después-del-arranque)
- [Estructura de carpetas](#estructura-de-carpetas)
- [Qué hace cada archivo](#qué-hace-cada-archivo)
- [Despliegue público para el póster de clase](#despliegue-público-para-el-póster-de-clase)
- [Endpoints](#endpoints)
- [Qué mirar durante una demo](#qué-mirar-durante-una-demo)

## Introducción

Una demo local de monitorización de un sistema de ML en producción, inspirada en el artículo [Monitoring Machine Learning Models in Production](https://christophergs.com/machine%20learning/2020/03/14/how-to-monitor-machine-learning-models/) de Christopher GS. Simula una API de predicción de precios inmobiliarios que alterna entre modo normal y ventanas de anomalía cada 30 segundos, lo que dispara alertas y deja ver el comportamiento del stack de monitoreo (Prometheus, Grafana, Alertmanager, ELK) frente a problemas típicos de ML: input drift, prediction drift, features faltantes, latencia, errores HTTP.


## Servicios

La demo combina 11 servicios. Para una explicación a fondo de cada uno (qué hace, cómo se conecta con el resto, qué métricas/logs produce), ver [`docs/descripcion_demo_es.md`](docs/descripcion_demo_es.md):

| Servicio | Rol | Doc detallada |
|---|---|---|
| `model_api` | API de predicción + generador de tráfico sintético + emisión de métricas y logs | [§4](docs/descripcion_demo_es.md#4-la-api-de-predicción-model_api) · [model_api_es.md](model_api/docs/model_api_es.md) |
| `prometheus` | Recolección de métricas y evaluación de alertas | [§5](docs/descripcion_demo_es.md#5-prometheus-el-colector-de-métricas) · [prometheus_es.md](prometheus/docs/prometheus_es.md) |
| `grafana` | Dashboard visual de métricas | [§6](docs/descripcion_demo_es.md#6-grafana-el-dashboard-de-ml) · [grafana_es.md](grafana/docs/grafana_es.md) |
| `alertmanager` | Agrupación y enrutamiento de alertas | [§5](docs/descripcion_demo_es.md#5-prometheus-el-colector-de-métricas) · [prometheus_es.md](prometheus/docs/prometheus_es.md#alertmanageryml) |
| `panic_duty` | Receptor de webhooks de alertas + UI mock estilo PagerDuty | [§7](docs/descripcion_demo_es.md#7-panicduty-el-receptor-de-alertas) · [panic_duty_es.md](panic_duty/docs/panic_duty_es.md) |
| `filebeat` | Log shipper (lee stdout de `model_api` vía Docker, reenvía a Logstash) | [§8](docs/descripcion_demo_es.md#8-el-pipeline-de-logs-filebeat--logstash--elasticsearch) · [filebeat_es.md](filebeat/docs/filebeat_es.md) |
| `logstash` | Procesador intermedio (parsea texto plano a JSON con `grok`, normaliza tipos) | [§8](docs/descripcion_demo_es.md#8-el-pipeline-de-logs-filebeat--logstash--elasticsearch) · [logstash_es.md](logstash/docs/logstash_es.md) |
| `elasticsearch` | Almacenamiento de logs (índices diarios `model-api-logs-*`) | [§8](docs/descripcion_demo_es.md#8-el-pipeline-de-logs-filebeat--logstash--elasticsearch) · [elasticsearch_es.md](elasticsearch/docs/elasticsearch_es.md) |
| `kibana` | UI para explorar logs (Discover + dashboard `ML Drift Investigation`) | [§9](docs/descripcion_demo_es.md#9-kibana-exploración-de-logs-y-dashboard) · [kibana_es.md](kibana/docs/kibana_es.md) |
| `kibana-init` | Bootstrap one-shot: crea data view y aprovisiona el dashboard de Kibana | [§9](docs/descripcion_demo_es.md#9-kibana-exploración-de-logs-y-dashboard) · [kibana_es.md](kibana/docs/kibana_es.md) |
| `caddy` *(opcional, profile `poster`)* | Reverse proxy con HTTPS automático para exponer la demo en una VM pública | [Despliegue público](#despliegue-público-para-el-póster-de-clase) |

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

Luego abra:

- Grafana — ML System Dashboard: [http://localhost:3000/d/ml-system](http://localhost:3000/d/ml-system) (o [http://localhost:3000](http://localhost:3000) para la página principal)
- Prometheus: [http://localhost:9090](http://localhost:9090)
- Health del API: [http://localhost:8000/health](http://localhost:8000/health)
- PanicDuty (UI de alertas): [http://localhost:8080](http://localhost:8080)
- Kibana — Discover (logs): [http://localhost:5601/app/discover](http://localhost:5601/app/discover)
- **Kibana — Dashboard `ML Drift Investigation`**: [http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard](http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard)
- Logstash monitoring API: [http://localhost:9600/_node/stats](http://localhost:9600/_node/stats)

**En el deployment público del póster** (`make poster-up`, ver [Despliegue público para el póster de clase](#despliegue-público-para-el-póster-de-clase) y la guía específica de [AWS](docs/aws_es.md)) las 6 herramientas listadas en el `Caddyfile` quedan detrás de subdominios HTTPS con cert de Let's Encrypt automático. Las URLs apuntan a la EIP del deployment actual (`3-226-31-220` con guiones, que sslip.io resuelve a `3.226.31.220`); si re-desplegás con otra IP, regenerá las URLs reemplazando ese tramo en cada hostname:

- Grafana — ML System Dashboard: [https://grafana.3-226-31-220.sslip.io/d/ml-system](https://grafana.3-226-31-220.sslip.io/d/ml-system) (o [https://grafana.3-226-31-220.sslip.io](https://grafana.3-226-31-220.sslip.io) para la home)
- Prometheus: [https://prometheus.3-226-31-220.sslip.io](https://prometheus.3-226-31-220.sslip.io)
- Alertmanager: [https://alertmanager.3-226-31-220.sslip.io](https://alertmanager.3-226-31-220.sslip.io)
- Health del API: [https://api.3-226-31-220.sslip.io/health](https://api.3-226-31-220.sslip.io/health)
- API Swagger UI: [https://api.3-226-31-220.sslip.io/docs](https://api.3-226-31-220.sslip.io/docs)
- PanicDuty (UI de alertas): [https://panicduty.3-226-31-220.sslip.io](https://panicduty.3-226-31-220.sslip.io)
- Kibana — Discover (logs): [https://kibana.3-226-31-220.sslip.io/app/discover](https://kibana.3-226-31-220.sslip.io/app/discover)
- **Kibana — Dashboard `ML Drift Investigation`**: [https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard](https://kibana.3-226-31-220.sslip.io/app/dashboards#/view/ml-derived-fields-dashboard)
- Logstash monitoring API: solo desde dentro de la VM (`docker exec` o `curl localhost:9600` adentro) — no se expone afuera

Qué debería pasar:

- dentro de 1-2 minutos, Grafana debería mostrar tráfico sostenido,
- poco después, las ventanas de anomalía deberían hacer que algunos paneles cambien más bruscamente,
- las reglas de alerta visibles en http://localhost:9090/alerts pasarán a Pending/Firing durante las ventanas de anomalía,
- en el dashboard de Kibana, el panel **Predicciones con missing features** debería poblarse durante las ventanas de anomalía cuando `bedrooms=None` dispara la imputación con mediana del training set (`bedrooms=3`), y la tabla **Top-20 outliers** debería llenarse con predicciones extremas y full feature context.

En Kibana **Discover**, el data view `model-api-logs-*` ya está seleccionado por defecto (lo crea kibana-init). Desde ahí podés:

- ver el stream de eventos de predicción uno por uno,
- filtrar por campos del log: `event_type`, `http_status`, `anomaly_window`, `internal`, `features.neighborhood`, `missing_features`, `latency_ms`, `prediction`,
- ajustar la ventana de tiempo (arriba a la derecha) para mirar las últimas predicciones o una ventana específica de anomalía.

Para el **dashboard auto-provisionado** (`ML Drift Investigation`), abrí Kibana → menú hamburguesa → *Analytics → Dashboards* → seleccionar `ML Drift Investigation`. Tiene 2 paneles: predicciones con missing features (histograma) y top-20 predicciones extremas con full feature context (datatable).

Si la máquina tiene poca RAM, ver [Modos de la demo](#modos-de-la-demo) para la versión mínima de 3 servicios.

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
Para exponer la demo a internet desde una VM pública — típicamente para una sesión de pósters donde códigos QR linkean a las herramientas en vivo por HTTPS. Agrega un servicio `caddy` (gateado detrás del profile `poster` de Compose) que termina TLS con certificados Let's Encrypt automáticos y hace reverse-proxy de seis herramientas (Grafana, Prometheus, Alertmanager, PanicDuty, la API del modelo y Kibana), cada una en su propio subdominio sslip.io. **No arranca por defecto en local** — solo cuando pasás `--profile poster` (o usás el atajo `make poster-up`).

Para los detalles operacionales — cómo levantarlo en AWS EC2, atajos del Makefile, hardening (`GF_ANONYMOUS_ROLE=Viewer`, `GF_ADMIN_PASSWORD`), tips de QR, IPs estables — ver la sección [Despliegue público para el póster de clase](#despliegue-público-para-el-póster-de-clase) más abajo.

## Qué esperar después del arranque

### Dentro del primer minuto

**En Grafana** ([http://localhost:3000/d/ml-system](http://localhost:3000/d/ml-system)):
- debería poblarse el panel de request rate,
- debería verse la latencia,
- deberían empezar a llenarse las métricas de predicción e inputs,
- la métrica de `bedrooms` debería comenzar a actualizarse junto con el resto de inputs,
- CPU, memoria y disco deberían ser no cero,
- media, mediana, mínimo, máximo y desviación estándar deberían comenzar a actualizarse.

**En Kibana** ([http://localhost:5601/app/discover](http://localhost:5601/app/discover)):
- los primeros documentos deberían aparecer en Discover dentro de 30-60 segundos (el tiempo que tarda Logstash en procesar el primer batch que recibe de Filebeat),
- el data view `model-api-logs-*` ya está seleccionado por default (lo crea `kibana-init`),
- los campos estructurados que produce el grok parsing de Logstash (`event_type`, `http_status`, `latency_ms`, `prediction`, `features.*`, `missing_features`, etc.) deberían aparecer en el sidebar de campos disponibles.

### Dentro de pocos minutos

**En Grafana / PanicDuty:**
- debería comenzar una ventana de anomalía,
- deberían dispararse una o más alertas,
- PanicDuty ([http://localhost:8080](http://localhost:8080)) debería mostrar incidentes activos,
- el dashboard de Grafana debería mostrar que las métricas operativas y las métricas específicas de ML cambian juntas.

**En el dashboard auto-provisionado de Kibana** ([http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard](http://localhost:5601/app/dashboards#/view/ml-derived-fields-dashboard)):
- el histograma **Predicciones con missing features** debería empezar vacío (durante tráfico normal no faltan features) y poblarse durante anomalías con predicciones concentradas alrededor de $1.4M-$1.8M (cuando `bedrooms=None` dispara la imputación con mediana del training set: `bedrooms=3`),
- la tabla **Top-20 predicciones extremas con feature context** debería mostrar siempre 20 filas ordenadas por `prediction` desc, y durante anomalías las filas se dominan con predicciones $1.5M-$2.2M con `neighborhood=industrial|downtown` y `square_meters > 320` — full feature context per-evento para drill-down post-alerta.

## Estructura de carpetas
Esta es la estructura relevante de `monitoring_demo/`:

```text
monitoring_demo/
├── README_EN.md
├── README_ES.md
├── docker-compose.yml
├── Makefile
├── Caddyfile
├── .env.poster.example
├── files_root/
│   ├── files_root_en.md
│   └── files_root_es.md
├── docs/
│   ├── aws_en.md
│   ├── aws_es.md
│   ├── descripcion_demo_en.md
│   └── descripcion_demo_es.md
├── presentacion/             # gitignoreada — material de presentación
│   ├── presentacion_completa.md
│   └── presentacion_corta.md
├── model_api/
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── prometheus/
│   ├── prometheus.yml
│   ├── rules.yml
│   └── alertmanager.yml
├── grafana/
│   ├── dashboards/
│   │   └── ml_dashboard.json
│   └── provisioning/
│       ├── dashboards/
│       │   └── dashboard.yml
│       └── datasources/
│           └── datasource.yml
├── panic_duty/
│   ├── app.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── templates/
│       └── index.html
├── filebeat/
│   └── filebeat.yml
├── logstash/
│   ├── config/
│   │   └── logstash.yml
│   └── pipeline/
│       └── logstash.conf
├── elasticsearch/
│   └── model-api-logs-template.json
└── kibana/
    └── init/
        ├── import.sh
        ├── dashboards/
        │   └── ml-derived-fields.ndjson
        └── saved_objects/
            ├── 00-lens-prediction-with-missing.json
            ├── 01-lens-top-prediction-outliers.json
            └── 05-dashboard-ml-derived-fields-dashboard.json
```

## Qué hace cada archivo

Resúmenes breves abajo; los archivos `README_ES.md` por carpeta contienen recorridos detallados.

### Archivos raíz
> Ver [files_root_es.md](files_root/files_root_es.md) para la referencia completa de los archivos a nivel raíz.

- `README_EN.md`
  - Documentación en inglés de la demo.
- `README_ES.md`
  - Documentación en español de la demo.
- `docker-compose.yml`
  - Define los 10 servicios distribuidos en los tres grupos lógicos (núcleo, alertas, logs). También configura puertos, redes, archivos montados, variables de entorno y el profile opcional `poster` que activa el reverse proxy Caddy para despliegues públicos.
- `Caddyfile`
  - Configuración del reverse proxy Caddy usado por el flujo de despliegue público. Lee seis hostnames de variables de entorno (Grafana, Prometheus, Alertmanager, PanicDuty, API del modelo, Kibana) y rutea cada uno al servicio que corresponda. Solo se carga cuando el profile `poster` está activo.
- `.env.poster.example`
  - Plantilla del archivo `.env.poster` para despliegue público (que está en gitignore). Ver [Despliegue público para el póster de clase](#despliegue-público-para-el-póster-de-clase).
- `Makefile`
  - Define targets cortos de Make (`poster-up`, `poster-down`, `poster-logs`, `poster-status`) que envuelven las invocaciones más largas de `docker compose --env-file .env.poster --profile poster ...`. Ver la subsección de atajos del Makefile en [Despliegue público para el póster de clase](#despliegue-público-para-el-póster-de-clase).

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

## Despliegue público para el póster de clase
Esta sección es para el caso específico de levantar una copia de la demo accesible públicamente para una sesión de pósters — códigos QR en un póster, cada uno abriendo una de las seis herramientas detrás de Caddy en vivo en el navegador del teléfono.

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

### Endurecimiento
- `.env.poster.example` ya setea `GF_ANONYMOUS_ROLE=Viewer` (solo lectura). Esto baja el rol anónimo de Grafana desde el default local `Admin`, así los que escanean el QR no pueden editar dashboards.
- Setear `GF_ADMIN_PASSWORD` a algo fuerte antes de levantar el stack — la página es accesible desde la internet pública.
- `.env.poster` está en gitignore — nunca commitearlo.

### Tips para los QR
- Si codificás los hostnames de sslip.io directamente, regenerá los QR cada vez que cambie la IP pública de la VM. En AWS específicamente, asigná una Elastic IP para mantener la dirección estable entre stop/start de la instancia.

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

## Qué mirar durante una demo
Si va a presentar esto a otras personas, una secuencia simple es:

1. Arrancar el stack.
2. Abrir Grafana y explicar las secciones del dashboard, empezando por la fila **Alert Status Overview** arriba del todo (todos los tiles deberían estar en verde).
3. Mostrar que el servicio ya está generando tráfico.
4. Explicar que la monitorización tradicional de software no alcanza para ML.
5. Esperar la ventana de anomalía.
6. Mostrar cómo cambian latencia, errores, distribuciones de inputs y valores de predicción al mismo tiempo — y señalar cómo el/los tile(s) correspondientes del overview se ponen rojos y aparece la banda roja del umbral en los paneles de series de tiempo.
7. Abrir PanicDuty y mostrar las alertas correspondientes.
8. Abrir Kibana en **Discover** y mostrar el otro pilar de observabilidad — los logs por evento. Filtrar por `anomaly_window: true` para ver, request por request, qué inputs llegaron al modelo durante la ventana (barrios `industrial`, `square_meters` más grandes, `bedrooms` faltantes) y qué predicciones salieron. Esto hace concreto el punto de la **Sección 9** del artículo: las métricas resumen, los logs explican.

Eso vuelve visible el argumento principal del artículo:

- una infraestructura sana no implica necesariamente un comportamiento sano del modelo,
- métricas + logs juntos cubren los dos pilares de observabilidad: agregados (Grafana) e inspección por evento (Kibana).


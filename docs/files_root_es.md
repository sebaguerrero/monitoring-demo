# Archivos a nivel raíz — referencia

Los cuatro archivos a nivel orquestación en la raíz de `monitoring_demo/` no pertenecen a ningún servicio en particular — describen cómo los servicios se juntan, dónde Caddy rutea el tráfico en el deployment público, cómo invocar el deployment con un solo comando y qué env vars setear cuando lo hacés. Este documento explica cada uno en detalle.

| Archivo | Usado por | Propósito |
|---|---|---|
| `docker-compose.yml` | `docker compose` | definiciones de servicios, puertos, volúmenes, redes, el profile `poster` |
| `Caddyfile` | el contenedor Caddy (solo poster) | mappings de reverse-proxy desde hostnames públicos hacia servicios internos |
| `Makefile` | `make` | atajos para la invocación larga `docker compose --env-file --profile` |
| `.env.poster.example` | lector / template de copia | la forma y defaults para el archivo `.env.poster` que creás en la VM |

Para documentación por carpeta ver el `README_ES.md` adentro de cada subcarpeta.

## `docker-compose.yml`

### Qué es
El archivo Compose principal. Define todos los servicios que componen la demo, la red que comparten, los volúmenes nombrados que usa Caddy y el profile opcional `poster` que activa el reverse proxy público.

### Rol en el stack
Todo arranca desde acá. `docker compose up` buildea las dos imágenes custom (`model_api`, `panic_duty`), pulla el resto, levanta la red bridge `monitor_net`, monta cada archivo de config de este repo en el contenedor correcto y arranca todo en el orden adecuado.

### Recorrido — servicios

El archivo declara once servicios. Diez levantan por default; el undécimo (`caddy`) está detrás de `profiles: ["poster"]` y solo arranca cuando optás explícitamente.

**`model_api`** *(build custom, puerto 8000)*

- `build: ./model_api` — Compose corre `docker build` sobre esa carpeta. Ver [`model_api_es.md`](../model_api/docs/model_api_es.md).
- `ports: "8000:8000"` — expone la API en el host. Útil localmente; en la VM pública el puerto no es estrictamente necesario porque Caddy la alcanza por la red interna.
- `environment:` — seis perillas de demo (`MODEL_VERSION`, `DEMO_BASE_RPS`, `DEMO_ANOMALY_INTERVAL_SECONDS`, `DEMO_ANOMALY_DURATION_SECONDS`, `DEMO_ANOMALY_FAILURE_RATE`, `DEMO_PREDICTION_STATS_WINDOW_SECONDS`) más `GF_ADMIN_PASSWORD` (que `model_api` usa para autenticar contra Grafana al postear annotations al hacer `bump_version`). El model API las lee al arrancar; ver su README para qué hace cada una.

**`prometheus`** *(prom/prometheus:v2.45.0, puerto 9090)*

- Pinned a v2.45.0 (LTS al momento de escribir la demo).
- Monta `./prometheus/prometheus.yml` y `./prometheus/rules.yml` en `/etc/prometheus/`. Ver [`prometheus_es.md`](../prometheus/docs/prometheus_es.md).
- `command: ['--config.file=/etc/prometheus/prometheus.yml']` — override explícito aunque coincida con el default; protege contra que versiones futuras de la imagen cambien la ubicación default.
- `depends_on: model_api` — Compose arranca `model_api` primero. Es solo orden de arranque, no healthcheck, así que Prometheus puede scrapear y errar los primeros segundos mientras la API termina de bootear.

**`alertmanager`** *(prom/alertmanager:v0.25.0, puerto 9093)*

- Monta `./prometheus/alertmanager.yml` en `/etc/alertmanager/`. Ver [`prometheus_es.md`](../prometheus/docs/prometheus_es.md#alertmanageryml).
- Mismo patrón explícito de override `--config.file` que Prometheus.

**`panic_duty`** *(build custom, puerto 8080)*

- `build: ./panic_duty`. Ver [`panic_duty_es.md`](../panic_duty/docs/panic_duty_es.md).

**`grafana`** *(grafana/grafana:10.0.3, puerto 3000)*

- Monta tanto `./grafana/provisioning` (auto-load de datasource + dashboard) como `./grafana/dashboards` (el JSON en sí). Ver [`grafana_es.md`](../grafana/docs/grafana_es.md).
- `GF_SECURITY_ADMIN_PASSWORD: ${GF_ADMIN_PASSWORD:-admin}` — default `admin` localmente, sobreescribible vía la env var en el deployment público.
- `GF_AUTH_ANONYMOUS_ENABLED: true` — el acceso anónimo de solo lectura está activado para que los que escanean el QR no vean una página de login.
- `GF_AUTH_ANONYMOUS_ORG_ROLE: ${GF_ANONYMOUS_ROLE:-Admin}` — default `Admin` localmente (útil para editar dashboards) y se sobreescribe a `Viewer` en el deployment público para que visitantes anónimos no puedan editar nada.
- `depends_on: prometheus` — solo orden de arranque.

**`elasticsearch`** *(docker.elastic.co/elasticsearch/elasticsearch:8.17.0, puerto 9200)*

- `discovery.type=single-node` — short-circuit de la formación del cluster; requerido para una demo de un nodo.
- `xpack.security.enabled=false` — desactiva auth/TLS para que la demo no necesite certs ni credenciales. **Solo grado demo.**
- `ES_JAVA_OPTS=-Xms512m -Xmx512m` — limita el heap JVM a 512 MB. Producción dimensionaría mucho más alto; este tamaño contribuye a que el stack completo entre cómodo en ~3 GB de RAM Docker (uso real ~2.6 GB).
- `healthcheck:` hace polling a `/_cluster/health` cada 10 s con timeout de 5 s, hasta 12 reintentos. Otros servicios que dependen de Elasticsearch esperan que el healthcheck pase a verde.

**`kibana`** *(docker.elastic.co/kibana/kibana:8.17.0, puerto 5601)*

- `ELASTICSEARCH_HOSTS=http://elasticsearch:9200` — DNS por nombre de servicio sobre la red bridge.
- `depends_on: elasticsearch` con `condition: service_healthy` — Compose espera el healthcheck de ES antes de arrancar Kibana, lo que evita el log spam ruidoso de Kibana sobre "ES aún no listo" en arranques en frío.

**`logstash`** *(`docker.elastic.co/logstash/logstash:8.17.0`, puertos 5044 + 9600)*

- `image: docker.elastic.co/logstash/logstash:8.17.0` — imagen oficial sin build local. Ver [`logstash_es.md`](../logstash/docs/logstash_es.md).
- `volumes:` monta tres archivos como volúmenes read-only sobre la imagen oficial:
  - `./logstash/config/logstash.yml` → `/usr/share/logstash/config/logstash.yml` (config mínima del runtime: puerto 9600 para la API de monitoreo, X-Pack monitoring deshabilitado).
  - `./logstash/pipeline/logstash.conf` → `/usr/share/logstash/pipeline/logstash.conf` (el pipeline: input `beats`, filtros `grok`/`mutate`/`date`, output a Elasticsearch).
  - `./elasticsearch/model-api-logs-template.json` → `/usr/share/logstash/templates/model-api-logs.json` (el template de índice). Logstash lo sube a Elasticsearch al arrancar vía `manage_template => true` en su output `elasticsearch` — así los mappings de campos se instalan sin sidecar externo. Ver [`elasticsearch_es.md`](../elasticsearch/docs/elasticsearch_es.md).
- `ports: 5044, 9600` — 5044 es el puerto del input `beats` (donde Filebeat se conecta); 9600 es la API HTTP de monitoreo de Logstash (`/_node/stats` para chequear que el pipeline esté vivo).
- `depends_on: elasticsearch` con `condition: service_healthy` — Logstash espera que Elasticsearch esté listo antes de inicializar su pipeline (y antes de emitir la llamada de registro del template).
- `healthcheck:` hace polling a `localhost:9600/_node/stats` cada 10 s. Otros servicios que dependen de Logstash (Filebeat) esperan a que pase a healthy.

**`filebeat`** *(docker.elastic.co/beats/filebeat:8.17.0, sin puerto)*

- `user: root` — requerido para leer `/var/lib/docker/containers` y el socket Docker.
- `command: ["filebeat", "-e", "--strict.perms=false"]` — `-e` loguea a stderr, `--strict.perms=false` permite que el `filebeat.yml` montado en solo lectura tenga ownership distinto a Filebeat.
- Tres volume mounts: `filebeat.yml` (la config, ver [`filebeat_es.md`](../filebeat/docs/filebeat_es.md)), `/var/lib/docker/containers` (solo lectura, donde Docker guarda los archivos de log por contenedor) y `/var/run/docker.sock` (solo lectura, usado por autodescubrimiento de Docker para enterarse de los contenedores corriendo).
- `depends_on:` Logstash healthy + `model_api` started. El gate de Logstash garantiza que el procesador esté listo para recibir (y que ya haya registrado el template de índice contra Elasticsearch) antes de que Filebeat empiece a empujar logs.

**`kibana-init`** *(curlimages/curl:8.5.0, sin puerto, corre una vez)*

- Monta `./kibana/init` en `/init` y corre `sh /init/import.sh`. Ver [`kibana_es.md`](../kibana/docs/kibana_es.md).
- Tres responsabilidades: (1) crear el data view `model-api-logs-*` y dejarlo como default, (2) iterar sobre los archivos en `/init/saved_objects/*.json` y POSTear cada saved object (2 panels Lens + 1 dashboard) vía `/api/saved_objects/<type>/<id>?overwrite=true`, (3) salir.
- El POST directo evita las migraciones automáticas del endpoint `/_import` que esperan campos legacy del schema viejo y romperían los objects 8.x actuales.
- `restart: "no"` — cuando el script termina, el contenedor sale y se queda salido. `docker compose ps` lo muestra en estado `Exit 0`, que es lo correcto.

**`caddy`** *(caddy:2-alpine, puertos 80/443, solo profile `poster`)*

- `profiles: ["poster"]` — el portón que mantiene a Caddy fuera del desarrollo local. Ver [`Caddyfile`](#caddyfile) abajo.
- `ports: 80, 443` — el desafío HTTP-01 de Let's Encrypt necesita el puerto 80; HTTPS necesita 443.
- Monta `./Caddyfile` en solo lectura más dos volúmenes nombrados (`caddy_data`, `caddy_config`) para que los certificados de Let's Encrypt y el estado de Caddy sobrevivan reinicios del contenedor. Sin los volúmenes, cada restart re-emitiría certificados y arriesgaría chocar contra los rate limits de Let's Encrypt.
- `environment:` — `${GRAFANA_HOST:-}`, `${PROMETHEUS_HOST:-}`, `${ALERTMANAGER_HOST:-}`, `${PANICDUTY_HOST:-}`, `${API_HOST:-}`, `${KIBANA_HOST:-}`. Los defaults vacíos silencian warnings de Compose durante `docker compose config` local aunque los valores no sean usables; el deployment del póster pasa un `.env.poster` real.
- `depends_on:` los seis servicios que Caddy proxea.

### Recorrido — redes y volúmenes

- `networks.monitor_net.driver: bridge` — una única red bridge user-defined de Docker. Todos los servicios se conectan a ella. El DNS por nombre de servicio funciona solo dentro de redes así (la bridge default no provee DNS, por eso declaramos la nuestra).
- `volumes.caddy_data` y `volumes.caddy_config` — declaraciones vacías de volúmenes nombrados, usadas solo por el servicio `caddy`. `caddy_data` guarda los certs de Let's Encrypt + cuenta ACME; `caddy_config` guarda el cache de config runtime de Caddy. Persisten entre reinicios del contenedor. Se pueden borrar con `docker compose down -v`, pero en el deployment público eso re-emite todos los certs y cuenta contra el rate limit de Let's Encrypt (5 certs duplicados por hostname cada 7 días), por eso `make poster-fresh` omite el `-v` deliberadamente para conservarlos.

## `Caddyfile`

### Qué es
La config de sites de Caddy. Usado solo cuando el profile `poster` está activo. Define seis virtual hosts que proxean a los seis servicios internos que Caddy necesita exponer públicamente.

### Rol en el stack
Caddy lee este archivo al arrancar, expande los placeholders `{$VAR}` contra el environment del contenedor y se configura. Con las seis vars de host seteadas a hostnames sslip.io reales, Caddy:

- Escucha en el puerto 443 para cada uno de esos seis hostnames.
- Pide un certificado Let's Encrypt por hostname al primer pedido.
- Hace reverse-proxy del tráfico al servicio interno correspondiente.
- Escucha en el puerto 80 para el desafío ACME HTTP-01 durante la emisión del certificado, después redirige todo el resto del tráfico 80 a HTTPS.

### Recorrido

El archivo son seis bloques de site casi idénticos. Cada bloque usa la forma compacta de Caddy: hostname (o cualquier dirección) seguido por directivas entre llaves.

```
{$GRAFANA_HOST} {
    reverse_proxy grafana:3000
}
```

- `{$GRAFANA_HOST}` — placeholder substituido desde el env del contenedor. Con `GRAFANA_HOST=grafana.18-204-12-50.sslip.io` seteado en `.env.poster`, esto se expande a ese hostname al arrancar.
- `reverse_proxy grafana:3000` — la directiva proxy integrada de Caddy. `grafana` es el nombre del servicio Compose, resuelto en la red Docker `monitor_net`. El puerto 3000 es el puerto interno de Grafana.

Los seis bloques difieren solo en el nombre de env var y el upstream:

| Placeholder de hostname | Servicio:puerto upstream | Qué sirve |
|---|---|---|
| `{$GRAFANA_HOST}` | `grafana:3000` | el dashboard unificado |
| `{$PROMETHEUS_HOST}` | `prometheus:9090` | métricas crudas + UI de reglas de alerta |
| `{$ALERTMANAGER_HOST}` | `alertmanager:9093` | UI de Alertmanager (alertas activas agrupadas) |
| `{$PANICDUTY_HOST}` | `panic_duty:8080` | UI mock de incidentes |
| `{$API_HOST}` | `model_api:8000` | Model API (Swagger UI en `/docs`, health en `/health`) |
| `{$KIBANA_HOST}` | `kibana:5601` | búsqueda de logs |

## `Makefile`

### Qué es
Un Makefile chico que envuelve las invocaciones de Compose más tipeadas — tanto las formas `docker compose --env-file .env.poster --profile poster ...` para el despliegue del póster como un target `fresh` para el ciclo de desarrollo local. `make` viene preinstalado en virtualmente cualquier entorno Linux/macOS, incluyendo la imagen Ubuntu de AWS EC2 usada en el deployment del póster, así que es cero dependencias.

### Rol en el stack
Opcional pero altamente recomendado para el flujo del día del póster. El comando Compose completo es difícil de tipear bien bajo estrés; `make poster-up` es difícil de mistypear.

### Recorrido

```makefile
.PHONY: poster-up poster-down poster-logs poster-status poster-fresh es-reset-logs fresh
```

Declara todos los targets como *phony* — Make no va a buscar archivos con esos nombres en disco, siempre va a correr la receta.

```makefile
poster-up:
	docker compose --env-file .env.poster --profile poster up -d --build
```

- `up -d --build` — buildea las imágenes custom, después arranca el stack completo en modo detached. `-d` devuelve el control al shell en lugar de streamear logs.

```makefile
poster-down:
	docker compose --env-file .env.poster --profile poster down
```

- Para y elimina los contenedores. Sin `-v`, los volúmenes nombrados (certificados Caddy, config Caddy) se preservan, lo que evita re-emitir certificados Let's Encrypt en el próximo `poster-up`.

```makefile
poster-logs:
	docker compose --env-file .env.poster --profile poster logs -f
```

- Tailea logs de cada servicio, siguiendo nuevas líneas (`-f`). Útil para chequeos rápidos de "¿está llegando algo a PanicDuty?".

```makefile
poster-status:
	docker compose --env-file .env.poster --profile poster ps
```

- Status de una línea por servicio. `docker compose ps` ya hace casi todo el trabajo; el wrapper solo mantiene los flags de env file y profile consistentes.

```makefile
es-reset-logs:
	-curl -fsS -X DELETE 'http://localhost:9200/model-api-logs-*'
	@echo
	docker compose restart logstash filebeat
```

- El target on-demand para limpiar los índices de logs ([`elasticsearch_es.md`](../elasticsearch/docs/elasticsearch_es.md)). Antes esto era un sidecar que corría en cada `docker compose up`; ahora es una acción deliberada del usuario.
- Borra todos los índices `model-api-logs-*` y reinicia Logstash + Filebeat. Reiniciar Logstash fuerza a que vuelva a registrar el template de índice contra el namespace (ahora vacío); reiniciar Filebeat dispara el re-ship al índice recién creado. El `-` al inicio en la línea de `curl` deja que Make tolere el caso donde no hay índices para borrar.

```makefile
fresh:
	docker compose down -v --rmi local --remove-orphans
	docker compose up -d --build
```

- El target "limpiar todo y volver a empezar" para desarrollo local. `down -v --rmi local --remove-orphans` para cada contenedor, borra cada volumen (nombrado y anónimo — incluyendo el volumen de datos de ES, así que los índices viejos desaparecen), elimina las imágenes buildeadas localmente para que el próximo build vuelva a leer el código fuente, y limpia contenedores residuales de configuraciones de compose previas. `up -d --build` después rebuildea y arranca el stack default en detached. Opera sobre los servicios default (sin `--profile poster`); para el flujo del despliegue público usar `poster-down` seguido de `poster-up`.

## `.env.poster.example`

### Qué es
El template **commiteado** del archivo gitignored `.env.poster` que el deployer crea en su VM. Contiene cada variable que el flujo de deployment público espera, con valores placeholder y comentarios inline.

### Rol en el stack
Nunca leído directamente por `docker compose`. El deployer corre `cp .env.poster.example .env.poster`, edita los valores, y `docker compose --env-file .env.poster ...` lee el archivo resultante. Compose substituye los valores en los placeholders `${...}` de `docker-compose.yml` antes de que arranque cualquier contenedor.

### Recorrido

El archivo tiene ocho variables, divididas en dos grupos por propósito.

**Hostnames (seis).** Le dicen a Caddy en qué subdominios sslip.io escuchar, y `docker-compose.yml` los inyecta al environment del contenedor Caddy como `GRAFANA_HOST` etc.

```
GRAFANA_HOST=grafana.YOUR-IP-WITH-DASHES.sslip.io
PROMETHEUS_HOST=prometheus.YOUR-IP-WITH-DASHES.sslip.io
ALERTMANAGER_HOST=alertmanager.YOUR-IP-WITH-DASHES.sslip.io
PANICDUTY_HOST=panicduty.YOUR-IP-WITH-DASHES.sslip.io
API_HOST=api.YOUR-IP-WITH-DASHES.sslip.io
KIBANA_HOST=kibana.YOUR-IP-WITH-DASHES.sslip.io
```

El placeholder `YOUR-IP-WITH-DASHES` recuerda al deployer substituir la IP pública de la VM con los puntos reemplazados por guiones (el wildcard DNS de sslip.io espera cualquiera de las dos formas, pero la forma con guiones es más amigable para URLs). Para una instancia en `18.204.12.50`, el hostname queda `grafana.18-204-12-50.sslip.io`.

**Endurecimiento de Grafana (dos).**

```
GF_ANONYMOUS_ROLE=Viewer
GF_ADMIN_PASSWORD=change-me-to-something-strong
```

- `GF_ANONYMOUS_ROLE=Viewer` cambia el rol de acceso anónimo del default local (`Admin`) a solo lectura. Cualquier visitante que escanee un QR aterriza como Viewer anónimo y no puede editar dashboards ni cambiar la configuración.
- `GF_ADMIN_PASSWORD` sobrescribe el default local `admin`. El placeholder `change-me-to-something-strong` está intencionalmente difícil de confundir con una password real.


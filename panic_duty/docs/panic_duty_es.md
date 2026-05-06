# PanicDuty — referencia de archivos

PanicDuty es una **UI mock de respuesta a incidentes estilo PagerDuty**. Recibe webhooks de Alertmanager, mantiene una lista en memoria de alertas firing y las renderiza en una única página HTML que se auto-refresca cada cinco segundos. La idea de esta carpeta **no** es shippear una herramienta real de respuesta a incidentes — es darle a la demo un destino visible al final del pipeline para las alertas sin requerir una cuenta real de PagerDuty ni un servicio externo. Todo cabe en ~50 líneas de Python y un template HTML.

Esta carpeta tiene cuatro archivos:

- `app.py` — aplicación FastAPI: receiver del webhook y UI HTML.
- `Dockerfile` — definición de la imagen del contenedor.
- `requirements.txt` — dependencias Python.
- `templates/index.html` — template Jinja2 para la UI.

## `app.py`

### Qué es
Una pequeña app FastAPI con dos rutas — un endpoint HTTP que llama Alertmanager (`POST /webhook`), y una página HTML que miran los humanos (`GET /`).

### Rol en el stack
- Alcanzable en el puerto 8080 dentro de la red `monitor_net`.
- Alertmanager tiene una entrada `webhook_configs[].url: 'http://panic_duty:8080/webhook'` que pushea acá el payload JSON estándar de Alertmanager cuando una alerta dispara o se resuelve.
- La página HTML en `GET /` se expone en el host en [http://localhost:8080](http://localhost:8080) (port mapping de Compose) y detrás de Caddy en [https://panicduty.3-226-31-220.sslip.io](https://panicduty.3-226-31-220.sslip.io) en el deployment público del póster.
- Todo el estado está en una lista Python en proceso — reiniciar el contenedor deja la lista de incidentes vacía hasta el próximo webhook.

### Recorrido

**Imports y setup (líneas 1–13).**

- `from fastapi import FastAPI, Request` — el objeto FastAPI y un tipo `Request` usado por templates Jinja.
- `from fastapi.responses import HTMLResponse` — usado como `response_class` en la ruta home para que devolver un string sea interpretado como HTML, no JSON.
- `from fastapi.templating import Jinja2Templates` — wrapper de FastAPI sobre Jinja2 que sabe cómo renderizar templates con un contexto de request HTTP.
- `app = FastAPI(title="PanicDuty Webhook Receiver")` — el title aparece en `/docs` (la UI Swagger autogenerada de FastAPI viene activada por default).
- `active_alerts = []` — todo el data store. Lista Python a nivel módulo. Vive solo lo que dura el proceso. Elegida por sobre una base de datos porque la demo no necesita persistencia y el artículo nunca lo pidió.
- `os.makedirs("templates", exist_ok=True)` — defensivo: asegura que existe el directorio `templates/` antes que Jinja2 intente leer de ahí. El Dockerfile copia los templates adentro, así que en uso normal es redundante, pero la línea permite correr la app desde un checkout sin el directorio presente.
- `templates = Jinja2Templates(directory="templates")` — loader Jinja bindeado a esa carpeta.

**`POST /webhook` (líneas 15–38).**

La forma del handler refleja el [payload genérico de webhook de Alertmanager](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config). Puntos clave:

- `payload = await request.json()` — Alertmanager manda un body JSON. El esquema completo incluye `version`, `groupKey`, `status`, `receiver`, `groupLabels`, `commonLabels`, `commonAnnotations`, `externalURL` y un array `alerts`. Solo consumimos el array `alerts`.
- Por cada alerta del array, el handler lee `labels.alertname` y `status` (uno de `firing` / `resolved`).
- **Camino firing**: dedup por `alertname`. Si una alerta con el mismo nombre ya está en `active_alerts`, skip; si no, append del objeto entrante completo. El dedup hace que una alerta que flapea y dispara dos veces sin resolverse en el medio aparezca una sola vez. Es intencional — Alertmanager ya agrupa por `alertname` (ver `prometheus/alertmanager.yml`), pero Compose puede entregar un webhook más de una vez durante ciertas carreras y no queremos cards duplicadas en la UI.
- **Camino resolved**: filtra `active_alerts` para sacar cada entrada con ese `alertname`. La card desaparece de la UI en el próximo refresh de 5 s.
- `print(f"Received webhook! Active alerts count: {len(active_alerts)}")` — emitido a stdout, capturado por `docker logs panic_duty`. Útil para verificar que los webhooks están llegando durante una demo.
- Devuelve un JSON `{"status": "success"}`. Alertmanager no actúa según el body de respuesta pero sí chequea el status HTTP — 200 significa que la alerta fue acusada. Un no-200 acá haría que Alertmanager reintente.

**`GET /` (líneas 40–46).**

- `@app.get("/", response_class=HTMLResponse)` — declara que la ruta devuelve HTML.
- `templates.TemplateResponse(request, "index.html", {"alerts": active_alerts, "request": request})` — renderiza `templates/index.html` con la lista de alertas actual. El primer `request` posicional es la firma preferida en FastAPI 0.100+; el kwarg `request` en el dict de contexto es lo que esperaban versiones más viejas de Jinja2Templates y se mantiene por compatibilidad.

## `Dockerfile`

### Qué es
La receta de build de la imagen del contenedor PanicDuty. Refleja `model_api/Dockerfile` casi línea por línea.

### Rol en el stack
Buildeado al arrancar por `docker compose up --build`. El archivo Compose referencia `build: ./panic_duty`, así que Compose alimenta este Dockerfile y el resto de la carpeta como contexto de build.

### Recorrido

- `FROM python:3.10-slim` — misma base que `model_api`. La variante slim quita docs y paquetes -dev; ahorra ~700 MB sobre la imagen completa `python:3.10`.
- `WORKDIR /app` — toda instrucción siguiente corre desde `/app` y el contenedor final también arranca ahí.
- `COPY requirements.txt .` luego `RUN pip install --no-cache-dir -r requirements.txt` — copiar solo requirements primero permite a Docker cachear la capa de instalación; rebuildar después de editar `app.py` no reinstala las deps. `--no-cache-dir` mantiene el cache de wheels fuera de la imagen.
- `COPY . .` — copia el resto de la carpeta (`app.py`, `templates/`) en `/app`. Como `requirements.txt` se copió antes, Docker reusa la capa de instalación.
- `EXPOSE 8080` — metadata declarativa. Compose igual tiene que publicar el puerto (lo hace en `docker-compose.yml`).
- `CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]` — servidor ASGI Uvicorn. `0.0.0.0` es necesario para que el puerto sea alcanzable desde fuera del contenedor; bindear a `127.0.0.1` aislaría la app de la red Compose.

## `requirements.txt`

### Qué es
Dependencias Python de PanicDuty. Tres paquetes, sin versiones pinned.

### Rol en el stack
Leído por `pip install` durante el build de Docker.

### Recorrido

- **`fastapi`** — el framework web. Provee la clase `FastAPI`, `Request`, `HTMLResponse` y `Jinja2Templates`. Async por default, corre sobre Uvicorn.
- **`uvicorn`** — el servidor ASGI que corre la app. Invocado desde el `CMD` del Dockerfile. Los extras `[standard]` (httptools, websockets, etc.) *no* se piden; la instalación pelada alcanza para servir HTTP.
- **`jinja2`** — el motor de templates que `Jinja2Templates` envuelve. En algunos setups ya es dependencia transitiva de FastAPI, pero listada explícitamente para que las instalaciones no dependan de la decisión transitiva de FastAPI.

## `templates/index.html`

### Qué es
El único template Jinja2 renderizado por `GET /`. Una página HTML autocontenida con CSS embebido y auto-refresh de 5 segundos.

### Rol en el stack
Cargado una vez al arrancar por `Jinja2Templates(directory="templates")`. Renderizado en cada `GET /`.

### Recorrido

**`<head>` (líneas 3–50).**

- `<title>` muestra el emoji panic literal en la pestaña del navegador. Lindo.
- `<style>` embebido. Sin CSS externo — mantiene la página autocontenida y evita pegarle a cualquier CDN que pueda estar bloqueada desde una red corporativa durante una presentación. Tema oscuro: fondo negro (`#111`), contenedor gris (`#222`), acento rojo de alerta (`#d32f2f`). Las clases `.status-banner.ok` (verde `#1b5e20`) y `.status-banner.alert` (rojo oscuro `#5f2120`) dan un resumen de salud arriba.
- `<script>` inline que llama a `setTimeout` con un callback que ejecuta `window.location.reload(1)` cada 5 s. Así es como la UI refleja cambios de estado — no hay loop WebSocket o fetch.

**`<body>` (líneas 51–73).**

- `<div class="container">` envuelve todo en un layout de card centrada.
- `<h1>` y `<h2>` son branding estático.
- `{% if alerts %}` — condicional Jinja sobre la lista `alerts` pasada desde el handler de la ruta.
  - **Hay alertas**: muestra el `.status-banner.alert` rojo y una lista de cards de alerta. Cada card muestra:
    - Prefijo `[FIRING]` (el filtro Jinja `| upper` también funcionaría con `[RESOLVED]`, pero en práctica `active_alerts` solo contiene alertas firing — las resolved se filtran out en el handler antes de llegar al template).
    - `alertname` desde los `labels` de la alerta.
    - `severity` desde `labels`.
    - `summary` desde `annotations` — la primera línea amigable definida en `prometheus/rules.yml`.
    - `description` desde `annotations` — la prosa explicativa más larga.
    - `startsAt` — el timestamp ISO 8601 de Alertmanager indicando cuándo empezó esta ventana firing.
  - **Sin alertas**: muestra el `.status-banner.ok` verde más un mensaje "No current panics".

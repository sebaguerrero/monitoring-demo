# PanicDuty — file reference

PanicDuty is a **mock PagerDuty-style incident UI**. It receives Alertmanager webhooks, keeps an in-memory list of currently firing alerts, and renders them in a single HTML page that auto-refreshes every five seconds. The point of this folder is **not** to ship a real incident-response tool — it's to give the demo a visible end-of-pipeline destination for alerts without requiring an actual PagerDuty account or any external service. The whole thing fits in ~50 lines of Python and one HTML template.

This folder contains four files:

- `app.py` — FastAPI application: webhook receiver and HTML UI.
- `Dockerfile` — container image definition.
- `requirements.txt` — Python dependencies.
- `templates/index.html` — Jinja2 template for the UI.

## `app.py`

### What it is
A small FastAPI app with two routes — one HTTP endpoint that Alertmanager calls (`POST /webhook`), and one HTML page that humans look at (`GET /`).

### Role in the stack
- Reachable on port 8080 inside the `monitor_net` network.
- Alertmanager has a `webhook_configs[].url: 'http://panic_duty:8080/webhook'` entry that pushes Alertmanager's standard JSON payload here whenever an alert fires or resolves.
- The HTML page at `GET /` is exposed on the host at [http://localhost:8080](http://localhost:8080) (Compose port mapping) and behind Caddy at [https://panicduty.3-226-31-220.sslip.io](https://panicduty.3-226-31-220.sslip.io) in the public poster deployment.
- All state is in-process Python list — restart the container and the incident list is empty until the next webhook arrives.

### Walkthrough

**Imports and setup (lines 1–13).**

- `from fastapi import FastAPI, Request` — FastAPI app object plus a `Request` type used by Jinja templates.
- `from fastapi.responses import HTMLResponse` — used as the `response_class` on the home route so that returning a string is interpreted as HTML, not JSON.
- `from fastapi.templating import Jinja2Templates` — FastAPI's wrapper over Jinja2 that knows how to render templates against an HTTP request context.
- `app = FastAPI(title="PanicDuty Webhook Receiver")` — title shows up in `/docs` (FastAPI's auto-generated Swagger UI is on by default).
- `active_alerts = []` — the entire data store. Module-level Python list. Lives only as long as the process. Picked over a database because the demo doesn't need persistence and the article never asked for it.
- `os.makedirs("templates", exist_ok=True)` — defensive: makes sure the `templates/` directory exists before Jinja2 tries to read from it. The Dockerfile copies the templates in, so under normal use this is redundant, but the line lets the app run from a checkout without the directory present.
- `templates = Jinja2Templates(directory="templates")` — Jinja loader bound to that folder.

**`POST /webhook` (lines 15–38).**

The handler shape mirrors Alertmanager's [generic webhook payload](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config). Highlights:

- `payload = await request.json()` — Alertmanager sends a JSON body. The full schema includes `version`, `groupKey`, `status`, `receiver`, `groupLabels`, `commonLabels`, `commonAnnotations`, `externalURL`, and an `alerts` array. We only consume the `alerts` array.
- For each alert in the array, the handler reads `labels.alertname` and `status` (one of `firing` / `resolved`).
- **Firing path**: dedupe by `alertname`. If an alert with the same name is already in `active_alerts`, skip; otherwise append the full incoming object. The dedup means a flapping alert that fires twice without resolving in between only shows once. This is intentional — Alertmanager already groups by `alertname` (see `prometheus/alertmanager.yml`), but Compose can deliver a webhook more than once during certain races and we don't want duplicate cards on the UI.
- **Resolved path**: filter `active_alerts` to remove every entry with that `alertname`. The card disappears from the UI on the next 5-second refresh.
- `print(f"Received webhook! Active alerts count: {len(active_alerts)}")` — emitted to stdout, captured by `docker logs panic_duty`. Useful for verifying that webhooks are landing during a demo.
- Returns a JSON `{"status": "success"}`. Alertmanager doesn't act on the response body but does check the HTTP status — 200 means the alert is acknowledged. A non-200 here would cause Alertmanager to retry.

**`GET /` (lines 40–46).**

- `@app.get("/", response_class=HTMLResponse)` — declares the route returns HTML.
- `templates.TemplateResponse(request, "index.html", {"alerts": active_alerts, "request": request})` — renders `templates/index.html` with the current alert list. The first positional `request` is FastAPI 0.100+'s preferred call signature; the kwarg `request` in the context dict is what older Jinja2Templates expected and is kept for backward compatibility.

## `Dockerfile`

### What it is
The image build recipe for the PanicDuty container. Mirrors `model_api/Dockerfile` almost line-for-line.

### Role in the stack
Built at startup time by `docker compose up --build`. The Compose file references `build: ./panic_duty`, so Compose feeds this Dockerfile and the rest of the folder as the build context.

### Walkthrough

- `FROM python:3.10-slim` — same base as `model_api`. The slim variant strips out documentation and -dev packages; saves ~700 MB versus the full `python:3.10` image.
- `WORKDIR /app` — every subsequent instruction runs from `/app` and the final container also starts there.
- `COPY requirements.txt .` then `RUN pip install --no-cache-dir -r requirements.txt` — copying just the requirements first lets Docker cache the install layer; rebuilding after editing `app.py` doesn't reinstall the deps. `--no-cache-dir` keeps the wheel cache out of the image.
- `COPY . .` — copies the rest of the folder (`app.py`, `templates/`) into `/app`. Because `requirements.txt` was copied earlier, Docker re-uses the install layer.
- `EXPOSE 8080` — declarative metadata. Compose still has to publish the port (it does in `docker-compose.yml`).
- `CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]` — Uvicorn ASGI server. `0.0.0.0` is required for the port to be reachable from outside the container; binding to `127.0.0.1` would isolate the app from the Compose network.

## `requirements.txt`

### What it is
Python dependencies for PanicDuty. Three packages, no version pins.

### Role in the stack
Read by `pip install` during the Docker build.

### Walkthrough

- **`fastapi`** — the web framework. Provides the `FastAPI` app class, `Request`, `HTMLResponse`, and `Jinja2Templates`. Async by default, runs on Uvicorn.
- **`uvicorn`** — the ASGI server that runs the app. Invoked from the Dockerfile's `CMD`. The `[standard]` extras (httptools, websockets, etc.) are *not* requested; the bare install is enough for serving HTTP.
- **`jinja2`** — the template engine `Jinja2Templates` wraps. Already a transitive dependency of FastAPI in some setups, but listed explicitly so installs don't depend on FastAPI's transitive choice.

## `templates/index.html`

### What it is
The single Jinja2 template rendered by `GET /`. A self-contained HTML page with embedded CSS and a 5-second auto-refresh.

### Role in the stack
Loaded once at startup by `Jinja2Templates(directory="templates")`. Rendered on every `GET /`.

### Walkthrough

**`<head>` (lines 3–50).**

- `<title>` shows the panic emoji literally in the browser tab. Cute.
- Embedded `<style>`. No external CSS — keeps the page self-contained and avoids hitting any CDN that might be blocked from a corporate network during a presentation. Dark theme: black background (`#111`), grey container (`#222`), red alert accent (`#d32f2f`). The `.status-banner.ok` (green `#1b5e20`) and `.status-banner.alert` (dark red `#5f2120`) classes give a top-of-page health summary.
- Inline `<script>` that calls `setTimeout` with a callback running `window.location.reload(1)` every 5 s. This is how the UI reflects state changes — there is no WebSocket or fetch loop.

**`<body>` (lines 51–73).**

- `<div class="container">` wraps everything for the centered card layout.
- `<h1>` and `<h2>` are static branding.
- `{% if alerts %}` — Jinja conditional on the `alerts` list passed from the route handler.
  - **Has alerts**: shows the red `.status-banner.alert` and a list of alert cards. Each card displays:
    - `[FIRING]` prefix (Jinja's `| upper` filter would also work for `[RESOLVED]`, but in practice `active_alerts` only contains firing alerts — resolved ones are filtered out in the handler before reaching the template).
    - `alertname` from the alert's `labels`.
    - `severity` from `labels`.
    - `summary` from `annotations` — the human-friendly first line defined in `prometheus/rules.yml`.
    - `description` from `annotations` — the longer prose explanation.
    - `startsAt` — Alertmanager's ISO 8601 timestamp for when this firing window began.
  - **No alerts**: shows the green `.status-banner.ok` plus a "No current panics" message.

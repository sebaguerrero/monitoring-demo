# Prometheus — referencia de archivos

Prometheus es el **motor de métricas** de la demo. Hace scraping de las series temporales que expone `model_api`, evalúa reglas de alerta contra ellas y envía las alertas firing a Alertmanager. Esta carpeta tiene tres archivos YAML: uno para el servidor Prometheus en sí, uno para las reglas de alerta que evalúa, y uno para Alertmanager (que técnicamente es un binario aparte pero vive junto a Prometheus por convención).

## `prometheus.yml`

### Qué es
La configuración principal del servidor Prometheus. Le dice qué scrapear, con qué frecuencia, de dónde cargar las reglas de alerta y a dónde mandar las alertas.

### Rol en el stack
- Montado en el contenedor de Prometheus en `/etc/prometheus/prometheus.yml` por `docker-compose.yml`.
- Se lee una vez al arrancar; los cambios requieren un reload (`docker compose restart prometheus`).
- Maneja todo el pipeline de métricas: sin una entrada acá, un target es invisible para Prometheus y para Grafana.

### Recorrido

**`global`** — defaults que aplican a cada scrape y regla salvo override por job.

- `scrape_interval: 5s` — Prometheus pulla `/metrics` de cada target cada 5 segundos. Mucho más agresivo que los defaults de producción (15–60 s) para que las ventanas de anomalía de la demo aparezcan en el dashboard en pocos scrapes en lugar de un minuto. El tráfico de fondo de `model_api` está dimensionado para sostener este ritmo.
- `evaluation_interval: 5s` — las reglas de alerta se reevalúan al mismo ritmo que el scraping. Combinado con la cláusula `for: 5s` de cada regla, eso significa que las alertas pueden pasar de `Pending` a `Firing` en ~10 s desde que el umbral subyacente se viola.

**`rule_files`** — paths a definiciones de reglas de alerta.

- `"/etc/prometheus/rules.yml"` — el archivo documentado más abajo. Montado desde `./prometheus/rules.yml` en el host.

**`alerting`** — adónde Prometheus pushea las alertas firing.

- `alertmanagers[0].static_configs[0].targets: ['alertmanager:9093']` — nombre de servicio en `monitor_net` más el puerto default de Alertmanager. Si el contenedor de Alertmanager no está corriendo, Prometheus igual evalúa las reglas y las muestra en `/alerts`, pero loguea warnings por no poder pushear.

**`scrape_configs`** — qué scrapear.

- `job_name: 'house_price_predictor'` — el único scrape job. El nombre del job se vuelve la etiqueta `job` en cada métrica que Prometheus guarda.
- `static_configs[0].targets: ['model_api:8000']` — único target en la red. `model_api` resuelve al contenedor FastAPI.
- `static_configs[0].labels.group: 'ml_models'` — etiqueta extra adjuntada a cada métrica de este target. Útil para agrupar en dashboards o expresiones de reglas cuando se agreguen más servicios.

## `rules.yml`

### Qué es
Cinco reglas de alerta de Prometheus agrupadas bajo `ml_monitoring_alerts`. Cada regla nombra una condición, le da una severidad y le pega anotaciones legibles que Alertmanager (y PanicDuty) muestran cuando la alerta dispara.

### Rol en el stack
- Cargadas por Prometheus al arrancar vía la directiva `rule_files:` en `prometheus.yml`.
- Reevaluadas cada `evaluation_interval` (5 s en la demo).
- Una regla cuya expresión devuelve un resultado no vacío durante al menos su ventana `for:` pasa de `Pending` a `Firing` y se pushea a Alertmanager.

Las reglas cubren tres pilares de monitorización (data-science / ops / disponibilidad), que es exactamente la división que la demo intenta enseñar.

### Recorrido — cada regla

Las cinco reglas comparten `for: 5s`. Eso significa que el umbral tiene que estar violado durante una evaluación (5 s) antes de disparar — el mínimo retardo para que la página de PanicDuty se ponga roja a los pocos segundos de que arranca la ventana de anomalía. Combinado con las ventanas de lookback cortas de `[15s]` abajo, eso es lo que hace que una cadencia 30 s verde / 30 s rojo alterne visiblemente; el trade-off es que un único scrape ruidoso puede flipear brevemente una alerta.

**1. `PredictionDriftDetected`** *(severidad: critical)*

```promql
sum(rate(ml_prediction_value_sum[15s])) / sum(rate(ml_prediction_value_count[15s])) > 600000
```

Computa la media móvil de predicciones de los últimos 15 segundos y dispara si supera **$600 000**. Mecánica: `ml_prediction_value` es un histograma; `_sum / _count` sobre `rate(…)` es el modismo PromQL estándar para "valor promedio por observación en la ventana de lookback". Una ventana de 15 s significa que la media sigue de cerca la señal de anomalía en vivo en lugar de quedar embarrada a través del ciclo entero. Durante una ventana de anomalía, `model_api` agrega un offset de 180k–420k a las predicciones, superando este umbral con facilidad.

**2. `HighApiLatency`** *(severidad: warning)*

```promql
sum(rate(api_request_duration_seconds_sum[15s])) / sum(rate(api_request_duration_seconds_count[15s])) > 0.35
```

Mismo patrón `_sum / _count` aplicado al histograma de duración de request. Umbral **350 ms**. La latencia normal es 15–50 ms; las ventanas de anomalía inyectan 450–850 ms extra, así que la media cruza 350 ms rápido.

**3. `ElevatedApiErrorRate`** *(severidad: critical)*

```promql
sum(rate(api_requests_total{http_status="500"}[15s])) / sum(rate(api_requests_total[15s])) > 0.08
```

Ratio de respuestas 500 sobre el total. Umbral **8 %**. Durante ventanas de anomalía, `model_api` lanza excepciones con la `DEMO_ANOMALY_FAILURE_RATE` configurada (default del código `0.2`, sobrescrita a `0.7` en `docker-compose.yml`), lo que pone el ratio móvil bien arriba del 8 %.

**4. `MissingFeatureSpike`** *(severidad: warning)*

```promql
sum(rate(ml_missing_feature_total[15s])) > 0.5
```

Tasa por segundo de incrementos de feature faltante (actualmente `bedrooms`). Umbral **0.5 incrementos/segundo** ≈ 30/min. El tráfico normal nunca omite `bedrooms`; las ventanas de anomalía lo omiten ~35 % de las veces (con `BASE_RPS=8`, eso es ~2.8 faltantes/s durante anomalía), así que la tasa cruza el umbral con facilidad.

**5. `ModelApiTargetDown`** *(severidad: critical)*

```promql
up{job="house_price_predictor"} == 0
```

El check clásico de "¿el target está vivo?". Prometheus pone `up` en 0 cuando un scrape falla. Demuestra el ángulo de disponibilidad operativa del marco de monitorización del artículo.

## `alertmanager.yml`

### Qué es
La configuración de enrutamiento de Alertmanager. Un receiver, una ruta, sin sutilezas de agrupamiento.

### Rol en el stack
- Cargado por Alertmanager al arrancar. Montado desde `./prometheus/alertmanager.yml`.
- Recibe cada alerta firing que Prometheus pushea.
- Las reenvía como webhooks a la UI mock PanicDuty.

### Recorrido

- `route.group_by: ['alertname']` — Alertmanager batchea notificaciones con el mismo alertname. Con cinco alertas independientes y ventanas de demo cortas, esto mayormente significa "manda cada una por separado" pero previene ráfagas si múltiples instancias de la misma alerta disparan a la vez.
- `route.group_wait: 5s` / `group_interval: 10s` / `repeat_interval: 1m` — explícitamente reducidos respecto a los defaults de Alertmanager (30 s / 5 min / 4 h). Con los valores default, una ventana de anomalía de 30 s terminaría antes de que Alertmanager terminara de esperar para mandar el primer webhook. Con estos valores, el webhook firing llega a PanicDuty en ~5 s desde que la alerta dispara, follow-ups cada 10 s, y los webhooks de resolved no se demoran.
- `route.receiver: 'panic_duty_webhook'` — toda alerta va a este único receiver. No hay enrutamiento por severidad; todas las alertas (warning y critical) van a la misma UI.
- `receivers[0].name: 'panic_duty_webhook'` — coincide con el `receiver:` de la ruta.
- `webhook_configs[0].url: 'http://panic_duty:8080/webhook'` — el endpoint HTTP en el contenedor PanicDuty. PanicDuty expone esta ruta en `panic_duty/app.py` para recibir el payload webhook estándar de Alertmanager.
- `webhook_configs[0].send_resolved: true` — Alertmanager también manda una notificación de resolved cuando la alerta se cierra, así PanicDuty puede marcar el incidente como cerrado en lugar de dejarlo pegado.

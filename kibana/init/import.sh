#!/bin/sh
set -eu

KIBANA_URL="http://kibana:5601"
DATA_VIEW_ID="model-api-logs"

echo "[kibana-init] Waiting for Kibana to be ready at ${KIBANA_URL}..."
i=0
until curl -fs "${KIBANA_URL}/api/status" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "${i}" -gt 60 ]; then
        echo "[kibana-init] Kibana did not become ready in time" >&2
        exit 1
    fi
    sleep 5
done
echo "[kibana-init] Kibana is up."

echo "[kibana-init] Creating data view ${DATA_VIEW_ID}..."
HTTP_CODE=$(curl -s -o /tmp/resp.json -w "%{http_code}" \
    -X POST "${KIBANA_URL}/api/data_views/data_view" \
    -H "kbn-xsrf: true" \
    -H "Content-Type: application/json" \
    --data '{
        "data_view": {
            "id": "'"${DATA_VIEW_ID}"'",
            "title": "model-api-logs-*",
            "name": "model-api-logs",
            "timeFieldName": "@timestamp"
        },
        "override": true
    }')

if [ "${HTTP_CODE}" = "200" ] || [ "${HTTP_CODE}" = "201" ]; then
    echo "[kibana-init] Data view created (HTTP ${HTTP_CODE})."
elif [ "${HTTP_CODE}" = "409" ]; then
    echo "[kibana-init] Data view already exists (HTTP 409). OK."
else
    echo "[kibana-init] Failed to create data view (HTTP ${HTTP_CODE}):" >&2
    cat /tmp/resp.json >&2
    exit 1
fi

echo "[kibana-init] Setting ${DATA_VIEW_ID} as the default data view..."
curl -s -o /dev/null \
    -X POST "${KIBANA_URL}/api/data_views/default" \
    -H "kbn-xsrf: true" \
    -H "Content-Type: application/json" \
    --data '{"data_view_id": "'"${DATA_VIEW_ID}"'", "force": true}' || true

# ---------------------------------------------------------------------------
# Importar saved objects pre-generados (5 panels Lens + 1 dashboard).
#
# Usamos el endpoint POST /api/saved_objects/<type>/<id>?overwrite=true en
# lugar del endpoint /_import porque este último corre migraciones que
# requieren el schema legacy con campos como currentIndexPatternId. Los
# saved-objects que generamos están en el schema 8.x actual, así que el POST
# directo (que no migra) los acepta sin problema.
#
# Filename convention: NN-<type>-<id>.json — por ejemplo
#   00-lens-latency-bucket-donut.json   → POST /api/saved_objects/lens/latency-bucket-donut
#   05-dashboard-ml-derived-fields-dashboard.json → POST /api/saved_objects/dashboard/ml-derived-fields-dashboard
#
# El prefijo numérico fuerza el orden de creación: panels (lens) primero,
# dashboard último (para que las references se resuelvan).
# ---------------------------------------------------------------------------
SAVED_OBJECTS_DIR="/init/saved_objects"
if [ -d "${SAVED_OBJECTS_DIR}" ]; then
    echo "[kibana-init] Importing saved objects from ${SAVED_OBJECTS_DIR}..."
    for f in "${SAVED_OBJECTS_DIR}"/*.json; do
        [ -e "${f}" ] || continue
        # Extraer type e id desde el nombre: NN-<type>-<id>.json
        base="${f##*/}"          # ej. 00-lens-latency-bucket-donut.json
        base="${base%.json}"     # ej. 00-lens-latency-bucket-donut
        rest="${base#*-}"        # ej. lens-latency-bucket-donut
        so_type="${rest%%-*}"    # ej. lens
        so_id="${rest#*-}"       # ej. latency-bucket-donut

        HTTP_CODE=$(curl -s -o /tmp/so_resp.json -w "%{http_code}" \
            -X POST "${KIBANA_URL}/api/saved_objects/${so_type}/${so_id}?overwrite=true" \
            -H "kbn-xsrf: true" \
            -H "Content-Type: application/json" \
            --data @"${f}")
        if [ "${HTTP_CODE}" = "200" ]; then
            echo "[kibana-init]   OK ${so_type}/${so_id}"
        else
            echo "[kibana-init]   FAIL ${so_type}/${so_id} (HTTP ${HTTP_CODE}):" >&2
            cat /tmp/so_resp.json >&2
            echo >&2
            # No abortamos — un panel que falla no debe romper el resto.
        fi
    done
else
    echo "[kibana-init] No saved_objects directory found, skipping dashboard import."
fi

echo "[kibana-init] Done. Open ${KIBANA_URL} > Discover for logs, or > Dashboards for the auto-provisioned 'ML Drift Investigation' dashboard."

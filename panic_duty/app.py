from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

app = FastAPI(title="PanicDuty Webhook Receiver")

# Simple in-memory storage for our alerts
active_alerts = []

# Ensure templates directory exists (it should be mounted/copied)
os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")

@app.post("/webhook")
async def receive_webhook(request: Request):
    """
    Endpoint that Alertmanager calls when an alert fires.
    """
    global active_alerts
    payload = await request.json()
    
    # Payload from Alertmanager has an 'alerts' array
    for incoming_alert in payload.get("alerts", []):
        alert_name = incoming_alert.get("labels", {}).get("alertname")
        status = incoming_alert.get("status")
        
        if status == "firing":
            # Add or update alert
            existing = [a for a in active_alerts if a.get("labels", {}).get("alertname") == alert_name]
            if not existing:
                active_alerts.append(incoming_alert)
        elif status == "resolved":
            # Remove resolved alerts
            active_alerts = [a for a in active_alerts if a.get("labels", {}).get("alertname") != alert_name]
            
    print(f"Received webhook! Active alerts count: {len(active_alerts)}")
    return {"status": "success"}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """
    The UI for PanicDuty
    """
    # Simply render the HTML template with the current alerts
    return templates.TemplateResponse(request, "index.html", {"alerts": active_alerts, "request": request})

"""Meridian Group — CRM / Relation Client (simulated).

A real FastAPI service (runs on crm-web-01 and crm-web-02, behind the CRM business service) with a
/health endpoint for Event-Driven Ansible monitoring and playbook verification.

Fault injection for the demo: create HEALTH_FLAG to make /health report 503 (degraded); remove it
or restart the service to recover.
"""
import os
import socket
from datetime import datetime, timezone

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

APP_NAME = "CRM"
APP_VERSION = os.environ.get("APP_VERSION", "3.2.0")
SERVER = os.environ.get("MERIDIAN_SERVER") or socket.gethostname()
HEALTH_FLAG = os.environ.get("HEALTH_FLAG", "/var/lib/crm/unhealthy")
STARTED = datetime.now(timezone.utc)

app = FastAPI(title=f"Meridian Group — {APP_NAME}")

CUSTOMERS = [
    {"id": "C-5001", "name": "Atlas Logistics", "tier": "Gold"},
    {"id": "C-5002", "name": "Boreal Foods", "tier": "Silver"},
    {"id": "C-5003", "name": "Ceres Energy", "tier": "Gold"},
]
OPPORTUNITIES = [
    {"id": "OPP-8801", "customer": "Atlas Logistics", "stage": "Negotiation", "amount_eur": 42000},
    {"id": "OPP-8802", "customer": "Ceres Energy", "stage": "Proposal", "amount_eur": 78000},
]


def _page() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "web", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
def home():
    return _page().replace("{{SERVER}}", SERVER)


@app.get("/api/customers")
def customers():
    return {"count": len(CUSTOMERS), "customers": CUSTOMERS}


@app.get("/api/opportunities")
def opportunities():
    return {"count": len(OPPORTUNITIES), "opportunities": OPPORTUNITIES}


@app.get("/health")
def health(response: Response):
    degraded = os.path.exists(HEALTH_FLAG)
    response.status_code = 503 if degraded else 200
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "server": SERVER,
        "status": "degraded" if degraded else "ok",
        "uptime_seconds": int((datetime.now(timezone.utc) - STARTED).total_seconds()),
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

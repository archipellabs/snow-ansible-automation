"""Meridian Group — HR Self-Service portal (simulated).

A small but real FastAPI service with a /health endpoint, so Event-Driven Ansible can monitor it
(ansible.eda.url_check) and the remediation playbooks can verify it for real.

Fault injection for the demo: create HEALTH_FLAG to make /health report 503 (degraded) without
killing the process; remove it — or restart the service — to recover.
"""
import os
import socket
from datetime import datetime, timezone

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

APP_NAME = "HR Portal"
APP_VERSION = os.environ.get("APP_VERSION", "1.4.2")
SERVER = os.environ.get("MERIDIAN_SERVER") or socket.gethostname()
HEALTH_FLAG = os.environ.get("HEALTH_FLAG", "/var/lib/hr-portal/unhealthy")
STARTED = datetime.now(timezone.utc)

app = FastAPI(title=f"Meridian Group — {APP_NAME}")

EMPLOYEES = [
    {"id": "E-1024", "name": "Camille Roux", "department": "Finance"},
    {"id": "E-2087", "name": "Yanis Bernard", "department": "Logistics"},
    {"id": "E-3310", "name": "Aicha Diallo", "department": "Human Resources"},
]


def _page() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "web", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
def home():
    return _page()


@app.get("/api/employees")
def employees():
    return {"count": len(EMPLOYEES), "employees": EMPLOYEES}


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

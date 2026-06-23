"""Meridian Group — GED / Gestion Documentaire (simulated).

A real FastAPI document-management service (runs on ged-01) with a /health endpoint for
Event-Driven Ansible monitoring and playbook verification.

Fault injection for the demo: create HEALTH_FLAG to make /health report 503 (degraded); remove it
or restart the service to recover.
"""
import os
import socket
from datetime import datetime, timezone

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

APP_NAME = "GED"
APP_VERSION = os.environ.get("APP_VERSION", "2.0.5")
SERVER = os.environ.get("MERIDIAN_SERVER") or socket.gethostname()
HEALTH_FLAG = os.environ.get("HEALTH_FLAG", "/var/lib/ged/unhealthy")
STARTED = datetime.now(timezone.utc)

app = FastAPI(title=f"Meridian Group — {APP_NAME}")

DOCUMENTS = [
    {"id": "DOC-1001", "title": "Employee handbook 2026", "format": "PDF", "owner": "HR-IT"},
    {"id": "DOC-1002", "title": "ISO 27001 procedures", "format": "DOCX", "owner": "Quality"},
    {"id": "DOC-1003", "title": "Supplier contract — Atlas", "format": "PDF", "owner": "Legal"},
]


def _page() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "web", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
def home():
    return _page().replace("{{SERVER}}", SERVER)


@app.get("/api/documents")
def documents():
    return {"count": len(DOCUMENTS), "documents": DOCUMENTS}


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

"""Meridian Group — HR Self-Service portal (simulated).

A small but real FastAPI service with a /health endpoint, so Event-Driven Ansible can monitor it
(ansible.eda.url_check) and the remediation playbooks can verify it for real.

SSO: when the OIDC_* environment is present (see compose + bootstrap/keycloak), the portal requires
a Keycloak login (Authorization Code flow) for the UI and the directory API. /health stays public —
the EDA monitor probes it without a token, so authenticating it would break the self-healing loop.
With no OIDC env, the app runs open (auth disabled), so it still works standalone.

Fault injection for the demo: create HEALTH_FLAG to make /health report 503 (degraded) without
killing the process; remove it — or restart the service — to recover.
"""
import json
import os
import secrets
import socket
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

APP_NAME = "HR Portal"
APP_VERSION = os.environ.get("APP_VERSION", "1.4.2")
SERVER = os.environ.get("MERIDIAN_SERVER") or socket.gethostname()
HEALTH_FLAG = os.environ.get("HEALTH_FLAG", "/var/lib/hr-portal/unhealthy")
STARTED = datetime.now(timezone.utc)

# --- OIDC / SSO (Keycloak) -------------------------------------------------------------------
OIDC_ISSUER = os.environ.get("OIDC_ISSUER")                 # https://<FQDN>:9443/auth/realms/meridian
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "hr-portal")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI")     # https://<FQDN>:9443/hr/callback
APP_BASE_URL = (os.environ.get("APP_BASE_URL") or "").rstrip("/")   # https://<FQDN>:9443/hr
OIDC_ENABLED = bool(OIDC_ISSUER and OIDC_CLIENT_SECRET and OIDC_REDIRECT_URI)

# Keycloak is reached through the edge, which uses a self-signed (internal CA) cert -> don't verify.
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

EMPLOYEES = [
    {"id": "E-1024", "name": "Camille Roux", "department": "Finance"},
    {"id": "E-2087", "name": "Yanis Bernard", "department": "Logistics"},
    {"id": "E-3310", "name": "Aicha Diallo", "department": "Human Resources"},
]

app = FastAPI(title=f"Meridian Group — {APP_NAME}")
app.add_middleware(SessionMiddleware,
                   secret_key=os.environ.get("SESSION_SECRET", "dev-insecure-session-secret"),
                   https_only=True, same_site="lax")


def _oidc(path):
    return f"{OIDC_ISSUER}/protocol/openid-connect/{path}"


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, context=_SSL, timeout=15) as r:
        return json.load(r)


def _get_json(url, bearer):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    with urllib.request.urlopen(req, context=_SSL, timeout=15) as r:
        return json.load(r)


def _page(user) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "web", "index.html"), encoding="utf-8") as f:
        html = f.read()
    if user:
        banner = (f'Signed in as <b>{user.get("name")}</b> · '
                  f'<a href="logout" style="color:#fff;text-decoration:underline">Sign out</a>')
    else:
        banner = "auth disabled"
    return html.replace("__USER__", banner)


def _login_page() -> str:
    return (
        "<!doctype html><meta charset='utf-8'><title>Meridian HR — Sign in</title>"
        "<body style=\"font-family:'Segoe UI',Arial,sans-serif;background:#f4f7fb;color:#1f2328;"
        "display:grid;place-items:center;height:100vh;margin:0\">"
        "<div style='background:#fff;border:1px solid #d8dee4;border-radius:12px;padding:40px;text-align:center'>"
        "<h1 style='color:#0a4a8f;margin:0 0 8px'>Meridian Group · HR Self-Service</h1>"
        "<p style='color:#57606a'>Corporate single sign-on required.</p>"
        "<a href='login' style='display:inline-block;margin-top:8px;background:#0a4a8f;color:#fff;"
        "padding:10px 22px;border-radius:8px;text-decoration:none'>Sign in with SSO</a></div></body>"
    )


@app.get("/login")
def login(request: Request):
    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=303)
    state = secrets.token_urlsafe(16)
    request.session["state"] = state
    params = urllib.parse.urlencode({
        "response_type": "code", "client_id": OIDC_CLIENT_ID, "redirect_uri": OIDC_REDIRECT_URI,
        "scope": "openid profile email", "state": state})
    return RedirectResponse(url=f"{_oidc('auth')}?{params}", status_code=303)


@app.get("/callback")
def callback(request: Request, code: str = "", state: str = ""):
    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=303)
    if not code or not state or state != request.session.get("state"):
        return HTMLResponse("<h1>Login failed</h1><p>Invalid state or code.</p>", status_code=400)
    try:
        tok = _post_form(_oidc("token"), {
            "grant_type": "authorization_code", "code": code, "redirect_uri": OIDC_REDIRECT_URI,
            "client_id": OIDC_CLIENT_ID, "client_secret": OIDC_CLIENT_SECRET})
        info = _get_json(_oidc("userinfo"), tok["access_token"])
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<h1>Login failed</h1><pre>{e}</pre>", status_code=502)
    request.session.pop("state", None)
    request.session["user"] = {"name": info.get("name") or info.get("preferred_username"),
                               "email": info.get("email"), "groups": info.get("groups", [])}
    return RedirectResponse(url=f"{APP_BASE_URL}/" or "/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    if not OIDC_ENABLED:
        return RedirectResponse(url="/", status_code=303)
    params = urllib.parse.urlencode({"post_logout_redirect_uri": f"{APP_BASE_URL}/",
                                     "client_id": OIDC_CLIENT_ID})
    return RedirectResponse(url=f"{_oidc('logout')}?{params}", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if OIDC_ENABLED and not request.session.get("user"):
        return HTMLResponse(_login_page())
    return _page(request.session.get("user") if OIDC_ENABLED else None)


@app.get("/api/employees")
def employees(request: Request):
    if OIDC_ENABLED and not request.session.get("user"):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return {"count": len(EMPLOYEES), "employees": EMPLOYEES}


@app.get("/health")
def health(response: Response):
    # Always public — the EDA monitor (url_check) probes this without a token.
    degraded = os.path.exists(HEALTH_FLAG)
    response.status_code = 503 if degraded else 200
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "server": SERVER,
        "status": "degraded" if degraded else "ok",
        "sso": "enabled" if OIDC_ENABLED else "disabled",
        "uptime_seconds": int((datetime.now(timezone.utc) - STARTED).total_seconds()),
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

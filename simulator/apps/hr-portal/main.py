"""Meridian Group — HR Self-Service portal (simulated).

A small but real FastAPI service with a /health endpoint, so Event-Driven Ansible can monitor it
(ansible.eda.url_check) and the remediation playbooks can verify it for real.

SSO: when the OIDC_* environment is present (see compose + bootstrap/3_keycloak), the portal requires
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

# --- HR business data (Postgres on hr-db-01) -------------------------------------------------
# Read-only display of leave requests. Employee IDENTITY is in Keycloak; this DB holds HR business
# data. Connects as 'hr_app' with no password (pg_hba trusts it on the 'hr' db, container network).
HR_DB_HOST = os.environ.get("HR_DB_HOST")           # e.g. hr-db-01 (unset -> DB display disabled)
HR_DB_PORT = os.environ.get("HR_DB_PORT", "5432")
HR_DB_NAME = os.environ.get("HR_DB_NAME", "hr")
HR_DB_USER = os.environ.get("HR_DB_USER", "hr_app")
try:
    import psycopg
except Exception:  # noqa: BLE001 - the app still runs (DB display just disabled) without the driver
    psycopg = None


def leave_requests():
    """Return the leave requests from Postgres, or None if the DB is unreachable / not wired."""
    if not (HR_DB_HOST and psycopg):
        return None
    try:
        with psycopg.connect(host=HR_DB_HOST, port=HR_DB_PORT, dbname=HR_DB_NAME,
                             user=HR_DB_USER, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT employee_name, leave_type, start_date, end_date, status "
                            "FROM leave_requests ORDER BY start_date")
                return [{"employee": r[0], "type": r[1], "start": str(r[2]),
                         "end": str(r[3]), "status": r[4]} for r in cur.fetchall()]
    except Exception:  # noqa: BLE001 - DB down should not break the portal
        return None

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
    rows = leave_requests()
    if rows is None:
        leave_html = '<p class="muted">Leave data unavailable (HR database unreachable).</p>'
    elif not rows:
        leave_html = '<p class="muted">No leave requests on file.</p>'
    else:
        body = "".join(
            f"<tr><td>{r['employee']}</td><td>{r['type']}</td>"
            f"<td>{r['start']} → {r['end']}</td><td>{r['status']}</td></tr>" for r in rows)
        leave_html = (
            "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
            "<tr style='text-align:left;color:#57606a'><th>Employee</th><th>Type</th>"
            "<th>Dates</th><th>Status</th></tr>" + body + "</table>")
    return html.replace("__USER__", banner).replace("__LEAVE__", leave_html)


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


@app.get("/api/leave")
def leave(request: Request):
    if OIDC_ENABLED and not request.session.get("user"):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    rows = leave_requests()
    if rows is None:
        return JSONResponse({"detail": "HR database unreachable"}, status_code=503)
    return {"count": len(rows), "leave_requests": rows}


@app.get("/health")
def health(response: Response):
    # Always public — the EDA monitor (url_check) probes this without a token. Liveness is the
    # service itself (the degraded flag); the DB is reported for info only, it does NOT flip 503.
    degraded = os.path.exists(HEALTH_FLAG)
    response.status_code = 503 if degraded else 200
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "server": SERVER,
        "status": "degraded" if degraded else "ok",
        "sso": "enabled" if OIDC_ENABLED else "disabled",
        "db": "ok" if leave_requests() is not None else "unreachable",
        "uptime_seconds": int((datetime.now(timezone.utc) - STARTED).total_seconds()),
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

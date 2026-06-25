"""Shared helpers for the PoC config-as-code and test scripts (stdlib only).

Each script computes the repo root (`ROOT`) and imports from here:

    sys.path.insert(0, ROOT)
    from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json

This module holds the API-agnostic transport (`http_json`, auth, TLS, `.env`) plus two helpers
shared across every layer: `env()` (load `.env` + return the environment) and `ssh()` (run a
command on the VM). The per-API clients live in sibling lib modules: `lib.servicenow` (ServiceNow
Table API + Business Rule builder) and `lib.aap` (controller/EDA client with job polling).
"""
import os
import sys
import ssl
import json
import base64
import subprocess
import urllib.request
import urllib.error

# Repo root, derived from this file's location (lib/poc.py -> repo root), so callers don't recompute
# it. Note: a script still needs `sys.path.insert(0, <root>)` before it can import lib at all.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSH_KEY = "~/.ssh/snow-aap-poc"


def load_dotenv(root=None, required=()):
    """Load a .env file (KEY=VALUE) into os.environ without overriding existing vars, looking
    in the CWD then `root`. Values are taken verbatim — never quote-stripped — because they can
    contain shell-hostile characters (% ! > { } # & ; $ ...). If any `required` key is still
    missing afterwards, exit with a clear message."""
    for p in [os.path.join(os.getcwd(), ".env")] + ([os.path.join(root, ".env")] if root else []):
        if os.path.isfile(p):
            for line in open(p):
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    k, v = s.split("=", 1)
                    os.environ.setdefault(k.strip(), v)
            break
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit("Missing in .env: " + ", ".join(missing))


def insecure_ctx():
    """An SSL context that skips verification — the AAP gateway uses a self-signed certificate."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def basic_auth(user, password):
    """The value for an HTTP Basic `Authorization` header."""
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def http_json(url, method="GET", headers=None, body=None, ctx=None, timeout=30):
    """Make an HTTP request and return the parsed JSON response ({} if the body is empty).
    `body` is a Python object, JSON-encoded automatically (with a Content-Type header). On an
    HTTP error, print the status + response body to stderr, then re-raise the HTTPError."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code} {method} {url}\n{e.read().decode()}\n")
        raise


def env(required=()):
    """Load `.env` (from REPO_ROOT) into the environment and return os.environ."""
    load_dotenv(REPO_ROOT, required=required)
    return os.environ


def ssh(cmd, fqdn=None, timeout=40, key=SSH_KEY, connect_timeout=15):
    """Run a command on the VM over SSH (azureuser@host); return stdout, stripped. fqdn defaults to the
    RUNTIME's estate host (AAP_FQDN, or AWX_FQDN when RUNTIME=awx), so scenarios are multi-runtime via the
    RUNTIME env var. `connect_timeout` bounds the TCP connect (lower it for liveness probes so a dead host
    fails fast); `timeout` is the hard subprocess kill. Raises on connection/timeout failure."""
    # Resolve the estate host inline — importing lib.runtime here would be circular (it imports lib.aap,
    # which imports lib.poc).
    if not fqdn:
        rt = (os.environ.get("RUNTIME") or "aap").lower()
        fqdn = os.environ.get("AWX_FQDN") if rt == "awx" else os.environ.get("AAP_FQDN")
    return subprocess.run(
        ["ssh", "-i", os.path.expanduser(key), "-o", "StrictHostKeyChecking=accept-new",
         "-o", f"ConnectTimeout={connect_timeout}", f"azureuser@{fqdn}", cmd],
        capture_output=True, text=True, timeout=timeout).stdout.strip()

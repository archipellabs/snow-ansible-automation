#!/usr/bin/env python3
"""hr-portal SSO smoke test (no browser). Run from repo root:

  python3 tests/e2e_hrportal_sso.py

Verifies the OIDC wiring without driving a browser:
  1. GET /hr/login         -> 30x redirect to Keycloak's auth endpoint (client_id=hr-portal)
  2. GET /hr/health        -> 200 and reports sso=enabled (stays public for the EDA monitor)
  3. GET /hr/api/employees -> 401 when unauthenticated (the UI/API is protected)
  4. Resource-owner password grant for a demo user (from fleet.yml) -> access token + userinfo,
     with the user's team in the groups claim (proves realm + user + client secret + mapper).
Exit 0 if all pass. Requires the simulator stack up and bootstrap/keycloak/configure.py applied.
"""
import http.client
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv  # noqa: E402

load_dotenv(ROOT)
E = os.environ
FQDN = E["FQDN"]
PORT = 9443
ISSUER = f"https://{FQDN}:{PORT}/auth/realms/meridian"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(path, headers=None):
    c = http.client.HTTPSConnection(FQDN, PORT, context=CTX, timeout=15)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdrs, body


def post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
        return json.load(r)


def get_bearer(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
        return json.load(r)


def main():
    fleet = yaml.safe_load(open(os.path.join(ROOT, "simulator", "fleet.yml")))
    person = next(p for p in fleet["people"] if p.get("team"))    # a DSI/IT staff member
    username = person["email"].split("@")[0]
    ok = True

    st, h, _ = get("/hr/login")
    loc = h.get("location", "")
    c1 = st in (302, 303, 307) and "protocol/openid-connect/auth" in loc and "client_id=hr-portal" in loc
    print(f"  /hr/login            -> {st}, redirect to Keycloak: {c1}")
    ok &= c1

    st, _, body = get("/hr/health")
    try:
        hj = json.loads(body)
    except Exception:  # noqa: BLE001
        hj = {}
    c2 = st == 200 and hj.get("sso") == "enabled"
    print(f"  /hr/health           -> {st}, sso={hj.get('sso')}: {c2}")
    ok &= c2

    st, _, _ = get("/hr/api/leave")
    c3 = st == 401
    print(f"  /hr/api/leave        -> {st} (expect 401 unauthenticated): {c3}")
    ok &= c3

    try:
        tok = post_form(f"{ISSUER}/protocol/openid-connect/token", {
            "grant_type": "password", "client_id": "hr-portal",
            "client_secret": E["KC_HRPORTAL_CLIENT_SECRET"],
            "username": username, "password": E["KC_DEMO_PASSWORD"],
            "scope": "openid profile email"})
        ui = get_bearer(f"{ISSUER}/protocol/openid-connect/userinfo", tok["access_token"])
        groups = ui.get("groups") or []
        c4 = bool(ui.get("email")) and person["team"] in groups
        print(f"  password grant {username} -> email={ui.get('email')} groups={groups}: {c4}")
    except Exception as ex:  # noqa: BLE001
        c4 = False
        print(f"  password grant {username} -> FAILED: {ex}")
    ok &= c4

    print("\n>> " + ("HR-PORTAL SSO PASSED" if ok else "HR-PORTAL SSO FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

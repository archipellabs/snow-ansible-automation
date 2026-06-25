#!/usr/bin/env python3
"""Configure Keycloak as Meridian's corporate IdP (idempotent, admin REST API, stdlib only).

Builds the 'meridian' realm from simulator/fleet.yml — the same manifest that feeds the ServiceNow
CMDB. It creates:
  - groups: one per support team, plus 'IT-Admins' (umbrella for DSI staff) and 'Employees';
  - users: every person in fleet.yml (username = email local part, shared demo password);
  - OIDC clients: 'hr-portal' (employee SSO, Étape 2) and 'aap' (admin SSO via the AAP gateway,
    Étape 3, with a groups mapper so AAP sees the user's groups). Client secrets come from .env, so
    Keycloak and the apps share the same value deterministically.

Keycloak is reached through the edge at https://<FQDN>:9443/auth (self-signed -> TLS not verified).
Run AFTER bootstrap/2_fleet/sync.sh (the stack must be up; this waits for Keycloak to answer). From the
repo root:
  python3 bootstrap/3_keycloak/configure.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
REALM = "meridian"

sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx  # noqa: E402
from lib.runtime import fqdn  # noqa: E402

load_dotenv(ROOT, required=("KEYCLOAK_ADMIN_PASSWORD", "KC_DEMO_PASSWORD",
                            "KC_HRPORTAL_CLIENT_SECRET", "KC_AAP_CLIENT_SECRET",
                            "KC_PROVISIONER_SECRET"))

FQDN = fqdn()                          # estate host for the selected runtime (RUNTIME=aap|awx)
EXT = f"https://{FQDN}:9443"            # browser-facing base (through the edge)
BASE = f"{EXT}/auth"                    # Keycloak relative path
ADMIN = f"{BASE}/admin/realms"
CTX = insecure_ctx()
FLEET = yaml.safe_load(open(os.path.join(ROOT, "simulator", "fleet.yml")))
TOK = None


def get_token():
    data = urllib.parse.urlencode(
        {"grant_type": "password", "client_id": "admin-cli",
         "username": "admin", "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"]}).encode()
    print(f">> Connecting to Keycloak at {BASE} …", flush=True)
    last = None
    for attempt in range(36):           # wait up to ~3 min for Keycloak to come up
        try:
            rq = urllib.request.Request(
                f"{BASE}/realms/master/protocol/openid-connect/token", data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(rq, context=CTX, timeout=15) as r:
                return json.load(r)["access_token"]
        except Exception as e:          # noqa: BLE001 - keep retrying while it boots
            last = e
            if attempt % 4 == 0:        # surface progress instead of hanging silently
                print(f"   …waiting ({attempt * 5}s) — {type(e).__name__}: {e}", flush=True)
            time.sleep(5)
    sys.exit(f"could not reach Keycloak at {BASE} after 180s ({type(last).__name__}: {last}).\n"
             f"   Check: NSG allows :9443 inbound, and the edge + keycloak containers are up.")


def api(method, path, body=None):
    """Call the admin REST API; return (status, parsed_json_or_None). Never raises on HTTP errors."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {TOK}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    rq = urllib.request.Request(f"{ADMIN}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(rq, context=CTX, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else None)
        except Exception:               # noqa: BLE001
            return e.code, None


def get_until(path):
    """GET, retrying until non-empty — start-dev (H2) can lag right after a write."""
    res = None
    for _ in range(6):
        _, res = api("GET", path)
        if res:
            return res
        time.sleep(1)
    return res or []


def ensure_realm():
    st, _ = api("GET", f"/{REALM}")
    if st == 200:
        print(f"= realm '{REALM}' exists")
        return
    api("POST", "", {"realm": REALM, "enabled": True, "displayName": "Meridian Group"})
    print(f"+ realm '{REALM}' created")


def ensure_group(name):
    st, groups = api("GET", f"/{REALM}/groups?{urllib.parse.urlencode({'search': name})}")
    for g in groups or []:
        if g["name"] == name:
            return g["id"]
    api("POST", f"/{REALM}/groups", {"name": name})
    groups = get_until(f"/{REALM}/groups?{urllib.parse.urlencode({'search': name})}")
    gid = next(g["id"] for g in groups if g["name"] == name)
    print(f"+ group '{name}'")
    return gid


def ensure_user(person, group_ids):
    username = person["email"].split("@")[0]
    first, _, last = person["name"].partition(" ")
    q = urllib.parse.urlencode({"username": username, "exact": "true"})
    st, found = api("GET", f"/{REALM}/users?{q}")
    if found:
        uid = found[0]["id"]
    else:
        cst, _ = api("POST", f"/{REALM}/users", {
            "username": username, "email": person["email"], "firstName": first, "lastName": last or first,
            "enabled": True, "emailVerified": True})
        # Keycloak returns 201 + a Location header (no body); re-fetch to get the id, retrying a few
        # times because start-dev (H2) can lag right after the write.
        found = None
        for _ in range(5):
            st, found = api("GET", f"/{REALM}/users?{q}")
            if found:
                break
            time.sleep(1)
        if not found:
            sys.exit(f"user '{username}' not found after create (POST status {cst})")
        uid = found[0]["id"]
        print(f"+ user '{username}'")
    # Deterministic demo password + group memberships (both idempotent).
    api("PUT", f"/{REALM}/users/{uid}/reset-password",
        {"type": "password", "value": os.environ["KC_DEMO_PASSWORD"], "temporary": False})
    for gid in group_ids:
        api("PUT", f"/{REALM}/users/{uid}/groups/{gid}")
    return uid


def ensure_client(client_id, name, secret, redirect_uris, web_origins, groups_mapper=False):
    st, found = api("GET", f"/{REALM}/clients?{urllib.parse.urlencode({'clientId': client_id})}")
    if found:
        cid = found[0]["id"]
        print(f"= client '{client_id}' exists")
    else:
        api("POST", f"/{REALM}/clients", {
            "clientId": client_id, "name": name, "protocol": "openid-connect", "enabled": True,
            "publicClient": False, "secret": secret, "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True, "redirectUris": redirect_uris,
            "webOrigins": web_origins,
            "attributes": {"post.logout.redirect.uris": "+"}})
        found = get_until(f"/{REALM}/clients?{urllib.parse.urlencode({'clientId': client_id})}")
        cid = found[0]["id"]
        print(f"+ client '{client_id}'")
    if groups_mapper:
        # So the token carries a 'groups' claim AAP can map to teams/roles (Étape 3).
        st, mappers = api("GET", f"/{REALM}/clients/{cid}/protocol-mappers/models")
        if not any(m.get("name") == "groups" for m in (mappers or [])):
            api("POST", f"/{REALM}/clients/{cid}/protocol-mappers/models", {
                "name": "groups", "protocol": "openid-connect",
                "protocolMapper": "oidc-group-membership-mapper",
                "config": {"claim.name": "groups", "full.path": "false",
                           "id.token.claim": "true", "access.token.claim": "true",
                           "userinfo.token.claim": "true"}})
            print(f"  + groups mapper on '{client_id}'")
    return cid


def ensure_service_account_client(client_id, secret, mgmt_roles):
    """A confidential client with a service account (client_credentials), granted realm-management
    roles so a backend (the onboarding playbook) can create users. No browser flow."""
    q = urllib.parse.urlencode({"clientId": client_id})
    st, found = api("GET", f"/{REALM}/clients?{q}")
    if found:
        cid = found[0]["id"]
        print(f"= client '{client_id}' exists")
    else:
        api("POST", f"/{REALM}/clients", {
            "clientId": client_id, "name": "AAP user provisioner", "protocol": "openid-connect",
            "enabled": True, "publicClient": False, "secret": secret,
            "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled": True})
        found = get_until(f"/{REALM}/clients?{q}")
        cid = found[0]["id"]
        print(f"+ client '{client_id}' (service account)")
    # Grant the realm-management roles to the client's service-account user.
    sa = get_until(f"/{REALM}/clients/{cid}/service-account-user")
    rm = get_until(f"/{REALM}/clients?{urllib.parse.urlencode({'clientId': 'realm-management'})}")
    rm_id = rm[0]["id"]
    roles = get_until(f"/{REALM}/clients/{rm_id}/roles")
    want = [r for r in (roles or []) if r["name"] in mgmt_roles]
    api("POST", f"/{REALM}/users/{sa['id']}/role-mappings/clients/{rm_id}", want)
    print(f"  + granted {sorted(r['name'] for r in want)} to {client_id}")
    return cid


def main():
    global TOK
    TOK = get_token()
    ensure_realm()

    # Groups: one per ServiceNow assignment group, plus two umbrellas.
    gids = {t["name"]: ensure_group(t["name"]) for t in FLEET["teams"]}
    gids["IT-Admins"] = ensure_group("IT-Admins")
    gids["Employees"] = ensure_group("Employees")

    # Users from the same manifest as the CMDB. Staff (have 'team') are IT-Admins; the rest are
    # business users (Employees).
    staff = others = 0
    for p in FLEET["people"]:
        if p.get("team"):
            ensure_user(p, [gids[p["team"]], gids["IT-Admins"]])
            staff += 1
        else:
            ensure_user(p, [gids["Employees"]])
            others += 1

    # OIDC clients.
    ensure_client("hr-portal", "HR Portal", os.environ["KC_HRPORTAL_CLIENT_SECRET"],
                  [f"{EXT}/hr/*"], [EXT], groups_mapper=True)
    ensure_client("aap", "Ansible Automation Platform", os.environ["KC_AAP_CLIENT_SECRET"],
                  [f"https://{FQDN}/*"], [f"https://{FQDN}"], groups_mapper=True)
    # Backend client the onboarding playbook uses to create users (client_credentials).
    ensure_service_account_client("aap-provisioner", os.environ["KC_PROVISIONER_SECRET"],
                                  ["manage-users", "view-users", "query-users"])

    print(f"\n= realm '{REALM}': {len(FLEET['teams']) + 2} groups, {staff + others} users "
          f"({staff} IT-Admins, {others} Employees), clients hr-portal + aap + aap-provisioner")
    print(f">> Admin console : {BASE}/admin/  (master realm, user 'admin')")
    print(f">> Account portal: {BASE}/realms/{REALM}/account  (any user, demo password from .env)")
    print(">> Next (Étape 2): wire hr-portal to this 'hr-portal' client; (Étape 3) AAP gateway -> 'aap' client.")


if __name__ == "__main__":
    main()

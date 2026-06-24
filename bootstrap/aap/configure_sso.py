#!/usr/bin/env python3
"""Federate AAP admin login to Keycloak (idempotent, Platform Gateway API, stdlib only).

Creates, in the AAP Platform Gateway:
  - an **OIDC authenticator** pointing at the 'aap' client of Meridian's Keycloak realm. We use the
    generic `oidc` plugin (not the dedicated `keycloak` one) because it exposes VERIFY_SSL — the edge
    serves a self-signed cert — and uses OIDC discovery instead of a hand-pasted realm public key;
  - an **authenticator map** granting `is_superuser` to members of the Keycloak `IT-Admins` group
    (revoke=false, so it only ever grants — it never touches the local admin).

The built-in **Local Database Authenticator stays enabled**, so `admin` can always log in locally
(no lock-out). After this, the gateway login page offers "Sign in with Keycloak (Meridian)".

Run AFTER bootstrap/keycloak/configure.py (the 'aap' client + IT-Admins group must exist) and the
simulator stack must be up (the gateway reaches Keycloak at https://<FQDN>:9443/auth). From the repo
root:
  python3 bootstrap/aap/configure_sso.py
"""
import os
import sys
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

AUTH_NAME = "Keycloak (Meridian)"
MAP_NAME = "IT-Admins -> AAP superuser"
OIDC_TYPE = "ansible_base.authentication.authenticator_plugins.oidc"
ADMIN_GROUP = "IT-Admins"

sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

load_dotenv(ROOT, required=("FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD", "KC_AAP_CLIENT_SECRET"))

FQDN = os.environ["FQDN"]
BASE = f"https://{FQDN}/api/gateway/v1"
ISSUER = f"https://{FQDN}:9443/auth/realms/meridian"     # reached via the edge (hairpin works here)
HEADERS = {"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])}
CTX = insecure_ctx()


def api(method, path, body=None):
    url = path if path.startswith("http") else f"{BASE}/{path}"
    return http_json(url, method=method, headers=HEADERS, body=body, ctx=CTX, timeout=60)


def find_by_name(endpoint, name):
    res = api("GET", f"{endpoint}/?{urllib.parse.urlencode({'page_size': 200})}")
    return next((x for x in res.get("results", []) if x.get("name") == name), None)


def main():
    # 1) OIDC authenticator (the local one is left untouched -> admin keeps a local login).
    auth = find_by_name("authenticators", AUTH_NAME)
    if auth:
        print(f"= authenticator '{AUTH_NAME}' exists (id={auth['id']})")
    else:
        auth = api("POST", "authenticators/", {
            "name": AUTH_NAME, "type": OIDC_TYPE, "enabled": True,
            "create_objects": True, "remove_users": False, "order": 1,
            "configuration": {
                "OIDC_ENDPOINT": ISSUER,
                "VERIFY_SSL": False,            # the edge uses a self-signed (internal CA) cert
                "KEY": "aap",
                "SECRET": os.environ["KC_AAP_CLIENT_SECRET"],
                "GROUPS_CLAIM": "groups",       # matches the Keycloak group-membership mapper
            }})
        print(f"+ authenticator '{AUTH_NAME}' created (id={auth['id']})")

    # 2) Map: members of the Keycloak IT-Admins group become AAP superusers. revoke=false => grant
    #    only; it never removes superuser from anyone (the local admin is safe).
    mp = find_by_name("authenticator_maps", MAP_NAME)
    if mp:
        print(f"= map '{MAP_NAME}' exists (id={mp['id']})")
    else:
        mp = api("POST", "authenticator_maps/", {
            "name": MAP_NAME, "authenticator": auth["id"], "map_type": "is_superuser",
            "revoke": False, "order": 1,
            "triggers": {"groups": {"has_or": [ADMIN_GROUP]}}})
        print(f"+ map '{MAP_NAME}' created (id={mp['id']})")

    print("\n>> AAP admin SSO is wired. The local 'admin' login is unchanged (no lock-out).")
    print(f">> Test: open https://{FQDN}/ -> 'Sign in with {AUTH_NAME}' -> log in as an IT-Admins")
    print("   user (e.g. nadia.haddad / the demo password) -> you land in AAP as a superuser.")
    print(">> Validate from the CLI: python3 tests/health.py --only sso")


if __name__ == "__main__":
    main()

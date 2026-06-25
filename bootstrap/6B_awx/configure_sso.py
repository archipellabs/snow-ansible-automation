#!/usr/bin/env python3
"""Wire AWX admin SSO to Keycloak (OIDC) — the `awx` twin of bootstrap/6A_aap/configure_sso.py.

AAP did admin SSO through the Platform Gateway; AWX has no gateway, so it uses django-social-auth's
OIDC settings (`/api/v2/settings/oidc/`). This points AWX at the Meridian realm's `awx` client, so
admins sign in to AWX with the same Keycloak users as the apps. The realm + the `awx` client (with a
groups mapper + a wildcard redirect that covers `/sso/complete/oidc/`) are created by
bootstrap/3_keycloak/configure.py (run it with RUNTIME=awx first).

OIDC endpoint = the browser-facing issuer (`https://<AWX_FQDN>:9443/auth/realms/meridian`, via the edge);
AWX's back-channel reaches it too — the activation pods proved the pod->edge hairpin works. Reads .env
(AWX_FQDN, AWX_ADMIN_*, KC_AAP_CLIENT_SECRET). From the repo root:
  python3 bootstrap/6B_awx/configure_sso.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv  # noqa: E402
from lib.runtime import controller, fqdn  # noqa: E402

load_dotenv(ROOT, required=("AWX_FQDN", "AWX_ADMIN_PASSWORD", "KC_AAP_CLIENT_SECRET"))

AWX = controller("awx")
ISSUER = f"https://{fqdn('awx')}:9443/auth/realms/meridian"


def main():
    AWX.call("settings/oidc/", {
        "SOCIAL_AUTH_OIDC_KEY": "awx",
        "SOCIAL_AUTH_OIDC_SECRET": os.environ["KC_AAP_CLIENT_SECRET"],
        "SOCIAL_AUTH_OIDC_OIDC_ENDPOINT": ISSUER,
        "SOCIAL_AUTH_OIDC_VERIFY_SSL": False,      # the edge serves Keycloak with a self-signed cert
    }, method="PATCH")
    o = AWX.call("settings/oidc/")
    print(f"+ AWX OIDC wired: key={o['SOCIAL_AUTH_OIDC_KEY']!r}, endpoint={o['SOCIAL_AUTH_OIDC_OIDC_ENDPOINT']}, "
          f"verify_ssl={o['SOCIAL_AUTH_OIDC_VERIFY_SSL']}")
    print(f">> Test: open https://{fqdn('awx')}/  ->  the OIDC sign-in button  ->  log in as a Meridian user.")
    print(f">> (the 'awx' Keycloak client's wildcard redirect covers https://{fqdn('awx')}/sso/complete/oidc/)")


if __name__ == "__main__":
    main()

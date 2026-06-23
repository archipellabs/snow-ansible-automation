#!/usr/bin/env python3
"""AAP admin SSO end-to-end test — headless OIDC login (no browser). Run from repo root:

  python3 tests/e2e_aap_sso.py

Drives the real Authorization Code flow against the AAP Platform Gateway:
  1. hit the gateway's SSO begin URL  -> 302 to Keycloak's login page (cookies kept in a jar);
  2. POST an IT-Admins user's credentials to the Keycloak login form;
  3. Keycloak redirects to the gateway callback, which establishes a session;
  4. GET /api/gateway/v1/me/ -> assert the logged-in user is that person AND is a superuser
     (granted by the 'IT-Admins -> superuser' authenticator map).
Proves the whole chain: gateway OIDC authenticator -> Keycloak 'aap' client -> groups claim -> map.
Re-runnable. Exit 0 if the SSO login lands a superuser.
"""
import html
import http.cookiejar
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

load_dotenv(ROOT)
E = os.environ
FQDN = E["FQDN"]
GW = f"https://{FQDN}/api/gateway/v1"
CTX = insecure_ctx()
SSO_USER = "nadia.haddad"          # a DSI/DBA staff member -> IT-Admins group
SSO_PASS = E["KC_DEMO_PASSWORD"]


def sso_login_url():
    u = http_json(f"{GW}/ui_auth/",
                  headers={"Authorization": basic_auth(E["AAP_ADMIN_USER"], E["AAP_ADMIN_PASSWORD"])},
                  ctx=CTX)
    sso = next((s for s in u.get("ssos", []) if s.get("type") == "oidc"), None)
    if not sso:
        sys.exit("no OIDC SSO button on the gateway login page — run bootstrap/aap/configure_sso.py first")
    return f"https://{FQDN}{sso['login_url']}"


def main():
    begin = sso_login_url()
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=CTX))
    opener.addheaders = [("User-Agent", "meridian-sso-test")]

    print(f">> 1. begin SSO -> {begin}")
    page = opener.open(begin, timeout=30).read().decode("utf-8", "replace")
    m = re.search(r'<form[^>]+action="([^"]+)"', page)
    if not m:
        sys.exit("could not find the Keycloak login form (unexpected page):\n" + page[:300])
    action = html.unescape(m.group(1))
    print(f">> 2. posting {SSO_USER} credentials to Keycloak")
    data = urllib.parse.urlencode({"username": SSO_USER, "password": SSO_PASS}).encode()
    resp = opener.open(urllib.request.Request(action, data=data), timeout=30)
    final_url = resp.geturl()
    resp.read()
    print(f"   landed at: {final_url}")

    print(">> 3. checking the gateway session (/me/)")
    me = json.loads(opener.open(f"{GW}/me/", timeout=30).read().decode())
    user = (me.get("results") or [me])[0] if isinstance(me, dict) else {}
    username = user.get("username")
    is_super = user.get("is_superuser")

    print()
    print(f"  logged in as     : {username}")
    print(f"  is_superuser     : {is_super}")
    ok = username == SSO_USER and bool(is_super)
    print("\n>> " + ("AAP SSO PASSED" if ok else "AAP SSO FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

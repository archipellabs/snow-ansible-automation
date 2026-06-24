#!/usr/bin/env python3
"""Employee-onboarding end-to-end test (catalog -> EDA -> Ansible -> Keycloak). From repo root:

  python3 tests/e2e_employee_onboarding.py

Orders the "Arrivée collaborateur" Service Catalog item for a fresh test joiner, then waits for the
push chain to provision the person's identity in Keycloak and close the request:
  catalog request -> Business Rule -> Event Stream -> push-employee-onboarding -> "Provision
  Employee" JT -> provision_employee.yml (creates the Keycloak user, closes the RITM).
Pre-deletes the test user so the run proves real creation. Asserts the Keycloak user exists (in the
Employees group) and the request item is Closed Complete. Re-runnable. Exit 0 if all pass.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

load_dotenv(ROOT)
E = os.environ
CTX = insecure_ctx()
ITEM_NAME = "Arrivée collaborateur"
EMP_NAME = "Sofia Marchetti"
EMP_EMAIL = "sofia.marchetti@meridian.example"
EMP_SERVICE = "RH Self-Service"
USERNAME = EMP_EMAIL.split("@")[0]
KC = f"https://{E['FQDN']}:9443/auth"
TIMEOUT = 150


def sn(method, path, body=None):
    return http_json(f"https://{E['SN_INSTANCE']}/api/now/{path}", method=method,
                     headers={"Authorization": basic_auth(E["SN_USER"], E["SN_PASS"])}, body=body)


def sc_order(item_sid, variables):
    return http_json(f"https://{E['SN_INSTANCE']}/api/sn_sc/servicecatalog/items/{item_sid}/order_now",
                     method="POST", headers={"Authorization": basic_auth(E["SN_USER"], E["SN_PASS"])},
                     body={"sysparm_quantity": "1", "variables": variables})["result"]


def kc_token():
    data = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                   "username": "admin", "password": E["KEYCLOAK_ADMIN_PASSWORD"]}).encode()
    req = urllib.request.Request(f"{KC}/realms/master/protocol/openid-connect/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
        return json.load(r)["access_token"]


def kc(method, path, tok):
    req = urllib.request.Request(f"{KC}/admin/realms/meridian{path}",
                                 headers={"Authorization": f"Bearer {tok}"}, method=method)
    with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def main():
    tok = kc_token()

    # Pre-clean so this run proves real creation.
    existing = kc("GET", f"/users?username={USERNAME}&exact=true", tok)
    if existing:
        kc("DELETE", f"/users/{existing[0]['id']}", tok)
        print(f">> pre-deleted existing Keycloak user '{USERNAME}'")

    item = sn("GET", "table/sc_cat_item?" + urllib.parse.urlencode(
        {"sysparm_query": f"name={ITEM_NAME}", "sysparm_fields": "sys_id", "sysparm_limit": "1"}))["result"]
    if not item:
        sys.exit(f"catalog item '{ITEM_NAME}' not found — run bootstrap/servicenow/setup_onboarding.py first")
    item_sid = item[0]["sys_id"]

    print(f">> Ordering '{ITEM_NAME}' for {EMP_NAME} <{EMP_EMAIL}> ({EMP_SERVICE})")
    order = sc_order(item_sid, {"employee_name": EMP_NAME, "employee_email": EMP_EMAIL,
                                "employee_service": EMP_SERVICE})
    req_sid = order.get("sys_id")
    print(f"   request {order.get('request_number') or order.get('number')}")

    print(">> Waiting for the chain to create the Keycloak user + close the request...")
    user = None
    state = None
    for _ in range(TIMEOUT // 5):
        time.sleep(5)
        if not user:
            found = kc("GET", f"/users?username={USERNAME}&exact=true", tok)
            user = found[0] if found else None
        if req_sid:
            rq = urllib.parse.urlencode({"sysparm_query": f"request={req_sid}",
                                         "sysparm_fields": "number,state", "sysparm_limit": "1"})
            ritm = sn("GET", f"table/sc_req_item?{rq}")["result"]
            state = ritm[0]["state"] if ritm else None
        if user and state == "3":
            break

    groups = kc("GET", f"/users/{user['id']}/groups", tok) if user else []
    in_employees = any(g.get("name") == "Employees" for g in (groups or []))

    print()
    print(f"  Keycloak user created    : {bool(user)} ({USERNAME})")
    print(f"  in 'Employees' group     : {in_employees}")
    print(f"  request closed (state 3) : {state == '3'}")
    ok = bool(user) and in_employees and state == "3"
    print("\n>> " + ("ONBOARDING E2E PASSED" if ok else "ONBOARDING E2E FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

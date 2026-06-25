#!/usr/bin/env python3
"""Scenario — employee onboarding (catalog -> EDA -> Ansible -> Keycloak). From repo root:

  python3 tests/scenarios/5_employee_onboarding.py

Orders the "Onboard a new employee" Service Catalog item for a fresh test joiner, then waits for the
push chain to provision the person's identity in Keycloak and close the request:
  catalog request -> Business Rule -> Event Stream -> push-employee-onboarding -> "Provision
  Employee" JT -> provision_employee.yml (creates the Keycloak user, closes the RITM).
Pre-deletes the test user so the run proves real creation. Asserts the Keycloak user exists (in the
Employees group) and the request item is Closed Complete.

Run standalone; run() returns (ok, detail) and the __main__ wrapper prints PASS/FAIL.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import env, insecure_ctx  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

CTX = insecure_ctx()
ITEM_NAME = "Onboard a new employee"
EMP_NAME = "Sofia Marchetti"
EMP_EMAIL = "sofia.marchetti@meridian.example"
EMP_SERVICE = "HR Self-Service"
USERNAME = EMP_EMAIL.split("@")[0]
TIMEOUT = 150


def kc_base():
    return f"https://{os.environ['AAP_FQDN']}:9443/auth"


def kc_token():
    data = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                   "username": "admin", "password": os.environ["KEYCLOAK_ADMIN_PASSWORD"]}).encode()
    req = urllib.request.Request(f"{kc_base()}/realms/master/protocol/openid-connect/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
        return json.load(r)["access_token"]


def kc(method, path, tok):
    req = urllib.request.Request(f"{kc_base()}/admin/realms/meridian{path}",
                                 headers={"Authorization": f"Bearer {tok}"}, method=method)
    with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def run():
    env()
    snow = Snow(creds="admin")
    tok = kc_token()

    # Pre-clean so this run proves real creation.
    existing = kc("GET", f"/users?username={USERNAME}&exact=true", tok)
    if existing:
        kc("DELETE", f"/users/{existing[0]['id']}", tok)
        print(f">> pre-deleted existing Keycloak user '{USERNAME}'")

    item = snow.call("table/sc_cat_item?" + urllib.parse.urlencode(
        {"sysparm_query": f"name={ITEM_NAME}", "sysparm_fields": "sys_id", "sysparm_limit": "1"}))["result"]
    if not item:
        sys.exit(f"catalog item '{ITEM_NAME}' not found — run bootstrap/4_servicenow/4_catalog.py first")
    item_sid = item[0]["sys_id"]

    print(f">> Ordering '{ITEM_NAME}' for {EMP_NAME} <{EMP_EMAIL}> ({EMP_SERVICE})")
    order = snow.order_now(item_sid, {"employee_name": EMP_NAME, "employee_email": EMP_EMAIL,
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
            ritm = snow.call(f"table/sc_req_item?{rq}")["result"]
            state = ritm[0]["state"] if ritm else None
        if user and state == "3":
            break

    groups = kc("GET", f"/users/{user['id']}/groups", tok) if user else []
    in_employees = any(g.get("name") == "Employees" for g in (groups or []))

    print(f"  Keycloak user created    : {bool(user)} ({USERNAME})")
    print(f"  in 'Employees' group     : {in_employees}")
    print(f"  request closed (state 3) : {state == '3'}")
    ok = bool(user) and in_employees and state == "3"
    return ok, f"{USERNAME}: created={bool(user)}, in Employees={in_employees}, RITM state {state}"


if __name__ == "__main__":
    ok, detail = run()
    print("\n>> " + ("PASS" if ok else "FAIL") + f" — {detail}")
    sys.exit(0 if ok else 1)

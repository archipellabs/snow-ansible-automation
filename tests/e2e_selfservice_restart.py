#!/usr/bin/env python3
"""Self-service catalog restart test (push path), re-runnable. Run from repo root:

  python3 tests/e2e_selfservice_restart.py

Stops hr-portal on hr-web-01, then ORDERS the "Redémarrer un service" Service Catalog item with the
server variable set to hr-web-01 (Service Catalog API, order_now). The catalog Business Rule POSTs
to the AAP Event Stream -> push-selfservice-restart activation -> "Restart Service (Self-Service)"
job template -> restart_service_selfservice.yml restarts the service and closes the request item.
Asserts the service is active again (and reports the request item state). Exit 0 if it recovers.
"""
import os
import sys
import time
import subprocess
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TARGET = "hr-web-01"
SERVICE = "hr-portal"
ITEM_NAME = "Redémarrer un service"
RECOVER_TIMEOUT = 150       # seconds to wait for the whole push chain to restart the service
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

INSECURE = insecure_ctx()
load_dotenv(ROOT)
E = os.environ


def sn(path, body=None):
    method = "POST" if body is not None else "GET"
    return http_json(f"https://{E['SN_INSTANCE']}/api/now/{path}", method=method,
                     headers={"Authorization": basic_auth(E["SN_USER"], E["SN_PASS"])}, body=body)


def sc(path, body):
    return http_json(f"https://{E['SN_INSTANCE']}/api/sn_sc/{path}", method="POST",
                     headers={"Authorization": basic_auth(E["SN_USER"], E["SN_PASS"])}, body=body)


def ssh(cmd):
    return subprocess.run(
        ["ssh", "-i", os.path.expanduser("~/.ssh/snow-aap-poc"),
         "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15",
         f"azureuser@{E['FQDN']}", cmd],
        capture_output=True, text=True, timeout=40).stdout.strip()


def main():
    q = urllib.parse.urlencode({"sysparm_query": f"name={ITEM_NAME}", "sysparm_fields": "sys_id", "sysparm_limit": "1"})
    item = sn(f"table/sc_cat_item?{q}")["result"]
    if not item:
        sys.exit(f"catalog item '{ITEM_NAME}' not found — run bootstrap/servicenow/setup_selfservice.py first")
    item_sid = item[0]["sys_id"]

    print(f">> Breaking {SERVICE} on {TARGET}")
    ssh(f"podman exec {TARGET} systemctl stop {SERVICE}")

    print(f">> Ordering the catalog item (server={TARGET})")
    order = sc(f"servicecatalog/items/{item_sid}/order_now",
               {"sysparm_quantity": "1", "variables": {"server": TARGET}})["result"]
    req_sid = order.get("sys_id")
    print(f"   request {order.get('request_number') or order.get('number')}")

    def ritm():
        rq = urllib.parse.urlencode({"sysparm_query": f"request={req_sid}",
                                     "sysparm_fields": "number,state", "sysparm_limit": "1"})
        res = sn(f"table/sc_req_item?{rq}")["result"]
        return res[0] if res else None

    print(">> Waiting for the self-service chain to restart the service and close the request...")
    svc, rec = "unknown", None
    for _ in range(RECOVER_TIMEOUT // 5):
        time.sleep(5)
        svc = ssh(f"podman exec {TARGET} systemctl is-active {SERVICE}")
        rec = ritm() if req_sid else None
        # The playbook restarts first, then (after a recheck) closes the RITM to state 3.
        if svc == "active" and rec and rec["state"] == "3":
            break

    if svc != "active":
        ssh(f"podman exec {TARGET} systemctl start {SERVICE}")   # safety net: never leave it down

    closed = bool(rec and rec["state"] == "3")
    print()
    print(f"  {SERVICE} active            : {svc == 'active'}")
    print(f"  request item closed (3)   : {closed}" + (f" ({rec['number']} state={rec['state']})" if rec else ""))
    ok = svc == "active" and closed
    print("\n>> " + ("SELF-SERVICE E2E PASSED" if ok else "SELF-SERVICE E2E FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

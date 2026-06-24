#!/usr/bin/env python3
"""Scenario — self-service catalog restart (push path), re-runnable. Run from repo root:

  python3 tests/scenarios/4_selfservice_restart.py

Stops hr-portal on hr-web-01, then ORDERS the "Restart a service" Service Catalog item with the
server variable set to hr-web-01 (Service Catalog API, order_now). The catalog Business Rule POSTs
to the AAP Event Stream -> push-selfservice-restart activation -> "Restart Service (Self-Service)"
job template -> restart_service_selfservice.yml restarts the service and closes the request item.
Asserts the service is active again (and reports the request item state).

Run standalone; run() returns (ok, detail) and the __main__ wrapper prints PASS/FAIL.
"""
import os
import sys
import time
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import env, ssh  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

TARGET = "hr-web-01"
SERVICE = "hr-portal"
ITEM_NAME = "Restart a service"
RECOVER_TIMEOUT = 150       # seconds to wait for the whole push chain to restart the service


def run():
    env()
    snow = Snow(creds="admin")

    q = urllib.parse.urlencode({"sysparm_query": f"name={ITEM_NAME}", "sysparm_fields": "sys_id", "sysparm_limit": "1"})
    item = snow.call(f"table/sc_cat_item?{q}")["result"]
    if not item:
        sys.exit(f"catalog item '{ITEM_NAME}' not found — run bootstrap/4_servicenow/4_catalog.py first")
    item_sid = item[0]["sys_id"]

    print(f">> Breaking {SERVICE} on {TARGET}")
    ssh(f"podman exec {TARGET} systemctl stop {SERVICE}")

    print(f">> Ordering the catalog item (server={TARGET})")
    order = snow.order_now(item_sid, {"server": TARGET})
    req_sid = order.get("sys_id")
    print(f"   request {order.get('request_number') or order.get('number')}")

    def ritm():
        rq = urllib.parse.urlencode({"sysparm_query": f"request={req_sid}",
                                     "sysparm_fields": "number,state", "sysparm_limit": "1"})
        res = snow.call(f"table/sc_req_item?{rq}")["result"]
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
    print(f"  {SERVICE} active            : {svc == 'active'}")
    print(f"  request item closed (3)   : {closed}" + (f" ({rec['number']} state={rec['state']})" if rec else ""))
    ok = svc == "active" and closed
    return ok, f"{SERVICE}={svc}, RITM closed={closed}" + (f" ({rec['number']})" if rec else "")


if __name__ == "__main__":
    ok, detail = run()
    print("\n>> " + ("PASS" if ok else "FAIL") + f" — {detail}")
    sys.exit(0 if ok else 1)

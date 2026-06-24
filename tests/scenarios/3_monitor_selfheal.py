#!/usr/bin/env python3
"""Scenario — self-driving pull loop (monitoring -> incident -> remediation), re-runnable.

  python3 tests/scenarios/3_monitor_selfheal.py

Unlike the pull scenario (1_pull_incident_remediation.py, which opens the incident itself), this test
only injects a fault: it sets hr-web-01's degraded flag so /health returns 503. Then it waits for the
whole chain
to run with NO ticket created by the test:
  ansible.eda.url_check (monitor-health) sees "down" -> "Open Incident" -> ServiceNow incident
  -> pull-incident-remediation -> "Restart Service" -> flag cleared, service restarted, incident resolved.
Asserts that an Auto-Remediation incident was opened for hr-web-01 and then resolved, and that the
app is healthy again. Requires both activations enabled (created by the "Configure EDA" job template).

Run standalone; run() returns (ok, detail) and the __main__ wrapper prints PASS/FAIL.
"""
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import env, ssh  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

TARGET = "hr-web-01"
SERVICE = "hr-portal"
FLAG = "/var/lib/hr-portal/unhealthy"
OPEN_TIMEOUT = 150         # seconds to wait for the monitor to open an incident
RESOLVE_TIMEOUT = 150      # seconds to wait for remediation to resolve it


def active_incident(snow):
    q = f"cmdb_ci.name={TARGET}^assignment_group.name=Auto-Remediation^active=true"
    res = snow.call(f"table/incident?sysparm_query={q}&sysparm_fields=number,sys_id&sysparm_limit=1")["result"]
    return res[0] if res else None


def run():
    env()
    snow = Snow(creds="eda")

    # Clear any leftover active incident from a previous broken run so we observe a fresh open.
    pre = active_incident(snow)
    if pre:
        print(f"!! pre-existing active incident {pre['number']} for {TARGET}; "
              "let it resolve or close it before a clean run.")

    print(f">> Injecting fault: degrading {SERVICE} on {TARGET} (/health -> 503)")
    ssh(f"podman exec {TARGET} touch {FLAG}")

    print(">> Waiting for the monitor to OPEN an incident (no ticket from this test)...")
    inc = None
    for _ in range(OPEN_TIMEOUT // 5):
        time.sleep(5)
        inc = active_incident(snow)
        if inc and (not pre or inc["number"] != pre["number"]):
            break
    if not inc:
        ssh(f"podman exec {TARGET} rm -f {FLAG}")    # restore before bailing
        return False, "no incident was opened by the monitor"
    num, sid = inc["number"], inc["sys_id"]
    print(f"   incident {num} opened")

    print(">> Waiting for remediation to RESOLVE it...")
    state = None
    for _ in range(RESOLVE_TIMEOUT // 5):
        time.sleep(5)
        state = snow.call(f"table/incident/{sid}?sysparm_fields=state")["result"]["state"]
        if state == "6":          # Resolved
            break

    svc = ssh(f"podman exec {TARGET} systemctl is-active {SERVICE}")
    flag = ssh(f"podman exec {TARGET} sh -c 'test -f {FLAG} && echo present || echo absent'")
    if flag != "absent":          # safety net: never leave the app degraded
        ssh(f"podman exec {TARGET} rm -f {FLAG}")

    print(f"  incident opened by monitor : True ({num})")
    print(f"  incident resolved          : {state == '6'} (state={state})")
    print(f"  {SERVICE} active            : {svc == 'active'}")
    print(f"  degraded flag cleared      : {flag == 'absent'}")
    ok = state == "6" and svc == "active" and flag == "absent"
    return ok, f"{num}: state {state}, {SERVICE}={svc}, flag={flag}"


if __name__ == "__main__":
    ok, detail = run()
    print("\n>> " + ("PASS" if ok else "FAIL") + f" — {detail}")
    sys.exit(0 if ok else 1)

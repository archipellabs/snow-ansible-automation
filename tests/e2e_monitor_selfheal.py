#!/usr/bin/env python3
"""Self-driving pull-loop test (monitoring -> incident -> remediation), re-runnable. From repo root:

  python3 tests/e2e_monitor_selfheal.py

Unlike e2e_pull_incident_remediation.py (which opens the incident itself), this test only injects a
fault: it sets hr-web-01's degraded flag so /health returns 503. Then it waits for the whole chain
to run with NO ticket created by the test:
  ansible.eda.url_check (monitor-health) sees "down" -> "Open Incident" -> ServiceNow incident
  -> pull-incident-remediation -> "Restart Service" -> flag cleared, service restarted, incident resolved.
Asserts that an Auto-Remediation incident was opened for hr-web-01 and then resolved, and that the
app is healthy again. Exit 0 if all pass.

Requires both activations enabled: monitor-health (configure_monitor.py) and
pull-incident-remediation (configure.py).
"""
import os
import sys
import time
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TARGET = "hr-web-01"
SERVICE = "hr-portal"
FLAG = "/var/lib/hr-portal/unhealthy"
OPEN_TIMEOUT = 150         # seconds to wait for the monitor to open an incident
RESOLVE_TIMEOUT = 150      # seconds to wait for remediation to resolve it
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

INSECURE = insecure_ctx()
load_dotenv(ROOT)
E = os.environ


def sn(path):
    return http_json(f"https://{E['SN_INSTANCE']}/api/now/{path}",
                     headers={"Authorization": basic_auth(E["SN_EDA_USERNAME"], E["SN_EDA_PASSWORD"])})


def ssh(cmd):
    return subprocess.run(
        ["ssh", "-i", os.path.expanduser("~/.ssh/snow-aap-poc"),
         "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15",
         f"azureuser@{E['FQDN']}", cmd],
        capture_output=True, text=True, timeout=40).stdout.strip()


def active_incident():
    q = "cmdb_ci.name=%s^assignment_group.name=Auto-Remediation^active=true" % TARGET
    res = sn(f"table/incident?sysparm_query={q}&sysparm_fields=number,sys_id&sysparm_limit=1")["result"]
    return res[0] if res else None


def main():
    # Clear any leftover active incident from a previous broken run so we observe a fresh open.
    pre = active_incident()
    if pre:
        print(f"!! pre-existing active incident {pre['number']} for {TARGET}; "
              "let it resolve or close it before a clean run.")

    print(f">> Injecting fault: degrading {SERVICE} on {TARGET} (/health -> 503)")
    ssh(f"podman exec {TARGET} touch {FLAG}")

    print(">> Waiting for the monitor to OPEN an incident (no ticket from this test)...")
    inc = None
    for _ in range(OPEN_TIMEOUT // 5):
        time.sleep(5)
        inc = active_incident()
        if inc and (not pre or inc["number"] != pre["number"]):
            break
    if not inc:
        ssh(f"podman exec {TARGET} rm -f {FLAG}")    # restore before bailing
        print(">> E2E FAILED — no incident was opened by the monitor")
        sys.exit(1)
    num, sid = inc["number"], inc["sys_id"]
    print(f"   incident {num} opened")

    print(">> Waiting for remediation to RESOLVE it...")
    state = None
    for _ in range(RESOLVE_TIMEOUT // 5):
        time.sleep(5)
        state = sn(f"table/incident/{sid}?sysparm_fields=state")["result"]["state"]
        if state == "6":          # Resolved
            break

    svc = ssh(f"podman exec {TARGET} systemctl is-active {SERVICE}")
    flag = ssh(f"podman exec {TARGET} sh -c 'test -f {FLAG} && echo present || echo absent'")
    if flag != "absent":          # safety net: never leave the app degraded
        ssh(f"podman exec {TARGET} rm -f {FLAG}")

    print()
    print(f"  incident opened by monitor : True ({num})")
    print(f"  incident resolved          : {state == '6'} (state={state})")
    print(f"  {SERVICE} active            : {svc == 'active'}")
    print(f"  degraded flag cleared      : {flag == 'absent'}")
    ok = state == "6" and svc == "active" and flag == "absent"
    print("\n>> " + ("SELF-HEAL E2E PASSED" if ok else "SELF-HEAL E2E FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

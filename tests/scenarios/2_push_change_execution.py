#!/usr/bin/env python3
"""Scenario — PUSH path (Change Request -> Event Stream -> EDA), re-runnable.

Mirrors the pull scenario (1_pull_incident_remediation.py) for the push pattern: it never launches the
job itself. It opens a ServiceNow change request and approves it; the Business Rule POSTs to the AAP
event stream, which
feeds the webhook rulebook, which launches the "Execute Change Request" job. Run from the repo root:

  python3 tests/scenarios/2_push_change_execution.py

Asserts that EDA launched a NEW controller job carrying our change number, that it succeeded,
and that the change's content was actually deployed on the target.

Run standalone; run() returns (ok, detail) and the __main__ wrapper prints PASS/FAIL.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import env, ssh  # noqa: E402
from lib.runtime import controller  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

TARGET = "hr-web-01"       # a Meridian Fleet server
SERVICE = "hr-portal"      # the systemd unit it runs
JT_NAME = "Execute Change Request"
TRIGGER_TIMEOUT = 180


def run():
    env()
    ctl, snow = controller(), Snow(creds="admin")
    jt = ctl.jt_id(JT_NAME)
    baseline = max((j["id"] for j in ctl.recent_jobs(jt)), default=0)
    print(f">> Baseline: latest '{JT_NAME}' job id = {baseline}")

    print(">> Opening + approving a ServiceNow change request")
    chg = snow.call("table/change_request?sysparm_input_display_value=true",
                    {"short_description": f"Deploy content to {TARGET} (push scenario)", "cmdb_ci": TARGET})["result"]
    num, sid = chg["number"], chg["sys_id"]
    snow.call(f"table/change_request/{sid}", {"approval": "approved"}, method="PATCH")  # fires the Business Rule
    print(f"   change {num} approved")

    print(">> Waiting for EDA to auto-launch the job (push via event stream)...")
    job = ctl.wait_triggered_job(jt, baseline, num, timeout=TRIGGER_TIMEOUT)
    if not job:
        return False, f"{num}: no EDA-launched job within {TRIGGER_TIMEOUT}s"
    jid = job["id"]
    print(f"   EDA launched job id={jid}")

    status = ctl.wait_job(jid)
    print(f"   job status: {status}")

    marker = ssh(f"podman exec {TARGET} cat /var/lib/meridian/last_change 2>/dev/null")
    applied = num in marker
    svc = ssh(f"podman exec {TARGET} systemctl is-active {SERVICE}")

    print(f"  EDA auto-launched : True")
    print(f"  job successful    : {status == 'successful'}")
    print(f"  change applied    : {applied} (marker mentions {num})")
    print(f"  {SERVICE} active   : {svc == 'active'}")
    ok = status == "successful" and applied and svc == "active"
    return ok, f"{num}: EDA job {jid} {status}, applied={applied}, {SERVICE}={svc}"


if __name__ == "__main__":
    ok, detail = run()
    print("\n>> " + ("PASS" if ok else "FAIL") + f" — {detail}")
    sys.exit(0 if ok else 1)

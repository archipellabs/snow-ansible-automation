#!/usr/bin/env python3
"""Scenario — EDA auto-trigger, full event-driven pull path, re-runnable.

This test never launches the job template itself: it only breaks the service and opens the
incident, then proves that the EDA rulebook activation detected it and auto-launched the
"Restart Service" job. Run from repo root:

  python3 tests/scenarios/1_pull_incident_remediation.py

Steps: stop crm on crm-web-01 -> open a ServiceNow incident in the Auto-Remediation
group -> wait for EDA to launch a NEW controller job for the template -> assert that job
carries our incident number, succeeds, the service is active again, and the incident resolves.

Run standalone; run() returns (ok, detail) and the __main__ wrapper prints PASS/FAIL.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import env, ssh  # noqa: E402
from lib.runtime import controller  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

TARGET = "crm-web-01"      # a distinct server per scenario, so the suite is parallel-safe (--scenarios --parallel)
SERVICE = "crm"            # the systemd unit it runs (matches the inventory host var)
JT_NAME = "Restart Service"
TRIGGER_TIMEOUT = 180  # EDA poll interval is 10s; allow margin + job runtime


def run():
    env()
    ctl, snow = controller(), Snow(creds="eda")
    jt = ctl.jt_id(JT_NAME)
    baseline = max((j["id"] for j in ctl.recent_jobs(jt)), default=0)
    print(f">> Baseline: latest '{JT_NAME}' job id = {baseline}")

    print(f">> Breaking {SERVICE} on {TARGET}")
    ssh(f"podman exec {TARGET} systemctl stop {SERVICE}")

    print(">> Opening ServiceNow incident (Auto-Remediation group)")
    inc = snow.call("table/incident?sysparm_input_display_value=true",
                    {"short_description": f"{TARGET} service down (pull scenario)",
                     "assignment_group": "Auto-Remediation", "cmdb_ci": TARGET})["result"]
    num, sid = inc["number"], inc["sys_id"]
    print(f"   incident {num}")

    print(">> Waiting for EDA to auto-launch the job (no manual launch)...")
    job = ctl.wait_triggered_job(jt, baseline, num, timeout=TRIGGER_TIMEOUT)
    if not job:
        return False, f"{num}: no EDA-launched job within {TRIGGER_TIMEOUT}s"
    jid = job["id"]
    print(f"   EDA launched job id={jid} (launch_type={job.get('launch_type')})")

    status = ctl.wait_job(jid)
    print(f"   job status: {status}")

    svc = ssh(f"podman exec {TARGET} systemctl is-active {SERVICE}")
    state = snow.call(f"table/incident/{sid}?sysparm_fields=state")["result"]["state"]  # 6 = Resolved

    print(f"  EDA auto-launched : True")
    print(f"  job successful    : {status == 'successful'}")
    print(f"  {SERVICE} active   : {svc == 'active'}")
    print(f"  incident state    : {state} ({'Resolved' if state == '6' else 'not resolved'})")
    ok = status == "successful" and svc == "active" and state == "6"
    return ok, f"{num}: EDA job {jid} {status}, {SERVICE}={svc}, incident state {state}"


if __name__ == "__main__":
    ok, detail = run()
    print("\n>> " + ("PASS" if ok else "FAIL") + f" — {detail}")
    sys.exit(0 if ok else 1)

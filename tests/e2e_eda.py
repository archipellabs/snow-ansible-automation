#!/usr/bin/env python3
"""End-to-end EDA auto-trigger test (full event-driven path), re-runnable.

Unlike e2e_remediation.py, this test never launches the job template itself: it only
breaks the service and opens the incident, then proves that the EDA rulebook activation
detected it and auto-launched the "Restart Service" job. Run from repo root:

  python3 tests/e2e_eda.py

Steps: stop hr-portal on hr-web-01 -> open a ServiceNow incident in the Auto-Remediation
group -> wait for EDA to launch a NEW controller job for the template -> assert that job
carries our incident number, succeeds, the service is active again, and the incident resolves.
Exit 0 if it all passes, 1 otherwise.
"""
import os
import sys
import time
import subprocess
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSH_KEY = os.path.expanduser("~/.ssh/snow-aap-poc")
TARGET = "hr-web-01"       # a Meridian Fleet server
SERVICE = "hr-portal"      # the systemd unit it runs (matches the inventory host var)
JT_NAME = "Restart Service"
TRIGGER_TIMEOUT = 180  # EDA poll interval is 10s; allow margin + job runtime
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

INSECURE = insecure_ctx()
load_dotenv(ROOT)
E = os.environ


def call(url, user, pw, body=None, insecure=False):
    method = "POST" if body is not None else "GET"
    return http_json(url, method=method, headers={"Authorization": basic_auth(user, pw)},
                     body=body, ctx=INSECURE if insecure else None)


def sn(path, body=None):
    return call(f"https://{E['SN_INSTANCE']}/api/now/{path}",
                E["SN_EDA_USERNAME"], E["SN_EDA_PASSWORD"], body)


def aap(path, body=None):
    return call(f"https://{E['FQDN']}/api/controller/v2/{path}",
                E["AAP_ADMIN_USER"], E["AAP_ADMIN_PASSWORD"], body, insecure=True)


def ssh(cmd):
    return subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=accept-new",
         "-o", "ConnectTimeout=15", f"azureuser@{E['FQDN']}", cmd],
        capture_output=True, text=True, timeout=40).stdout.strip()


def jt_jobs(jt_id):
    q = urllib.parse.urlencode({"order_by": "-id", "page_size": 20})
    return aap(f"job_templates/{jt_id}/jobs/?{q}").get("results", [])


def main():
    jt = aap("job_templates/?name=" + urllib.parse.quote(JT_NAME))["results"][0]["id"]
    baseline = max((j["id"] for j in jt_jobs(jt)), default=0)
    print(f">> Baseline: latest '{JT_NAME}' job id = {baseline}")

    print(f">> Breaking {SERVICE} on {TARGET}")
    ssh(f"podman exec {TARGET} systemctl stop {SERVICE}")

    print(">> Opening ServiceNow incident (Auto-Remediation group)")
    inc = sn("table/incident?sysparm_input_display_value=true",
             {"short_description": f"{TARGET} service down (eda e2e test)",
              "assignment_group": "Auto-Remediation", "cmdb_ci": TARGET})["result"]
    num, sid = inc["number"], inc["sys_id"]
    print(f"   incident {num}")

    print(">> Waiting for EDA to auto-launch the job (no manual launch)...")
    job = None
    deadline = time.monotonic() + TRIGGER_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(5)
        for j in jt_jobs(jt):
            if j["id"] > baseline and num in (j.get("extra_vars") or ""):
                job = j
                break
        if job:
            break
    if not job:
        print(f"\n>> EDA E2E FAILED: no auto-launched job for {num} within {TRIGGER_TIMEOUT}s")
        sys.exit(1)
    jid = job["id"]
    print(f"   EDA launched job id={jid} (launch_type={job.get('launch_type')})")

    status = job["status"]
    for _ in range(40):
        if status in ("successful", "failed", "error", "canceled"):
            break
        time.sleep(4)
        status = aap(f"jobs/{jid}/")["status"]
    print(f"   job status: {status}")

    svc = ssh(f"podman exec {TARGET} systemctl is-active {SERVICE}")
    state = sn(f"table/incident/{sid}?sysparm_fields=state")["result"]["state"]  # 6 = Resolved

    print()
    print(f"  EDA auto-launched : {job is not None}")
    print(f"  job successful    : {status == 'successful'}")
    print(f"  {SERVICE} active   : {svc == 'active'}")
    print(f"  incident state    : {state} ({'Resolved' if state == '6' else 'not resolved'})")
    ok = job is not None and status == "successful" and svc == "active" and state == "6"
    print("\n>> " + ("EDA E2E PASSED" if ok else "EDA E2E FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

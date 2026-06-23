#!/usr/bin/env python3
"""End-to-end PUSH test (Change Request -> Event Stream -> EDA), re-runnable.

Mirrors e2e_eda.py for the push pattern: it never launches the job itself. It opens a
ServiceNow change request and approves it; the Business Rule POSTs to the AAP event stream,
which feeds the webhook rulebook, which launches the "Execute Change Request" job. Run from
the repo root:

  python3 tests/e2e_change.py

Asserts that EDA launched a NEW controller job carrying our change number, that it succeeded,
and that the change's content was actually deployed on the target. Exit 0 if all pass.
"""
import os
import sys
import time
import subprocess
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSH_KEY = os.path.expanduser("~/.ssh/snow-aap-poc")
TARGET = "app-node-1"
JT_NAME = "Execute Change Request"
TRIGGER_TIMEOUT = 180
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

INSECURE = insecure_ctx()
load_dotenv(ROOT)
E = os.environ


def call(url, user, pw, body=None, insecure=False, method=None):
    return http_json(url, method=method or ("POST" if body is not None else "GET"),
                     headers={"Authorization": basic_auth(user, pw)},
                     body=body, ctx=INSECURE if insecure else None)


def sn(path, body=None, method=None):
    return call(f"https://{E['SN_INSTANCE']}/api/now/{path}", E["SN_USER"], E["SN_PASS"], body, method=method)


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

    print(">> Opening + approving a ServiceNow change request")
    chg = sn("table/change_request?sysparm_input_display_value=true",
             {"short_description": f"Deploy content to {TARGET} (push e2e)", "cmdb_ci": TARGET})["result"]
    num, sid = chg["number"], chg["sys_id"]
    sn(f"table/change_request/{sid}", {"approval": "approved"}, method="PATCH")  # fires the Business Rule
    print(f"   change {num} approved")

    print(">> Waiting for EDA to auto-launch the job (push via event stream)...")
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
        print(f"\n>> PUSH E2E FAILED: no auto-launched job for {num} within {TRIGGER_TIMEOUT}s")
        sys.exit(1)
    jid = job["id"]
    print(f"   EDA launched job id={jid}")

    status = job["status"]
    for _ in range(40):
        if status in ("successful", "failed", "error", "canceled"):
            break
        time.sleep(4)
        status = aap(f"jobs/{jid}/")["status"]
    print(f"   job status: {status}")

    deployed = ssh(f"podman exec {TARGET} cat /var/www/html/index.html 2>/dev/null")
    content_ok = num in deployed

    print()
    print(f"  EDA auto-launched : {job is not None}")
    print(f"  job successful    : {status == 'successful'}")
    print(f"  change deployed   : {content_ok} (index.html mentions {num})")
    ok = job is not None and status == "successful" and content_ok
    print("\n>> " + ("PUSH E2E PASSED" if ok else "PUSH E2E FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

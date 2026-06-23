#!/usr/bin/env python3
"""End-to-end remediation test (controller path), re-runnable. Run from repo root:

  python3 tests/e2e_remediation.py

Breaks httpd on app-node-1, opens a ServiceNow incident, launches the "Remediate Ping
Server" job template (incident_number + target_host), then asserts the job succeeds, the
service is back up, and the incident is resolved. Exit 0 if it all passes, 1 otherwise.
"""
import os
import sys
import time
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSH_KEY = os.path.expanduser("~/.ssh/snow-aap-poc")
TARGET = "app-node-1"
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


def main():
    print(f">> Breaking httpd on {TARGET}")
    ssh(f"podman exec {TARGET} systemctl stop httpd")

    print(">> Opening ServiceNow incident")
    inc = sn("table/incident?sysparm_input_display_value=true",
             {"short_description": f"{TARGET} service down (e2e test)",
              "assignment_group": "Auto-Remediation", "cmdb_ci": TARGET})["result"]
    num, sid = inc["number"], inc["sys_id"]
    print(f"   incident {num}")

    print(">> Launching job template 'Remediate Ping Server'")
    jt = aap("job_templates/?name=Remediate%20Ping%20Server")["results"][0]["id"]
    job = aap(f"job_templates/{jt}/launch/",
              {"extra_vars": {"incident_number": num, "target_host": TARGET}})
    jid = job["id"]
    status = "pending"
    for _ in range(40):
        time.sleep(4)
        status = aap(f"jobs/{jid}/")["status"]
        if status in ("successful", "failed", "error", "canceled"):
            break
    print(f"   job status: {status}")

    httpd = ssh(f"podman exec {TARGET} systemctl is-active httpd")
    state = sn(f"table/incident/{sid}?sysparm_fields=state")["result"]["state"]  # 6 = Resolved

    print()
    print(f"  job successful : {status == 'successful'}")
    print(f"  httpd active   : {httpd == 'active'}")
    print(f"  incident state : {state} ({'Resolved' if state == '6' else 'not resolved'})")
    ok = status == "successful" and httpd == "active" and state == "6"
    print("\n>> " + ("E2E PASSED" if ok else "E2E FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

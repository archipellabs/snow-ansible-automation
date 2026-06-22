#!/usr/bin/env python3
"""End-to-end remediation test (controller path), re-runnable. Run from repo root:

  python3 tests/e2e_remediation.py

Breaks httpd on app-node-1, opens a ServiceNow incident, launches the "Remediate Ping
Server" job template (incident_number + target_host), then asserts the job succeeds, the
service is back up, and the incident is resolved. Exit 0 if it all passes, 1 otherwise.
"""
import os
import sys
import ssl
import json
import time
import base64
import subprocess
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSH_KEY = os.path.expanduser("~/.ssh/snow-aap-poc")
TARGET = "app-node-1"
INSECURE = ssl.create_default_context()
INSECURE.check_hostname = False
INSECURE.verify_mode = ssl.CERT_NONE


def load_dotenv():
    for p in (os.path.join(os.getcwd(), ".env"), os.path.join(ROOT, ".env")):
        if os.path.isfile(p):
            for line in open(p):
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    k, v = s.split("=", 1)
                    os.environ.setdefault(k.strip(), v)
            return


load_dotenv()
E = os.environ


def call(url, user, pw, body=None, insecure=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    r.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    if data:
        r.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(r, context=INSECURE if insecure else None, timeout=30)
    raw = resp.read()
    return json.loads(raw) if raw else {}


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

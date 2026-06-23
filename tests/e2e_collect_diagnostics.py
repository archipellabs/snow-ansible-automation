#!/usr/bin/env python3
"""Diagnostics-collection test (controller path), re-runnable. Run from repo root:

  python3 tests/e2e_collect_diagnostics.py

Opens a ServiceNow incident against hr-web-01, launches the read-only "Collect Diagnostics" job
template (incident_number + target_host), then asserts the job succeeds and the incident was
acknowledged (moved to In Progress with the diagnostics work note). Makes no changes to the server.
Exit 0 if all pass.
"""
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TARGET = "hr-web-01"       # a Meridian Fleet server
JT_NAME = "Collect Diagnostics"
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


def main():
    print(">> Opening ServiceNow incident")
    inc = sn("table/incident?sysparm_input_display_value=true",
             {"short_description": f"{TARGET} investigate (e2e diagnostics test)",
              "assignment_group": "Auto-Remediation", "cmdb_ci": TARGET})["result"]
    num, sid = inc["number"], inc["sys_id"]
    print(f"   incident {num}")

    print(f">> Launching job template '{JT_NAME}'")
    jt = aap("job_templates/?name=" + JT_NAME.replace(" ", "%20"))["results"][0]["id"]
    jid = aap(f"job_templates/{jt}/launch/",
              {"extra_vars": {"incident_number": num, "target_host": TARGET}})["id"]
    status = "pending"
    for _ in range(40):
        time.sleep(4)
        status = aap(f"jobs/{jid}/")["status"]
        if status in ("successful", "failed", "error", "canceled"):
            break
    print(f"   job status: {status}")

    state = sn(f"table/incident/{sid}?sysparm_fields=state")["result"]["state"]  # 2 = In Progress

    print()
    print(f"  job successful   : {status == 'successful'}")
    print(f"  incident state   : {state} ({'In Progress' if state == '2' else 'not acknowledged'})")
    ok = status == "successful" and state == "2"
    print("\n>> " + ("E2E PASSED" if ok else "E2E FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

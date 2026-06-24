#!/usr/bin/env python3
"""Scenario — diagnostics collection (controller path), re-runnable. Run from repo root:

  python3 tests/scenarios/6_collect_diagnostics.py

Opens a ServiceNow incident against hr-web-01, launches the read-only "Collect Diagnostics" job
template (incident_number + target_host), then asserts the job succeeds and the incident was
acknowledged (moved to In Progress with the diagnostics work note). Makes no changes to the server.

Run standalone; run() returns (ok, detail) and the __main__ wrapper prints PASS/FAIL.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import env  # noqa: E402
from lib.runtime import controller  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

TARGET = "hr-web-01"       # a Meridian Fleet server
JT_NAME = "Collect Diagnostics"


def run():
    env()
    ctl, snow = controller(), Snow(creds="eda")

    print(">> Opening ServiceNow incident")
    inc = snow.call("table/incident?sysparm_input_display_value=true",
                    {"short_description": f"{TARGET} investigate (diagnostics scenario)",
                     "assignment_group": "Auto-Remediation", "cmdb_ci": TARGET})["result"]
    num, sid = inc["number"], inc["sys_id"]
    print(f"   incident {num}")

    print(f">> Launching job template '{JT_NAME}'")
    jid, status = ctl.run_jt(JT_NAME, {"incident_number": num, "target_host": TARGET})
    print(f"   job status: {status}")

    state = snow.call(f"table/incident/{sid}?sysparm_fields=state")["result"]["state"]  # 2 = In Progress

    print(f"  job successful   : {status == 'successful'}")
    print(f"  incident state   : {state} ({'In Progress' if state == '2' else 'not acknowledged'})")
    ok = status == "successful" and state == "2"
    return ok, f"{num}: job {status}, incident state {state}"


if __name__ == "__main__":
    ok, detail = run()
    print("\n>> " + ("PASS" if ok else "FAIL") + f" — {detail}")
    sys.exit(0 if ok else 1)

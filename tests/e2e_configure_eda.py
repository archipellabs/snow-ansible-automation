#!/usr/bin/env python3
"""GitOps EDA config test, re-runnable. Run from repo root:

  python3 tests/e2e_configure_eda.py

Launches the "Configure EDA" job template (which applies bootstrap/aap/eda/configure.yml via
infra.aap_configuration — GitOps), waits for the job and ASSERTS it succeeded, then checks that all
five activations exist and reach 'running'. Idempotent: the playbook reconciles activations by
delete-then-create, so a re-run converges. Exit 0 if the job is successful and the activations run.
"""
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

load_dotenv(ROOT)
E = os.environ
CTX = insecure_ctx()
C = f"https://{E['FQDN']}/api/controller/v2"
EDA = f"https://{E['FQDN']}/api/eda/v1"
H = {"Authorization": basic_auth(E["AAP_ADMIN_USER"], E["AAP_ADMIN_PASSWORD"])}
EXPECTED = {"pull-incident-remediation", "monitor-health", "push-change-execution",
            "push-selfservice-restart", "push-employee-onboarding"}


def g(url):
    return http_json(url, headers=H, ctx=CTX)


def main():
    # sync the project so the JT runs the latest committed config
    proj = g(f"{C}/projects/?name=snow-ansible-automation")["results"][0]["id"]
    http_json(f"{C}/projects/{proj}/update/", method="POST", headers=H, ctx=CTX)
    for _ in range(60):
        time.sleep(4)
        if g(f"{C}/projects/{proj}/")["status"] in ("successful", "failed", "error"):
            break

    jt = g(f"{C}/job_templates/?name=Configure%20EDA")["results"]
    if not jt:
        sys.exit("'Configure EDA' job template not found — run bootstrap/aap/controller/configure.py")
    print(">> Launching the 'Configure EDA' job template")
    jid = http_json(f"{C}/job_templates/{jt[0]['id']}/launch/", method="POST", headers=H, ctx=CTX)["id"]
    status = "pending"
    for _ in range(120):
        time.sleep(6)
        status = g(f"{C}/jobs/{jid}/")["status"]
        if status in ("successful", "failed", "error", "canceled"):
            break
    print(f"   job {jid} status: {status}")

    print(">> Waiting for the activations to run")
    names, running = set(), set()
    for _ in range(36):
        acts = g(f"{EDA}/activations/?page_size=50")["results"]
        names = {a["name"] for a in acts}
        running = {a["name"] for a in acts if a.get("status") == "running"}
        if EXPECTED <= running:
            break
        time.sleep(5)

    print()
    print(f"  job successful        : {status == 'successful'}")
    print(f"  all 5 activations     : {EXPECTED <= names} ({len(names & EXPECTED)}/5)")
    print(f"  all 5 running         : {EXPECTED <= running}")
    ok = status == "successful" and EXPECTED <= running
    print("\n>> " + ("CONFIGURE-EDA E2E PASSED" if ok else "CONFIGURE-EDA E2E FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

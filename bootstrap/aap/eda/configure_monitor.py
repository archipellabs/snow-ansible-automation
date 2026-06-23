#!/usr/bin/env python3
"""Configure the health-monitoring activation in EDA (idempotent, API, stdlib only).

Creates the rulebook activation that runs monitor_health_open_incident.yml: ansible.eda.url_check
probes each Meridian app's /health endpoint and, on a failure, launches the "Open Incident" job
template. The incident it opens (assignment group Auto-Remediation) is then remediated by the
existing pull-incident-remediation activation -> "Restart Service". This closes the loop with no
human or test in the middle: app down -> incident opened -> service restarted -> incident resolved.

Reuses the decision environment (snow-eda-de) and the "AAP Controller" credential created by
bootstrap/aap/eda/configure.py. The url_check source needs no credentials. Run AFTER the rulebook
is pushed to Git (the EDA project must sync it) and AFTER bootstrap/aap/controller/configure.py
(the "Open Incident" job template must exist). Run from the repo root:
  python3 bootstrap/aap/eda/configure_monitor.py
"""
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

DE_NAME = "snow-eda-de"
CONTROLLER_CRED_NAME = "AAP Controller"
PROJECT_NAME = "snow-ansible-automation"
RULEBOOK_NAME = "monitor_health_open_incident.yml"
ACTIVATION_NAME = "monitor-health"


sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

load_dotenv(ROOT, required=("FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD"))

FQDN = os.environ["FQDN"]
BASE = f"https://{FQDN}/api/eda/v1"
HEADERS = {"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])}
CTX = insecure_ctx()


def api(method, path, body=None):
    url = path if path.startswith("http") else f"{BASE}/{path}"
    return http_json(url, method=method, headers=HEADERS, body=body, ctx=CTX, timeout=60)


def find(endpoint, name):
    res = api("GET", f"{endpoint}/?{urllib.parse.urlencode({'name': name})}")
    return (res.get("results") or [None])[0]


def main():
    org = (api("GET", "organizations/?name=Default").get("results") or [{"id": 1}])[0]["id"]

    de = find("decision-environments", DE_NAME)
    controller_cred = find("eda-credentials", CONTROLLER_CRED_NAME)
    if not de or not controller_cred:
        sys.exit("run bootstrap/aap/eda/configure.py first (decision environment + AAP Controller credential)")

    # Make sure the project has synced the monitoring rulebook.
    proj = find("projects", PROJECT_NAME)
    if not proj:
        sys.exit("EDA project not found (run bootstrap/aap/eda/configure.py)")
    api("POST", f"projects/{proj['id']}/sync/")
    for _ in range(40):
        if api("GET", f"projects/{proj['id']}/").get("import_state") in ("completed", "failed"):
            break
        time.sleep(3)
    rb = find("rulebooks", RULEBOOK_NAME)
    if not rb:
        sys.exit(f"rulebook '{RULEBOOK_NAME}' not found — push it to Git and re-run")
    print(f"= rulebook {RULEBOOK_NAME} (id={rb['id']})")

    # Activation: runs the url_check rulebook; the AAP Controller credential lets its rules launch
    # the "Open Incident" job template. No source_mappings (url_check is an internal source) and no
    # extra vars (it carries no ServiceNow creds — the Open Incident playbook owns that).
    act = find("activations", ACTIVATION_NAME)
    if act:
        print(f"= Activation exists (id={act['id']}). To re-apply, delete it first:")
        print(f"    DELETE {BASE}/activations/{act['id']}/")
    else:
        act = api("POST", "activations/", {
            "name": ACTIVATION_NAME, "decision_environment_id": de["id"],
            "rulebook_id": rb["id"], "organization_id": org,
            "eda_credentials": [controller_cred["id"]],
            "restart_policy": "on-failure", "log_level": "info", "is_enabled": True})
        print(f"+ Activation created (id={act['id']})")

    print("\n>> Health monitoring is live. The pull-incident-remediation activation handles the")
    print("   incidents this opens. Validate the self-driving loop: python3 tests/e2e_monitor_selfheal.py")


if __name__ == "__main__":
    main()

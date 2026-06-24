#!/usr/bin/env python3
"""Configure the EMPLOYEE-ONBOARDING push pattern in EDA (idempotent, API, stdlib only).

Creates an Event Stream (servicenow-onboarding-stream) and the rulebook activation that maps it onto
the webhook source of push_employee_onboarding.yml. A ServiceNow catalog request "Arrivée
collaborateur" (set up by bootstrap/servicenow/setup_onboarding.py) POSTs to this stream, which
launches the "Provision Employee" job template -> provision_employee.yml (creates the Keycloak user
and closes the request). Reuses the decision environment, the "AAP Controller" credential, and the
shared Event Stream token credential.

Run AFTER the rulebook push_employee_onboarding.yml is pushed to Git, AFTER bootstrap/aap/eda/
configure.py, and AFTER bootstrap/aap/controller/configure.py (the "Provision Employee" job template
must exist). From the repo root:
  python3 bootstrap/aap/eda/configure_onboarding.py

It prints the Event Stream URL — feed that (with SN_EVENTSTREAM_TOKEN) to the ServiceNow side via
bootstrap/servicenow/setup_onboarding.py.
"""
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

DE_NAME = "snow-eda-de"
CONTROLLER_CRED_NAME = "AAP Controller"
ES_CRED_NAME = "ServiceNow CHG Event Stream cred"      # shared inbound SN->AAP token credential
ES_CRED_TYPE = "ServiceNow Event Stream"
STREAM_NAME = "servicenow-onboarding-stream"
PROJECT_NAME = "snow-ansible-automation"
RULEBOOK_NAME = "push_employee_onboarding.yml"
ACTIVATION_NAME = "push-employee-onboarding"


sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

load_dotenv(ROOT, required=("FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD", "SN_EVENTSTREAM_TOKEN"))

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

    cred = find("eda-credentials", ES_CRED_NAME)
    if not cred:
        es_type = find("credential-types", ES_CRED_TYPE)
        if not es_type:
            sys.exit(f"credential type '{ES_CRED_TYPE}' not found")
        cred = api("POST", "eda-credentials/", {
            "name": ES_CRED_NAME, "credential_type_id": es_type["id"], "organization_id": org,
            "inputs": {"auth_type": "token", "token": os.environ["SN_EVENTSTREAM_TOKEN"],
                       "http_header_key": "Authorization"}})
        print(f"+ Event Stream credential created (id={cred['id']})")
    else:
        print(f"= Event Stream credential exists (id={cred['id']})")

    stream = find("event-streams", STREAM_NAME)
    if not stream:
        stream = api("POST", "event-streams/", {
            "name": STREAM_NAME, "eda_credential_id": cred["id"],
            "organization_id": org, "test_mode": False})
        print(f"+ Event Stream created (id={stream['id']})")
    else:
        if stream.get("test_mode"):
            api("PATCH", f"event-streams/{stream['id']}/", {"test_mode": False})
        stream = api("GET", f"event-streams/{stream['id']}/")
        print(f"= Event Stream exists (id={stream['id']})")

    de = find("decision-environments", DE_NAME)
    controller_cred = find("eda-credentials", CONTROLLER_CRED_NAME)
    if not de or not controller_cred:
        sys.exit("run bootstrap/aap/eda/configure.py first (decision environment + AAP Controller credential)")

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
    sources = api("GET", f"rulebooks/{rb['id']}/sources/").get("results", [])
    if not sources:
        sys.exit(f"no sources found in {RULEBOOK_NAME}")
    src = sources[0]
    print(f"= rulebook {RULEBOOK_NAME} (id={rb['id']}) source '{src['name']}'")

    act = find("activations", ACTIVATION_NAME)
    if act:
        print(f"= Activation exists (id={act['id']}). To re-apply, delete it first:")
        print(f"    DELETE {BASE}/activations/{act['id']}/")
    else:
        source_mappings = (
            f"- event_stream_name: {stream['name']}\n"
            f"  event_stream_id: {stream['id']}\n"
            f"  source_name: {src['name']}\n"
            f"  rulebook_hash: {src['rulebook_hash']}\n"
        )
        act = api("POST", "activations/", {
            "name": ACTIVATION_NAME, "decision_environment_id": de["id"],
            "rulebook_id": rb["id"], "organization_id": org,
            "eda_credentials": [controller_cred["id"]],
            "source_mappings": source_mappings,
            "restart_policy": "on-failure", "log_level": "info", "is_enabled": True})
        print(f"+ Activation created (id={act['id']})")

    print("\n>> Event Stream URL (configure ServiceNow to POST here):")
    print(f"   {stream['url']}")
    print(">> Next: python3 bootstrap/servicenow/setup_onboarding.py   (creates the catalog item + Business Rule)")
    print(">> Validate end-to-end: python3 tests/e2e_employee_onboarding.py")


if __name__ == "__main__":
    main()

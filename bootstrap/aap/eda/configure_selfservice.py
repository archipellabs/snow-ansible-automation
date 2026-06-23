#!/usr/bin/env python3
"""Configure the SELF-SERVICE push pattern in EDA (idempotent, API, stdlib only).

Creates a second Event Stream (servicenow-catalog-stream) and the rulebook activation that maps it
onto the webhook source of push_selfservice_restart.yml. A ServiceNow Service Catalog request (set
up by bootstrap/servicenow/setup_selfservice.py) POSTs to this stream, which launches the "Restart
Service (Self-Service)" job template. Reuses the decision environment (snow-eda-de), the "AAP
Controller" credential, and the shared Event Stream token credential created by configure_push.py
(created here if absent, from SN_EVENTSTREAM_TOKEN).

Run AFTER the rulebook push_selfservice_restart.yml is pushed to Git, AFTER
bootstrap/aap/eda/configure.py (decision environment + AAP Controller credential), and AFTER
bootstrap/aap/controller/configure.py (the "Restart Service (Self-Service)" job template).
Run from the repo root:
  python3 bootstrap/aap/eda/configure_selfservice.py

It prints the Event Stream URL — feed that (with SN_EVENTSTREAM_TOKEN) to the ServiceNow side via
bootstrap/servicenow/setup_selfservice.py.
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
STREAM_NAME = "servicenow-catalog-stream"
PROJECT_NAME = "snow-ansible-automation"
RULEBOOK_NAME = "push_selfservice_restart.yml"
ACTIVATION_NAME = "push-selfservice-restart"


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

    # 1) Event Stream credential — reuse the shared token credential, or create it if absent.
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

    # 2) Event Stream dedicated to catalog requests (kept separate from the change stream).
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

    # 3) Sync the project, then read the rulebook source + hash.
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

    # 4) Activation mapping the catalog stream onto the webhook source.
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
    print(">> Next: python3 bootstrap/servicenow/setup_selfservice.py   (creates the catalog item + Business Rule)")
    print(">> Validate end-to-end: python3 tests/e2e_selfservice_restart.py")


if __name__ == "__main__":
    main()

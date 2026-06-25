#!/usr/bin/env python3
"""Configure the EDA *base* objects for the PoC (idempotent, API, stdlib only).

Creates the one-time pieces the declarative config (the "Configure EDA" job template running
bootstrap/5A_aap/eda/configure.yml) builds on:
  - the registry credential used to pull the custom decision environment from the private hub;
  - the decision environment `snow-eda-de` (the image built/pushed by build.sh);
  - the "AAP Controller" credential the rulebooks use to launch job templates
    -- its host MUST be `https://<FQDN>/api/controller/` (see note below);
  - the shared "ServiceNow CHG Event Stream cred" (token) the event streams authenticate with;
  - the EDA project (this Git repo).

The event streams + rulebook activations themselves are NOT created here anymore — they are declared
in bootstrap/5A_aap/eda/vars/eda.yml and applied by the "Configure EDA" job template
(infra.aap_configuration). Run this once first, then launch that job template.

Run AFTER bootstrap/5A_aap/eda/build.sh (the DE image must be in the hub). From the repo root:
  python3 bootstrap/5A_aap/eda/configure.py

Non-obvious requirement encoded here (cost real debugging time): ansible-rulebook picks the
controller API path from the credential host — a host with a path (".../api/controller/") selects
the AAP 2.5+ gateway slugs; a bare host selects the legacy "/api/v2/" slugs, which 404 behind the
gateway. (The GMT-timezone requirement for the records source lives in bootstrap/4_servicenow/1_account.py.)
"""
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

DE_NAME = "snow-eda-de"
DE_IMAGE_TAG = "snow-eda-de:latest"          # pushed to <FQDN>/<this> by build.sh
HUB_CRED_NAME = "Hub Decision Environment Container Registry"
CONTROLLER_CRED_NAME = "AAP Controller"
ES_CRED_NAME = "ServiceNow CHG Event Stream cred"
ES_CRED_TYPE = "ServiceNow Event Stream"
PROJECT_NAME = "snow-ansible-automation"


sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

load_dotenv(ROOT, required=("AAP_FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD",
                            "GIT_REPO_URL", "SN_EVENTSTREAM_TOKEN"))

FQDN = os.environ["AAP_FQDN"]
BASE = f"https://{FQDN}/api/eda/v1"
HEADERS = {"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])}
CTX = insecure_ctx()


def api(method, path, body=None):
    url = path if path.startswith("http") else f"{BASE}/{path}"
    return http_json(url, method=method, headers=HEADERS, body=body, ctx=CTX, timeout=60)


def find(endpoint, name):
    res = api("GET", f"{endpoint}/?{urllib.parse.urlencode({'name': name})}")
    return (res.get("results") or [None])[0]


def get_or_create(endpoint, name, body, label):
    obj = find(endpoint, name)
    if obj:
        print(f"= {label} exists (id={obj['id']})")
        return obj
    obj = api("POST", f"{endpoint}/", body)
    print(f"+ {label} created (id={obj['id']})")
    return obj


def cred_type_id(name):
    res = api("GET", f"credential-types/?{urllib.parse.urlencode({'name': name})}")
    hit = (res.get("results") or [None])[0]
    if not hit:
        sys.exit(f"credential type '{name}' not found")
    return hit["id"]


def main():
    org = (api("GET", "organizations/?name=Default").get("results") or [{"id": 1}])[0]["id"]

    # Registry credential to pull the custom DE from the private hub (installer usually made it).
    hub_cred = get_or_create(
        "eda-credentials", HUB_CRED_NAME,
        {"name": HUB_CRED_NAME, "credential_type_id": cred_type_id("Container Registry"),
         "organization_id": org,
         "inputs": {"host": FQDN, "username": os.environ["AAP_ADMIN_USER"],
                    "password": os.environ["AAP_ADMIN_PASSWORD"], "verify_ssl": True}},
        "Hub registry credential",
    )

    # Controller credential for run_job_template. The "/api/controller/" path is required so
    # ansible-rulebook uses the gateway API slugs (a bare host -> legacy /api/v2 -> 404).
    get_or_create(
        "eda-credentials", CONTROLLER_CRED_NAME,
        {"name": CONTROLLER_CRED_NAME, "credential_type_id": cred_type_id("Red Hat Ansible Automation Platform"),
         "organization_id": org,
         "inputs": {"host": f"https://{FQDN}/api/controller/",
                    "username": os.environ["AAP_ADMIN_USER"],
                    "password": os.environ["AAP_ADMIN_PASSWORD"],
                    "verify_ssl": False, "request_timeout": "40"}},
        "AAP Controller credential",
    )

    # Shared inbound token credential the event streams authenticate with (ServiceNow sends it in
    # the Authorization header). The streams themselves are declared in vars/eda.yml.
    get_or_create(
        "eda-credentials", ES_CRED_NAME,
        {"name": ES_CRED_NAME, "credential_type_id": cred_type_id(ES_CRED_TYPE), "organization_id": org,
         "inputs": {"auth_type": "token", "token": os.environ["SN_EVENTSTREAM_TOKEN"],
                    "http_header_key": "Authorization"}},
        "Event Stream credential",
    )

    get_or_create(
        "decision-environments", DE_NAME,
        {"name": DE_NAME, "image_url": f"{FQDN}/{DE_IMAGE_TAG}",
         "eda_credential_id": hub_cred["id"], "organization_id": org},
        "Decision environment",
    )

    proj = get_or_create(
        "projects", PROJECT_NAME,
        {"name": PROJECT_NAME, "url": os.environ["GIT_REPO_URL"], "organization_id": org},
        "EDA project",
    )
    print("   waiting for project import...")
    state = "pending"
    for _ in range(60):
        state = api("GET", f"projects/{proj['id']}/").get("import_state", "")
        if state in ("completed", "failed"):
            break
        time.sleep(3)
    print(f"   project import_state: {state}")

    print("\n>> EDA base ready. Apply the activations declaratively: launch the 'Configure EDA' job")
    print("   template (bootstrap/5A_aap/eda/configure.yml via infra.aap_configuration).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Configure Event-Driven Ansible for the PoC (idempotent, API, stdlib only).

Creates, in the EDA controller:
  - the registry credential used to pull the custom decision environment from the
    private Automation Hub;
  - the decision environment `snow-eda-de` (the image built/pushed by build.sh);
  - the "AAP Controller" credential the rulebook uses to launch the job template
    -- its host MUST be `https://<FQDN>/api/controller/` (see note below);
  - the EDA project (this Git repo) and the rulebook activation that wires it all
    together, injecting SN_HOST/SN_USERNAME/SN_PASSWORD for the records source.

Run AFTER bootstrap/aap/eda/build.sh (the DE image must be in the hub) and AFTER the
controller is configured (the "Remediate Ping Server" job template must exist).
Run from the repo root:
  python3 bootstrap/aap/eda/configure.py

Two non-obvious requirements this script encodes (both cost real debugging time):
  1. ansible-rulebook picks the controller API path from the credential host: a host
     with a path (".../api/controller/") selects the AAP 2.5+ gateway slugs; a bare
     host selects the legacy "/api/v2/" slugs, which 404 behind the gateway.
  2. The records source builds its time filter with `gs.dateGenerate`, evaluated in the
     ServiceNow *user's* timezone. The rulebook pins `remote_servicenow_timezone: UTC`,
     so eda.integration's ServiceNow timezone must be GMT (set by bootstrap/servicenow
     /setup.py) or new incidents fall outside the poll window and never trigger.
"""
import os
import sys
import ssl
import json
import time
import base64
import urllib.request
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

DE_NAME = "snow-eda-de"
DE_IMAGE_TAG = "snow-eda-de:latest"          # pushed to <FQDN>/<this> by build.sh
HUB_CRED_NAME = "Hub Decision Environment Container Registry"
CONTROLLER_CRED_NAME = "AAP Controller"
PROJECT_NAME = "snow-ansible-automation"
RULEBOOK_NAME = "snow_ping_remediation.yml"
ACTIVATION_NAME = "snow-ping-remediation"


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
REQUIRED = ("FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD",
            "SN_INSTANCE", "SN_EDA_USERNAME", "SN_EDA_PASSWORD", "GIT_REPO_URL")
_missing = [k for k in REQUIRED if not os.environ.get(k)]
if _missing:
    sys.exit("Missing in .env: " + ", ".join(_missing))

FQDN = os.environ["FQDN"]
BASE = f"https://{FQDN}/api/eda/v1"
AUTH = "Basic " + base64.b64encode(
    f"{os.environ['AAP_ADMIN_USER']}:{os.environ['AAP_ADMIN_PASSWORD']}".encode()
).decode()
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api(method, path, body=None):
    url = path if path.startswith("http") else f"{BASE}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", AUTH)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, context=CTX, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {method} {url}\n{e.read().decode()}", file=sys.stderr)
        raise


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
    registry_type = cred_type_id("Container Registry")
    controller_type = cred_type_id("Red Hat Ansible Automation Platform")

    # Registry credential to pull the custom DE from the private hub. The AAP installer
    # usually creates this already (it mirrors the default DE there); reuse if present.
    hub_cred = get_or_create(
        "eda-credentials", HUB_CRED_NAME,
        {"name": HUB_CRED_NAME, "credential_type_id": registry_type, "organization_id": org,
         "inputs": {"host": FQDN, "username": os.environ["AAP_ADMIN_USER"],
                    "password": os.environ["AAP_ADMIN_PASSWORD"], "verify_ssl": True}},
        "Hub registry credential",
    )

    # Controller credential for run_job_template. The "/api/controller/" path is required
    # so ansible-rulebook uses the gateway API slugs (a bare host -> legacy /api/v2 -> 404).
    get_or_create(
        "eda-credentials", CONTROLLER_CRED_NAME,
        {"name": CONTROLLER_CRED_NAME, "credential_type_id": controller_type, "organization_id": org,
         "inputs": {"host": f"https://{FQDN}/api/controller/",
                    "username": os.environ["AAP_ADMIN_USER"],
                    "password": os.environ["AAP_ADMIN_PASSWORD"],
                    "verify_ssl": False, "request_timeout": "40"}},
        "AAP Controller credential",
    )
    controller_cred = find("eda-credentials", CONTROLLER_CRED_NAME)

    de = get_or_create(
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

    rb = find("rulebooks", RULEBOOK_NAME)
    if not rb:
        sys.exit(f"rulebook '{RULEBOOK_NAME}' not found in the project "
                 f"(expected under extensions/eda/rulebooks/)")
    print(f"= rulebook {RULEBOOK_NAME} (id={rb['id']})")

    # SN_* are consumed by the records source in the rulebook (Jinja vars).
    extra = (f"SN_HOST: https://{os.environ['SN_INSTANCE']}\n"
             f"SN_USERNAME: {os.environ['SN_EDA_USERNAME']}\n"
             f"SN_PASSWORD: {os.environ['SN_EDA_PASSWORD']}\n")

    act = find("activations", ACTIVATION_NAME)
    if act:
        print(f"= Activation exists (id={act['id']}). To re-apply changes, delete it first:")
        print(f"    DELETE {BASE}/activations/{act['id']}/")
        return

    body = {"name": ACTIVATION_NAME, "decision_environment_id": de["id"],
            "rulebook_id": rb["id"], "organization_id": org,
            "eda_credentials": [controller_cred["id"]],
            "restart_policy": "on-failure", "log_level": "info", "is_enabled": True}
    # extra_vars: newer EDA wants an extra-vars object id; fall back to an inline string.
    try:
        ev = api("POST", "extra-vars/", {"extra_var": extra})
        body["extra_var_id"] = ev["id"]
    except urllib.error.HTTPError:
        body["extra_var"] = extra
    act = api("POST", "activations/", body)
    print(f"+ Activation created (id={act['id']})")
    print("\n>> EDA configured. Validate end-to-end: python3 tests/e2e_eda.py")


if __name__ == "__main__":
    main()

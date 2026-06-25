#!/usr/bin/env python3
"""Configure eda-server for the `awx` runtime (idempotent, API, stdlib only) — the twin of
bootstrap/5A_aap/eda/configure.py, but against the eda-server API (/api/eda/v1) and end-to-end (it
also creates the activations, which AAP did via infra.aap_configuration GitOps).

Creates: an AWX OAuth token (eda-server launches job templates on AWX via this token + its configured
automation_server_url — the in-cluster AWX service), the `snow-eda-de` decision environment (pulled
from the in-cluster registry:2, no auth), the Git project, and the four PULL activations (incident,
self-service, onboarding, monitor). Push (change) is phase 2 (F).

Key difference from AAP: AAP's rulebooks reached the controller through an "AAP Controller" eda_credential
whose host carries `/api/controller/`; eda-server uses an **AWX OAuth token** (awx_token_id) instead — no
controller-host gotcha here.

Access: eda-server's API is a NodePort (31080), not exposed externally — point EDA_HOST at an SSH tunnel:
  ssh -fNL 31080:localhost:31080 azureuser@$AWX_FQDN
then EDA_HOST defaults to localhost:31080. AWX itself is reached on :443 (Traefik) via lib.runtime.
Reads .env (AWX_FQDN, AWX_ADMIN_*, EDA_ADMIN_PASSWORD, SN_*, GIT_REPO_URL). From the repo root:
  python3 bootstrap/5B_awx/eda/configure.py
"""
import os
import sys
import time
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, basic_auth, http_json  # noqa: E402
from lib.runtime import controller  # noqa: E402

load_dotenv(ROOT, required=("AWX_FQDN", "AWX_ADMIN_PASSWORD", "EDA_ADMIN_PASSWORD",
                            "SN_INSTANCE", "SN_EDA_USERNAME", "SN_EDA_PASSWORD", "GIT_REPO_URL"))

DE_NAME = "snow-eda-de"
DE_IMAGE = "localhost:30500/snow-eda-de:latest"   # in-cluster registry:2 (containerd pulls it over HTTP)
PROJECT_NAME = "snow-ansible-automation"
EDA_HOST = os.environ.get("EDA_HOST", "localhost:31080")
EDA_BASE = f"http://{EDA_HOST}/api/eda/v1"
EDA_H = {"Authorization": basic_auth(os.environ.get("EDA_ADMIN_USER", "admin"), os.environ["EDA_ADMIN_PASSWORD"])}
# Mono-machine: the monitor's url_check reaches the host-published app ports via the k3s node gateway
# (same as the inventory's ansible_host — see docs/10 · Notes).
HEALTH_HOST = "10.42.0.1"
SN_EXTRA = (f"SN_HOST: https://{os.environ['SN_INSTANCE']}\n"
            f"SN_USERNAME: {os.environ['SN_EDA_USERNAME']}\n"
            f"SN_PASSWORD: {os.environ['SN_EDA_PASSWORD']}\n")

AWX = controller("awx")   # AWX controller client (/api/v2), reached on :443

# name, rulebook filename, activation extra_vars (YAML)
ACTIVATIONS = [
    ("pull-incident-remediation", "pull_incident_remediation.yml", SN_EXTRA),
    ("pull-selfservice-restart",  "pull_selfservice_restart.yml",  SN_EXTRA),
    ("pull-onboarding",           "pull_employee_onboarding.yml",  SN_EXTRA),
    ("monitor-health",            "monitor_health_open_incident.yml", f"health_host: {HEALTH_HOST}\n"),
]


def eda(method, path, body=None):
    url = path if path.startswith("http") else f"{EDA_BASE}/{path}"
    return http_json(url, method=method, headers=EDA_H, body=body, timeout=60)


def eda_find(endpoint, name):
    res = eda("GET", f"{endpoint}/?{urllib.parse.urlencode({'name': name})}")
    return (res.get("results") or [None])[0]


def eda_get_or_create(endpoint, name, body, label):
    obj = eda_find(endpoint, name)
    if obj:
        print(f"= {label} exists (id={obj['id']})")
        return obj
    obj = eda("POST", f"{endpoint}/", body)
    print(f"+ {label} created (id={obj['id']})")
    return obj


def awx_token_id():
    """Mint an AWX OAuth token (once) and register it in eda-server as an awx-token; return its id."""
    name = "AWX (eda-server)"
    hit = (eda("GET", f"users/me/awx-tokens/?{urllib.parse.urlencode({'name': name})}").get("results") or [None])[0]
    if hit:
        print(f"= AWX token registered in eda-server (id={hit['id']})")
        return hit["id"]
    tok = AWX.call("tokens/", {"description": "eda-server activations", "scope": "write"}, method="POST")
    reg = eda("POST", "users/me/awx-tokens/",
              {"name": name, "description": "AWX controller token for activations", "token": tok["token"]})
    print(f"+ AWX OAuth token minted + registered in eda-server (id={reg['id']})")
    return reg["id"]


def rulebook_id(fname):
    res = eda("GET", "rulebooks/?page_size=200")
    for rb in res.get("results", []):
        if rb.get("name", "").rsplit("/", 1)[-1] == fname:
            return rb["id"]
    sys.exit(f"rulebook '{fname}' not found in the project (import failed or not synced yet?)")


def main():
    tok_id = awx_token_id()

    eda_get_or_create("decision-environments", DE_NAME,
                      {"name": DE_NAME, "image_url": DE_IMAGE}, "Decision environment")

    proj = eda_get_or_create("projects", PROJECT_NAME,
                             {"name": PROJECT_NAME, "url": os.environ["GIT_REPO_URL"],
                              "scm_type": "git", "scm_branch": "main"}, "EDA project")
    # Re-sync on every run so a fresh push to the branch is imported (rulebooks + playbook paths).
    try:
        eda("POST", f"projects/{proj['id']}/sync/")
    except urllib.error.HTTPError:
        pass
    print("   syncing project (import)...")
    state = ""
    for _ in range(80):
        time.sleep(3)
        state = eda("GET", f"projects/{proj['id']}/").get("import_state", "")
        if state in ("completed", "failed"):
            break
    print(f"   project import_state: {state}")
    if state != "completed":
        sys.exit("project did not import — fix the repo/branch and re-run")

    de = eda_find("decision-environments", DE_NAME)
    for name, rb, extra in ACTIVATIONS:
        if eda_find("activations", name):
            print(f"= activation {name} exists")
            continue
        ev = eda("POST", "extra-vars/", {"extra_var": extra})
        act = eda("POST", "activations/",
                  {"name": name, "decision_environment_id": de["id"], "rulebook_id": rulebook_id(rb),
                   "extra_var_id": ev["id"], "awx_token_id": tok_id,
                   "restart_policy": "on-failure", "is_enabled": True})
        print(f"+ activation {name} created (id={act['id']})")

    print("\n>> eda-server configured. Validate: create an incident in 'Auto-Remediation' (or order a")
    print("   catalog item) and watch the activation launch the job in AWX.")


if __name__ == "__main__":
    main()

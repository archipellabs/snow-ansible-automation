#!/usr/bin/env python3
"""Configure the AAP controller for the PoC (idempotent, API, stdlib only).

Creates: the Machine credential (target SSH), the ServiceNow credential (+ its custom
credential type), the "Meridian Fleet" inventory (the 9 servers from simulator/fleet.yml),
the Git project, and the two job templates ("Restart Service" for pull, "Execute Change
Request" for push). Re-runs sync the project and reconcile the job-template playbook paths.
Validate the result — including the EE -> target path — with `python3 tests/healthcheck.py`.

Reads .env (FQDN, AAP_ADMIN_*, SN_*, GIT_REPO_URL) and the target SSH private key
(bootstrap/targets/keys/target_key). Run from the repo root:
  python3 bootstrap/aap/controller/configure.py
"""
import os
import sys
import time
import urllib.error
import urllib.parse

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

load_dotenv(ROOT, required=("FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD",
                            "SN_INSTANCE", "SN_EDA_USERNAME", "SN_EDA_PASSWORD", "GIT_REPO_URL"))

BASE = f"https://{os.environ['FQDN']}/api/controller/v2"
HEADERS = {"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])}
CTX = insecure_ctx()
KEY_PATH = os.path.join(ROOT, "bootstrap", "targets", "keys", "target_key")
FLEET = yaml.safe_load(open(os.path.join(ROOT, "simulator", "fleet.yml")))


def api(method, path, body=None):
    url = path if path.startswith("http") else f"{BASE}/{path}"
    return http_json(url, method=method, headers=HEADERS, body=body, ctx=CTX)


def first(endpoint, name):
    res = api("GET", f"{endpoint}/?{urllib.parse.urlencode({'name': name})}")
    if not res.get("count"):
        sys.exit(f"{endpoint} '{name}' not found")
    return res["results"][0]


def get_or_create(endpoint, query, body, label):
    res = api("GET", f"{endpoint}/?{urllib.parse.urlencode(query)}")
    if res.get("count"):
        obj = res["results"][0]
        print(f"= {label} exists (id={obj['id']})")
        return obj
    obj = api("POST", f"{endpoint}/", body)
    print(f"+ {label} created (id={obj['id']})")
    return obj


def delete_by_name(endpoint, name):
    res = api("GET", f"{endpoint}/?{urllib.parse.urlencode({'name': name})}")
    if res.get("count"):
        oid = res["results"][0]["id"]
        api("DELETE", f"{endpoint}/{oid}/")
        print(f"- removed {endpoint} '{name}' (id={oid})")


def main():
    # Remove the installer's demo objects (keep 'Ansible Galaxy', a system default).
    for ep, nm in (("job_templates", "Demo Job Template"), ("projects", "Demo Project"),
                   ("inventories", "Demo Inventory"), ("credentials", "Demo Credential"),
                   ("job_templates", "Remediate Ping Server"), ("inventories", "POC Targets")):
        delete_by_name(ep, nm)

    org = first("organizations", "Default")["id"]
    machine_type = first("credential_types", "Machine")["id"]
    ee = first("execution_environments", "Default execution environment")["id"]

    cred = get_or_create(
        "credentials", {"name": "Target SSH"},
        {"name": "Target SSH", "organization": org, "credential_type": machine_type,
         "inputs": {"username": "ansible", "ssh_key_data": open(KEY_PATH).read()}},
        "Credential Target SSH",
    )

    # Inventory generated from the fleet manifest (simulator/fleet.yml). The EE uses pasta
    # networking: host.containers.internal resolves to the host and reaches its rootless-published
    # SSH ports (221x). The per-host vars (service/role/app/...) make the playbooks role-aware.
    inv = get_or_create(
        "inventories", {"name": "Meridian Fleet"},
        {"name": "Meridian Fleet", "organization": org, "variables": "ansible_user: ansible"},
        "Inventory Meridian Fleet",
    )
    for s in FLEET["servers"]:
        hvars = yaml.safe_dump(
            {"ansible_host": "host.containers.internal", "ansible_port": s["ssh_port"],
             "service": s["service"], "role": s["role"], "app": s["app"],
             "business_service": s["business_service"], "support_group": s["support_group"]},
            default_flow_style=False)
        res = api("GET", f"hosts/?{urllib.parse.urlencode({'name': s['name'], 'inventory': inv['id']})}")
        if res.get("count"):
            api("PATCH", f"hosts/{res['results'][0]['id']}/", {"variables": hvars})
        else:
            api("POST", "hosts/", {"name": s["name"], "inventory": inv["id"], "enabled": True, "variables": hvars})
    print(f"= Inventory Meridian Fleet: {len(FLEET['servers'])} hosts upserted")

    # ServiceNow credential: custom type injecting SN_HOST/USERNAME/PASSWORD as env vars
    # (read by the servicenow.itsm collection in the playbook).
    sn_type = get_or_create(
        "credential_types", {"name": "ServiceNow"},
        {"name": "ServiceNow", "kind": "cloud",
         "inputs": {"fields": [
             {"id": "sn_host", "label": "Instance URL", "type": "string"},
             {"id": "sn_username", "label": "Username", "type": "string"},
             {"id": "sn_password", "label": "Password", "type": "string", "secret": True}],
             "required": ["sn_host", "sn_username", "sn_password"]},
         "injectors": {"env": {"SN_HOST": "{{ sn_host }}", "SN_USERNAME": "{{ sn_username }}",
                               "SN_PASSWORD": "{{ sn_password }}"}}},
        "Credential type ServiceNow",
    )
    sn_cred = get_or_create(
        "credentials", {"name": "ServiceNow PDI"},
        {"name": "ServiceNow PDI", "organization": org, "credential_type": sn_type["id"],
         "inputs": {"sn_host": "https://" + os.environ["SN_INSTANCE"],
                    "sn_username": os.environ["SN_EDA_USERNAME"],
                    "sn_password": os.environ["SN_EDA_PASSWORD"]}},
        "Credential ServiceNow PDI",
    )

    # Git project (public repo) — the controller pulls the playbooks from here.
    proj = get_or_create(
        "projects", {"name": "snow-ansible-automation"},
        {"name": "snow-ansible-automation", "organization": org, "scm_type": "git",
         "scm_url": os.environ["GIT_REPO_URL"], "scm_branch": "main"},
        "Project snow-ansible-automation",
    )
    # Sync on every run so the controller has the latest content (e.g. moved playbook paths).
    try:
        api("POST", f"projects/{proj['id']}/update/")
    except urllib.error.HTTPError:
        pass  # 409 if a sync is already running
    print("   waiting for project sync...")
    status = "pending"
    for _ in range(40):
        status = api("GET", f"projects/{proj['id']}/")["status"]
        if status in ("successful", "failed", "error"):
            break
        time.sleep(3)
    print(f"   project status: {status}")

    # Job templates: role-aware playbooks against the "Meridian Fleet" inventory. The two core
    # patterns are EDA-triggered; the incident-helper (P1) and db-admin (P2) templates run
    # on-demand or from an incident/change.
    #   pull  -> "Restart Service"        runs restart_service.yml    (incident remediation)
    #   push  -> "Execute Change Request" runs execute_change.yml     (change execution)
    #   pull  -> "Collect Diagnostics"    runs collect_diagnostics.yml (read-only triage)
    #   pull  -> "Free Disk"              runs free_disk.yml           (disk-space remediation)
    #   mon   -> "Open Incident"          runs open_incident.yml        (monitor raises the ticket)
    #   self  -> "Restart Service (Self-Service)" runs restart_service_selfservice.yml (catalog)
    #   ops   -> "Patch OS" / "Housekeeping" (change / scheduled maintenance)
    #   db    -> "DB Create Role" / "DB Apply Migration" / "DB Status" / "DB Backup"
    # All share the inventory/project/EE and the machine + ServiceNow credentials.
    for jt_name, pb in (("Restart Service", "playbooks/restart_service.yml"),
                        ("Execute Change Request", "playbooks/execute_change.yml"),
                        ("Collect Diagnostics", "playbooks/collect_diagnostics.yml"),
                        ("Free Disk", "playbooks/free_disk.yml"),
                        ("Open Incident", "playbooks/open_incident.yml"),
                        ("Restart Service (Self-Service)", "playbooks/restart_service_selfservice.yml"),
                        ("Patch OS", "playbooks/patch_os.yml"),
                        ("Housekeeping", "playbooks/housekeeping.yml"),
                        ("DB Create Role", "playbooks/db_create_role.yml"),
                        ("DB Apply Migration", "playbooks/db_apply_migration.yml"),
                        ("DB Status", "playbooks/db_status.yml"),
                        ("DB Backup", "playbooks/db_backup.yml")):
        jt = get_or_create(
            "job_templates", {"name": jt_name},
            {"name": jt_name, "job_type": "run", "inventory": inv["id"],
             "project": proj["id"], "playbook": pb,
             "execution_environment": ee, "ask_variables_on_launch": True},
            f"Job Template {jt_name}",
        )
        if jt.get("playbook") != pb or jt.get("inventory") != inv["id"]:  # reconcile on re-run
            api("PATCH", f"job_templates/{jt['id']}/", {"playbook": pb, "inventory": inv["id"]})
            print(f"   reconciled '{jt_name}' (playbook + inventory)")
        have = {c["id"] for c in api("GET", f"job_templates/{jt['id']}/credentials/").get("results", [])}
        for cid in (cred["id"], sn_cred["id"]):
            if cid not in have:
                api("POST", f"job_templates/{jt['id']}/credentials/", {"id": cid})
                print(f"   attached credential id={cid} to '{jt_name}'")

    print("\n>> Controller configured. Validate: python3 tests/healthcheck.py")


if __name__ == "__main__":
    main()

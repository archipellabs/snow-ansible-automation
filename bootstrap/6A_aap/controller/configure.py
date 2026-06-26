#!/usr/bin/env python3
"""Configure the AAP controller for the PoC (idempotent, API, stdlib only).

Creates: the Machine credential (target SSH), the ServiceNow credential (+ its custom
credential type), the "Meridian Fleet" inventory (the 9 servers from simulator/fleet.yml),
the Git project, and the two job templates ("Restart Service" for pull, "Execute Change
Request" for push). Re-runs sync the project and reconcile the job-template playbook paths.
Validate the result — including the EE -> target path — with `python3 tests/health.py`.

Reads .env (AAP_FQDN, AAP_ADMIN_*, SN_*, GIT_REPO_URL) and the target SSH private key
(bootstrap/2_fleet/keys/target_key). Run from the repo root:
  python3 bootstrap/6A_aap/controller/configure.py
"""
import os
import sys
import time
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402
from lib import vaultwire  # noqa: E402

load_dotenv(ROOT, required=("AAP_FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD",
                            "SN_INSTANCE", "SN_EDA_USERNAME", "GIT_REPO_URL"))
# With Vault, credentials are created WITHOUT their secrets (Vault sources them at runtime — see
# vaultwire); without Vault, the .env literals are required and used directly. SN_INSTANCE/_USERNAME
# stay required either way — non-secret topology the AAP Config credential still injects.
USE_VAULT = bool(os.environ.get("VAULT_TOKEN"))
if not USE_VAULT:
    load_dotenv(ROOT, required=("SN_EDA_PASSWORD",))

BASE = f"https://{os.environ['AAP_FQDN']}/api/controller/v2"
HEADERS = {"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])}
CTX = insecure_ctx()
KEY_PATH = os.path.join(ROOT, "bootstrap", "2_fleet", "keys", "target_key")
# Mono-machine connectivity (docs/11 · Notes): the fleet is reached at the VM's published SSH ports, and
# ansible_host is the VM address — podman injects host.containers.internal into the EE. Set once as an
# inventory variable so the shared CMDB source stays neutral (it no longer pins this).
FLEET_HOST = "host.containers.internal"
INV_VARS = f"ansible_user: ansible\nansible_host: {FLEET_HOST}"


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

    ssh_inputs = {"username": "ansible"}
    if not USE_VAULT:
        ssh_inputs["ssh_key_data"] = open(KEY_PATH).read()
    cred = get_or_create(
        "credentials", {"name": "Target SSH"},
        {"name": "Target SSH", "organization": org, "credential_type": machine_type,
         "inputs": ssh_inputs},
        "Credential Target SSH",
    )

    # "Meridian Fleet" inventory. Its hosts come from the ServiceNow CMDB via a dynamic inventory
    # source (created below, after the project + ServiceNow credential it needs) — not a static list.
    # ansible_user is an inventory-wide var; the per-host connection/role vars come from the CMDB.
    inv = get_or_create(
        "inventories", {"name": "Meridian Fleet"},
        {"name": "Meridian Fleet", "organization": org, "variables": INV_VARS},
        "Inventory Meridian Fleet",
    )
    api("PATCH", f"inventories/{inv['id']}/", {"variables": INV_VARS})  # reconcile ansible_host on re-run

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
    sn_inputs = {} if USE_VAULT else {"sn_host": "https://" + os.environ["SN_INSTANCE"],
                                      "sn_username": os.environ["SN_EDA_USERNAME"],
                                      "sn_password": os.environ["SN_EDA_PASSWORD"]}
    sn_cred = get_or_create(
        "credentials", {"name": "ServiceNow PDI"},
        {"name": "ServiceNow PDI", "organization": org, "credential_type": sn_type["id"],
         "inputs": sn_inputs},
        "Credential ServiceNow PDI",
    )

    # Keycloak Provisioner credential: injects the 'aap-provisioner' service-account client details
    # for the onboarding playbook (creates Keycloak users). kc_secret comes from Vault (or
    # KC_PROVISIONER_SECRET in .env without Vault) — the Provision Employee JT works once it is set.
    kc_type = get_or_create(
        "credential_types", {"name": "Keycloak Provisioner"},
        {"name": "Keycloak Provisioner", "kind": "cloud",
         "inputs": {"fields": [
             {"id": "kc_url", "label": "Keycloak base URL", "type": "string"},
             {"id": "kc_realm", "label": "Realm", "type": "string"},
             {"id": "kc_client", "label": "Client ID", "type": "string"},
             {"id": "kc_secret", "label": "Client secret", "type": "string", "secret": True}],
             "required": ["kc_url", "kc_realm", "kc_client", "kc_secret"]},
         "injectors": {"env": {"KC_URL": "{{ kc_url }}", "KC_REALM": "{{ kc_realm }}",
                               "KC_PROVISIONER_CLIENT": "{{ kc_client }}",
                               "KC_PROVISIONER_SECRET": "{{ kc_secret }}"}}},
        "Credential type Keycloak Provisioner",
    )
    kc_inputs = {"kc_url": f"https://{os.environ['AAP_FQDN']}:9443/auth", "kc_realm": "meridian",
                 "kc_client": "aap-provisioner"}
    if not USE_VAULT:
        kc_inputs["kc_secret"] = os.environ.get("KC_PROVISIONER_SECRET", "")
    kc_cred = get_or_create(
        "credentials", {"name": "Keycloak Provisioner"},
        {"name": "Keycloak Provisioner", "organization": org, "credential_type": kc_type["id"],
         "inputs": kc_inputs},
        "Credential Keycloak Provisioner",
    )

    # AAP Config credential: lets the "Configure EDA" job template configure AAP from Git (GitOps)
    # with infra.aap_configuration. Injects the platform connection + ServiceNow secrets as extra
    # vars the collection / the pull activation's extra_vars read — so nothing comes from a laptop.
    aapcfg_type = get_or_create(
        "credential_types", {"name": "AAP Config"},
        {"name": "AAP Config", "kind": "cloud",
         "inputs": {"fields": [
             {"id": "aap_hostname", "label": "AAP URL", "type": "string"},
             {"id": "aap_username", "label": "AAP user", "type": "string"},
             {"id": "aap_password", "label": "AAP password", "type": "string", "secret": True},
             {"id": "sn_instance", "label": "ServiceNow instance", "type": "string"},
             {"id": "sn_eda_username", "label": "ServiceNow EDA user", "type": "string"},
             {"id": "sn_eda_password", "label": "ServiceNow EDA password", "type": "string", "secret": True}],
             "required": ["aap_hostname", "aap_username", "aap_password"]},
         "injectors": {"extra_vars": {
             "aap_hostname": "{{ aap_hostname }}", "aap_username": "{{ aap_username }}",
             "aap_password": "{{ aap_password }}", "sn_instance": "{{ sn_instance }}",
             "sn_eda_username": "{{ sn_eda_username }}", "sn_eda_password": "{{ sn_eda_password }}"}}},
        "Credential type AAP Config",
    )
    aapcfg_inputs = {"aap_hostname": f"https://{os.environ['AAP_FQDN']}",
                     "aap_username": os.environ["AAP_ADMIN_USER"],
                     "aap_password": os.environ["AAP_ADMIN_PASSWORD"],
                     "sn_instance": os.environ["SN_INSTANCE"],
                     "sn_eda_username": os.environ["SN_EDA_USERNAME"]}
    if not USE_VAULT:
        aapcfg_inputs["sn_eda_password"] = os.environ["SN_EDA_PASSWORD"]
    aapcfg_cred = get_or_create(
        "credentials", {"name": "AAP Config"},
        {"name": "AAP Config", "organization": org, "credential_type": aapcfg_type["id"],
         "inputs": aapcfg_inputs},
        "Credential AAP Config",
    )

    # Source every credential's secret from Meridian's Vault at job runtime (native lookup — same on
    # AAP/AWX). The containerized controller reaches the host Vault via host.containers.internal:8200.
    # AAP's edge over AWX: even the EDA secret (AAP Config -> the Configure EDA GitOps) is a *native*
    # lookup, whereas eda-server (AWX) reads it from Vault at config time. See lib/vaultwire.py.
    if USE_VAULT:
        vaultwire.wire(api, org, os.environ.get("VAULT_LOOKUP_URL", "http://host.containers.internal:8200"), [
            (sn_cred, "sn_host", "meridian/servicenow", "host"),
            (sn_cred, "sn_username", "meridian/servicenow", "username"),
            (sn_cred, "sn_password", "meridian/servicenow", "password"),
            (kc_cred, "kc_secret", "meridian/keycloak", "secret"),
            (cred, "ssh_key_data", "meridian/ssh", "private_key"),
            (aapcfg_cred, "sn_eda_password", "meridian/servicenow", "password"),
        ])
    else:
        print("= Vault not configured (no VAULT_TOKEN) — credentials keep their literal secrets")

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
    for _ in range(120):     # the first sync installs collections/requirements.yml (incl. the big
        status = api("GET", f"projects/{proj['id']}/")["status"]    # infra.aap_configuration) -> slow
        if status in ("successful", "failed", "error"):
            break
        time.sleep(3)
    print(f"   project status: {status}")

    # Dynamic inventory source: the "Meridian Fleet" hosts come from the ServiceNow CMDB
    # (inventory/meridian.now.yml, servicenow.itsm.now). The ServiceNow credential
    # injects SN_* so the plugin can authenticate. Replaces the old static host list from fleet.yml.
    src = get_or_create(
        "inventory_sources", {"name": "ServiceNow CMDB"},
        {"name": "ServiceNow CMDB", "inventory": inv["id"], "source": "scm",
         "source_project": proj["id"], "source_path": "inventory/meridian.now.yml",
         "credential": sn_cred["id"], "overwrite": True, "overwrite_vars": True},
        "Inventory source ServiceNow CMDB",
    )
    api("PATCH", f"inventory_sources/{src['id']}/",   # reconcile path/project/credential on re-run
        {"source_project": proj["id"], "source_path": "inventory/meridian.now.yml",
         "credential": sn_cred["id"], "overwrite": True, "overwrite_vars": True})
    try:
        api("POST", f"inventory_sources/{src['id']}/update/")
    except urllib.error.HTTPError:
        pass
    print("   syncing inventory from the ServiceNow CMDB...")
    isrc = {}
    for _ in range(40):
        isrc = api("GET", f"inventory_sources/{src['id']}/")
        if isrc.get("status") in ("successful", "failed", "error"):
            break
        time.sleep(3)
    hosts = api("GET", f"inventories/{inv['id']}/").get("total_hosts")
    print(f"   inventory sync: {isrc.get('status')} — {hosts} hosts from the CMDB")

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
    #   hr    -> "Provision Employee"      runs provision_employee.yml (Keycloak onboarding)
    # All share the inventory/project/EE and the machine + ServiceNow credentials; "Provision
    # Employee" also gets the Keycloak Provisioner credential.
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
                        ("DB Backup", "playbooks/db_backup.yml"),
                        ("Provision Employee", "playbooks/provision_employee.yml")):
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
        want = [cred["id"], sn_cred["id"]]
        if jt_name == "Provision Employee":
            want.append(kc_cred["id"])     # onboarding needs the Keycloak Provisioner credential
        for cid in want:
            if cid not in have:
                api("POST", f"job_templates/{jt['id']}/credentials/", {"id": cid})
                print(f"   attached credential id={cid} to '{jt_name}'")

    # GitOps config-as-code: a job template that runs the declarative EDA config (configure.yml)
    # from this project with infra.aap_configuration (installed into the EE from
    # collections/requirements.yml). The "AAP Config" credential supplies the connection + secrets.
    try:
        eda_jt = get_or_create(
            "job_templates", {"name": "Configure EDA"},
            {"name": "Configure EDA", "job_type": "run", "inventory": inv["id"], "project": proj["id"],
             "playbook": "bootstrap/6A_aap/eda/configure.yml", "execution_environment": ee},
            "Job Template Configure EDA",
        )
        have = {c["id"] for c in api("GET", f"job_templates/{eda_jt['id']}/credentials/").get("results", [])}
        if aapcfg_cred["id"] not in have:
            api("POST", f"job_templates/{eda_jt['id']}/credentials/", {"id": aapcfg_cred["id"]})
            print("   attached AAP Config credential to 'Configure EDA'")
    except urllib.error.HTTPError:
        # Usually means the project hasn't finished syncing the new playbook yet — re-run shortly.
        print("!  could not create 'Configure EDA' (project playbook not synced yet?) — re-run this script")

    print("\n>> Controller configured. Validate: python3 tests/health.py")
    print(">> GitOps: launch the 'Configure EDA' job template to apply bootstrap/6A_aap/eda/configure.yml")


if __name__ == "__main__":
    main()

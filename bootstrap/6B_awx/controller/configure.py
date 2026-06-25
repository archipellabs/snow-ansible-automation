#!/usr/bin/env python3
"""Configure the AWX controller for the PoC (idempotent, API, stdlib only) — the `awx` runtime twin
of bootstrap/6A_aap/controller/configure.py.

AWX is the bare Automation Controller (`/api/v2`, no Platform Gateway), so this is the AAP config
minus the AAP-only GitOps bricks: there is NO "AAP Config" credential and NO "Configure EDA" job
template here — on AWX, EDA lives in a separate eda-server and is configured by bootstrap/6B_awx/eda/
(chantier E), not by the controller. Everything else is identical: the controller REST API is the
same AWX↔AAP, so the credentials, the "Meridian Fleet" inventory (hosts from the ServiceNow CMDB via
the shared servicenow.itsm.now source), the Git project, and the job templates are created the same way.

Connection details (base/auth/TLS) come from lib.runtime.controller('awx') — the same client the tests
use — so this stays in lockstep with lib/awx.py. The shared content it points at (playbooks/, the
inventory/meridian.now.yml CMDB source) lives on the repo's main branch, which the AWX project pulls.

Reads .env (AWX_FQDN, AWX_ADMIN_*, SN_*, GIT_REPO_URL) and the target SSH private key
(bootstrap/2_fleet/keys/target_key). Run from the repo root:
  python3 bootstrap/6B_awx/controller/configure.py
Validate the result with: python3 tests/health.py --runtime awx
"""
import os
import sys
import time
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, http_json  # noqa: E402
from lib.runtime import controller  # noqa: E402
from lib import vaultwire  # noqa: E402

load_dotenv(ROOT, required=("AWX_ADMIN_PASSWORD", "GIT_REPO_URL"))
# With Vault, credentials are created WITHOUT their secrets (Vault sources them at runtime — see
# vaultwire); without Vault, the .env literals are required and used directly.
USE_VAULT = bool(os.environ.get("VAULT_TOKEN"))
if not USE_VAULT:
    load_dotenv(ROOT, required=("SN_INSTANCE", "SN_EDA_USERNAME", "SN_EDA_PASSWORD"))

# The AWX client owns base/auth/TLS — keep config + tests reading the same place.
AWX = controller("awx")
BASE, HEADERS, CTX = AWX.base, AWX.h, AWX.ctx
HOST = AWX.fqdn                                   # the AWX VM also runs the simulator + Keycloak (:9443)
KEY_PATH = os.path.join(ROOT, "bootstrap", "2_fleet", "keys", "target_key")
# AWX has no "Default execution environment" (an AAP name); its stock EE carries ansible-core and the
# playbooks' collections are installed from the project's collections/requirements.yml at sync time.
EE_NAME = "AWX EE (latest)"
# Shared CMDB inventory source — runtime-neutral, lives at inventory/ on the main branch this project pulls.
INV_SOURCE_PATH = "inventory/meridian.now.yml"
# Mono-machine connectivity (docs/10 · Notes): the fleet is reached at the VM's published SSH ports, and
# ansible_host is the VM address. From a k3s job pod that's the node gateway (cni0) — reachable to the
# host-published ports without a DNS zone or a subnet. Set once as an inventory variable so the shared
# CMDB source stays neutral (it no longer pins host.containers.internal).
FLEET_HOST = "10.42.0.1"
INV_VARS = f"ansible_user: ansible\nansible_host: {FLEET_HOST}"


def api(method, path, body=None):
    url = path if path.startswith("http") else f"{BASE}/{path}"
    return http_json(url, method=method, headers=HEADERS, body=body, ctx=CTX)


def poll(path):
    """A GET for the sync loops — AWX goes briefly unresponsive while a project sync installs
    collections, so a transient network blip returns {} (treated as 'still pending') instead of
    aborting the whole run."""
    try:
        return api("GET", path)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        print(f"   (transient {type(e).__name__}, retrying)")
        return {}


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
    # Remove AWX's demo objects (keep 'Ansible Galaxy', a system default credential).
    for ep, nm in (("job_templates", "Demo Job Template"), ("projects", "Demo Project"),
                   ("inventories", "Demo Inventory"), ("credentials", "Demo Credential")):
        delete_by_name(ep, nm)

    org = first("organizations", "Default")["id"]
    machine_type = first("credential_types", "Machine")["id"]
    ee = first("execution_environments", EE_NAME)["id"]

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
    inv = get_or_create(
        "inventories", {"name": "Meridian Fleet"},
        {"name": "Meridian Fleet", "organization": org, "variables": INV_VARS},
        "Inventory Meridian Fleet",
    )
    api("PATCH", f"inventories/{inv['id']}/", {"variables": INV_VARS})  # reconcile ansible_host on re-run

    # ServiceNow credential: custom type injecting SN_HOST/USERNAME/PASSWORD as env vars
    # (read by the servicenow.itsm collection in the playbooks and the inventory plugin).
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

    # Keycloak Provisioner credential: injects the 'aap-provisioner' service-account client details for
    # the onboarding playbook (creates Keycloak users). SSO is optional; created with whatever
    # KC_PROVISIONER_SECRET is in .env — the Provision Employee JT works once it is set. The AWX VM
    # runs the simulator + Keycloak on :9443, so the URL targets this host.
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
    kc_inputs = {"kc_url": f"https://{HOST}:9443/auth", "kc_realm": "meridian", "kc_client": "aap-provisioner"}
    if not USE_VAULT:
        kc_inputs["kc_secret"] = os.environ.get("KC_PROVISIONER_SECRET", "")
    kc_cred = get_or_create(
        "credentials", {"name": "Keycloak Provisioner"},
        {"name": "Keycloak Provisioner", "organization": org, "credential_type": kc_type["id"],
         "inputs": kc_inputs},
        "Credential Keycloak Provisioner",
    )

    # Source the credentials' secrets from Meridian's Vault at job runtime (native lookup — identical on
    # AAP/AWX, AWX being the controller upstream). A k3s job pod reaches the host Vault via the node
    # gateway (:8200, the mono-machine host-port path as the fleet). See lib/vaultwire.py. The fields
    # were created empty above, so nothing literal is ever written. Skipped cleanly without Vault.
    if USE_VAULT:
        vaultwire.wire(api, org, os.environ.get("VAULT_LOOKUP_URL", "http://10.42.0.1:8200"), [
            (sn_cred, "sn_host", "meridian/servicenow", "host"),
            (sn_cred, "sn_username", "meridian/servicenow", "username"),
            (sn_cred, "sn_password", "meridian/servicenow", "password"),
            (kc_cred, "kc_secret", "meridian/keycloak", "secret"),
            (cred, "ssh_key_data", "meridian/ssh", "private_key"),
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
    for _ in range(120):     # the first sync installs collections/requirements.yml -> slow
        status = poll(f"projects/{proj['id']}/").get("status", status)
        if status in ("successful", "failed", "error"):
            break
        time.sleep(3)
    print(f"   project status: {status}")

    # Dynamic inventory source: the "Meridian Fleet" hosts come from the ServiceNow CMDB
    # (servicenow.itsm.now, the shared inventory/meridian.now.yml). The ServiceNow credential injects SN_* so
    # the plugin can authenticate.
    src = get_or_create(
        "inventory_sources", {"name": "ServiceNow CMDB"},
        {"name": "ServiceNow CMDB", "inventory": inv["id"], "source": "scm",
         "source_project": proj["id"], "source_path": INV_SOURCE_PATH,
         "credential": sn_cred["id"], "overwrite": True, "overwrite_vars": True},
        "Inventory source ServiceNow CMDB",
    )
    api("PATCH", f"inventory_sources/{src['id']}/",   # reconcile path/project/credential on re-run
        {"source_project": proj["id"], "source_path": INV_SOURCE_PATH,
         "credential": sn_cred["id"], "overwrite": True, "overwrite_vars": True})
    try:
        api("POST", f"inventory_sources/{src['id']}/update/")
    except urllib.error.HTTPError:
        pass
    print("   syncing inventory from the ServiceNow CMDB...")
    isrc = {}
    for _ in range(40):
        isrc = poll(f"inventory_sources/{src['id']}/") or isrc
        if isrc.get("status") in ("successful", "failed", "error"):
            break
        time.sleep(3)
    hosts = poll(f"inventories/{inv['id']}/").get("total_hosts")
    print(f"   inventory sync: {isrc.get('status')} — {hosts} hosts from the CMDB")

    # Job templates: role-aware playbooks against the "Meridian Fleet" inventory. Same set as AAP; the
    # EDA-triggered ones (pull/push) are activated against eda-server in chantier E.
    #   pull  -> "Restart Service"        runs restart_service.yml    (incident remediation)
    #   push  -> "Execute Change Request" runs execute_change.yml     (change execution)
    #   pull  -> "Collect Diagnostics"    runs collect_diagnostics.yml (read-only triage)
    #   pull  -> "Free Disk"              runs free_disk.yml           (disk-space remediation)
    #   mon   -> "Open Incident"          runs open_incident.yml        (monitor raises the ticket)
    #   self  -> "Restart Service (Self-Service)" runs restart_service_selfservice.yml (catalog)
    #   ops   -> "Patch OS" / "Housekeeping" (change / scheduled maintenance)
    #   db    -> "DB Create Role" / "DB Apply Migration" / "DB Status" / "DB Backup"
    #   hr    -> "Provision Employee"      runs provision_employee.yml (Keycloak onboarding)
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

    print("\n>> AWX controller configured. Validate: python3 tests/health.py --runtime awx")
    print(">> EDA (activations) is configured separately against eda-server — see bootstrap/6B_awx/eda/.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Configure the AAP controller for the PoC (idempotent, API, stdlib only).

Creates: the Machine credential (target SSH), the ServiceNow credential (+ its custom
credential type), the inventory with the two target hosts, the Git project, and the two
job templates ("Remediate Ping Server" for pull, "Execute Change Request" for push).
Re-runs sync the project and reconcile the job-template playbook paths. Validate the
result — including the EE -> target path — with `python3 tests/healthcheck.py`.

Reads .env (FQDN, AAP_ADMIN_*, SN_*, GIT_REPO_URL) and the target SSH private key
(bootstrap/targets/keys/target_key). Run from the repo root:
  python3 bootstrap/aap/controller/configure.py
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

BASE = f"https://{os.environ['FQDN']}/api/controller/v2"
AUTH = "Basic " + base64.b64encode(
    f"{os.environ['AAP_ADMIN_USER']}:{os.environ['AAP_ADMIN_PASSWORD']}".encode()
).decode()
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
KEY_PATH = os.path.join(ROOT, "bootstrap", "targets", "keys", "target_key")


def api(method, path, body=None):
    url = path if path.startswith("http") else f"{BASE}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", AUTH)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, context=CTX, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {method} {url}\n{e.read().decode()}", file=sys.stderr)
        raise


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
                   ("inventories", "Demo Inventory"), ("credentials", "Demo Credential")):
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

    inv = get_or_create(
        "inventories", {"name": "POC Targets"},
        {"name": "POC Targets", "organization": org, "variables": "ansible_user: ansible"},
        "Inventory POC Targets",
    )

    # The EE uses pasta networking: host.containers.internal resolves to the host, with
    # access to its rootless-published ports (2201/2202). Upsert the host vars on re-run.
    for name, port in (("app-node-1", 2201), ("app-node-2", 2202)):
        hvars = f"ansible_host: host.containers.internal\nansible_port: {port}"
        res = api("GET", f"hosts/?{urllib.parse.urlencode({'name': name, 'inventory': inv['id']})}")
        if res.get("count"):
            hid = res["results"][0]["id"]
            api("PATCH", f"hosts/{hid}/", {"variables": hvars})
            print(f"= Host {name} updated (id={hid})")
        else:
            obj = api("POST", "hosts/", {"name": name, "inventory": inv["id"],
                                         "enabled": True, "variables": hvars})
            print(f"+ Host {name} created (id={obj['id']})")

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

    # Job templates: one per pattern.
    #   pull  -> "Remediate Ping Server" (incident auto-remediation), launched by the EDA
    #            servicenow.itsm.records rulebook.
    #   push  -> "Execute Change Request" (change execution), launched by the EDA webhook
    #            rulebook that an Event Stream feeds from ServiceNow.
    # Both share the inventory/project/EE and the machine + ServiceNow credentials.
    for jt_name, pb in (("Remediate Ping Server", "playbooks/remediate_ping.yml"),
                        ("Execute Change Request", "playbooks/execute_change.yml")):
        jt = get_or_create(
            "job_templates", {"name": jt_name},
            {"name": jt_name, "job_type": "run", "inventory": inv["id"],
             "project": proj["id"], "playbook": pb,
             "execution_environment": ee, "ask_variables_on_launch": True},
            f"Job Template {jt_name}",
        )
        if jt.get("playbook") != pb:  # reconcile path (e.g. after the playbooks moved)
            api("PATCH", f"job_templates/{jt['id']}/", {"playbook": pb})
            print(f"   set playbook {pb} on '{jt_name}'")
        have = {c["id"] for c in api("GET", f"job_templates/{jt['id']}/credentials/").get("results", [])}
        for cid in (cred["id"], sn_cred["id"]):
            if cid not in have:
                api("POST", f"job_templates/{jt['id']}/credentials/", {"id": cid})
                print(f"   attached credential id={cid} to '{jt_name}'")

    print("\n>> Controller configured. Validate: python3 tests/healthcheck.py")


if __name__ == "__main__":
    main()

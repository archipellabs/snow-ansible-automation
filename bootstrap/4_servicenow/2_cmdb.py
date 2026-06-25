#!/usr/bin/env python3
"""Step 2 — the Meridian CMDB, loaded into ServiceNow from simulator/fleet.yml (idempotent).

Creates, from the single manifest:
  - assignment groups (the support teams);
  - people (DSI staff + business users) and the DSI staff's group memberships;
  - business services, applications, and server CIs (+ the custom u_* columns the dynamic
    inventory reads — see inventory/meridian.now.yml);
  - CI relationships (application "Runs on" server; business service "Depends on" application).

Requires PyYAML (the simulator layer is allowed dependencies). Run from the repo root:
  python3 bootstrap/4_servicenow/2_cmdb.py
"""
import os
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

load_dotenv(ROOT, required=("SN_INSTANCE", "SN_USER", "SN_PASS"))
with open(os.path.join(ROOT, "simulator", "fleet.yml")) as f:
    FLEET = yaml.safe_load(f)
SNOW = Snow()


def find(table, query):
    rec = SNOW.get_one(table, query)
    return rec["sys_id"] if rec else None


def ensure(table, key_query, body, label, display=False):
    """Get-or-create by key_query; returns the sys_id (no reconcile of existing rows — the dataset is
    additive). display=True sends display values (so reference fields can be set by name)."""
    found = SNOW.get_one(table, key_query)
    if found:
        return found["sys_id"]
    path = f"table/{table}" + ("?sysparm_input_display_value=true" if display else "")
    sid = SNOW.result(path, body)["sys_id"]
    print(f"  + {label}")
    return sid


def ensure_rel(parent, child, type_sid, label):
    if not type_sid:
        return
    if find("cmdb_rel_ci", f"parent={parent}^child={child}^type={type_sid}"):
        return
    SNOW.call("table/cmdb_rel_ci", {"parent": parent, "child": child, "type": type_sid})
    print(f"  + rel {label}")


def ensure_field(table, element, label, internal_type="string"):
    """Idempotently add a custom column (sys_dictionary) so the dynamic inventory can read it."""
    if find("sys_dictionary", f"name={table}^element={element}"):
        return
    SNOW.call("table/sys_dictionary",
              {"name": table, "element": element, "column_label": label, "internal_type": internal_type})
    print(f"  + field {table}.{element}")


def main():
    print("Groups:")
    group = {t["name"]: ensure("sys_user_group", f"name={t['name']}",
                               {"name": t["name"], "description": t["description"]}, t["name"])
             for t in FLEET["teams"]}

    print("People + memberships:")
    for p in FLEET["people"]:
        user_name = p["email"].split("@")[0]
        first, _, last = p["name"].partition(" ")
        uid = ensure("sys_user", f"user_name={user_name}",
                     {"user_name": user_name, "first_name": first, "last_name": last or first,
                      "email": p["email"], "title": p.get("title") or p.get("department", "")},
                     p["name"])
        if "team" in p:  # DSI staff -> assignment-group member (technical access)
            ensure("sys_user_grmember", f"user={uid}^group={group[p['team']]}",
                   {"user": uid, "group": group[p["team"]]}, f"{p['name']} in {p['team']}")

    print("Business services:")
    svc = {b["name"]: ensure("cmdb_ci_service", f"name={b['name']}",
                             {"name": b["name"], "short_description": f"Meridian business service ({b['criticality']})"},
                             b["name"]) for b in FLEET["business_services"]}

    print("Applications:")
    app = {n: ensure("cmdb_ci_appl", f"name={n}",
                     {"name": n, "short_description": f"Meridian application ({a['kind']})"}, n)
           for n, a in FLEET["apps"].items()}

    print("Servers:")
    # Custom columns the dynamic inventory (servicenow.itsm.now) reads as host vars. u_ssh_port is a
    # simulator artifact (real servers use SSH :22 on a real IP); u_service/u_role drive role-aware
    # playbooks (u_role also scopes the inventory to our fleet). The support group comes from the
    # standard 'support_group' reference field — no custom copy needed (the plugin reads display
    # values, so it returns the group name). See inventory/meridian.now.yml.
    ensure_field("cmdb_ci_linux_server", "u_ssh_port", "SSH port", "integer")
    ensure_field("cmdb_ci_linux_server", "u_service", "Systemd service", "string")
    ensure_field("cmdb_ci_linux_server", "u_role", "Server role", "string")
    srv = {}
    for s in FLEET["servers"]:
        sid = ensure(
            "cmdb_ci_linux_server", f"name={s['name']}",
            {"name": s["name"], "os": s["os"], "support_group": s["support_group"],
             "short_description": f"{s['role']} server for {s['business_service']} ({s['env']})"},
            s["name"], display=True)
        # Upsert the inventory attributes (also updates CIs created before these fields existed).
        SNOW.call(f"table/cmdb_ci_linux_server/{sid}",
                  {"u_ssh_port": s["ssh_port"], "u_service": s["service"], "u_role": s["role"]}, method="PATCH")
        srv[s["name"]] = sid

    print("Relationships:")
    runs_on = find("cmdb_rel_type", "name=Runs on::Runs")
    depends = find("cmdb_rel_type", "name=Depends on::Used by")
    if not runs_on or not depends:
        print("  ! standard relationship types not found — skipping relationships")
    for s in FLEET["servers"]:
        if s["app"]:  # application Runs on server
            ensure_rel(srv[s["name"]], app[s["app"]], runs_on, f"{s['app']} runs on {s['name']}")
    for b in FLEET["business_services"]:
        if b["app"]:  # business service Depends on application
            ensure_rel(app[b["app"]], svc[b["name"]], depends, f"{b['name']} depends on {b['app']}")

    print(f"\n>> Loaded: {len(group)} groups, {len(FLEET['people'])} people, {len(svc)} services, "
          f"{len(app)} apps, {len(srv)} servers.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Step 3 — the ServiceNow Service Catalog flows (idempotent, Table API, stdlib only).

Builds the two self-service catalog items under the "Automation Self-Service" category:

  - "Restart a service"      -> pick a web/app fleet server -> restart its service.
  - "Onboard a new employee" -> name/email + business service -> provision the joiner in Keycloak.

Both are PULL-driven: EDA's servicenow.itsm.records source polls sc_req_item for new (Open) requests of
these items, and the job's playbook fetches the request's catalog variables by sys_id (see the pull_*
rulebooks + playbooks/{restart_service_selfservice,provision_employee}.yml). So this step only creates the
items + their variables — no Business Rule, no event stream, no gateway-CA trust (only `change` stays push).

Run from the repo root:
  python3 bootstrap/4_servicenow/3_catalog.py
"""
import os
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CATEGORY_TITLE = "Automation Self-Service"
SINGLE_LINE = "6"          # item_option_new.type for a single-line text field
SELECT_BOX = "5"           # item_option_new.type for a Select Box

sys.path.insert(0, ROOT)
from lib.poc import load_dotenv  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

load_dotenv(ROOT, required=("SN_INSTANCE", "SN_USER", "SN_PASS"))

with open(os.path.join(ROOT, "simulator", "fleet.yml")) as f:
    FLEET = yaml.safe_load(f)


def provision_selfservice_restart(snow, cat_sid, category_sid):
    """Catalog item: pick a web/app server, restart its service. Picked up by the pull-selfservice
    activation (servicenow.itsm.records on sc_req_item)."""
    item_name = "Restart a service"
    servers = [s["name"] for s in FLEET["servers"] if s["role"] in ("web", "app")]
    item_sid = snow.ensure(
        "sc_cat_item", f"name={item_name}",
        {"name": item_name, "short_description": "Restart an application service on a Meridian server",
         "description": "Self-service restart. Pick a server; Ansible restarts its service and closes the request.",
         "sc_catalogs": cat_sid, "category": category_sid, "active": "true", "billable": "false"},
        "Catalog item (restart)",
    )
    var_sid = snow.ensure(
        "item_option_new", f"cat_item={item_sid}^name=server",
        {"cat_item": item_sid, "name": "server", "question_text": "Server to restart",
         "type": SELECT_BOX, "order": "100", "mandatory": "true"},
        "Variable 'server'",
    )
    for i, host in enumerate(servers):
        snow.ensure("question_choice", f"question={var_sid}^value={host}",
                    {"question": var_sid, "text": host, "value": host, "order": str((i + 1) * 100)},
                    f"Choice {host}")
    print(f"   restart    : item {item_sid}  servers={', '.join(servers)}")


def provision_onboarding(snow, cat_sid, category_sid):
    """Catalog item: HR requests a new joiner; Ansible creates their Keycloak identity. Picked up by
    the pull-onboarding activation (servicenow.itsm.records on sc_req_item)."""
    item_name = "Onboard a new employee"
    services = [s["name"] for s in FLEET["business_services"]]
    item_sid = snow.ensure(
        "sc_cat_item", f"name={item_name}",
        {"name": item_name, "short_description": "Onboard a new employee (create their Keycloak account)",
         "description": "HR self-service: a new joiner's identity is created in Keycloak by Ansible, then the request closes.",
         "sc_catalogs": cat_sid, "category": category_sid, "active": "true", "billable": "false"},
        "Catalog item (onboarding)",
    )
    snow.ensure("item_option_new", f"cat_item={item_sid}^name=employee_name",
                {"cat_item": item_sid, "name": "employee_name", "question_text": "Full name",
                 "type": SINGLE_LINE, "order": "100", "mandatory": "true"}, "Variable employee_name")
    snow.ensure("item_option_new", f"cat_item={item_sid}^name=employee_email",
                {"cat_item": item_sid, "name": "employee_email", "question_text": "Email",
                 "type": SINGLE_LINE, "order": "200", "mandatory": "true"}, "Variable employee_email")
    svc_var = snow.ensure("item_option_new", f"cat_item={item_sid}^name=employee_service",
                          {"cat_item": item_sid, "name": "employee_service", "question_text": "Service",
                           "type": SELECT_BOX, "order": "300", "mandatory": "true"}, "Variable employee_service")
    for i, name in enumerate(services):
        snow.ensure("question_choice", f"question={svc_var}^value={name}",
                    {"question": svc_var, "text": name, "value": name, "order": str((i + 1) * 100)},
                    f"Choice {name}")
    print(f"   onboarding : item {item_sid}  services={', '.join(services)}")


def main():
    snow = Snow()

    # Shared catalog + category for both items. "Service Catalog" exists by default on a PDI.
    catalog = snow.get_one("sc_catalog", "title=Service Catalog") or snow.get_one("sc_catalog", "active=true")
    if not catalog:
        sys.exit("no Service Catalog found on this instance")
    cat_sid = catalog["sys_id"]
    category_sid = snow.ensure(
        "sc_category", f"title={CATEGORY_TITLE}^sc_catalog={cat_sid}",
        {"title": CATEGORY_TITLE, "sc_catalog": cat_sid, "description": "Ansible-driven self-service automation (PoC)"},
        "Catalog category",
    )

    provision_selfservice_restart(snow, cat_sid, category_sid)
    provision_onboarding(snow, cat_sid, category_sid)
    print("\n>> Validate: python3 tests/scenarios/4_selfservice_restart.py  +  python3 tests/scenarios/5_employee_onboarding.py")


if __name__ == "__main__":
    main()

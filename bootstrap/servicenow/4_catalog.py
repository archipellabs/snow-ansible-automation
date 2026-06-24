#!/usr/bin/env python3
"""Step 4 — the ServiceNow Service Catalog flows (idempotent, Table API, stdlib only).

Builds the two self-service catalog items that push to AAP via EDA (both under the same
"Automation Self-Service" category, sharing the gateway-CA trust step):

  - "Redémarrer un service"  -> pick a web/app fleet server -> restart its service.
    Business Rule on sc_req_item -> servicenow-catalog-stream -> push-selfservice-restart
    activation -> "Restart Service (Self-Service)" job template.
  - "Arrivée collaborateur"  -> name/email + business service -> provision the joiner in Keycloak.
    Business Rule on sc_req_item -> servicenow-onboarding-stream -> push-employee-onboarding
    activation -> "Provision Employee" job template -> provision_employee.yml.

Each event stream URL is read live from the EDA API; the shared token is SN_EVENTSTREAM_TOKEN.
Run AFTER the 'Configure EDA' job template (the streams must exist). From the repo root:
  python3 bootstrap/servicenow/4_catalog.py

TLS note: ServiceNow validates TLS on the outbound POST. trust_gateway_ca() adds the AAP gateway's
self-signed CA to ServiceNow's trust store (best-effort, idempotent) so the flows work standalone.
"""
import os
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
CATEGORY_TITLE = "Automation Self-Service"
SINGLE_LINE = "6"          # item_option_new.type for a single-line text field
SELECT_BOX = "5"           # item_option_new.type for a Select Box

sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx  # noqa: E402
from lib.servicenow import Snow, eda_stream_url, business_rule_script, trust_gateway_ca  # noqa: E402

load_dotenv(ROOT, required=("SN_INSTANCE", "SN_USER", "SN_PASS", "SN_EVENTSTREAM_TOKEN",
                            "FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD"))

CTX = insecure_ctx()
TOKEN = os.environ["SN_EVENTSTREAM_TOKEN"]
with open(os.path.join(ROOT, "simulator", "fleet.yml")) as f:
    FLEET = yaml.safe_load(f)


def provision_selfservice_restart(snow, cat_sid, category_sid):
    """Catalog item: pick a web/app server, restart its service."""
    item_name = "Redémarrer un service"
    servers = [s["name"] for s in FLEET["servers"] if s["role"] in ("web", "app")]
    item_sid = snow.ensure(
        "sc_cat_item", f"name={item_name}",
        {"name": item_name, "short_description": "Restart an application service on a Meridian server",
         "description": "Self-service restart. Pick a server; AAP restarts its service and closes the request.",
         "sc_catalogs": cat_sid, "category": category_sid, "active": "true", "billable": "false"},
        "Catalog item (restart)",
    )
    var_sid = snow.ensure(
        "item_option_new", f"cat_item={item_sid}^name=server",
        {"cat_item": item_sid, "name": "server", "question_text": "Serveur à redémarrer",
         "type": SELECT_BOX, "order": "100", "mandatory": "true"},
        "Variable 'server'",
    )
    for i, host in enumerate(servers):
        snow.ensure("question_choice", f"question={var_sid}^value={host}",
                    {"question": var_sid, "text": host, "value": host, "order": str((i + 1) * 100)},
                    f"Choice {host}")

    endpoint = eda_stream_url("servicenow-catalog-stream", CTX)
    script = business_rule_script(
        endpoint, TOKEN,
        payload={"request_number": "current.number.toString()",
                 "request_sysid": "current.sys_id.toString()",
                 "target_host": "server",
                 "short_description": "'Self-service restart on ' + server"},
        preamble=["var server = current.variables.server ? current.variables.server.toString() : '';",
                  "if (!server) { gs.warn('EDA self-service: no server on ' + current.number); return; }"],
        log_label="EDA self-service push: RITM",
    )
    snow.ensure(
        "sys_script", "name=EDA - push self-service restart to AAP",
        {"name": "EDA - push self-service restart to AAP", "collection": "sc_req_item", "active": "true",
         "when": "after", "action_insert": "true", "action_update": "false", "advanced": "true", "order": "100",
         "condition": f"current.cat_item == '{item_sid}'",
         "description": "PoC: push a self-service restart request (chosen server) to the AAP event stream.",
         "script": script},
        "Business Rule (restart)",
    )
    print(f"   restart    : item {item_sid}  servers={', '.join(servers)}\n   endpoint={endpoint}")


def provision_onboarding(snow, cat_sid, category_sid):
    """Catalog item: HR requests a new joiner; AAP creates their Keycloak identity."""
    item_name = "Arrivée collaborateur"
    services = [s["name"] for s in FLEET["business_services"]]
    item_sid = snow.ensure(
        "sc_cat_item", f"name={item_name}",
        {"name": item_name, "short_description": "Onboard a new employee (create their Keycloak account)",
         "description": "HR self-service: a new joiner's identity is created in Keycloak by AAP, then the request closes.",
         "sc_catalogs": cat_sid, "category": category_sid, "active": "true", "billable": "false"},
        "Catalog item (onboarding)",
    )
    snow.ensure("item_option_new", f"cat_item={item_sid}^name=employee_name",
                {"cat_item": item_sid, "name": "employee_name", "question_text": "Nom complet",
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

    endpoint = eda_stream_url("servicenow-onboarding-stream", CTX)
    script = business_rule_script(
        endpoint, TOKEN,
        payload={"employee_name": "name", "employee_email": "email", "employee_service": "service",
                 "request_number": "current.number.toString()",
                 "request_sysid": "current.sys_id.toString()"},
        preamble=["var name = current.variables.employee_name ? current.variables.employee_name.toString() : '';",
                  "var email = current.variables.employee_email ? current.variables.employee_email.toString() : '';",
                  "var service = current.variables.employee_service ? current.variables.employee_service.toString() : '';",
                  "if (!email) { gs.warn('EDA onboarding: no email on ' + current.number); return; }"],
        log_label="EDA onboarding push: RITM",
    )
    snow.ensure(
        "sys_script", "name=EDA - push employee onboarding to AAP",
        {"name": "EDA - push employee onboarding to AAP", "collection": "sc_req_item", "active": "true",
         "when": "after", "action_insert": "true", "action_update": "false", "advanced": "true", "order": "100",
         "condition": f"current.cat_item == '{item_sid}'",
         "description": "PoC: push a new-employee onboarding request to the AAP event stream.",
         "script": script},
        "Business Rule (onboarding)",
    )
    print(f"   onboarding : item {item_sid}  services={', '.join(services)}\n   endpoint={endpoint}")


def main():
    snow = Snow()
    trust_gateway_ca(snow, os.environ["FQDN"])

    # Shared catalog + category for both items. "Service Catalog" exists by default on a PDI.
    catalog = snow.get_one("sc_catalog", "title=Service Catalog") or snow.get_one("sc_catalog", "active=true")
    if not catalog:
        sys.exit("no Service Catalog found on this instance")
    cat_sid = catalog["sys_id"]
    category_sid = snow.ensure(
        "sc_category", f"title={CATEGORY_TITLE}^sc_catalog={cat_sid}",
        {"title": CATEGORY_TITLE, "sc_catalog": cat_sid, "description": "AAP-driven self-service automation (PoC)"},
        "Catalog category",
    )

    provision_selfservice_restart(snow, cat_sid, category_sid)
    provision_onboarding(snow, cat_sid, category_sid)
    print("\n>> Validate: python3 tests/scenarios/4_selfservice_restart.py  +  python3 tests/scenarios/5_employee_onboarding.py")


if __name__ == "__main__":
    main()

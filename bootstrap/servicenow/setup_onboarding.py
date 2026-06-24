#!/usr/bin/env python3
"""Provision the EMPLOYEE-ONBOARDING catalog flow in ServiceNow (idempotent, Table API, stdlib only).

Builds a Service Catalog item where HR requests a new joiner; fulfilling it provisions the person's
identity in Keycloak (via AAP) and closes the request:
  - the catalog item "Arrivée collaborateur";
  - variables: full name + email (single-line text) and business service (a Select Box whose choices
    are the fleet's business services, from simulator/fleet.yml);
  - a Business Rule on sc_req_item that POSTs the request to the AAP Event Stream
    (servicenow-onboarding-stream). The push-employee-onboarding activation then runs the "Provision
    Employee" job template -> provision_employee.yml (creates the Keycloak user, closes the request).

The event stream URL is read live from the EDA API; the shared token is SN_EVENTSTREAM_TOKEN.
Run AFTER the 'Configure EDA' job template (the stream must exist). From the repo root:
  python3 bootstrap/servicenow/setup_onboarding.py
"""
import os
import sys
import urllib.parse

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BR_NAME = "EDA - push employee onboarding to AAP"
STREAM_NAME = "servicenow-onboarding-stream"
CATEGORY_TITLE = "Automation Self-Service"
ITEM_NAME = "Arrivée collaborateur"
SINGLE_LINE = "6"          # item_option_new.type for a single-line text field
SELECT_BOX = "5"           # item_option_new.type for a Select Box

sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

try:
    import setup_change  # reuse the idempotent gateway-CA trust step (sibling module)
except Exception:          # pragma: no cover
    setup_change = None

load_dotenv(ROOT, required=("SN_INSTANCE", "SN_USER", "SN_PASS", "SN_EVENTSTREAM_TOKEN",
                            "FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD"))

SN = os.environ["SN_INSTANCE"].replace("https://", "").rstrip("/")
SN_HEADERS = {"Authorization": basic_auth(os.environ["SN_USER"], os.environ["SN_PASS"]),
              "Accept": "application/json"}
CTX = insecure_ctx()
FLEET = yaml.safe_load(open(os.path.join(ROOT, "simulator", "fleet.yml")))
SERVICES = [s["name"] for s in FLEET["business_services"]]


def sn(method, path, body=None):
    return http_json(f"https://{SN}/api/now/{path}", method=method, headers=SN_HEADERS, body=body)["result"]


def get_one(table, query, fields="sys_id"):
    q = urllib.parse.urlencode({"sysparm_query": query, "sysparm_limit": "1", "sysparm_fields": fields})
    res = sn("GET", f"table/{table}?{q}")
    return res[0] if res else None


def ensure(table, key_query, body, label):
    found = get_one(table, key_query)
    if found:
        sn("PATCH", f"table/{table}/{found['sys_id']}", body)
        print(f"= {label} exists (sys_id={found['sys_id']})")
        return found["sys_id"]
    created = sn("POST", f"table/{table}", body)
    print(f"+ {label} created (sys_id={created['sys_id']})")
    return created["sys_id"]


def eda_stream_url():
    url = f"https://{os.environ['FQDN']}/api/eda/v1/event-streams/?{urllib.parse.urlencode({'name': STREAM_NAME})}"
    headers = {"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])}
    res = http_json(url, headers=headers, ctx=CTX).get("results") or []
    if not res:
        sys.exit(f"event stream '{STREAM_NAME}' not found — run the 'Configure EDA' job template first")
    return res[0]["url"]


def br_script(endpoint, token):
    return (
        "(function executeRule(current, previous) {\n"
        "    try {\n"
        "        var name = current.variables.employee_name ? current.variables.employee_name.toString() : '';\n"
        "        var email = current.variables.employee_email ? current.variables.employee_email.toString() : '';\n"
        "        var service = current.variables.employee_service ? current.variables.employee_service.toString() : '';\n"
        "        if (!email) { gs.warn('EDA onboarding: no email on ' + current.number); return; }\n"
        "        var r = new sn_ws.RESTMessageV2();\n"
        f"        r.setEndpoint('{endpoint}');\n"
        "        r.setHttpMethod('post');\n"
        "        r.setRequestHeader('Content-Type', 'application/json');\n"
        f"        r.setRequestHeader('Authorization', '{token}');\n"
        "        r.setRequestBody(JSON.stringify({\n"
        "            employee_name: name,\n"
        "            employee_email: email,\n"
        "            employee_service: service,\n"
        "            request_number: current.number.toString(),\n"
        "            request_sysid: current.sys_id.toString()\n"
        "        }));\n"
        "        var resp = r.execute();\n"
        "        gs.info('EDA onboarding push: RITM ' + current.number + ' -> HTTP ' + resp.getStatusCode());\n"
        "    } catch (e) {\n"
        "        gs.error('EDA onboarding Business Rule error: ' + e);\n"
        "    }\n"
        "})(current, previous);\n"
    )


def main():
    if setup_change:
        try:
            setup_change.trust_gateway_ca()
        except Exception as e:
            print(f"! gateway-CA trust step skipped ({e}); ensure the CA is in ServiceNow's trust store")

    endpoint = eda_stream_url()
    token = os.environ["SN_EVENTSTREAM_TOKEN"]

    catalog = get_one("sc_catalog", "title=Service Catalog") or get_one("sc_catalog", "active=true")
    if not catalog:
        sys.exit("no Service Catalog found on this instance")
    cat_sid = catalog["sys_id"]
    category_sid = ensure(
        "sc_category", f"title={CATEGORY_TITLE}^sc_catalog={cat_sid}",
        {"title": CATEGORY_TITLE, "sc_catalog": cat_sid, "description": "AAP-driven self-service (PoC)"},
        "Catalog category",
    )

    item_sid = ensure(
        "sc_cat_item", f"name={ITEM_NAME}",
        {"name": ITEM_NAME, "short_description": "Onboard a new employee (create their Keycloak account)",
         "description": "HR self-service: a new joiner's identity is created in Keycloak by AAP, then the request closes.",
         "sc_catalogs": cat_sid, "category": category_sid, "active": "true", "billable": "false"},
        "Catalog item",
    )

    ensure("item_option_new", f"cat_item={item_sid}^name=employee_name",
           {"cat_item": item_sid, "name": "employee_name", "question_text": "Nom complet",
            "type": SINGLE_LINE, "order": "100", "mandatory": "true"}, "Variable employee_name")
    ensure("item_option_new", f"cat_item={item_sid}^name=employee_email",
           {"cat_item": item_sid, "name": "employee_email", "question_text": "Email",
            "type": SINGLE_LINE, "order": "200", "mandatory": "true"}, "Variable employee_email")
    svc_var = ensure("item_option_new", f"cat_item={item_sid}^name=employee_service",
                     {"cat_item": item_sid, "name": "employee_service", "question_text": "Service",
                      "type": SELECT_BOX, "order": "300", "mandatory": "true"}, "Variable employee_service")
    for i, name in enumerate(SERVICES):
        ensure("question_choice", f"question={svc_var}^value={name}",
               {"question": svc_var, "text": name, "value": name, "order": str((i + 1) * 100)},
               f"Choice {name}")

    fields = {
        "name": BR_NAME, "collection": "sc_req_item", "active": "true", "when": "after",
        "action_insert": "true", "action_update": "false", "advanced": "true", "order": "100",
        "condition": f"current.cat_item == '{item_sid}'",
        "description": "PoC: push a new-employee onboarding request to the AAP event stream.",
        "script": br_script(endpoint, token),
    }
    ensure("sys_script", f"name={BR_NAME}", fields, "Business Rule")

    print(f"\n   catalog item sys_id = {item_sid}")
    print(f"   services = {', '.join(SERVICES)}")
    print(f"   endpoint = {endpoint}")
    print("\n>> Validate end-to-end: python3 tests/e2e_employee_onboarding.py")


if __name__ == "__main__":
    main()

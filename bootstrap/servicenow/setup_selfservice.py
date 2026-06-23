#!/usr/bin/env python3
"""Provision the SELF-SERVICE catalog flow in ServiceNow (idempotent, Table API, stdlib only).

Builds a Service Catalog item that lets a user pick a fleet server and request a service restart:
  - a catalog category ("Automation Self-Service") under the Service Catalog;
  - the catalog item "Redémarrer un service";
  - a Select Box variable `server` whose choices are the fleet's web/app servers (from
    simulator/fleet.yml);
  - a Business Rule on sc_req_item that, when one of these items is requested, POSTs the chosen
    server to the AAP Event Stream (servicenow-catalog-stream). The push-selfservice-restart
    activation then launches the "Restart Service (Self-Service)" job template.

The event stream URL is read live from the EDA API; the shared token is SN_EVENTSTREAM_TOKEN.
Run AFTER bootstrap/aap/eda/configure_selfservice.py (the stream must exist). From the repo root:
  python3 bootstrap/servicenow/setup_selfservice.py

TLS note: ServiceNow validates TLS on the outbound POST. The AAP gateway's self-signed CA must be
in ServiceNow's trust store — bootstrap/servicenow/setup_change.py already uploads it; this script
reuses that step (best-effort) so the self-service flow also works standalone.
"""
import os
import sys
import urllib.parse

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BR_NAME = "EDA - push self-service restart to AAP"
STREAM_NAME = "servicenow-catalog-stream"
CATEGORY_TITLE = "Automation Self-Service"
ITEM_NAME = "Redémarrer un service"
VAR_NAME = "server"
SELECT_BOX = "5"            # item_option_new.type for a Select Box

sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

# Reuse setup_change's idempotent gateway-CA trust step (sibling module, same directory).
try:
    import setup_change  # noqa: E402
except Exception:          # pragma: no cover - only if env/import is unavailable
    setup_change = None

load_dotenv(ROOT, required=("SN_INSTANCE", "SN_USER", "SN_PASS", "SN_EVENTSTREAM_TOKEN",
                            "FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD"))

SN = os.environ["SN_INSTANCE"].replace("https://", "").rstrip("/")
SN_HEADERS = {"Authorization": basic_auth(os.environ["SN_USER"], os.environ["SN_PASS"]),
              "Accept": "application/json"}
CTX = insecure_ctx()
FLEET = yaml.safe_load(open(os.path.join(ROOT, "simulator", "fleet.yml")))
CHOICE_SERVERS = [s["name"] for s in FLEET["servers"] if s["role"] in ("web", "app")]


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
        sys.exit(f"event stream '{STREAM_NAME}' not found — run bootstrap/aap/eda/configure_selfservice.py first")
    return res[0]["url"]


def br_script(endpoint, token):
    # Business Rule (ServiceNow JS) on sc_req_item: read the chosen server from the request item's
    # variables and POST it to the event stream. The condition (set in main) limits it to our item.
    return (
        "(function executeRule(current, previous) {\n"
        "    try {\n"
        "        var server = current.variables.server ? current.variables.server.toString() : '';\n"
        "        if (!server) { gs.warn('EDA self-service: no server on ' + current.number); return; }\n"
        "        var r = new sn_ws.RESTMessageV2();\n"
        f"        r.setEndpoint('{endpoint}');\n"
        "        r.setHttpMethod('post');\n"
        "        r.setRequestHeader('Content-Type', 'application/json');\n"
        f"        r.setRequestHeader('Authorization', '{token}');\n"
        "        r.setRequestBody(JSON.stringify({\n"
        "            request_number: current.number.toString(),\n"
        "            request_sysid: current.sys_id.toString(),\n"
        "            target_host: server,\n"
        "            short_description: 'Self-service restart on ' + server\n"
        "        }));\n"
        "        var resp = r.execute();\n"
        "        gs.info('EDA self-service push: RITM ' + current.number + ' -> HTTP ' + resp.getStatusCode());\n"
        "    } catch (e) {\n"
        "        gs.error('EDA self-service Business Rule error: ' + e);\n"
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

    # 1) Catalog + category. The "Service Catalog" exists by default on a PDI; fall back to any.
    catalog = get_one("sc_catalog", "title=Service Catalog") or get_one("sc_catalog", "active=true")
    if not catalog:
        sys.exit("no Service Catalog found on this instance")
    cat_sid = catalog["sys_id"]
    category_sid = ensure(
        "sc_category", f"title={CATEGORY_TITLE}^sc_catalog={cat_sid}",
        {"title": CATEGORY_TITLE, "sc_catalog": cat_sid,
         "description": "AAP-driven self-service automation (PoC)"},
        "Catalog category",
    )

    # 2) Catalog item.
    item_sid = ensure(
        "sc_cat_item", f"name={ITEM_NAME}",
        {"name": ITEM_NAME, "short_description": "Restart an application service on a Meridian server",
         "description": "Self-service restart. Pick a server; AAP restarts its service and closes the request.",
         "sc_catalogs": cat_sid, "category": category_sid, "active": "true", "billable": "false"},
        "Catalog item",
    )

    # 3) Select Box variable 'server'.
    var_sid = ensure(
        "item_option_new", f"cat_item={item_sid}^name={VAR_NAME}",
        {"cat_item": item_sid, "name": VAR_NAME, "question_text": "Serveur à redémarrer",
         "type": SELECT_BOX, "order": "100", "mandatory": "true"},
        "Catalog variable 'server'",
    )

    # 4) One choice per web/app fleet server.
    for i, host in enumerate(CHOICE_SERVERS):
        ensure(
            "question_choice", f"question={var_sid}^value={host}",
            {"question": var_sid, "text": host, "value": host, "order": str((i + 1) * 100)},
            f"Choice {host}",
        )

    # 5) Business Rule that pushes the request to the event stream (scoped to our item).
    fields = {
        "name": BR_NAME, "collection": "sc_req_item", "active": "true", "when": "after",
        "action_insert": "true", "action_update": "false", "advanced": "true", "order": "100",
        "condition": f"current.cat_item == '{item_sid}'",
        "description": "PoC: push a self-service restart request (chosen server) to the AAP event stream.",
        "script": br_script(endpoint, token),
    }
    ensure("sys_script", f"name={BR_NAME}", fields, "Business Rule")

    print(f"\n   catalog item sys_id = {item_sid}")
    print(f"   choices = {', '.join(CHOICE_SERVERS)}")
    print(f"   endpoint = {endpoint}")
    print("\n>> Validate end-to-end: python3 tests/e2e_selfservice_restart.py")


if __name__ == "__main__":
    main()

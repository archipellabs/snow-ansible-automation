#!/usr/bin/env python3
"""Provision the PUSH-pattern ServiceNow objects (idempotent, Table API, stdlib only).

Creates a Business Rule on the change_request table that POSTs to the AAP Event Stream when
a change is approved (and has a CI target), sending the change number/sys_id/target to EDA.
The event stream URL is read live from the EDA API (so nothing host-specific is hard-coded);
the shared token comes from SN_EVENTSTREAM_TOKEN in .env.

Trigger note: ServiceNow's change state model rejects arbitrary state jumps via the Table
API, so we key off the freely-writable `approval` field (approved) rather than a state, and
the playbook records its result as a work note rather than transitioning the change.

Run AFTER ansible/eda/configure_push.py (the event stream must exist). From the repo root:
  python3 bootstrap/servicenow/setup_change.py

Note: ServiceNow validates TLS on outbound REST. If the AAP gateway uses a self-signed
certificate, either install a trusted certificate on the gateway or add it to ServiceNow's
certificate trust store, otherwise the Business Rule's POST fails with an SSL error.
"""
import os
import sys
import ssl
import json
import base64
import urllib.request
import urllib.error
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BR_NAME = "EDA - push approved change to AAP"
STREAM_NAME = "servicenow-chg-stream"


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
REQUIRED = ("SN_INSTANCE", "SN_USER", "SN_PASS", "SN_EVENTSTREAM_TOKEN",
            "FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD")
_missing = [k for k in REQUIRED if not os.environ.get(k)]
if _missing:
    sys.exit("Missing in .env: " + ", ".join(_missing))

SN = os.environ["SN_INSTANCE"].replace("https://", "").rstrip("/")
SN_AUTH = "Basic " + base64.b64encode(
    f"{os.environ['SN_USER']}:{os.environ['SN_PASS']}".encode()).decode()
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def sn(method, path, body=None):
    url = f"https://{SN}/api/now/{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", SN_AUTH)
    r.add_header("Accept", "application/json")
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.load(resp)["result"]


def eda_stream_url():
    url = f"https://{os.environ['FQDN']}/api/eda/v1/event-streams/?{urllib.parse.urlencode({'name': STREAM_NAME})}"
    auth = "Basic " + base64.b64encode(
        f"{os.environ['AAP_ADMIN_USER']}:{os.environ['AAP_ADMIN_PASSWORD']}".encode()).decode()
    r = urllib.request.Request(url)
    r.add_header("Authorization", auth)
    with urllib.request.urlopen(r, context=CTX, timeout=30) as resp:
        res = json.load(resp).get("results") or []
    if not res:
        sys.exit(f"event stream '{STREAM_NAME}' not found — run ansible/eda/configure_push.py first")
    return res[0]["url"]


def br_script(endpoint, token):
    # Server-side Business Rule body (ServiceNow JS). Posts the change to the event stream.
    return (
        "(function executeRule(current, previous) {\n"
        "    try {\n"
        "        var ci = current.cmdb_ci.nil() ? '' : current.cmdb_ci.getDisplayValue();\n"
        "        var r = new sn_ws.RESTMessageV2();\n"
        f"        r.setEndpoint('{endpoint}');\n"
        "        r.setHttpMethod('post');\n"
        "        r.setRequestHeader('Content-Type', 'application/json');\n"
        f"        r.setRequestHeader('Authorization', '{token}');\n"
        "        r.setRequestBody(JSON.stringify({\n"
        "            change_number: current.number.toString(),\n"
        "            change_sysid: current.sys_id.toString(),\n"
        "            target_host: ci,\n"
        "            short_description: current.short_description.toString()\n"
        "        }));\n"
        "        var resp = r.execute();\n"
        "        gs.info('EDA push: change ' + current.number + ' -> HTTP ' + resp.getStatusCode() + ' ' + resp.getErrorMessage());\n"
        "    } catch (e) {\n"
        "        gs.error('EDA push Business Rule error: ' + e);\n"
        "    }\n"
        "})(current, previous);\n"
    )


def main():
    endpoint = eda_stream_url()
    token = os.environ["SN_EVENTSTREAM_TOKEN"]
    fields = {
        "name": BR_NAME,
        "collection": "change_request",
        "active": "true",
        "when": "after",
        "action_insert": "false",
        "action_update": "true",
        "advanced": "true",
        "order": "100",
        "condition": "current.approval.changesTo('approved') && !current.cmdb_ci.nil()",
        "description": "PoC: push an approved change request (with a CI target) to the AAP event stream.",
        "script": br_script(endpoint, token),
    }
    q = urllib.parse.urlencode({"sysparm_query": f"name={BR_NAME}", "sysparm_limit": "1",
                                "sysparm_fields": "sys_id"})
    found = sn("GET", f"table/sys_script?{q}")
    if found:
        sid = found[0]["sys_id"]
        sn("PATCH", f"table/sys_script/{sid}", fields)
        print(f"= Business Rule updated (sys_id={sid})")
    else:
        created = sn("POST", "table/sys_script", fields)
        print(f"+ Business Rule created (sys_id={created['sys_id']})")
    print(f"   table=change_request  when=after  condition=approval changesTo approved")
    print(f"   endpoint={endpoint}")
    print("\n>> Validate end-to-end: python3 tests/e2e_change.py")


if __name__ == "__main__":
    main()

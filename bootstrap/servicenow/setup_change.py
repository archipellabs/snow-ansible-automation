#!/usr/bin/env python3
"""Provision the PUSH-pattern ServiceNow objects (idempotent, Table API, stdlib only).

Creates a Business Rule on the change_request table that POSTs to the AAP Event Stream when
a change is approved (and has a CI target), sending the change number/sys_id/target to EDA.
The event stream URL is read live from the EDA API (so nothing host-specific is hard-coded);
the shared token comes from SN_EVENTSTREAM_TOKEN in .env.

Trigger note: ServiceNow's change state model rejects arbitrary state jumps via the Table
API, so we key off the freely-writable `approval` field (approved) rather than a state, and
the playbook records its result as a work note rather than transitioning the change.

Run AFTER the 'Configure EDA' job template (the event stream must exist). From the repo root:
  python3 bootstrap/servicenow/setup_change.py

Note: ServiceNow validates TLS on outbound REST. If the AAP gateway uses a self-signed
certificate, either install a trusted certificate on the gateway or add it to ServiceNow's
certificate trust store, otherwise the Business Rule's POST fails with an SSL error.
"""
import os
import sys
import subprocess
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BR_NAME = "EDA - push approved change to AAP"
STREAM_NAME = "servicenow-chg-stream"
CA_NAME = "AAP gateway CA - PoC"
GATEWAY_CA_PATH = "~/aap/tls/ca.cert"            # AAP installer's self-signed CA, on the VM
SSH_KEY = os.path.expanduser(os.environ.get("SSH_KEY", "~/.ssh/snow-aap-poc"))


sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

load_dotenv(ROOT, required=("SN_INSTANCE", "SN_USER", "SN_PASS", "SN_EVENTSTREAM_TOKEN",
                            "FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD"))

SN = os.environ["SN_INSTANCE"].replace("https://", "").rstrip("/")
SN_HEADERS = {"Authorization": basic_auth(os.environ["SN_USER"], os.environ["SN_PASS"]),
              "Accept": "application/json"}
CTX = insecure_ctx()  # for the AAP gateway (self-signed); ServiceNow itself has a valid cert


def sn(method, path, body=None):
    return http_json(f"https://{SN}/api/now/{path}", method=method, headers=SN_HEADERS, body=body)["result"]


def eda_stream_url():
    url = f"https://{os.environ['FQDN']}/api/eda/v1/event-streams/?{urllib.parse.urlencode({'name': STREAM_NAME})}"
    headers = {"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])}
    res = http_json(url, headers=headers, ctx=CTX).get("results") or []
    if not res:
        sys.exit(f"event stream '{STREAM_NAME}' not found — run the 'Configure EDA' job template first")
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


def trust_gateway_ca():
    """Add the AAP gateway's CA to ServiceNow's trust store so its outbound TLS to the event
    stream is verified. The gateway ships a self-signed CA (issuer 'Ansible Automation
    Platform') that ServiceNow does not trust by default, which makes the Business Rule's POST
    fail with 'HTTP 0'. We fetch that CA from the VM over SSH and store it as a trust_store_cert.
    Best-effort: if SSH is unavailable, upload GATEWAY_CA_PATH manually and re-run."""
    try:
        ca = subprocess.run(
            ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15",
             f"azureuser@{os.environ['FQDN']}", f"cat {GATEWAY_CA_PATH}"],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        ca = ""
    if "BEGIN CERTIFICATE" not in ca:
        print(f"! could not fetch the gateway CA over SSH ({GATEWAY_CA_PATH}); upload it to the\n"
              "  ServiceNow trust store manually (sys_certificate, type trust_store). Skipping.")
        return
    # ServiceNow parses the PEM on save: it overrides short_description with the cert's subject
    # CN and stores type as 'trust_store'. So dedup on that CN, not on a name we choose.
    cn = None
    try:
        out = subprocess.run(["openssl", "x509", "-noout", "-subject"],
                             input=ca, capture_output=True, text=True, timeout=10).stdout
        for part in out.split("CN=")[-1:]:
            cn = part.strip().splitlines()[0].strip()
    except Exception:
        pass
    if cn:
        q = urllib.parse.urlencode({"sysparm_query": f"short_description={cn}^type=trust_store",
                                    "sysparm_limit": "1", "sysparm_fields": "sys_id"})
        if sn("GET", f"table/sys_certificate?{q}"):
            print(f"= gateway CA '{cn}' already in ServiceNow trust store")
            return
    sn("POST", "table/sys_certificate",
       {"short_description": CA_NAME, "format": "pem", "type": "trust_store",
        "pem_certificate": ca, "active": "true"})
    print(f"+ gateway CA uploaded to ServiceNow trust store (CN={cn})")


def main():
    trust_gateway_ca()
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
    print("\n>> Validate end-to-end: python3 tests/e2e_push_change_execution.py")


if __name__ == "__main__":
    main()

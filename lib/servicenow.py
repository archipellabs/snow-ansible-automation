"""ServiceNow layer for the PoC (stdlib only) — the Table/Service-Catalog client and the
Business-Rule machinery shared by bootstrap/4_servicenow/ and tests/.

Sits on top of lib.poc (transport). The single `Snow` client replaces the per-script ServiceNow
wrappers the bootstrap and test code used to each redefine.
"""
import os
import sys
import json
import subprocess
import urllib.parse

from lib.poc import basic_auth, http_json, ssh


class Snow:
    """ServiceNow Table + Service Catalog client. `creds` selects the account: 'admin' (SN_USER,
    the default — catalog ordering, change approval, dataset writes) or 'eda' (the eda.integration
    user, whose incidents the EDA filter sees). `call` returns the full JSON response; `result`
    unwraps `["result"]`. `path` is relative to /api/now/, e.g. "table/incident?...".
    """

    def __init__(self, creds="admin"):
        instance = os.environ["SN_INSTANCE"].replace("https://", "").rstrip("/")
        self.base = f"https://{instance}/api/now"
        self.sc_base = f"https://{instance}/api/sn_sc"
        user, pw = ("SN_EDA_USERNAME", "SN_EDA_PASSWORD") if creds == "eda" else ("SN_USER", "SN_PASS")
        self.h = {"Authorization": basic_auth(os.environ[user], os.environ[pw]), "Accept": "application/json"}

    def call(self, path, body=None, method=None):
        return http_json(f"{self.base}/{path}", method=method or ("POST" if body is not None else "GET"),
                         headers=self.h, body=body)

    def result(self, path, body=None, method=None):
        return self.call(path, body, method)["result"]

    def get_one(self, table, query, fields="sys_id"):
        q = urllib.parse.urlencode({"sysparm_query": query, "sysparm_limit": "1", "sysparm_fields": fields})
        res = self.result(f"table/{table}?{q}")
        return res[0] if res else None

    def ensure(self, table, key_query, body, label, update=True, display=False):
        """Get-or-create by key_query; returns the sys_id. update=True PATCHes an existing record to
        the desired body (reconcile); display=True sends display values on create (so reference
        fields can be set by name)."""
        found = self.get_one(table, key_query)
        if found:
            if update:
                self.call(f"table/{table}/{found['sys_id']}", body, method="PATCH")
            print(f"= {label} exists (sys_id={found['sys_id']})")
            return found["sys_id"]
        path = f"table/{table}" + ("?sysparm_input_display_value=true" if display else "")
        sid = self.result(path, body)["sys_id"]
        print(f"+ {label} created (sys_id={sid})")
        return sid

    def order_now(self, item_sid, variables):
        """Order a Service Catalog item (sn_sc order_now); returns the request record."""
        return http_json(f"{self.sc_base}/servicecatalog/items/{item_sid}/order_now",
                         method="POST", headers=self.h,
                         body={"sysparm_quantity": "1", "variables": variables})["result"]


def eda_stream_url(stream_name, ctx):
    """The inbound URL of an EDA event stream, read live from the EDA API (nothing host-specific is
    hard-coded). Needs FQDN + AAP_ADMIN_USER/PASSWORD; ctx skips the gateway's self-signed cert."""
    url = (f"https://{os.environ['FQDN']}/api/eda/v1/event-streams/?"
           + urllib.parse.urlencode({"name": stream_name}))
    headers = {"Authorization": basic_auth(os.environ["AAP_ADMIN_USER"], os.environ["AAP_ADMIN_PASSWORD"])}
    res = http_json(url, headers=headers, ctx=ctx).get("results") or []
    if not res:
        sys.exit(f"event stream '{stream_name}' not found — run the 'Configure EDA' job template first")
    return res[0]["url"]


def business_rule_script(endpoint, token, payload, preamble=(), log_label="EDA push"):
    """Build a ServiceNow Business Rule body (server-side JS) that POSTs `payload` to the event
    stream. `payload` maps JSON keys to JS expressions; `preamble` is a list of JS statements run
    first (extract variables / early-return). endpoint and token are JSON-encoded so quotes in them
    can't break out of the JS string."""
    lines = ["(function executeRule(current, previous) {", "    try {"]
    lines += [f"        {s}" for s in preamble]
    lines += [
        "        var r = new sn_ws.RESTMessageV2();",
        f"        r.setEndpoint({json.dumps(endpoint)});",
        "        r.setHttpMethod('post');",
        "        r.setRequestHeader('Content-Type', 'application/json');",
        f"        r.setRequestHeader('Authorization', {json.dumps(token)});",
        "        r.setRequestBody(JSON.stringify({",
        ",\n".join(f"            {k}: {v}" for k, v in payload.items()),
        "        }));",
        "        var resp = r.execute();",
        f"        gs.info({json.dumps(log_label + ' ')} + current.number + ' -> HTTP ' + resp.getStatusCode());",
        "    } catch (e) {",
        f"        gs.error({json.dumps(log_label + ' error: ')} + e);",
        "    }",
        "})(current, previous);",
    ]
    return "\n".join(lines) + "\n"


def trust_gateway_ca(snow, fqdn, ca_path="~/aap/tls/ca.cert", ca_name="AAP gateway CA - PoC"):
    """Add the AAP gateway's self-signed CA to ServiceNow's trust store so its outbound TLS to the
    event stream is verified (otherwise the Business Rule POST fails with 'HTTP 0'). Fetches the CA
    from the VM over SSH; best-effort — if SSH is unavailable, upload it to sys_certificate manually.
    ServiceNow overrides short_description with the cert's subject CN on save, so we dedup on that CN."""
    try:
        ca = ssh(f"cat {ca_path}", fqdn=fqdn, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"! SSH to fetch the gateway CA failed ({e}); skipping")
        ca = ""
    if "BEGIN CERTIFICATE" not in ca:
        print(f"! could not fetch the gateway CA over SSH ({ca_path}); upload it to the\n"
              "  ServiceNow trust store manually (sys_certificate, type trust_store). Skipping.")
        return
    cn = None
    try:
        out = subprocess.run(["openssl", "x509", "-noout", "-subject"],
                             input=ca, capture_output=True, text=True, timeout=10).stdout
        cn = out.split("CN=")[-1].strip().splitlines()[0].strip() if "CN=" in out else None
    except (OSError, subprocess.SubprocessError) as e:
        print(f"! openssl could not parse the CA subject ({e}); deduping by name instead")
    if cn and snow.get_one("sys_certificate", f"short_description={cn}^type=trust_store"):
        print(f"= gateway CA '{cn}' already in ServiceNow trust store")
        return
    snow.call("table/sys_certificate",
              {"short_description": ca_name, "format": "pem", "type": "trust_store",
               "pem_certificate": ca, "active": "true"})
    print(f"+ gateway CA uploaded to ServiceNow trust store (CN={cn})")

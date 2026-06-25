#!/usr/bin/env python3
"""Step 4 — the PUSH-pattern Business Rule (change_request, idempotent, Table API, stdlib only).

Creates a Business Rule on the change_request table that POSTs to the AAP Event Stream when
a change is approved (and has a CI target), sending the change number/sys_id/target to EDA.
The event stream URL is read live from the EDA API (so nothing host-specific is hard-coded);
the shared token comes from SN_EVENTSTREAM_TOKEN in .env.

Trigger note: ServiceNow's change state model rejects arbitrary state jumps via the Table
API, so we key off the freely-writable `approval` field (approved) rather than a state, and
the playbook records its result as a work note rather than transitioning the change.

Run AFTER the 'Configure EDA' job template (the event stream must exist). From the repo root:
  python3 bootstrap/5_servicenow/4_push_change_aap.py

Note: ServiceNow validates TLS on outbound REST. If the AAP gateway uses a self-signed
certificate, either install a trusted certificate on the gateway or add it to ServiceNow's
certificate trust store, otherwise the Business Rule's POST fails with an SSL error
(trust_gateway_ca() below does this automatically from the VM).
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BR_NAME = "EDA - push approved change to AAP"
STREAM_NAME = "servicenow-chg-stream"

sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx  # noqa: E402
from lib.servicenow import Snow, eda_stream_url, business_rule_script, trust_gateway_ca  # noqa: E402

load_dotenv(ROOT, required=("SN_INSTANCE", "SN_USER", "SN_PASS", "SN_EVENTSTREAM_TOKEN",
                            "AAP_FQDN", "AAP_ADMIN_USER", "AAP_ADMIN_PASSWORD"))

CTX = insecure_ctx()  # for the AAP gateway (self-signed); ServiceNow itself has a valid cert


def main():
    snow = Snow()
    trust_gateway_ca(snow, os.environ["AAP_FQDN"])
    endpoint = eda_stream_url(STREAM_NAME, CTX)
    token = os.environ["SN_EVENTSTREAM_TOKEN"]
    script = business_rule_script(
        endpoint, token,
        payload={"change_number": "current.number.toString()",
                 "change_sysid": "current.sys_id.toString()",
                 "target_host": "ci",
                 "short_description": "current.short_description.toString()"},
        preamble=["var ci = current.cmdb_ci.nil() ? '' : current.cmdb_ci.getDisplayValue();"],
        log_label="EDA push: change",
    )
    snow.ensure(
        "sys_script", f"name={BR_NAME}",
        {"name": BR_NAME, "collection": "change_request", "active": "true", "when": "after",
         "action_insert": "false", "action_update": "true", "advanced": "true", "order": "100",
         "condition": "current.approval.changesTo('approved') && !current.cmdb_ci.nil()",
         "description": "PoC: push an approved change request (with a CI target) to the AAP event stream.",
         "script": script},
        "Business Rule",
    )
    print(f"   table=change_request  when=after  condition=approval changesTo approved")
    print(f"   endpoint={endpoint}")
    print("\n>> Validate end-to-end: python3 tests/scenarios/2_push_change_execution.py")


if __name__ == "__main__":
    main()

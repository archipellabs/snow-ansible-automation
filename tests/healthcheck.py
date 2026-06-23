#!/usr/bin/env python3
"""Re-runnable environment health check for the PoC (stdlib only). Run from repo root:

  python3 tests/healthcheck.py

Reads .env and checks: ServiceNow (PDI + eda.integration auth), AAP (gateway, controller
API, subscription), the target containers on the VM, and — if the controller is already
configured — the EE -> target path via an ad-hoc ping. Exit 0 if all pass, 1 otherwise.
"""
import os
import sys
import json
import time
import subprocess
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSH_KEY = os.path.expanduser("~/.ssh/snow-aap-poc")
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth  # noqa: E402

INSECURE = insecure_ctx()
load_dotenv(ROOT)
E = os.environ


def req(url, user=None, pw=None, insecure=False, method="GET", body=None, timeout=25):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if user is not None:
        r.add_header("Authorization", basic_auth(user, pw))
    if data:
        r.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(r, context=INSECURE if insecure else None, timeout=timeout)
    raw = resp.read()
    try:
        return resp.status, (json.loads(raw) if raw else {})
    except ValueError:
        return resp.status, raw.decode(errors="replace")  # non-JSON (e.g. HTML login page)


def cbase():
    return f"https://{E['FQDN']}/api/controller/v2"


# --- checks: return (ok, info); ok=None means SKIP ---

def c_servicenow():
    st, _ = req(f"https://{E['SN_INSTANCE']}/api/now/table/incident?sysparm_limit=1",
                E["SN_EDA_USERNAME"], E["SN_EDA_PASSWORD"])
    return st == 200, f"HTTP {st} as {E['SN_EDA_USERNAME']}"


def c_gateway():
    st, _ = req(f"https://{E['FQDN']}/", insecure=True)
    return st == 200, f"HTTP {st}"


def c_controller():
    st, d = req(f"{cbase()}/ping/", insecure=True)
    return st == 200, f"version {d.get('version')}, {len(d.get('instances', []))} instance(s)"


def c_subscription():
    st, d = req(f"{cbase()}/config/", E["AAP_ADMIN_USER"], E["AAP_ADMIN_PASSWORD"], insecure=True)
    li = d.get("license_info", {})
    return bool(li.get("valid_key")), f"{li.get('license_type')} valid={li.get('valid_key')}"


def c_targets():
    cmd = ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=accept-new",
           "-o", "ConnectTimeout=15", f"azureuser@{E['FQDN']}",
           "for n in hr-web-01 crm-web-01 ged-01 hr-db-01 mail-01; do printf '%s=' $n; "
           "podman exec $n systemctl is-active sshd 2>/dev/null | tr '\\n' ',' ; echo; done"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    txt = out.stdout.strip().replace("\n", " ")
    return txt.count("active") >= 5, txt or out.stderr.strip()


def c_ee_path():
    a, p = E["AAP_ADMIN_USER"], E["AAP_ADMIN_PASSWORD"]
    _, inv = req(f"{cbase()}/inventories/?name=Meridian%20Fleet", a, p, insecure=True)
    _, cred = req(f"{cbase()}/credentials/?name=Target%20SSH", a, p, insecure=True)
    if not inv.get("count") or not cred.get("count"):
        return None, "SKIP (run bootstrap/aap/controller/configure.py first)"
    _, cmd = req(f"{cbase()}/ad_hoc_commands/", a, p, insecure=True, method="POST",
                 body={"inventory": inv["results"][0]["id"], "credential": cred["results"][0]["id"],
                       "module_name": "ping", "module_args": ""})
    cid, status = cmd["id"], "pending"
    for _ in range(20):
        time.sleep(3)
        _, j = req(f"{cbase()}/ad_hoc_commands/{cid}/", a, p, insecure=True)
        status = j["status"]
        if status in ("successful", "failed", "error", "canceled"):
            break
    return status == "successful", f"ad-hoc ping: {status}"


CHECKS = [
    ("ServiceNow PDI + eda auth", c_servicenow),
    ("AAP gateway", c_gateway),
    ("AAP controller API", c_controller),
    ("AAP subscription", c_subscription),
    ("Targets (httpd/sshd)", c_targets),
    ("EE -> targets (ad-hoc ping)", c_ee_path),
]


def main():
    print()
    failed = False
    for name, fn in CHECKS:
        try:
            ok, info = fn()
        except Exception as e:
            ok, info = False, str(e)
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print(f"  [{tag}] {name:30} {info}")
        if ok is False:
            failed = True
    print()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

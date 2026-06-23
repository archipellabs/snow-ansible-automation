#!/usr/bin/env python3
"""Provision the PoC ServiceNow objects (idempotent, Table API, stdlib only).

Creates: the 'Auto-Remediation' assignment group (EDA trigger filter), the
'eda.integration' service account (+ itil role, activated, timezone GMT), and the
'app-node-1/2' CIs. The eda.integration password can't be set via the Table API: set it
once in the UI and store it in .env as SN_EDA_PASSWORD. (GMT matters for the EDA poll
window — see the inline note and the README key findings.)

Usage:
  # via .env (copy .env.example -> .env at the repo root):
  python3 bootstrap/servicenow/setup.py
  # or via environment variables:
  SN_INSTANCE=your-instance.service-now.com SN_USER=admin SN_PASS=*** python3 bootstrap/servicenow/setup.py
"""
import os
import sys
import json
import base64
import urllib.request
import urllib.error
import urllib.parse


def load_dotenv():
    """Load a .env file (KEY=VALUE) without overriding the existing environment."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (
        os.path.join(os.getcwd(), ".env"),
        os.path.join(here, ".env"),
        os.path.join(here, "..", ".env"),
        os.path.join(here, "..", "..", ".env"),
    ):
        if os.path.isfile(path):
            for line in open(path):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


load_dotenv()

_missing = [k for k in ("SN_INSTANCE", "SN_USER", "SN_PASS") if not os.environ.get(k)]
if _missing:
    sys.exit(
        "Missing variables: " + ", ".join(_missing)
        + "\nSet them in a .env file or as environment variables (see .env.example)."
    )

INSTANCE = os.environ["SN_INSTANCE"].replace("https://", "").rstrip("/")
USER = os.environ["SN_USER"]
PASS = os.environ["SN_PASS"]
BASE = f"https://{INSTANCE}/api/now/table"
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()


def req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", AUTH)
    r.add_header("Accept", "application/json")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r) as resp:
            return json.load(resp)["result"]
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} on {method} {url}\n{e.read().decode()}", file=sys.stderr)
        raise


def get_one(table, query, fields="sys_id"):
    q = urllib.parse.urlencode(
        {"sysparm_query": query, "sysparm_limit": "1", "sysparm_fields": fields}
    )
    res = req("GET", f"{BASE}/{table}?{q}")
    return res[0] if res else None


def ensure(table, key_query, body, label):
    found = get_one(table, key_query)
    if found:
        print(f"= {label} already exists (sys_id={found['sys_id']})")
        return found["sys_id"]
    created = req("POST", f"{BASE}/{table}", body)
    print(f"+ {label} created (sys_id={created['sys_id']})")
    return created["sys_id"]


def main():
    # 1) Trigger group
    ensure(
        "sys_user_group",
        "name=Auto-Remediation",
        {"name": "Auto-Remediation", "description": "POC EDA/AAP auto-remediation"},
        "Auto-Remediation group",
    )

    # 2) Service account
    existing = get_one("sys_user", "user_name=eda.integration")
    if existing:
        user_sid = existing["sys_id"]
        print(f"= User eda.integration already exists (sys_id={user_sid})")
    else:
        created = req(
            "POST",
            f"{BASE}/sys_user",
            {
                "user_name": "eda.integration",
                "first_name": "EDA",
                "last_name": "Integration",
                "email": "eda.integration@example.com",
            },
        )
        user_sid = created["sys_id"]
        print(f"+ User eda.integration created (sys_id={user_sid})")

    # Activate the account and clear the reset flag (these DO work via the API).
    # The password itself cannot be set reliably via the Table API -> set it once in
    # the UI (open the user -> Set Password), then store it in .env as SN_EDA_PASSWORD.
    # time_zone=GMT is REQUIRED: the EDA records source builds its poll-window filter with
    # gs.dateGenerate, evaluated in this user's timezone; the rulebook pins UTC, so a
    # non-GMT user shifts the window and new incidents never trigger the activation.
    req("PATCH", f"{BASE}/sys_user/{user_sid}",
        {"active": "true", "locked_out": "false", "password_needs_reset": "false",
         "time_zone": "GMT"})
    print("  -> active=true, locked_out=false, password_needs_reset=false, time_zone=GMT")

    # 3) itil role (incident read/write)
    role = get_one("sys_user_role", "name=itil")
    if not role:
        print("! 'itil' role not found", file=sys.stderr)
        sys.exit(1)
    ensure(
        "sys_user_has_role",
        f"user={user_sid}^role={role['sys_id']}",
        {"user": user_sid, "role": role["sys_id"]},
        "itil role on eda.integration",
    )

    # 4) Target CIs (sshd containers)
    for name in ("app-node-1", "app-node-2"):
        ensure(
            "cmdb_ci_linux_server",
            f"name={name}",
            {"name": name, "short_description": "POC target (sshd container)"},
            f"CI {name}",
        )

    # 5) Reminder — the eda password is managed in .env (set via the UI)
    print("\n=== Next ===")
    print("Set eda.integration's password in the ServiceNow UI (user -> Set Password),")
    print("then store it in .env as SN_EDA_PASSWORD (with SN_EDA_USERNAME=eda.integration).")
    print("EDA filter: assignment_group.name=Auto-Remediation^state=1")


if __name__ == "__main__":
    main()

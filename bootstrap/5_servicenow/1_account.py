#!/usr/bin/env python3
"""Step 1 — the ServiceNow integration account (idempotent, Table API, stdlib only).

Creates: the 'Auto-Remediation' assignment group (EDA trigger filter) and the
'eda.integration' service account (+ itil role, activated, timezone GMT). The server CIs
and the rest of the Meridian CMDB are loaded separately from simulator/fleet.yml by
2_cmdb.py. The eda.integration password can't be set via the Table API: set it once in the
UI and store it in .env as SN_EDA_PASSWORD. (GMT matters for the EDA poll window — see the
inline note and the README key findings.)

Usage:
  # via .env (copy .env.example -> .env at the repo root):
  python3 bootstrap/5_servicenow/1_account.py
  # or via environment variables:
  SN_INSTANCE=your-instance.service-now.com SN_USER=admin SN_PASS=*** python3 bootstrap/5_servicenow/1_account.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv  # noqa: E402
from lib.servicenow import Snow  # noqa: E402

load_dotenv(ROOT, required=("SN_INSTANCE", "SN_USER", "SN_PASS"))


def main():
    snow = Snow()

    # 1) Trigger group
    snow.ensure(
        "sys_user_group",
        "name=Auto-Remediation",
        {"name": "Auto-Remediation", "description": "POC EDA/AAP auto-remediation"},
        "Auto-Remediation group",
    )

    # 2) Service account
    existing = snow.get_one("sys_user", "user_name=eda.integration")
    if existing:
        user_sid = existing["sys_id"]
        print(f"= User eda.integration already exists (sys_id={user_sid})")
    else:
        created = snow.result("table/sys_user", {
            "user_name": "eda.integration",
            "first_name": "EDA",
            "last_name": "Integration",
            "email": "eda.integration@example.com",
        })
        user_sid = created["sys_id"]
        print(f"+ User eda.integration created (sys_id={user_sid})")

    # Activate the account and clear the reset flag (these DO work via the API).
    # The password itself cannot be set reliably via the Table API -> set it once in
    # the UI (open the user -> Set Password), then store it in .env as SN_EDA_PASSWORD.
    # time_zone=GMT is REQUIRED: the EDA records source builds its poll-window filter with
    # gs.dateGenerate, evaluated in this user's timezone; the rulebook pins UTC, so a
    # non-GMT user shifts the window and new incidents never trigger the activation.
    snow.call(f"table/sys_user/{user_sid}",
              {"active": "true", "locked_out": "false", "password_needs_reset": "false",
               "time_zone": "GMT"}, method="PATCH")
    print("  -> active=true, locked_out=false, password_needs_reset=false, time_zone=GMT")

    # 3) itil role (incident read/write)
    role = snow.get_one("sys_user_role", "name=itil")
    if not role:
        print("! 'itil' role not found", file=sys.stderr)
        sys.exit(1)
    snow.ensure(
        "sys_user_has_role",
        f"user={user_sid}^role={role['sys_id']}",
        {"user": user_sid, "role": role["sys_id"]},
        "itil role on eda.integration",
    )

    # The server CIs and the rest of the Meridian CMDB (applications, business services,
    # relationships, people, support groups) are loaded by dataset.py from simulator/fleet.yml.

    # Reminder — the eda password is managed in .env (set via the UI)
    print("\n=== Next ===")
    print("Set eda.integration's password in the ServiceNow UI (user -> Set Password),")
    print("then store it in .env as SN_EDA_PASSWORD (with SN_EDA_USERNAME=eda.integration).")
    print("EDA filter: assignment_group.name=Auto-Remediation^state=1")


if __name__ == "__main__":
    main()

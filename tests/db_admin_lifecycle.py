#!/usr/bin/env python3
"""DB-admin lifecycle smoke test (controller path), re-runnable. Run from repo root:

  python3 tests/db_admin_lifecycle.py

Drives the four P2 db-admin job templates against hr-db-01 (a real PostgreSQL fleet server):
  1. DB Apply Migration  -> creates the `hr` database + schema from 001_init_hr_schema.sql
  2. DB Create Role      -> creates the `hr_app` login role + CONNECT grant on `hr`
  3. DB Status           -> read-only health report
  4. DB Backup           -> pg_dump the `hr` database
Then verifies the resulting Postgres state over SSH (database, role, seeded rows, dump file).
Idempotent: a second run is a no-op for the migration and reconciles the role. Exit 0 if all pass.
"""
import os
import sys
import time
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TARGET = "hr-db-01"        # a Meridian Fleet db server
DB = "hr"
ROLE = "reporting"         # a demo role created by db_create_role (distinct from the app's hr_app)
ROLE_PW = "Demo_reporting_pw_2026"
MIGRATION = "001_leave_requests.sql"
PSQL = "/usr/pgsql-16/bin/psql"
sys.path.insert(0, ROOT)
from lib.poc import load_dotenv, insecure_ctx, basic_auth, http_json  # noqa: E402

INSECURE = insecure_ctx()
load_dotenv(ROOT)
E = os.environ


def aap(path, body=None):
    method = "POST" if body is not None else "GET"
    return http_json(f"https://{E['FQDN']}/api/controller/v2/{path}", method=method,
                     headers={"Authorization": basic_auth(E["AAP_ADMIN_USER"], E["AAP_ADMIN_PASSWORD"])},
                     body=body, ctx=INSECURE)


def ssh(cmd):
    return subprocess.run(
        ["ssh", "-i", os.path.expanduser("~/.ssh/snow-aap-poc"),
         "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15",
         f"azureuser@{E['FQDN']}", cmd],
        capture_output=True, text=True, timeout=60).stdout.strip()


def psql(sql, db=None):
    d = f"-d {db} " if db else ""
    return ssh(f'podman exec {TARGET} runuser -u postgres -- {PSQL} {d}-tAc "{sql}"')


def run_jt(name, extra_vars):
    jt = aap("job_templates/?name=" + name.replace(" ", "%20"))["results"][0]["id"]
    jid = aap(f"job_templates/{jt}/launch/", {"extra_vars": extra_vars})["id"]
    status = "pending"
    for _ in range(40):
        time.sleep(4)
        status = aap(f"jobs/{jid}/")["status"]
        if status in ("successful", "failed", "error", "canceled"):
            break
    print(f"   {name:20s} -> {status}")
    return status == "successful"


def main():
    jobs_ok = True
    print(f">> 1. DB Apply Migration ({MIGRATION} -> {DB})")
    jobs_ok &= run_jt("DB Apply Migration", {"target_host": TARGET, "db_name": DB, "migration_file": MIGRATION})
    print(f">> 2. DB Create Role ({ROLE} on {DB})")
    jobs_ok &= run_jt("DB Create Role", {"target_host": TARGET, "db_role": ROLE,
                                         "db_password": ROLE_PW, "db_name": DB})
    print(">> 3. DB Status")
    jobs_ok &= run_jt("DB Status", {"target_host": TARGET})
    print(f">> 4. DB Backup ({DB})")
    jobs_ok &= run_jt("DB Backup", {"target_host": TARGET, "db_name": DB})

    print("\n>> Verifying PostgreSQL state on " + TARGET)
    db_present = psql(f"SELECT 1 FROM pg_database WHERE datname='{DB}'") == "1"
    role_present = psql(f"SELECT 1 FROM pg_roles WHERE rolname='{ROLE}'") == "1"
    leave = psql("SELECT count(*) FROM leave_requests", db=DB)
    migration_recorded = psql(f"SELECT 1 FROM meridian_schema_migrations WHERE filename='{MIGRATION}'", db=DB) == "1"
    dumps = ssh(f"podman exec {TARGET} bash -lc 'ls /var/backups/postgresql/{DB}-*.dump 2>/dev/null | wc -l'")

    print()
    print(f"  jobs all successful   : {jobs_ok}")
    print(f"  database '{DB}' exists : {db_present}")
    print(f"  role '{ROLE}' exists   : {role_present}")
    print(f"  leave_requests seeded : {leave == '3'} ({leave})")
    print(f"  migration recorded    : {migration_recorded}")
    print(f"  backup dump present   : {dumps not in ('', '0')} ({dumps} file[s])")
    ok = jobs_ok and db_present and role_present and leave == "3" and migration_recorded and dumps not in ("", "0")
    print("\n>> " + ("DB LIFECYCLE PASSED" if ok else "DB LIFECYCLE FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Scenario — DB-admin lifecycle (controller path), re-runnable. Run from repo root:

  python3 tests/scenarios/7_db_admin_lifecycle.py

Drives the four P2 db-admin job templates against hr-db-01 (a real PostgreSQL fleet server):
  1. DB Apply Migration  -> creates the `hr` database + schema from 001_init_hr_schema.sql
  2. DB Create Role      -> creates the `hr_app` login role + CONNECT grant on `hr`
  3. DB Status           -> read-only health report
  4. DB Backup           -> pg_dump the `hr` database
Then verifies the resulting Postgres state over SSH (database, role, seeded rows, dump file).
Idempotent: a second run is a no-op for the migration and reconciles the role.

Run standalone; run() returns (ok, detail) and the __main__ wrapper prints PASS/FAIL.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
from lib.poc import env, ssh  # noqa: E402
from lib.runtime import controller  # noqa: E402

TARGET = "hr-db-01"        # a Meridian Fleet db server
DB = "hr"
ROLE = "reporting"         # a demo role created by db_create_role (distinct from the app's hr_app)
ROLE_PW = "Demo_reporting_pw_2026"
MIGRATION = "001_leave_requests.sql"
PSQL = "/usr/pgsql-16/bin/psql"


def psql(sql, db=None):
    d = f"-d {db} " if db else ""
    return ssh(f'podman exec {TARGET} runuser -u postgres -- {PSQL} {d}-tAc "{sql}"', timeout=60)


def run():
    env()
    ctl = controller()

    def jt(name, extra_vars):
        _, status = ctl.run_jt(name, extra_vars)
        print(f"   {name:20s} -> {status}")
        return status == "successful"

    jobs_ok = True
    print(f">> 1. DB Apply Migration ({MIGRATION} -> {DB})")
    jobs_ok &= jt("DB Apply Migration", {"target_host": TARGET, "db_name": DB, "migration_file": MIGRATION})
    print(f">> 2. DB Create Role ({ROLE} on {DB})")
    jobs_ok &= jt("DB Create Role", {"target_host": TARGET, "db_role": ROLE, "db_password": ROLE_PW, "db_name": DB})
    print(">> 3. DB Status")
    jobs_ok &= jt("DB Status", {"target_host": TARGET})
    print(f">> 4. DB Backup ({DB})")
    jobs_ok &= jt("DB Backup", {"target_host": TARGET, "db_name": DB})

    print("\n>> Verifying PostgreSQL state on " + TARGET)
    db_present = psql(f"SELECT 1 FROM pg_database WHERE datname='{DB}'") == "1"
    role_present = psql(f"SELECT 1 FROM pg_roles WHERE rolname='{ROLE}'") == "1"
    leave = psql("SELECT count(*) FROM leave_requests", db=DB)
    migration_recorded = psql(f"SELECT 1 FROM meridian_schema_migrations WHERE filename='{MIGRATION}'", db=DB) == "1"
    dumps = ssh(f"podman exec {TARGET} bash -lc 'ls /var/backups/postgresql/{DB}-*.dump 2>/dev/null | wc -l'", timeout=60)

    print(f"  jobs all successful   : {jobs_ok}")
    print(f"  database '{DB}' exists : {db_present}")
    print(f"  role '{ROLE}' exists   : {role_present}")
    print(f"  leave_requests seeded : {leave == '3'} ({leave})")
    print(f"  migration recorded    : {migration_recorded}")
    print(f"  backup dump present   : {dumps not in ('', '0')} ({dumps} file[s])")
    ok = jobs_ok and db_present and role_present and leave == "3" and migration_recorded and dumps not in ("", "0")
    return ok, f"jobs={jobs_ok}, db={db_present}, role={role_present}, leave={leave}, dumps={dumps}"


if __name__ == "__main__":
    ok, detail = run()
    print("\n>> " + ("PASS" if ok else "FAIL") + f" — {detail}")
    sys.exit(0 if ok else 1)

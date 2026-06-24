-- 001_leave_requests.sql — HR business data: employee leave requests (demandes de congés).
-- This is the HR app's *business* schema; employee IDENTITY lives in Keycloak (see the onboarding
-- flow), not here. Applied by db_apply_migration.yml (tracked once in meridian_schema_migrations).
--
-- It also provisions a read-only login role 'hr_app' that the HR Portal uses to display the
-- requests. The role has no password: pg_hba (simulator/base/db.Containerfile) trusts it for the
-- 'hr' database only, from inside the (unexposed) container network — a PoC simplification.

CREATE TABLE IF NOT EXISTS leave_requests (
    id             serial PRIMARY KEY,
    employee_name  text        NOT NULL,
    employee_email text        NOT NULL,
    leave_type     text        NOT NULL DEFAULT 'Congés payés',
    start_date     date        NOT NULL,
    end_date       date        NOT NULL,
    status         text        NOT NULL DEFAULT 'pending',
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT leave_dates_ok CHECK (end_date >= start_date)
);

-- Read-only application role (idempotent; no password — pg_hba trusts it on this db only).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hr_app') THEN
        CREATE ROLE hr_app LOGIN;
    END IF;
END
$$;
GRANT CONNECT ON DATABASE hr TO hr_app;
GRANT USAGE ON SCHEMA public TO hr_app;
GRANT SELECT ON leave_requests TO hr_app;

-- Seed a few requests (the demo HR employees, by name — no FK to an identity table).
INSERT INTO leave_requests (employee_name, employee_email, leave_type, start_date, end_date, status) VALUES
    ('Camille Roux',  'camille.roux@meridian.example',  'Congés payés', '2026-07-06', '2026-07-17', 'approved'),
    ('Yanis Bernard', 'yanis.bernard@meridian.example', 'RTT',          '2026-06-30', '2026-06-30', 'pending'),
    ('Aicha Diallo',  'aicha.diallo@meridian.example',  'Congés payés', '2026-08-10', '2026-08-21', 'pending');

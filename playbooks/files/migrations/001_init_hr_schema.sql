-- 001_init_hr_schema.sql — initial HR schema for the RH Self-Service database (demo migration).
-- Written to be re-runnable on its own (IF NOT EXISTS); db_apply_migration.yml additionally records
-- it in meridian_schema_migrations so it is applied at most once. Mirrors the hr-portal app domain.

CREATE TABLE IF NOT EXISTS employees (
    id          serial PRIMARY KEY,
    full_name   text        NOT NULL,
    email       text        NOT NULL UNIQUE,
    department  text,
    hired_on    date        NOT NULL DEFAULT current_date
);

CREATE TABLE IF NOT EXISTS leave_requests (
    id          serial PRIMARY KEY,
    employee_id integer     NOT NULL REFERENCES employees (id),
    start_date  date        NOT NULL,
    end_date    date        NOT NULL,
    status      text        NOT NULL DEFAULT 'pending',
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT leave_dates_ok CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS leave_requests_employee_idx ON leave_requests (employee_id);

-- Seed the three employees the hr-portal app serves (idempotent).
INSERT INTO employees (full_name, email, department) VALUES
    ('Camille Roux',  'camille.roux@meridian.example',  'Finance'),
    ('Yanis Bernard', 'yanis.bernard@meridian.example', 'Logistics'),
    ('Aicha Diallo',  'aicha.diallo@meridian.example',  'Human Resources')
ON CONFLICT (email) DO NOTHING;

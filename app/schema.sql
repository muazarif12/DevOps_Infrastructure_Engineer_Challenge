-- Postgres schema for the patient intake voice agent.
--
-- You do NOT need to run this by hand for the app to work: app/worker.py calls
-- create_schema() (app/store.py -> ORMBase.metadata.create_all()) once per worker process at
-- startup, and app/web.py does the same in its lifespan handler — the table is created
-- automatically the first time either service connects to an empty database.
--
-- This file exists so the schema can be inspected or run manually (e.g. via Railway's Postgres
-- query console) without spinning up the app first, and as documentation. It's kept in sync by
-- hand with the SQLAlchemy model in app/store.py (PatientRow) — that model is the source of
-- truth; if the two ever disagree, the ORM wins because that's what actually runs.

CREATE TABLE IF NOT EXISTS patient_records (
    record_id     VARCHAR(32)              PRIMARY KEY, -- generated app-side (uuid4().hex), no DB default
    given_name    VARCHAR(60)              NOT NULL,
    family_name   VARCHAR(60)              NOT NULL,
    birth_date    DATE                     NOT NULL,
    sex           VARCHAR(20)              NOT NULL,
    phone         VARCHAR(10)              NOT NULL,
    street        VARCHAR(255)             NOT NULL,
    unit          VARCHAR(255),
    city          VARCHAR(120)             NOT NULL,
    state         VARCHAR(2)               NOT NULL,
    postal_code   VARCHAR(10)              NOT NULL,
    email         VARCHAR(255),
    insurer       VARCHAR(255),
    member_id     VARCHAR(64),
    language      VARCHAR(60)              NOT NULL, -- app-side default 'English' (NewPatient/PatientRow), not a DB default
    contact_name  VARCHAR(255),
    contact_phone VARCHAR(10),
    created_at    TIMESTAMPTZ              NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ              NOT NULL DEFAULT now(),
    archived_at   TIMESTAMPTZ                                       -- soft delete marker; NULL = active
);

-- Matches the ORM's phone lookup (find_by_phone / check_returning in app/flow.py) and
-- SQLAlchemy's own default index-naming convention for index=True on this column.
CREATE INDEX IF NOT EXISTS ix_patient_records_phone ON patient_records (phone);

-- Optional, for parity with the ORM's onupdate=func.now() on `updated_at`: SQLAlchemy already
-- sets updated_at correctly whenever the app itself does an UPDATE (it issues now() as part of
-- that statement), so this trigger only matters if someone updates a row directly in psql/the
-- Railway query console rather than through the app.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_patient_records_updated_at ON patient_records;
CREATE TRIGGER trg_patient_records_updated_at
    BEFORE UPDATE ON patient_records
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

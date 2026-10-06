BEGIN;

-- Running upgrade f92a206supa01 -> a03preusers01

CREATE TABLE preuser_imports (
    id VARCHAR(36) NOT NULL, 
    filename VARCHAR(255) NOT NULL, 
    file_hash VARCHAR(64) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    rows INTEGER NOT NULL, 
    imported INTEGER NOT NULL, 
    duplicates INTEGER NOT NULL, 
    invalid INTEGER NOT NULL, 
    errors JSON NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_preuser_imports_file_hash ON preuser_imports (file_hash);

CREATE TABLE preusers (
    id VARCHAR(36) NOT NULL, 
    email VARCHAR(254) NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    import_id VARCHAR(36) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (email), 
    FOREIGN KEY(import_id) REFERENCES preuser_imports (id)
);

CREATE INDEX ix_preusers_status ON preusers (status);

CREATE INDEX ix_preusers_import_id ON preusers (import_id);

ALTER TABLE public."preusers" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."preusers" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."preusers" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."preusers" FROM authenticated; END IF; END $$;

ALTER TABLE public."preuser_imports" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public."preuser_imports" FROM PUBLIC;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN REVOKE ALL ON TABLE public."preuser_imports" FROM anon; END IF; END $$;

DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN REVOKE ALL ON TABLE public."preuser_imports" FROM authenticated; END IF; END $$;

UPDATE alembic_version SET version_num='a03preusers01' WHERE alembic_version.version_num = 'f92a206supa01';

COMMIT;


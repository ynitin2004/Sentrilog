-- Phase 13: which sanctions list a screen ran against, and what the evaluation harness measured.
-- Neither table holds tenant data -- a list snapshot and an eval run are the same for every
-- tenant -- so neither has a tenant_id or an RLS policy. Both are insert-only for the app role,
-- like audit_log: a snapshot is a historical fact a past screening decision points at, and a
-- measured result is evidence, so neither is ever edited in place.

-- One row per ingested list snapshot, per embedding model. Each gets its own Qdrant collection;
-- the alias the screener reads (SCREENING qdrant_collection) is pointed at one of them, which is
-- how a bad snapshot is rolled back without deleting what earlier cases were screened against.
CREATE TABLE list_snapshots (
    collection_name  TEXT PRIMARY KEY,
    list_version     TEXT NOT NULL,
    source           TEXT NOT NULL,
    content_hash     CHAR(64) NOT NULL,
    entry_count      INTEGER NOT NULL,
    embedding_model  TEXT NOT NULL,
    published_at     TIMESTAMPTZ,
    fetched_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_list_snapshots_list_version ON list_snapshots (list_version);

GRANT SELECT, INSERT ON list_snapshots TO sentrilog_app;

-- NULL on every hit recorded before Phase 13: those screens ran against the synthetic sample
-- list, which had no version identity. Not backfilled -- there is no true value to backfill.
ALTER TABLE sanctions_hits ADD COLUMN list_version TEXT;
ALTER TABLE sanctions_hits ADD COLUMN entity_id TEXT;

CREATE TABLE eval_runs (
    id              BIGSERIAL PRIMARY KEY,
    suite           TEXT NOT NULL CHECK (suite IN ('screening', 'extraction', 'face', 'risk')),
    dataset_name    TEXT NOT NULL,
    dataset_sha256  CHAR(64) NOT NULL,
    config          JSONB NOT NULL,
    metrics         JSONB NOT NULL,
    git_sha         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_eval_runs_suite_created_at ON eval_runs (suite, created_at DESC);

GRANT SELECT, INSERT ON eval_runs TO sentrilog_app;
GRANT USAGE, SELECT ON SEQUENCE eval_runs_id_seq TO sentrilog_app;

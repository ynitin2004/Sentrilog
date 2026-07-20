-- Core Sentrilog schema. Runs once against the `sentrilog` database on first container start
-- (docker-entrypoint-initdb.d scripts only execute against an empty data directory).

CREATE TABLE cases (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- TEXT + CHECK rather than a native ENUM: avoids ALTER TYPE friction while the schema is still moving.
    status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'processing', 'needs_review', 'approved', 'rejected')),
    subject_name  TEXT NOT NULL,
    subject_dob   DATE,
    risk_score    NUMERIC(5, 4),
    decision      TEXT CHECK (decision IN ('approved', 'rejected')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at    TIMESTAMPTZ
);

CREATE TABLE documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id      UUID NOT NULL REFERENCES cases (id),
    s3_key       TEXT NOT NULL,
    doc_type     TEXT NOT NULL,
    sha256       CHAR(64) NOT NULL,
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE extractions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id        UUID NOT NULL REFERENCES cases (id),
    document_id    UUID NOT NULL REFERENCES documents (id),
    model_version  TEXT NOT NULL,
    raw_json       JSONB NOT NULL,
    confidence     NUMERIC(5, 4) NOT NULL,
    valid          BOOLEAN NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE face_matches (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id           UUID NOT NULL REFERENCES cases (id),
    similarity_score  NUMERIC(5, 4) NOT NULL,
    model_version     TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sanctions_hits (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id       UUID NOT NULL REFERENCES cases (id),
    list_source   TEXT NOT NULL,
    matched_name  TEXT NOT NULL,
    match_score   NUMERIC(5, 4) NOT NULL,
    method        TEXT NOT NULL CHECK (method IN ('vector', 'phonetic')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE review_decisions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id        UUID NOT NULL REFERENCES cases (id),
    reviewer_id    TEXT NOT NULL,
    decision       TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'escalated')),
    justification  TEXT NOT NULL,
    decided_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only, hash-chained audit trail (INSERT-only DB grants enforced in Phase 8).
-- row_hash / prev_row_hash form the tamper-evidence chain: row_hash(N) is computed by the
-- application over its own payload + prev_row_hash, so altering any historical row breaks
-- every row_hash after it.
CREATE TABLE audit_log (
    id             BIGSERIAL PRIMARY KEY,
    case_id        UUID NOT NULL REFERENCES cases (id),
    event_type     TEXT NOT NULL,
    actor          TEXT NOT NULL,
    model_version  TEXT,
    input_hash     CHAR(64),
    payload        JSONB NOT NULL,
    prev_row_hash  CHAR(64),
    row_hash       CHAR(64) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_documents_case_id ON documents (case_id);
CREATE INDEX idx_extractions_case_id ON extractions (case_id);
CREATE INDEX idx_face_matches_case_id ON face_matches (case_id);
CREATE INDEX idx_sanctions_hits_case_id ON sanctions_hits (case_id);
CREATE INDEX idx_review_decisions_case_id ON review_decisions (case_id);
CREATE INDEX idx_audit_log_case_id ON audit_log (case_id);

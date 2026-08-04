-- Multi-tenancy amendment (PLAN.md §4/§7). Runs after 001_schema.sql on a fresh volume;
-- on an already-initialized volume it must be applied manually once (docker-entrypoint-initdb.d
-- scripts only execute against an empty data directory).

CREATE TABLE tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    plan_tier   TEXT NOT NULL DEFAULT 'standard' CHECK (plan_tier IN ('standard', 'pro', 'enterprise')),
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants (id),
    key_hash    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

CREATE TABLE reviewers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants (id),
    email       TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('reviewer', 'admin', 'auditor')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);

CREATE TABLE webhooks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenants (id),
    url          TEXT NOT NULL,
    secret       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    disabled_at  TIMESTAMPTZ
);

CREATE TABLE webhook_deliveries (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID NOT NULL REFERENCES tenants (id),
    webhook_id         UUID NOT NULL REFERENCES webhooks (id),
    case_id            UUID NOT NULL REFERENCES cases (id),
    event_type         TEXT NOT NULL,
    payload            JSONB NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'failed')),
    attempt_count      INT NOT NULL DEFAULT 0,
    last_attempted_at  TIMESTAMPTZ
);

-- Denormalize tenant_id onto every existing table. NOT NULL with no DEFAULT: safe because
-- these tables are still empty (Phase 3 hasn't written a case yet) -- if this were ever run
-- against non-empty tables it would fail loudly instead of silently leaving rows unscoped,
-- which is exactly the behavior wanted for a security-relevant column.
ALTER TABLE cases ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants (id);
ALTER TABLE documents ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants (id);
ALTER TABLE extractions ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants (id);
ALTER TABLE face_matches ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants (id);
ALTER TABLE sanctions_hits ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants (id);
ALTER TABLE review_decisions ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants (id);
ALTER TABLE audit_log ADD COLUMN tenant_id UUID NOT NULL REFERENCES tenants (id);

-- Idempotent case creation: a client retrying a submission must not create a duplicate case.
-- Nullable + UNIQUE is intentional -- Postgres treats multiple NULLs as distinct, so cases
-- created without a client-supplied key (e.g. internal/admin-created) are unaffected.
ALTER TABLE cases ADD COLUMN idempotency_key TEXT;
ALTER TABLE cases ADD CONSTRAINT cases_tenant_idempotency_key UNIQUE (tenant_id, idempotency_key);

-- reviewer_id was free-text; now a real FK into the reviewers table.
ALTER TABLE review_decisions ALTER COLUMN reviewer_id TYPE UUID USING reviewer_id::uuid;
ALTER TABLE review_decisions
    ADD CONSTRAINT review_decisions_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES reviewers (id);

CREATE INDEX idx_api_keys_tenant_id ON api_keys (tenant_id);
CREATE INDEX idx_reviewers_tenant_id ON reviewers (tenant_id);
CREATE INDEX idx_webhooks_tenant_id ON webhooks (tenant_id);
CREATE INDEX idx_webhook_deliveries_tenant_id ON webhook_deliveries (tenant_id);
CREATE INDEX idx_webhook_deliveries_webhook_id ON webhook_deliveries (webhook_id);
CREATE INDEX idx_webhook_deliveries_case_id ON webhook_deliveries (case_id);
CREATE INDEX idx_cases_tenant_id ON cases (tenant_id);
CREATE INDEX idx_documents_tenant_id ON documents (tenant_id);
CREATE INDEX idx_extractions_tenant_id ON extractions (tenant_id);
CREATE INDEX idx_face_matches_tenant_id ON face_matches (tenant_id);
CREATE INDEX idx_sanctions_hits_tenant_id ON sanctions_hits (tenant_id);
CREATE INDEX idx_review_decisions_tenant_id ON review_decisions (tenant_id);
CREATE INDEX idx_audit_log_tenant_id ON audit_log (tenant_id);

-- Row-level security is a no-op for superusers and table owners, and POSTGRES_USER
-- (sentrilog) is bootstrapped as a superuser -- so the application must connect as a
-- separate, unprivileged role for tenant-isolation policies to actually apply. This is
-- also just correct practice independent of RLS: application code should never hold the
-- DB superuser's credentials.
-- Dev-only password, consistent with the other hardcoded local credentials in
-- docker-compose.yml; Phase 10 replaces this with a Secrets-Manager-issued credential.
CREATE ROLE sentrilog_app LOGIN PASSWORD 'sentrilog_app_dev_password';
GRANT CONNECT ON DATABASE sentrilog TO sentrilog_app;
GRANT USAGE ON SCHEMA public TO sentrilog_app;
GRANT SELECT, INSERT, UPDATE ON
    tenants, api_keys, reviewers, webhooks, webhook_deliveries,
    cases, documents, extractions, face_matches, sanctions_hits, review_decisions
    TO sentrilog_app;
-- audit_log is append-only: no UPDATE/DELETE for the app role. Fully enforced (all grants
-- revoked except INSERT) in Phase 8; this is the first step, not the final state.
GRANT SELECT, INSERT ON audit_log TO sentrilog_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sentrilog_app;

-- tenant_id is resolved from the caller's API key and set per-connection via
-- `SET app.tenant_id = '<uuid>'` before any query runs. current_setting(..., true) returns
-- NULL instead of erroring when unset, and tenant_id = NULL is never true -- so a connection
-- that forgets to set it sees zero rows (fails closed) rather than every tenant's data.
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'cases', 'documents', 'extractions', 'face_matches', 'sanctions_hits',
        'review_decisions', 'audit_log', 'api_keys', 'reviewers', 'webhooks', 'webhook_deliveries'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)::uuid) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true)::uuid)',
            t
        );
    END LOOP;
END $$;

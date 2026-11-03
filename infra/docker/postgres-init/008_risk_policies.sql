-- Phase 13b: per-tenant, versioned risk policies (improvements.md P-3).
--
-- Insert-only for the app role, like audit_log: a policy version is the rule a past case was
-- decided under, so changing one in place would rewrite the explanation of decisions already
-- made. A tenant changes its policy by inserting the next version.
CREATE TABLE risk_policies (
    tenant_id   UUID NOT NULL REFERENCES tenants (id),
    version     INTEGER NOT NULL CHECK (version >= 1),
    document    JSONB NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, version)
);

ALTER TABLE risk_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_policies FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON risk_policies
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

GRANT SELECT, INSERT ON risk_policies TO sentrilog_app;

-- The policy version a case is decided under, fixed when the case is created so a policy
-- change mid-flight cannot alter how an in-progress case is judged (STUDY.md, Phase 13).
-- 0 is the built-in policy (services/common/policy.py), which every case created before this
-- migration was decided under -- so 0 is their true value, not a placeholder.
ALTER TABLE cases ADD COLUMN policy_version INTEGER NOT NULL DEFAULT 0;

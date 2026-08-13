-- Reviewer authentication (token-based, mirroring api_keys' hash-and-resolve pattern from
-- Phase 3) and case-claim tracking, for the Phase 7 human-in-the-loop review queue.

ALTER TABLE reviewers ADD COLUMN token_hash TEXT UNIQUE;
ALTER TABLE reviewers ADD COLUMN revoked_at TIMESTAMPTZ;

-- Claiming is advisory (a UI signal that someone's already working a case), not a hard lock:
-- the decision endpoint doesn't require a prior claim, so a slow/abandoned claim can never
-- block a case from being decided. See PLAN.md Phase 7 for the reasoning.
ALTER TABLE cases ADD COLUMN claimed_by_reviewer_id UUID REFERENCES reviewers (id);
ALTER TABLE cases ADD COLUMN claimed_at TIMESTAMPTZ;

-- Same RLS chicken-and-egg problem 003_auth_lookup.sql solved for api_keys: resolving a
-- reviewer's token to their tenant_id/role has to happen before app.tenant_id is known, which
-- the RLS policy on reviewers otherwise requires.
CREATE FUNCTION resolve_reviewer_token(p_token_hash TEXT)
RETURNS TABLE (tenant_id UUID, reviewer_id UUID, role TEXT, revoked_at TIMESTAMPTZ)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT tenant_id, id, role, revoked_at
    FROM reviewers
    WHERE token_hash = p_token_hash;
$$;

REVOKE ALL ON FUNCTION resolve_reviewer_token(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_reviewer_token(TEXT) TO sentrilog_app;

-- api_keys has row-level security requiring app.tenant_id to already be set (002_multi_tenancy.sql),
-- but resolving an API key to its tenant_id is exactly the step that happens *before* we know
-- the tenant -- a chicken-and-egg problem RLS alone can't solve. A SECURITY DEFINER function
-- (runs as its owner, which bypasses RLS) is the standard fix: it exposes only the one narrow
-- lookup the app needs, not unrestricted access to the api_keys table.
CREATE FUNCTION resolve_api_key(p_key_hash TEXT)
RETURNS TABLE (tenant_id UUID, api_key_id UUID, revoked_at TIMESTAMPTZ)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT tenant_id, id, revoked_at
    FROM api_keys
    WHERE key_hash = p_key_hash;
$$;

REVOKE ALL ON FUNCTION resolve_api_key(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_api_key(TEXT) TO sentrilog_app;

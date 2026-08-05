# Sentrilog — Study Notes

A running log of every non-trivial concept introduced while building this, written so you can learn it alongside the implementation rather than just watch code appear. Organized by phase. Each entry: **what it is**, **why we needed it here** (the specific bug or requirement that forced it), and **where to read more**.

This file grows every phase — treat it as a second changelog, one for concepts instead of commits.

---

## Phase 2 concepts

### Row-Level Security (RLS)

**What:** A Postgres feature where the database itself filters which rows a query can see/write, based on a policy — enforced at the engine level, not just in application code.

**Why here:** With many tenants sharing one Postgres instance, app-layer `WHERE tenant_id = ...` filtering is one missed clause away from a cross-tenant data leak. RLS makes that structurally impossible — even a buggy or malicious query against `cases` physically cannot see another tenant's rows, because the *database* rewrites the query.

**The gotcha we hit:** RLS is **silently bypassed for superusers and table owners**, with no override — not even `FORCE ROW LEVEL SECURITY` changes that. Since `POSTGRES_USER` is a superuser by default, the app had to connect as a separate, unprivileged role (`sentrilog_app`) for RLS to do anything at all. This is the single most important Postgres security fact in this whole project.

**Read more:** <https://www.postgresql.org/docs/current/ddl-rowsecurity.html>

### `SET LOCAL` / `set_config()`

**What:** Postgres session/transaction-scoped configuration variables — a way to stash a value (like "which tenant is this connection allowed to see") that a query can read back via `current_setting(...)`.

**Why here:** RLS policies need to know the current tenant *without* it being a literal in the SQL (that would mean building SQL strings by hand — an injection risk). `SET LOCAL` sets a variable for the rest of the current transaction only, so a pooled connection can never leak one tenant's context onto the next request that reuses it.

**The gotcha we hit:** `SET`/`SET LOCAL` do **not** accept bind parameters (`$1`) — only literal values. Writing `SET LOCAL app.tenant_id = $1` is a syntax error. The fix is `SELECT set_config('app.tenant_id', $1, true)` — `set_config()` is a real function, so it *does* accept parameters, and the third argument (`true`) makes it transaction-local, equivalent to `SET LOCAL`.

**Read more:** <https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADMIN-SET>

### `SECURITY DEFINER` functions

**What:** A Postgres function that runs with the *privileges of whoever defined it*, not whoever calls it — the one deliberate, narrow way to bypass RLS or other restrictions for a specific, controlled operation.

**Why here:** Authenticating an API key means looking up `api_keys` by its hash *before* we know which tenant we're dealing with — but RLS on `api_keys` requires the tenant to already be known. Classic chicken-and-egg. `resolve_api_key()` is `SECURITY DEFINER`, so it runs as its (superuser) owner and can see the whole table, but it only *returns* `tenant_id`/`api_key_id`/`revoked_at` — never exposing more than that one lookup needs.

**Read more:** <https://www.postgresql.org/docs/current/sql-createfunction.html> (search "SECURITY DEFINER")

### Append-only tables + hash chaining (tamper evidence)

**What:** A table that only ever gets `INSERT`s (enforced via revoked `UPDATE`/`DELETE` grants), where each row stores a hash of its own content plus the previous row's hash (`prev_row_hash` → `row_hash`). Altering any historical row breaks every hash after it — the same idea Git and blockchains use for integrity, applied to a regular table.

**Why here:** "The audit log wasn't tampered with" needs to be provable to a regulator, not just asserted. A plain `UPDATE`-able table can't prove that; a hash chain can — you can independently walk it and verify nothing changed.

**Read more:** no single canonical doc — the general technique is sometimes called a "hash chain" or "tamper-evident log"; Certificate Transparency logs are a well-known real-world example: <https://certificate.transparency.dev/howctworks/>

### Docker Compose healthchecks (`--wait`)

**What:** `docker compose up -d --wait` blocks until every service with a `healthcheck:` reports `healthy`, and fails fast if one doesn't — instead of the default behavior, which just reports "container started" even if the process inside is still initializing.

**Why here:** We hit this directly — Temporal's container was "running" long before it was actually ready to accept connections. A healthcheck is a real prove-it-works check (in our case, `tctl cluster health`), not just "is the process alive."

**Read more:** <https://docs.docker.com/reference/compose-file/services/#healthcheck>

---

## Phase 3 concepts

### Presigned URLs (S3/MinIO)

**What:** A time-limited, cryptographically signed URL that grants permission to upload (or download) one specific object, without the client ever having real S3 credentials.

**Why here:** The intake API should never see raw ID-document bytes pass through it — that's more PII exposure surface than necessary. Instead, the client uploads *directly* to S3/MinIO using a URL we hand it; the signature (in the query string) is what authorizes the `PUT`, valid only for that exact key, for a limited time (15 minutes here).

**Read more:** <https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/generate_presigned_url.html>

### Idempotency keys

**What:** A client-supplied token that makes "do this request" safe to retry — the server recognizes a repeated key and returns the *original* result instead of doing the work twice.

**Why here:** Network retries are a certainty, not an edge case — a client's HTTP call can time out *after* our server already created the case, and the client has no way to know that. Without idempotency, its retry creates a duplicate. The constraint is `UNIQUE (tenant_id, idempotency_key)` — scoped per tenant deliberately, so two different clients can coincidentally use the same key string without colliding (this was one of our actual tests).

**Read more:** <https://docs.stripe.com/api/idempotent_requests> — Stripe's is the industry-reference implementation of this pattern.

### API-key hashing (why SHA-256, not bcrypt)

**What:** Storing `SHA-256(raw_key)` instead of the raw key, so a database leak doesn't hand out working credentials.

**Why here — and why not bcrypt/argon2:** Those slow, salted hashes exist specifically to defend against *low-entropy, human-chosen* passwords (where an attacker can feasibly guess candidates). An API key here is a 256-bit random token — brute-forcing it is infeasible regardless of hash speed, so a fast hash is the *correct* choice, not a shortcut. Using bcrypt on a high-entropy token doesn't add security, it just adds latency.

**Read more:** <https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html> (see the discussion of when slow hashing is/isn't appropriate)

### Rate limiting (fixed-window)

**What:** Counting requests per key within a rolling time window, rejecting with `429 Too Many Requests` (+ a `Retry-After` header telling the client when to try again) once a threshold is hit.

**Why here — and the known limitation:** Our implementation is in-process memory — correct for one running server, but each additional replica gets its *own* independent counter, so real enforced limits under multiple replicas are effectively `limit × replica count`. This is a documented, deliberate gap: real enforcement belongs at the infrastructure edge (API Gateway/load balancer), which is already the Phase 10 plan.

**Read more:** <https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429> · rate-limiting algorithms overview: <https://blog.cloudflare.com/counting-things-a-lot-of-different-things/>

### FastAPI dependency injection (`Depends`)

**What:** FastAPI's pattern for declaring "this endpoint needs X" (an authenticated tenant, a DB connection, etc.) as a function parameter with a default of `Depends(some_function)` — FastAPI calls `some_function` for you and injects the result.

**Why here:** `require_api_key` (auth) is a dependency every protected endpoint declares, rather than every endpoint hand-rolling its own header-parsing/lookup logic.

**The gotcha:** linters that don't know FastAPI (like ruff's bugbear rule `B008`) flag `Depends(...)` in a default argument as a bug — it *looks* like the classic Python "mutable default argument" mistake, but it's actually required by how FastAPI works. Fixed by allowlisting it, not disabling the whole rule.

**Read more:** <https://fastapi.tiangolo.com/tutorial/dependencies/>

### asyncpg connection pooling

**What:** A pool of reusable database connections (`asyncpg.create_pool`) instead of opening a new TCP connection per request — connections are expensive to establish, so the pool hands one out, gets it back when the request finishes, and reuses it for the next request.

**Why here:** Combined with RLS, this is exactly why `SET LOCAL`/`set_config` had to be *transaction-scoped*: a pooled connection previously used for Tenant A's request could be handed to Tenant B's request next — if the tenant context leaked across that reuse, that's a cross-tenant data breach. Transaction-scoped config resets automatically, making that impossible.

**Read more:** <https://magicstack.github.io/asyncpg/current/api/index.html#connection-pools>

### pytest-asyncio event loop scoping

**What:** Async tests need an event loop to run on. By default, pytest-asyncio gives *each test function its own loop* — but a database connection pool created once (in a session-scoped fixture) is tied to the loop it was created on.

**The bug we actually hit:** `RuntimeError: Task ... attached to a different loop` — the pool's loop and each test's loop didn't match. Fixed by forcing both fixtures and tests to share one session-scoped loop (`asyncio_default_fixture_loop_scope = "session"`).

**Read more:** <https://pytest-asyncio.readthedocs.io/en/latest/concepts.html#event-loops>

### Least-privilege DB roles (why the app can't `DELETE`)

**What:** `sentrilog_app` (the role the application connects as) was granted `SELECT`/`INSERT`/`UPDATE` on most tables — deliberately **no `DELETE`, anywhere**.

**Why here:** An app that can create case records but never delete them is part of the audit-integrity story — if the app itself can't delete, "someone deleted evidence" can only mean "someone used elevated/superuser access," which is a much smaller, more auditable set of people/processes. This surfaced as a real bug when our *test cleanup* tried to delete test data using the app's own role and got `permission denied` — the fix was a separate, explicitly-labeled admin connection for test teardown only, never for application code.

**Read more:** <https://www.postgresql.org/docs/current/ddl-priv.html> · principle of least privilege: <https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>

---

## Coming up in Phase 4 (preview — filled in properly once built)

- **Structured output from a VLM** — forcing a vision-language model to return JSON matching a fixed schema (a Pydantic model here), rather than free-text you have to parse and hope is well-formed.
- **Bounded retry with error-feedback prompting** — when the model's output fails schema validation, feeding the *validation error itself* back into the next attempt, rather than just repeating the same prompt and hoping for a different result.
- **Confidence scoring** — treating "how sure is this extraction" as a first-class output, not an afterthought, since it's what routes a case to auto-clear vs. human review.

---

## Coming up later (named now so you know to watch for them)

- **Temporal workflows/activities** (Phase 5) — durable execution, retry policies, signals for human-in-the-loop.
- **Vector similarity + phonetic matching** (Phase 6) — how "Mohammed" and "Muhammad" get recognized as the same name.
- **OpenTelemetry tracing** (Phase 9) — following one request across multiple services/processes.
- **Terraform modules & remote state** (Phase 10) — infrastructure as version-controlled code.

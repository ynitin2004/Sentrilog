# Sentrilog — KYC / AML Document Intelligence Pipeline — Project Plan

> **Project name:** Sentrilog
> **Repository:** <https://github.com/ynitin2004/Sentrilog>
> **License:** Skipped for Phase 1 (no LICENSE file — proprietary by default absent one; revisit before any external release).

## 1. Overview

**Hook:** Automated identity verification — OCR + a vision-language model extract and validate ID documents, match faces, and screen against sanctions lists with fuzzy matching, producing an auditable risk score.

**Why it matters:** Sits on the fintech × AI seam (Onfido, Persona, Sumsub). It's a multi-stage async pipeline where each stage can fail, retry, or escalate to a human — with a compliance-grade audit trail. This is a production system, not a prototype: every design choice below is made with regulatory auditability and operational resilience as first-class requirements, not afterthoughts. Like the vendors it's benchmarked against, it's built to serve **many client organizations**, each with their own users, cases, and data boundaries — not a single-tenant internal tool (see §7).

## 2. Hard parts (the actual engineering problems)

- **VLM extraction with structured output** — force a JSON schema out of a document image; validate + retry.
- **Fuzzy sanctions screening** — "Mohammed" vs "Muhammad": phonetic + vector similarity vs OFAC/UN, tuned for recall.
- **Human-in-the-loop** — low-confidence cases pause the pipeline and resume on the reviewer's decision.
- **Immutable audit trail** — every decision, model version, and input hash stored for regulators.
- **Multi-tenant isolation at scale** — many client organizations' data must never leak across tenant boundaries, under load, without hand-rolling it per query (§7).

## 3. Architecture

```
Client (tenant API key) → Intake API (FastAPI)
           ├─→ presigned PUT → Encrypted S3 (raw ID + selfie)
           └─→ start Temporal workflow (case_id, tenant_id)

Temporal Workflow "kyc_case"
  1. OCR (PaddleOCR/Textract) + VLM extract → structured JSON
  2. validate against Pydantic schema
       fail → retry (bounded) → still fail → route to review_queue (reason: extraction_failure)
  3. parallel:
       a. face_match(selfie, id_photo) → similarity score
       b. sanctions_screen(name, dob, nationality) → vector + phonetic hits
  4. risk_score = f(confidence, face_score, sanctions_hits)
  5. decision:
       clear        → APPROVED
       low_conf/hit → review_queue → wait_for_signal(reviewer_decision) → APPROVED/REJECTED
  6. write immutable audit record (every transition, not just the end state)
  7. notify: webhook delivery to the tenant's registered endpoint on decision
```

Face match and sanctions screening run **in parallel** — they're independent evidence sources feeding one risk score, and running them sequentially only adds latency. Every request is scoped to a `tenant_id` resolved from the API key at the edge, before anything touches the database (§7).

### Production stack

| Layer | Tech |
|---|---|
| Intake | FastAPI · encrypted S3 · presigned uploads · per-tenant API-key auth + rate limiting |
| Extract | PaddleOCR / Textract · VLM (structured output) · face embeddings |
| Screen | Vector DB (Qdrant) · phonetic match · OFAC / UN feeds |
| Orchestrate | Temporal · review queue · retry + escalate · per-tenant task-queue routing |
| Record | Postgres (append-only) · audit log · model versioning · row-level tenant isolation |

**Temporal over Celery:** native support for long-running workflows that pause for external signals (the reviewer decision), built-in per-activity retry policies, and full execution history that doubles as half the audit trail. Trade-off: an extra stateful system to run/pay for (Temporal Cloud vs. self-hosted) — worth it here given the compliance requirements.

## 4. Core data model

```sql
-- Tenancy & access (new — see §7; requires a migration on top of the Phase 2 schema, which predates this)
tenants(id, name, slug, plan_tier, status, created_at)
api_keys(id, tenant_id, key_hash, name, created_at, revoked_at)
reviewers(id, tenant_id, email, role[reviewer|admin|auditor], created_at)
webhooks(id, tenant_id, url, secret, created_at, disabled_at)
webhook_deliveries(id, webhook_id, case_id, event_type, payload, status[pending|delivered|failed], attempt_count, last_attempted_at)

-- Core pipeline (tenant_id now denormalized onto every table, not just cases --
-- required for Postgres row-level security policies to work without a join on every query)
cases(id, tenant_id, idempotency_key, status, created_at, subject_name, subject_dob, risk_score, decision, decided_at)
documents(id, tenant_id, case_id, s3_key, doc_type, sha256, uploaded_at)
extractions(id, tenant_id, case_id, document_id, model_version, raw_json, confidence, valid)
face_matches(id, tenant_id, case_id, similarity_score, model_version)
sanctions_hits(id, tenant_id, case_id, list_source, matched_name, match_score, method[vector|phonetic])
review_decisions(id, tenant_id, case_id, reviewer_id, decision, justification, decided_at)
audit_log(id, tenant_id, case_id, event_type, actor, model_version, input_hash, payload, prev_row_hash, row_hash, created_at)
-- audit_log is append-only: INSERT-only grants, hash-chained via prev_row_hash/row_hash for tamper evidence
-- cases: UNIQUE (tenant_id, idempotency_key) -- a client retrying a submission must not create a duplicate case
```

**This is a schema change on top of what Phase 2 already shipped** (which had no tenant concept). It needs to land as its own migration at the start of Phase 3, before the intake API writes a single row against the old shape — see the updated Phase 3 scope in §9.

## 5. Infrastructure (AWS — deferred to Phase 10)

| Concern | Choice | Why |
|---|---|---|
| Compute | ECS Fargate for API/workers; Temporal self-hosted on Fargate *or* Temporal Cloud | Fargate avoids node-patching ops burden; Temporal Cloud trades $ for removing a stateful system from on-call |
| Storage | S3 (SSE-KMS), separate buckets per data class | Blast-radius isolation |
| DB | RDS Postgres, Multi-AZ, encrypted, PITR | Needed both for recovery and "prove nothing was altered" audits |
| DB connection scaling | RDS Proxy or PgBouncer in front of RDS | Many tenants means many more concurrent short-lived connections than Postgres handles well without pooling |
| Read path | A read replica for audit/reporting queries | Keeps regulator/analyst queries off the primary write path so they can't slow down live case processing |
| Vector DB | Qdrant on ECS or Qdrant Cloud | Self-host fine at this scale; managed removes an op burden |
| Secrets | Secrets Manager + customer-managed KMS CMKs | Auditors ask about key ownership specifically |
| Networking | Private subnets, no public ingress except ALB, VPC endpoints for S3/KMS/Secrets Manager | Keeps PII traffic off the public internet internally |
| Edge / API layer | API Gateway or ALB + WAF, per-tenant/per-key rate limiting enforced at the edge | A single misbehaving or compromised client key shouldn't be able to degrade service for every other tenant |
| Autoscaling | Target-tracking on the intake API and Temporal worker pools (CPU / queue depth) | Client-driven traffic is bursty and not under our control the way internal batch jobs are |
| IAM | Per-service roles, least privilege | A face-match worker shouldn't read the audit table's KMS key |
| Observability | Per-tenant usage/latency dashboards (tagged by `tenant_id`) | A single degraded or noisy tenant gets averaged away in fleet-wide metrics otherwise |

IaC: Terraform, module-per-layer (`network/`, `data/`, `compute/`, `security/`), one state per environment, stricter apply gate (PR + plan artifact + manual approval) for `prod`.

## 6. Open decisions (need your call before relevant phase)

1. **PaddleOCR (self-hosted) vs Textract (managed)** — cost/ops vs. speed-to-ship.
2. **Temporal self-hosted vs Temporal Cloud** — ops burden vs. cost.
3. **Data retention period** — regulatory/jurisdictional, not engineering; drives S3 lifecycle policy.
4. **Face match model** — Rekognition (managed, data leaves VPC) vs. self-hosted ArcFace/InsightFace (full control, more MLOps).
5. **Client auth mechanism** — simple per-tenant API keys (fast to ship) vs. OAuth2 client-credentials (more standard for enterprise clients, better rotation/revocation semantics). Affects Phase 3.
6. **Reviewer authentication** — plain username/password vs. SSO (Okta/Azure AD/etc.) — depends on whether reviewers are your own staff, each client's staff, or both. Affects Phase 7.
7. **Plan tiers & rate limits** — flat throughput for every tenant, or tiered quotas (e.g. free/pro/enterprise) from day one? Cheap to decide now, awkward to retrofit onto live client integrations later.
8. **Data residency** — does any prospective client require EU-only or single-region data storage? Determines whether Phase 10 is single-region AWS or needs a per-region deployment topology.

## 7. Multi-tenant & multi-user product requirements

The original design proved the hard technical problems (VLM extraction, fuzzy screening, human-in-the-loop, audit trail) for a single case in isolation. Turning that into a product that many client organizations can actually use concurrently adds a second, mostly orthogonal set of requirements. Called out here as its own section — like the audit trail, this is far cheaper to build in now than to retrofit once real tenant data exists.

- **Tenancy model:** shared database, `tenant_id` denormalized onto every table (not just joined through `cases`), enforced by **Postgres row-level security policies** as defense-in-depth on top of app-layer scoping. No schema-per-tenant or sharding — premature at this stage, and a shared-DB-with-RLS model is what every table in §4 is already designed around. Revisit only if a specific client's data-residency requirement (§6.8) forces isolation a shared DB can't provide.
- **Client authentication:** per-tenant API keys, hashed at rest (`api_keys.key_hash`), never logged or returned after creation. Every request resolves to a `tenant_id` before touching any other table.
- **Idempotency:** a client-supplied `Idempotency-Key` (or auto-generated equivalent) on case creation, enforced via `UNIQUE (tenant_id, idempotency_key)`. Network retries from a client's integration are a certainty, not an edge case, and must not create duplicate cases.
- **Rate limiting & quotas:** per-tenant, enforced both in-app (Phase 3) and at the infrastructure edge (Phase 10, §5) — app-layer alone can't protect against a client that overwhelms the load balancer before a request ever reaches application code.
- **Noisy-neighbor isolation:** Temporal task-queue routing keyed by tenant/plan tier (Phase 5), so one tenant's case backlog can't starve another's SLA. Verified under load in Phase 9, not assumed from the design.
- **Reviewer access control:** reviewers belong to a tenant with a role (`reviewer`, `admin`, `auditor`); `review_decisions.reviewer_id` is a real foreign key, not free text. A reviewer must never be able to list or decide another tenant's cases — this gets an explicit authorization test, not just a schema constraint (Phase 9).
- **Reviewer UI:** a minimal web console (list/claim/decide the queue), not just an API. Without it, "many reviewers across many client organizations" isn't actually usable — this was previously scoped out of Phase 7 as "a separate concern"; it isn't anymore. Kept deliberately small (Phase 7): list, claim, decide, done — a fuller console is a later iteration, not a Phase 7 blocker.
- **Client notifications:** webhook delivery on case decision (`webhooks` + `webhook_deliveries`), with retry and a recorded failure state — an async, potentially multi-day pipeline is unusable for integrators if the only way to know a case resolved is to poll.
- **Observability at the tenant level:** metrics and dashboards taggable by `tenant_id` (Phase 10), so a single degraded or abusive tenant is visible instead of averaged into fleet-wide numbers.

**Deliberately out of scope for now** (would be over-engineering ahead of an actual need): schema-per-tenant/sharded Postgres, per-tenant configurable risk-scoring thresholds, a full-featured reviewer console (bulk actions, saved views, SLA reporting UI), and SSO — none of these are required to prove the product works for multiple tenants, and adding them now would be designing for hypothetical requirements rather than the ones in front of us. Revisit if a specific client asks.

## 8. Project naming shortlist

| Name | Rationale |
|---|---|
| **Custos** | Latin "guardian" — short, evokes the gatekeeper role of KYC |
| **Veridex** | Veri(ty) + index — verification + searchable audit record |
| **Attestly** | "Attest" is literal compliance vocabulary |
| **Provenance** | Names exactly what the audit trail is |
| **Sentrilog** | Sentry + log — leans into the immutable-trail angle |
| **ClearChain** | "Clear" (risk-cleared) + "chain" (hash-chained audit log) |

**Resolved:** **Sentrilog** (confirmed 2026-07-20, Phase 1).

## 9. Delivery plan — 10 phases

Phases 1-9 build and prove the system locally (Docker Compose standing in for S3/RDS/Qdrant). AWS is touched only in Phase 10, so cloud debugging and logic debugging never happen at the same time.

**Workflow convention for every phase:**
- Branch per phase: `phase-N-<short-name>`, PR into `main`.
- Merge only when the phase's exit criteria **and** the testing standard below both pass.
- Tag `main` after merge: `git tag v0.<N>.0 && git push --tags`.
- Commit convention: `feat(phaseN): <what>` for the main commit, `fix/chore/test(phaseN): ...` for follow-ups.
- Update this file's **Status** table and **Changelog** (§11) as the last commit of the phase.

**Testing standard (applies to every phase, not just the ones with obvious business logic):**
A phase is not "done" on happy-path-works. Before merging, verify at the level expected of a senior engineer on a compliance-grade system:

- **Negative and edge cases**, not just the golden path — malformed input, empty/oversized payloads, network/service unavailability, concurrent access where relevant.
- **Failure-mode tests specific to this system**: retry exhaustion, partial writes, worker/process death mid-operation, replay/idempotency where a case could be processed twice.
- **Regression check** — re-run the previous phase's test suite, not just the new phase's; nothing earlier should silently break.
- **Static checks green**: lint (`ruff`), formatting (`black --check`), types (`mypy`), and `pre-commit run --all-files` all pass with zero suppressions added to make them pass.
- **Test evidence recorded**: what was tested and the result gets a short note in the Changelog entry for that phase — not just "tests pass," but *which* scenarios were exercised, so a reviewer six months from now can tell what's actually covered.
- Anything skipped (e.g., load testing deferred to Phase 9) is stated explicitly as a known gap, not silently omitted.

### Phase 1 — Repo & project scaffolding
- `git init`, connect remote to `github.com/ynitin2004/Sentrilog`, `.gitignore`, `README.md` (problem statement + architecture diagram). No `LICENSE` file for now (decision: skip, revisit later).
- Layout: `services/intake/`, `services/pipeline/`, `services/screening/`, `infra/terraform/`, `docs/`.
- Tooling: `uv`, `ruff` + `black` + `mypy`, pre-commit hooks.
- CI skeleton: GitHub Actions — lint + no-op test on every PR.
- **Exit criteria:** `pre-commit run --all-files` and CI both pass on the empty scaffold.
- **Phase 1 testing note:** no business logic exists yet, so "testing" means proving the scaffold itself is trustworthy — pre-commit hooks actually catch a deliberately introduced lint error, CI runs on a real PR (not just locally), and the repo structure/tooling versions are pinned (not "latest") so the build is reproducible.

### Phase 2 — Local infra (Docker Compose)
- `docker-compose.yml`: Postgres, Qdrant, MinIO, Temporal dev server (+ its Postgres/Elasticsearch).
- `make up` / `make down`; seed script for the Postgres schema.
- **Exit criteria:** `make up` brings up all services healthy; empty tables visible via `psql`.
- **Amendment required before Phase 3 starts:** this schema predates the multi-tenancy design in §4/§7 — it has no `tenant_id` anywhere. A follow-up migration (`002_multi_tenancy.sql`) adding `tenants`, `api_keys`, `webhooks`, `webhook_deliveries`, and `tenant_id` on every existing table must land first. Calling this out explicitly rather than silently carrying a single-tenant schema into a multi-tenant product.

### Phase 3 — Intake API *(scope updated for multi-tenancy — see §7)*
- Run the Phase 2 schema amendment above first.
- FastAPI: `POST /cases` → case row + presigned PUT URLs for ID + selfie, scoped to the authenticated tenant.
- Per-tenant API-key authentication: hashed at rest, resolved to a `tenant_id` before any other table is touched.
- Idempotency: `Idempotency-Key` support backed by `UNIQUE (tenant_id, idempotency_key)` — a retried request must not create a duplicate case.
- Per-tenant rate limiting middleware (429 + `Retry-After` on breach).
- File validation: content-type/size checks, with a malware-scan hook defined even if its implementation is a stub for now — the interface needs to exist before real client-uploaded files flow through it.
- Encryption on MinIO (or explicitly note deferral to real KMS in Phase 10).
- Integration tests: upload flow succeeds and is correctly tenant-scoped; a duplicate idempotency key does not create a second case; tenant A's API key cannot read or reference tenant B's case.
- **Exit criteria:** curl/Postman flow uploads a file with a valid API key; case row has the correct S3 key and `tenant_id`; a repeated request with the same idempotency key is a no-op; cross-tenant access is proven to fail, not just assumed to.

### Phase 4 — Extraction (OCR + VLM structured output)
- Pydantic `IDDocument` schema.
- OCR pass (PaddleOCR to start) + schema-constrained VLM call.
- Bounded retry (2-3 tries) with validation-error injected into the retry prompt.
- Confidence score attached to output.
- **Exit criteria:** unit tests cover valid extraction, malformed-image retry path, and exhausted-retries → `needs_review` flag.

### Phase 5 — Temporal workflow wiring *(scope updated for multi-tenancy — see §7)*
- `kyc_case` workflow calls extraction as a Temporal activity; retry policy at the Temporal level.
- Design (not yet load-tested — that's Phase 9) task-queue routing keyed by tenant/plan tier, so the mechanism for preventing one tenant's backlog from starving another's is in place from the start rather than bolted on after a real incident.
- **Exit criteria:** kill the worker mid-run, restart it, workflow resumes without losing progress.

### Phase 6 — Face match + sanctions screening (parallel)
- Face match activity: embedding similarity between selfie and ID photo.
- Sanctions screening activity: sample OFAC/UN list into Qdrant; vector + phonetic match, including "Mohammed/Muhammad" test case.
- **Exit criteria:** Temporal UI timeline shows both activities executing concurrently, not sequentially.

### Phase 7 — Risk scoring + human review queue *(scope updated for multi-tenancy — see §7)*
- Risk scoring combining confidence + face score + sanctions hits.
- `review_queue` table + minimal API (list pending, submit decision), scoped per tenant.
- Reviewer accounts (`reviewers` table) with roles (`reviewer`/`admin`/`auditor`); `review_decisions.reviewer_id` is a real foreign key.
- A minimal reviewer web UI — list, claim, decide. Deliberately small scope; not a full console (see §7 for what's explicitly deferred).
- Webhook delivery to the tenant's registered endpoint on decision, with retry and a `webhook_deliveries` record of the outcome (delivered/failed), so integrators aren't reduced to polling.
- Workflow signal handler unblocks on reviewer submission.
- **Exit criteria:** an ambiguous test case parks in the queue, a reviewer decides it via the UI, the workflow completes, and a webhook delivery is recorded; a reviewer from a different tenant cannot see or act on the case.

### Phase 8 — Immutable audit trail
- `audit_log` with `prev_row_hash`/`row_hash` chaining; `INSERT`-only DB grants.
- Retrofit: every prior-phase activity now writes audit rows on entry/exit.
- Verification script walks the hash chain to detect tampering.
- **Exit criteria:** manually editing a historical row breaks the chain-verification script.

### Phase 9 — Hardening: security, observability, tests *(scope updated for multi-tenancy — see §7)*
- Structured logging + OpenTelemetry tracing.
- Load test the extraction stage specifically (bottleneck/cost center).
- **Noisy-neighbor load test:** one tenant submitting a heavy burst of cases must not blow another tenant's SLA — validates the Phase 5 task-queue-routing design under real load, not just in theory.
- **Multi-tenant authorization test matrix:** tenant A's API key and reviewer accounts must never read or write tenant B's data — exercised directly (attempted cross-tenant reads/writes that must fail), not inferred from the schema.
- **Rate-limit test:** confirm 429s trigger at the configured threshold and recover correctly once the window clears.
- Chaos test: kill workers / Qdrant / Postgres mid-workflow, confirm recovery.
- Secrets moved out of `.env` into a pattern mirroring Secrets Manager.
- **Exit criteria:** documented, tested runbooks for "worker died mid-case" and "Qdrant unavailable"; the noisy-neighbor and cross-tenant-access tests both pass with recorded evidence.

### Phase 10 — AWS infra + deploy *(scope updated for multi-tenancy — see §5)*
- Terraform modules: network, RDS (+ RDS Proxy), S3 (KMS), Qdrant, Temporal per decisions made.
- API Gateway or ALB + WAF in front of the intake API, with per-tenant/per-key rate limiting enforced at the edge.
- Autoscaling policies for the intake API and Temporal worker pools (CPU/queue-depth driven).
- Per-tenant usage/latency dashboards.
- GitHub Actions: build → push → `terraform plan` on PR → manual-approval `apply`.
- Staging first, smoke-tested end-to-end (including a two-tenant isolation smoke test), then prod.
- **Exit criteria:** a real case flows through staging in AWS, audit chain verifies, edge rate limiting is confirmed working, rollback plan documented before prod go-live.

## 10. Status

| Phase | Status | Tag | Commit | Date |
|---|---|---|---|---|
| 1. Repo & scaffolding | **Done** | `v0.1.0` | `5dc28ed` | 2026-07-20 |
| 2. Local infra | **Done** (schema amendment pending — see §9) | `v0.2.0` | `74c0014` | 2026-07-20 |
| 3. Intake API | Not started | — | — | — |
| 4. Extraction | Not started | — | — | — |
| 5. Temporal wiring | Not started | — | — | — |
| 6. Face match + screening | Not started | — | — | — |
| 7. Risk scoring + review queue | Not started | — | — | — |
| 8. Audit trail | Not started | — | — | — |
| 9. Hardening | Not started | — | — | — |
| 10. AWS infra + deploy | Not started | — | — | — |

## 11. Changelog

### Phase 1 — 2026-07-20 (`v0.1.0`, commit `5dc28ed`)

**Done:**

- Repo initialized and connected to <https://github.com/ynitin2004/Sentrilog> (`main` branch).
- Layout scaffolded: `services/{intake,pipeline,screening}`, `infra/terraform/`, `docs/`, `tests/`.
- Python tooling: `uv`-managed `pyproject.toml`, `ruff`, `black`, `mypy` (strict), `pytest`, pre-commit hooks.
- GitHub Actions CI (`.github/workflows/ci.yml`): installs `uv`, runs ruff/black/mypy/pytest on every push and PR to `main`.
- README.md written with architecture, stack table, and repo layout.
- No `LICENSE` file added (explicit decision — proprietary by default, revisit before any external release).

**Testing evidence (senior-engineer standard, per §9):**

- `uv run ruff check .`, `uv run black --check .`, `uv run mypy services tests`, `uv run pytest -v` all run clean locally against the full scaffold.
- Pre-commit hooks were **proven, not assumed**: a throwaway file with an unused variable and a missing type annotation was deliberately added, confirmed that `ruff` flagged the unused variable (F841) and auto-fixed an unused import, and `mypy` flagged the missing annotation — then the probe file was deleted and never staged/committed.
- `pre-commit run --all-files` passes clean on the real, committed file set.
- **Known gap (explicit, not silent):** the first two pushes to `main` (`5dc28ed`, `a94443f`) both show CI as `failure` on GitHub, but not for a code/workflow reason — the GitHub API reports *"The job was not started because your account is locked due to a billing issue."* The runner never started; nothing in `ci.yml` has been validated on GitHub's infra yet. **Action item (owner: repo owner, not engineering):** resolve the GitHub account billing lock, then re-push (or re-run) to get a real CI signal before Phase 2 work lands.

### Phase 2 — 2026-07-20 (`v0.2.0`, commit `74c0014`)

**Done:**

- `docker-compose.yml`: Postgres 16, Qdrant, MinIO (+ a one-shot `minio-init` job that creates and versions the `sentrilog-documents` bucket), Temporal (`auto-setup`, sharing the Postgres instance via its own `temporal`/`temporal_visibility` databases), and Temporal UI — added ahead of schedule since Phase 5 needs it and it's free to include now.
- `infra/docker/postgres-init/001_schema.sql`: all 7 core tables from the data model (§4), with a schema refinement — added a `row_hash` column to `audit_log` alongside `prev_row_hash`, since a hash chain requires each row to store its own computed hash for the next row to reference; the original sketch only listed `prev_row_hash`, which wasn't actually enough to build a chain from.
- Status/method/decision fields use `TEXT` + `CHECK` constraints rather than native Postgres `ENUM` types, to avoid `ALTER TYPE` friction while the schema is still moving pre-Phase 8.
- `Makefile` with `up`/`down`/`reset`/`ps`/`logs`/`psql` targets. `up` uses `docker compose up -d --wait`, which blocks and fails fast rather than reporting false-positive success on a container that's merely "running" but not yet healthy.

**Testing evidence (senior-engineer standard, per §9):**

- **A real bug was found and fixed, not just checked for:** the initial Temporal healthcheck (`/dev/tcp/localhost/7233`) failed every time — `docker inspect` showed `Connection refused`. Root cause, confirmed by exec'ing into the container and inspecting `netstat`: the `auto-setup` image binds its gRPC frontend to the container's own hostname/IP (e.g. `172.19.0.6:7233`), never to loopback. Fixed by healthchecking via `tctl --address "$(hostname):7233" cluster health` instead of a raw `/dev/tcp` probe against `localhost` — this also has the advantage of checking the actual gRPC service is serving, not just that a TCP port is open.
- **Persistence, verified, not assumed:** inserted a probe row into `cases`, ran `docker compose restart postgres`, confirmed the row survived (named volume, not container-ephemeral storage) — then deleted the probe row before finalizing.
- **Full-reset path exercised:** `docker compose down -v` (destroys all named volumes) followed by `docker compose up -d --wait` reliably re-runs `postgres-init` from scratch — confirmed via `\dt` (all 7 tables present) and `SELECT count(*) FROM cases` (0 rows) on the fresh instance, and all 5 containers reported `healthy`/running again without manual intervention.
- **Regression check:** re-ran the full Phase 1 suite (`ruff`, `black --check`, `mypy`, `pytest`) after all Phase 2 changes — still clean; Phase 2 touched no Python code, so this mainly confirms nothing in the working tree drifted.
- Qdrant confirmed reachable (`GET /collections` → empty list, expected pre-Phase 6) and the MinIO bucket confirmed created and listable via `mc ls`.
- **Known gap (explicit, not silent):** `make up`/`make down` etc. were *not* run via `make` itself — this Windows dev machine has no `make` installed. Validated instead by running the underlying `docker compose` commands directly; the Makefile targets are a straight passthrough so this is low-risk, but genuinely untested with `make` as the entry point. Flag if `make` becomes available and this should be re-verified.
- **Workflow-convention deviation (explicit):** work was done on branch `phase-2-local-infra` per §9's convention, but merged directly into `main` via `git merge` + push rather than a GitHub PR — no `gh`/API auth is configured in this environment, matching how Phase 1 was actually committed (straight to `main`). If real PR review is wanted going forward, we need `gh auth login` or a token set up first.
- **Retroactive scope note (2026-07-20, this update):** the schema shipped in this phase has no `tenant_id`/multi-tenancy support. That gap is now tracked explicitly in §4/§7/§9 as a required migration at the start of Phase 3, rather than silently left for someone to discover later.

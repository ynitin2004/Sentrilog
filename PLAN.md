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
- **Amendment landed (`002_multi_tenancy.sql`, `v0.2.1`):** the original schema predated the multi-tenancy design in §4/§7 and had no `tenant_id` anywhere. `tenants`, `api_keys`, `reviewers`, `webhooks`, `webhook_deliveries` were added, `tenant_id` was denormalized onto every existing table, an idempotency-key uniqueness constraint was added to `cases`, `review_decisions.reviewer_id` became a real FK, and Postgres row-level security was enabled and **proven** (not just enabled) via a dedicated non-superuser `sentrilog_app` role — see the Changelog entry for the full test evidence, including the cross-tenant write that was actually rejected by the policy.

### Phase 3 — Intake API *(scope updated for multi-tenancy — see §7)*
- Multi-tenancy schema amendment already landed in Phase 2 (`002_multi_tenancy.sql`) — Phase 3 builds on it rather than needing to run it.
- FastAPI: `POST /cases` → case row + presigned PUT URLs for ID + selfie, scoped to the authenticated tenant.
- Per-tenant API-key authentication: hashed at rest, resolved to a `tenant_id` before any other table is touched.
- Idempotency: `Idempotency-Key` support backed by `UNIQUE (tenant_id, idempotency_key)` — a retried request must not create a duplicate case.
- Per-tenant rate limiting middleware (429 + `Retry-After` on breach).
- File validation: content-type/size checks, with a malware-scan hook defined even if its implementation is a stub for now — the interface needs to exist before real client-uploaded files flow through it.
- Encryption on MinIO (or explicitly note deferral to real KMS in Phase 10).
- Integration tests: upload flow succeeds and is correctly tenant-scoped; a duplicate idempotency key does not create a second case; tenant A's API key cannot read or reference tenant B's case.
- **Exit criteria:** curl/Postman flow uploads a file with a valid API key; case row has the correct S3 key and `tenant_id`; a repeated request with the same idempotency key is a no-op; cross-tenant access is proven to fail, not just assumed to.

### Phase 4 — Extraction (OCR + VLM structured output) *(open decisions resolved — see §11)*
- Pydantic `IDDocument` schema.
- Deterministic MRZ (ICAO 9303) fast path for passports/national IDs that have one — free, checksum-validated, preferred over the VLM whenever available.
- Schema-constrained VLM call (Gemini, free tier for now — swappable, see §11) as the fallback for documents without an MRZ.
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
| 2. Local infra | **Done** (incl. multi-tenancy amendment) | `v0.2.1` | `5bdbcfc` | 2026-08-04 |
| 3. Intake API | **Done** | `v0.3.0` | `01a1c0d` | 2026-08-04 |
| 4. Extraction | **Done** | `v0.4.0` | `ed00d1c` | 2026-08-06 |
| 5. Temporal wiring | **Done** | `v0.5.0` | `b904547` | 2026-08-11 |
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

### Phase 2 amendment — 2026-08-04 (`v0.2.1`, commit `5bdbcfc`)

Closes the multi-tenancy gap flagged above, before any Phase 3 code depends on the old single-tenant shape.

**Done:**

- `infra/docker/postgres-init/002_multi_tenancy.sql`: adds `tenants`, `api_keys`, `reviewers`, `webhooks`, `webhook_deliveries`; denormalizes `tenant_id` onto every existing table; adds `UNIQUE (tenant_id, idempotency_key)` on `cases`; converts `review_decisions.reviewer_id` from free-text to a real FK into `reviewers`.
- Enables Postgres row-level security on every tenant-scoped table, `FORCE`d so table owners don't silently bypass it.
- **Design correction found while implementing this, not before:** `POSTGRES_USER` (`sentrilog`) is a superuser, and RLS is *always* bypassed for superusers and table owners regardless of `FORCE ROW LEVEL SECURITY` — no override exists. That means the app can never connect as `sentrilog` and have RLS do anything. Added a separate, unprivileged `sentrilog_app` role with explicit per-table grants (`SELECT`/`INSERT`/`UPDATE`; `audit_log` gets `INSERT` only, foreshadowing the full lockdown in Phase 8) for the application to connect as instead.
- Both migration files (`001`, `002`) now run together in order on any fresh volume via `docker-entrypoint-initdb.d`'s filename-ordered execution — no separate manual step for new environments.

**Testing evidence (senior-engineer standard, per §9) — this is the part that actually matters for a security control:**

- **RLS proven with real cross-tenant attempts, not just "enabled and trusted":** connected as `sentrilog_app` (not the superuser), created two tenants, inserted a case for Tenant A, confirmed Tenant A's session sees it (`count = 1`) and Tenant B's session does not (`count = 0`) — then attempted an `INSERT` from a Tenant-B session tagged with Tenant A's `tenant_id`, and confirmed Postgres rejected it: `ERROR: new row violates row-level security policy for table "cases"`.
- **Fail-closed verified:** a `sentrilog_app` session that never sets `app.tenant_id` at all sees zero rows, not every tenant's data — confirmed directly, not inferred from the policy definition.
- **Idempotency constraint tested both directions:** two inserts with the same `(tenant_id, idempotency_key)` — second one correctly rejected (`duplicate key value violates unique constraint`); two inserts with `NULL` idempotency keys — both correctly succeeded (Postgres treats `NULL`s as distinct in a unique constraint), so internally-created cases without a client-supplied key aren't blocked by the same rule.
- **`reviewer_id` FK tested both directions:** an insert referencing a nonexistent reviewer was correctly rejected (`violates foreign key constraint`); after creating a real `reviewers` row, the same insert succeeded.
- **Full fresh-volume regression:** `docker compose down -v` followed by `up -d --wait` confirmed all 12 tables (7 original + 5 new) exist automatically from a cold start, `tenants`/`cases` are empty as expected, and the `sentrilog_app` role + RLS policies are present and functional without any manual step — proving `002` genuinely composes with `001` via `docker-entrypoint-initdb.d`'s ordering, not just that it worked when applied by hand once.
- **Regression check:** re-ran the full Phase 1 suite (`ruff`, `black --check`, `mypy`, `pytest`) after this change — still clean.
- **Attempted to close the Phase 2 "`make` not installed" gap:** `choco install make -y` was run to try to close it properly. It failed on `UnauthorizedAccessException` writing to `C:\ProgramData\chocolatey\lib-bad` — this shell doesn't have the admin rights Chocolatey needs. **Known gap, now root-caused instead of just noted:** installing `make` on this machine requires an elevated (admin) shell, which isn't available here. Still validated via the underlying `docker compose`/`psql` commands directly.
- Test tenants/cases/reviewers created during RLS testing were wiped by the fresh-volume regression step above, not left behind as stray data.

### Phase 3 — 2026-08-04 (`v0.3.0`, commit `01a1c0d`)

**Done:**

- `services/intake/`: FastAPI app (`main.py`) with `POST /cases` and `GET /cases/{id}`; `config.py` (pydantic-settings, reads `.env`); `db.py` (asyncpg pool, tenant-scoped connections); `storage.py` (MinIO/S3 presigned PUT URLs via boto3); `auth.py` (API-key resolution); `ratelimit.py` (in-memory fixed-window limiter); `scanning.py` (upload validation + malware-scan stub); `schemas.py` (request/response models).
- `.env.example` + local `.env` (gitignored) — the project's first actual use of environment-based config; credentials mirror `docker-compose.yml`'s existing dev-only values.
- `scripts/seed_dev_tenant.py`: bootstraps a tenant + API key for local testing (no admin API exists — deliberately out of scope per §7).
- `POST /cases` returns presigned upload URLs for `id_document` and `selfie`, namespaced under the authenticated tenant's own ID (`{tenant_id}/{case_id}/{doc_type}`) — a client can never influence where its own or another tenant's files land.
- `Idempotency-Key` support: replays return the original case, and the constraint is correctly scoped `(tenant_id, idempotency_key)` — the same key from two different tenants creates two independent cases.
- Cross-tenant reads return `404`, not `403` — proven, not just intended: a case genuinely invisible to another tenant doesn't confirm to them that it exists.

**Real bugs found and fixed while implementing this (not found later, not left in):**

1. **RLS chicken-and-egg on `api_keys`:** resolving an API key to its tenant is exactly the step that has to happen *before* `app.tenant_id` is known, but `api_keys` has RLS requiring it. Fixed with a narrow `SECURITY DEFINER` function, `resolve_api_key()` (migration `003_auth_lookup.sql`) — it exposes only the one lookup the app needs, not blanket access to the table.
2. **`documents.sha256 NOT NULL` was incompatible with presigned uploads:** the API creates the document row (to hand out a presigned URL tied to a specific document id) before the file exists in S3, so it cannot know the hash yet. Made nullable (migration `004_documents_sha256_nullable.sql`); real hash computation/verification is a Phase 4/5 concern once something actually reads the object.
3. **`SET LOCAL app.tenant_id = $1` doesn't work — Postgres doesn't accept bind parameters for `SET`/`SET LOCAL`, only literals.** This was in the core RLS-enforcing code path (`db.tenant_connection`) and had gone unnoticed because earlier Phase 2 testing used literal values typed directly into `psql`, never a real parameterized query. Caught immediately when the dev-seed script hit `PostgresSyntaxError: syntax error at or near "$1"`. Fixed by switching to `SELECT set_config('app.tenant_id', $1, true)`, which is a real function and does accept parameters — also closes what would otherwise have been a SQL-injection path on the one value RLS depends on.
4. **pytest-asyncio: session-scoped DB pool vs. function-scoped test event loops.** The connection pool (created once per test session) and each test's default per-test event loop didn't match, surfacing as `RuntimeError: Task ... attached to a different loop`. Fixed with `asyncio_default_fixture_loop_scope = "session"` and `asyncio_default_test_loop_scope = "session"`.
5. **Test cleanup hit `permission denied for table documents`:** `sentrilog_app` deliberately has no `DELETE` grant on any table (Phase 2 design — the app can create cases but never delete them, which is part of the audit-integrity story, not an oversight). Test teardown needs a separate, explicitly-marked admin connection; production code must never use it. **This bug briefly leaked 24 test tenants into the dev database** before the fix — caught by actually checking row counts after a "passing" test run, not by trusting green tests alone, and cleaned up by hand before moving on.
6. Minor: ruff's `B008` flagged FastAPI's required `Depends()`-in-default idiom as a bug; allowlisted via `extend-immutable-calls` rather than disabling the rule. mypy's "source file found twice" needed a missing `services/__init__.py`. mypy's untyped-boto3-client warning was fixed by using `mypy_boto3_s3.client.S3Client` properly instead of suppressing it.

**Testing evidence (senior-engineer standard, per §9):**

- **Manual smoke test against the real running service first** (8 scenarios via curl, before any automated test existed): case creation, idempotent replay, a real file `PUT` to the presigned URL followed by confirming the object in MinIO via `mc ls`, owner access (200), cross-tenant access (404), missing auth (401), invalid key (401), invalid content-type (422).
- **Rate limiting proven under real load, not configured and assumed:** 65 rapid requests against a 60/60s limit returned `200` exactly 60 times then `429` for the remaining 5, with a `Retry-After` header present.
- **14 automated integration tests** (`tests/intake/test_cases.py`) against the live Postgres/MinIO stack (no mocking) covering: tenant-scoped upload targets, idempotency replay, idempotency scoped per-tenant (not global — the one test that would catch a regression to a global uniqueness constraint), real presigned-URL upload, cross-tenant 404, missing/invalid auth, malformed case IDs including a SQL-injection-shaped string (never 500s), oversized/invalid-content-type rejection, and the rate limiter.
- **Regression check:** Phase 1 scaffold test and all Phase 1/2 static checks (`ruff`, `black --check`, `mypy`, `pre-commit run --all-files`) re-run clean after all Phase 3 changes.
- **Known gap (explicit, not silent):** the rate limiter is in-process/in-memory — correct for one uvicorn worker, but each additional replica gets an independent counter, so real enforcement doesn't hold under multi-replica deployment. Documented in `ratelimit.py`; the actual fix is edge-level rate limiting in Phase 10 (§5), which was already the plan before this was ever a gap.
- **Known gap (explicit, not silent):** malware scanning (`scanning.scan_for_malware`) is a stub that always returns clean — the call site and pipeline position exist now, per Phase 3 scope, but no real scanner is wired up yet.

### Phase 4 — 2026-08-06 (`v0.4.0`, commit `ed00d1c`)

**Open decisions resolved:** VLM provider is **Gemini** (`gemini-2.5-flash`, free tier for development), behind a swappable `VLMClient` interface so switching providers later (e.g. GPT-4o) is a new adapter class, not a rewrite. OCR/MRZ decision: a deterministic **ICAO 9303 MRZ parser** (free, checksum-validated) is tried first for passports/national IDs that have one; the VLM is the fallback for everything else, rather than OCR text being piped through an LLM to structure it. Face-match model choice (§6.4) is still open, for Phase 6.

**Done:**

- `services/pipeline/extraction/schemas.py`: `IDDocument`, and `VLMExtractionResponse` (see below).
- `services/pipeline/extraction/mrz.py`: full ICAO 9303 TD3 (passport) MRZ parser — check-digit algorithm, per-field and composite checksum validation, name/date/document-number extraction. Free, deterministic, no model call.
- `services/pipeline/extraction/vlm.py`: `VLMClient` protocol + `GeminiVLMClient`, using Gemini's native Structured Outputs (`response_schema`) rather than parsing free-text.
- `services/pipeline/extraction/extract.py`: orchestration — MRZ first (short-circuits the VLM entirely when valid), VLM fallback with bounded retry and validation-error-injected re-prompting, confidence scoring (first-try clean extraction scores highest), `needs_review` on exhaustion.
- `scripts/smoke_test_gemini_extraction.py`: one-off manual proof against the *real* Gemini API using a synthetically drawn image (fabricated fields, never real ID data) — deliberately not part of the automated suite, since it costs real API quota and isn't deterministic.

**Real bugs found and fixed while implementing this — the most consequential phase yet for this pattern:**

1. **Postgres `SET`-parameter mistake's sibling, this time in Python packaging:** numpy's own type stubs (pulled in transitively via `easyocr`) use PEP 695 `type` statement syntax, which mypy refuses to parse below Python 3.12. Not a bug in our code, but it forced a real decision: bumped `requires-python`, ruff/black `target-version`, mypy's `python_version`, and the CI Python install all to 3.12, consistently, rather than silencing the error.
2. **A VLM asked for a strict schema will hallucinate rather than admit failure — confirmed against the live API, not assumed.** Given a blank test image: first attempt returned `full_name=''`, `document_number=''`, and a suspicious `1970-01-01` default date — schema-valid, so it was silently accepted as a successful extraction at 0.85 confidence. Adding `min_length=1` closed that hole; the model's response to *that* was to return the literal string `"NOT_AVAILABLE"` in every field, which also trivially passes a non-empty check. There is no bounded set of placeholder strings to defend against. The actual fix: `VLMExtractionResponse` wraps `IDDocument` with a `document_visible: bool` field, giving the model an explicit, honest way to say "I can't read this" — `extract_with_vlm` now treats `document_visible=False` as a failed attempt (retry, then `needs_review`), not a valid empty result. Re-verified against the real API after the fix: the same blank image now correctly exhausts retries into `needs_review=True`, `document=None`.
3. **`truststore.inject_into_ssl()` patches `ssl.SSLContext` process-wide, not just for Gemini.** This was needed because a corporate TLS-inspecting proxy (Kaspersky) injects its own root certificate that certifi's bundled CA list doesn't trust — confirmed by testing a plain HTTPS request to google.com, which failed identically, before touching any Gemini-specific code. The global patch fixed Gemini but broke boto3/botocore's S3 client construction elsewhere in the *same test run* with a `RecursionError` in unrelated SSL setup — caught by running the full suite, not just the new tests. Fixed by scoping the trust-store behavior to a `truststore.SSLContext` passed into `GeminiVLMClient`'s own `httpx.Client` via `HttpOptions`, leaving the global `ssl` module — and every other component's TLS handling — untouched.
4. Minor: `google-genai`'s `contents` parameter type is a deeply nested Union of Lists that mypy can't resolve a mixed `[Part, str]` literal against, due to list invariance — a real SDK type-signature limitation, not a bug in the call (it matches Google's own documented usage and is proven working against the live API). Fixed with a narrowly-scoped, explained `type: ignore[arg-type]` rather than fighting it further.

**Testing evidence (senior-engineer standard, per §9):**

- **MRZ checksum algorithm verified two independent ways**, not just against itself: (a) hand-verifiable cases (`_check_digit("1") == 7`, computed by hand: value×weight = 1×7 = 7) that don't depend on any MRZ context at all; (b) a self-consistent synthetic MRZ built programmatically (not a memorized "known-good" real-world test vector, which risks silently encoding a transcription mistake as the expected answer) — field-extraction correctness is verified independently of checksums (pure string slicing), and checksum *sensitivity* is verified by corrupting one character and confirming both the specific field and the composite check fail together.
- **The real Gemini integration was proven twice against the live API, not just mocked**: once on a valid synthetic ID image (perfect extraction, `confidence=1.0`, all six fields correct), and once on a deliberately blank image, which is what surfaced bug #2 above and then confirmed the fix.
- **20 automated unit tests** (`test_mrz.py`, `test_extract.py`) using a fake `VLMClient` — deliberate, since hitting the real API on every test run costs quota and isn't deterministic — covering: clean first-try extraction, malformed-response retry-then-succeed, `document_visible=False` retry-then-succeed and exhaustion-into-`needs_review`, plain exhausted-retries, MRZ short-circuiting the VLM entirely (asserting zero model calls), missing/invalid MRZ falling through to the VLM instead of trusting partial data.
- **Full regression**: all 28 tests (Phase 3's 12 intake integration tests included, to catch exactly the cross-cutting `truststore` regression above) plus `ruff`, `black --check`, `mypy`, `pre-commit run --all-files` clean after every fix, not just at the end.
- **Known gap (explicit, not silent):** EasyOCR (~240MB, torch/torchvision) is installed as a dependency per the original plan to read MRZ text off a cropped image region, but that OCR-to-MRZ-text wiring itself isn't built yet — `mrz.py`'s `parse_td3()` takes already-extracted MRZ line strings, proven with synthetic text directly. Wiring a real image crop through EasyOCR into those two lines is Phase 5 work, when this gets connected to the actual case/document pipeline.
- **Known gap (explicit, not silent):** the Gemini key in use is a free-tier key with different data-usage terms than paid tiers (see `.env.example`) — only synthetic test images have been sent through it, consistent with the policy agreed before this phase started.

### Phase 5 — 2026-08-11 (`v0.5.0`, commit `b904547`)

**Done:**

- `services/pipeline/ocr.py`: closes Phase 4's known gap — an EasyOCR-based reader that crops the bottom of a document image, detects text lines, and assembles the two candidate MRZ lines (ordered top-to-bottom by bounding box, not detection order). Deliberately approximate: any OCR misread is caught downstream by `mrz.parse_td3()`'s checksum validation, which is the actual safety net, not this module's line-detection heuristics.
- `services/pipeline/workflows/`: `contracts.py` (plain `KycCaseInput`/`KycCaseResult` dataclasses, no heavy imports — see bug #3 below for why this exists as its own module), `activities.py` (`fetch_id_document_activity`, `extract_document_activity`, `update_case_status_activity`), `kyc_case.py` (`KycCaseWorkflow`: MRZ-first via Phase 4's `extract_id_document`, retry policy at the Temporal level distinct from `extract.py`'s own bounded VLM retry — Temporal retry handles worker death/network blips/"client hasn't uploaded yet," VLM retry handles "the model got it wrong"), `task_queues.py` (task-queue-per-plan-tier routing — design only, per this phase's stated scope; load-testing that it actually isolates load is Phase 9), `worker.py` (polls all three plan-tier queues concurrently for local dev).
- `services/intake/temporal.py`: starts the workflow when a case is created, by **string workflow-type name** rather than importing the workflow class (see bug #3). Idempotent on `case_id` — a deterministic workflow ID means replays/retries don't double-start anything, just hit `WorkflowAlreadyStartedError`, caught and treated as a no-op.
- `services/pipeline/db.py`, `services/pipeline/storage.py`: same RLS-safe `set_config`-based tenant connection pattern and S3 client pattern as the intake service, for the pipeline side.

**Real bugs found and fixed while implementing this:**

1. **`fetch_id_document_activity`'s failure was uncaught, unlike every other failure mode in this workflow.** Found by the manual worker-restart resilience test (not by inspection): a slow fake activity that legitimately exceeded its own timeout caused `WorkflowFailureError` to blow up the whole workflow execution, instead of resolving to `needs_review` like the extraction activity's failures already did. Fixed by wrapping it in the same `try/except ActivityError` pattern; added a regression test (`test_fetch_document_hard_failure_marks_case_needs_review_not_crash`) so this can't silently regress.
2. **My first two resilience-test attempts didn't actually test what I claimed.** Attempt 1: killed the worker process, but real latency between tool-invocation steps meant the worker had already *completed* the activity before the kill landed — the log showed `COMPLETED` before the kill, and the "restarted" worker never did any work. Attempt 2: fixed the timing, but used a 60-second fake-activity sleep against a 30-second `start_to_close_timeout` — a mismatch that made the *first* attempt fail regardless of whether the worker died, muddying what was actually being proven (this is what surfaced bug #1). Attempt 3, with a sleep genuinely inside the timeout budget and the kill executed immediately after confirming "STARTED" in the log, is what actually proves the exit criteria — verified by inspecting both worker processes' logs: worker 1 shows `STARTED` with no `COMPLETED`, worker 2 (a separate OS process, started after worker 1 was confirmed dead) shows the activity running to completion and the workflow resolving correctly.
3. **Intake's startup time went from near-instant to ~12s** the moment `services/intake/temporal.py` imported `KycCaseWorkflow` directly from `kyc_case.py` — because that module imports `activities.py`, which imports `boto3`/`easyocr`/`google-genai` at module level (`easyocr` alone pulls in `torch`). Intake never uses any of that; it only needed to reference the workflow by name. Fixed by extracting the plain-dataclass I/O contracts into their own `contracts.py` (no heavy imports) and starting the workflow by **string type name** (`Client.start_workflow` accepts either) instead of importing the class. Confirmed the fix by timing the restart: ~3s to fully ready, back to normal.
4. Minor: `WorkflowAlreadyStartedError` lives in `temporalio.exceptions`, not `temporalio.client` (guessed wrong on the first try, verified against the installed package rather than assumed).

**Testing evidence (senior-engineer standard, per §9):**

- **Exit criteria proven for real against the live Docker Temporal server**, not simulated: a deliberately slow fake `fetch_id_document_activity` (registered under the real activity name — Temporal matches by name, not Python object identity) gave a reliable, scriptable window to kill a real OS process mid-activity; a second, independent worker process was started afterward and shown (via its own log output) to be the one that actually completed the work and drove the workflow to its correct final result.
- **10 automated workflow control-flow tests** (`test_kyc_case_workflow.py`) against the real dev Temporal server with fake activities swapped in by name — no-document-found, fetch-activity hard failure (the bug-#1 regression test), successful extraction, low-confidence extraction, extraction hard failure, and a direct proof of the `WorkflowAlreadyStartedError` idempotency guarantee the intake integration depends on. Plus 5 unit tests for `ocr.py`'s pure line-assembly logic (bottom-two-lines ordering, junk-text filtering, pad/truncate to 44 chars, fewer-than-two/zero detections) — no EasyOCR/torch needed for these, only fabricated bounding-box data.
- **One full real end-to-end run, not just component-level tests**: seeded a real tenant, started the real intake API and the real production worker (both current code, post-refactor), created a case through the real `POST /cases`, uploaded a real synthetic image to the real presigned URL, and watched it flow entirely on its own — the workflow started before the upload finished and correctly retried ("document not yet uploaded") until the file appeared, then a real EasyOCR pass (models downloaded on first use), a real Gemini call (confirmed via the `200 OK` in the worker's logs), a row correctly written to `extractions` (`confidence=1.0`, every field matching what was drawn on the synthetic image), and the case's status correctly transitioning `pending` → `processing`. All test tenants/cases/objects cleaned up afterward (Postgres rows and the MinIO object) — confirmed zero leftover tenants after.
- **Full regression**: all 43 tests across every phase to date (Phase 3's intake integration tests, Phase 4's extraction/MRZ tests, and this phase's new OCR and workflow tests) plus `ruff`, `black --check`, `mypy`, `pre-commit run --all-files` clean.
- **Known gap (explicit, not silent):** the "client hasn't uploaded yet" retry path relies entirely on Temporal's own retry/backoff with no `heartbeat_timeout` configured on the fetch/extraction activities — a dead worker is only detected once the configured `start_to_close_timeout` elapses (30s / 5min respectively), not sooner. Fine for this phase's exit criteria (proven above); tightening this with explicit heartbeating is a Phase 9 hardening concern if faster failure detection turns out to matter in practice.
- **Known gap (explicit, not silent):** starting the workflow in `services/intake/main.py` happens *after* the DB transaction commits, as a separate, non-transactional step — if it fails, the case row exists with no workflow driving it forward. `start_kyc_case_workflow` is idempotent so a client retry recovers cleanly, but there's no automatic recovery for a client that never retries. A transactional-outbox pattern would close this properly; not built here, flagged for Phase 9/10.

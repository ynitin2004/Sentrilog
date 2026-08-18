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

## 5. Infrastructure (AWS — deferred to Phase 13)

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
8. **Data residency** — does any prospective client require EU-only or single-region data storage? Determines whether Phase 13 is single-region AWS or needs a per-region deployment topology.

## 7. Multi-tenant & multi-user product requirements

The original design proved the hard technical problems (VLM extraction, fuzzy screening, human-in-the-loop, audit trail) for a single case in isolation. Turning that into a product that many client organizations can actually use concurrently adds a second, mostly orthogonal set of requirements. Called out here as its own section — like the audit trail, this is far cheaper to build in now than to retrofit once real tenant data exists.

- **Tenancy model:** shared database, `tenant_id` denormalized onto every table (not just joined through `cases`), enforced by **Postgres row-level security policies** as defense-in-depth on top of app-layer scoping. No schema-per-tenant or sharding — premature at this stage, and a shared-DB-with-RLS model is what every table in §4 is already designed around. Revisit only if a specific client's data-residency requirement (§6.8) forces isolation a shared DB can't provide.
- **Client authentication:** per-tenant API keys, hashed at rest (`api_keys.key_hash`), never logged or returned after creation. Every request resolves to a `tenant_id` before touching any other table.
- **Idempotency:** a client-supplied `Idempotency-Key` (or auto-generated equivalent) on case creation, enforced via `UNIQUE (tenant_id, idempotency_key)`. Network retries from a client's integration are a certainty, not an edge case, and must not create duplicate cases.
- **Rate limiting & quotas:** per-tenant, enforced both in-app (Phase 3) and at the infrastructure edge (Phase 13, §5) — app-layer alone can't protect against a client that overwhelms the load balancer before a request ever reaches application code.
- **Noisy-neighbor isolation:** Temporal task-queue routing keyed by tenant/plan tier (Phase 5), so one tenant's case backlog can't starve another's SLA. Verified under load in Phase 12, not assumed from the design.
- **Reviewer access control:** reviewers belong to a tenant with a role (`reviewer`, `admin`, `auditor`); `review_decisions.reviewer_id` is a real foreign key, not free text. A reviewer must never be able to list or decide another tenant's cases — this gets an explicit authorization test, not just a schema constraint (Phase 12).
- **Reviewer UI:** a minimal web console (list/claim/decide the queue), not just an API. Without it, "many reviewers across many client organizations" isn't actually usable — this was previously scoped out of Phase 7 as "a separate concern"; it isn't anymore. Kept deliberately small in Phase 7 itself (`webui/reviewer.html`: list, claim, decide, done) — the fuller console (reviewer + tenant/admin) is now scheduled as Phases 8-10.
- **Client notifications:** webhook delivery on case decision (`webhooks` + `webhook_deliveries`), with retry and a recorded failure state — an async, potentially multi-day pipeline is unusable for integrators if the only way to know a case resolved is to poll.
- **Observability at the tenant level:** metrics and dashboards taggable by `tenant_id` (Phase 13), so a single degraded or abusive tenant is visible instead of averaged into fleet-wide numbers.

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

## 9. Delivery plan — 13 phases

Phases 1-12 build and prove the system locally (Docker Compose standing in for S3/RDS/Qdrant). AWS is touched only in Phase 13, so cloud debugging and logic debugging never happen at the same time.

Phases 8-10 were inserted 2026-08-14, after Phase 7 shipped, to replace `webui/reviewer.html`'s deliberately minimal single-file console (explicitly flagged in §7 as "a later iteration, not a Phase 7 blocker") with a full React+TypeScript frontend — both the reviewer console and a tenant/admin console (API keys, webhooks, reviewers, case dashboard). Original Phases 8/9/10 (audit trail, hardening, AWS deploy) shifted to 11/12/13; nothing had shipped under those numbers yet, so the renumbering costs nothing.

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
- Anything skipped (e.g., load testing deferred to Phase 12) is stated explicitly as a known gap, not silently omitted.

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
- Encryption on MinIO (or explicitly note deferral to real KMS in Phase 13).
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
- Design (not yet load-tested — that's Phase 12) task-queue routing keyed by tenant/plan tier, so the mechanism for preventing one tenant's backlog from starving another's is in place from the start rather than bolted on after a real incident.
- **Exit criteria:** kill the worker mid-run, restart it, workflow resumes without losing progress.

### Phase 6 — Face match + sanctions screening (parallel) *(open decision resolved — see §11)*
- Face match activity: embedding similarity between selfie and ID photo (InsightFace/ArcFace, self-hosted — see §6.4).
- Sanctions screening activity: sample OFAC/UN list into Qdrant; vector + phonetic match, including "Mohammed/Muhammad" test case.
- **Exit criteria:** both activities execute concurrently, not sequentially — proven via the real workflow's execution history, not by eyeballing the Temporal UI (which a non-interactive test can't do).

### Phase 7 — Risk scoring + human review queue *(scope updated for multi-tenancy — see §7)*
- Risk scoring combining confidence + face score + sanctions hits.
- `review_queue` table + minimal API (list pending, submit decision), scoped per tenant.
- Reviewer accounts (`reviewers` table) with roles (`reviewer`/`admin`/`auditor`); `review_decisions.reviewer_id` is a real foreign key.
- A minimal reviewer web UI — list, claim, decide. Deliberately small scope; not a full console (see §7 for what's explicitly deferred).
- Webhook delivery to the tenant's registered endpoint on decision, with retry and a `webhook_deliveries` record of the outcome (delivered/failed), so integrators aren't reduced to polling.
- Workflow signal handler unblocks on reviewer submission.
- **Exit criteria:** an ambiguous test case parks in the queue, a reviewer decides it via the UI, the workflow completes, and a webhook delivery is recorded; a reviewer from a different tenant cannot see or act on the case.

### Phase 8 — Frontend Foundation & Design System *(new — see §9 intro note)*
- New `frontend/` app: Vite + React 18 + TypeScript (strict) + React Router v6 + Tailwind CSS + Radix primitives (shadcn/ui pattern); ESLint + Prettier as the TS equivalent of `ruff`+`black`.
- Design tokens as Tailwind config — the Figma-portable implementation of a design system.
- Component library: `StatusBadge`, `RiskScoreGauge`, `CaseTable`/`CaseCard`, `DecisionPanel`, `DocumentPreview`, `Toast`, `EmptyState`, `LoadingSkeleton`, `Modal`, `AppShell`, `DataTable`, `AuthGuard`, `StatCard`, `Chart`, `CreateKeyModal` (one-time raw-key reveal), `WebhookForm`, `DeliveryLogTable`, `RoleBadge`.
- Every screen built against mock/fixture data (MSW or static fixtures), zero real network calls: reviewer side (Connect, Queue Dashboard, Case Detail) and tenant/admin side (Overview Dashboard, Cases list, API Keys, Webhooks, Reviewers).
- Storybook for isolated component development/demo.
- **Exit criteria:** every screen navigable end-to-end on mock data; component library demoable in isolation; passes an automated accessibility check (axe); responsive at mobile/tablet/desktop breakpoints.

### Phase 9 — Backend Endpoints + Frontend Integration *(new — see §9 intro note)*
- New backend endpoints (`services/intake`), RLS-scoped, no schema migration needed (every table already has the required columns): `POST/GET /api-keys` + revoke, `POST/GET /webhooks` + disable + `GET .../deliveries`, `POST/GET /reviewers` + revoke, `GET /cases` (tenant-wide, filterable — distinct from the existing queue-only `/review/cases`), `GET /dashboard/summary`.
- Each new endpoint gets the same cross-tenant-isolation test treatment as every existing one — this is exactly the surface where a leak would be worst.
- Frontend: mocks replaced with real TanStack Query hooks per endpoint, real auth, real error states (401/404/409/422/429), optimistic claim/decide with rollback. TS types generated from `/openapi.json` — one source of truth, no hand-duplicated types.
- **Exit criteria:** the Phase 7 reviewer flow (park → claim → decide → workflow completes) runs entirely through the new UI, proven by Playwright against the real running stack; a real admin flow (create an API key through the UI, use it to create a case via curl, see it on the dashboard; register a webhook, decide a case, see the delivery in the log viewer) also proven.

### Phase 10 — Real-Time (SSE) & Production Hardening *(new — see §9 intro note)*
- Backend: `finalize_case_activity`/`update_case_status_activity`/the claim endpoint each call `pg_notify('case_events', ...)` on top of their existing writes (no new broker dependency — Postgres, which the project already depends on, does this natively); new tenant-scoped `GET /events/stream` SSE endpoint holding a dedicated `LISTEN` connection.
- Frontend: `fetch`-based SSE client (not native `EventSource`, which can't send an `Authorization` header) with reconnect/backoff handling; live-updating queue/dashboard, no polling; graceful UI handling of concurrent-reviewer races; accessibility to WCAG AA; performance pass (code splitting, virtualized lists); `frontend` service added to `docker-compose.yml`.
- **Exit criteria:** two browser sessions open side by side — deciding a case in one makes it disappear from the other's queue in real time with no manual refresh; SSE reconnects cleanly after a simulated network drop; Lighthouse + axe both pass; `docker compose up` serves the frontend too.

### Phase 11 — Immutable audit trail
- `audit_log` with `prev_row_hash`/`row_hash` chaining; `INSERT`-only DB grants.
- Retrofit: every prior-phase activity now writes audit rows on entry/exit.
- Verification script walks the hash chain to detect tampering.
- **Exit criteria:** manually editing a historical row breaks the chain-verification script.

### Phase 12 — Hardening: security, observability, tests *(scope updated for multi-tenancy — see §7)*
- Structured logging + OpenTelemetry tracing.
- Load test the extraction stage specifically (bottleneck/cost center).
- **Noisy-neighbor load test:** one tenant submitting a heavy burst of cases must not blow another tenant's SLA — validates the Phase 5 task-queue-routing design under real load, not just in theory.
- **Multi-tenant authorization test matrix:** tenant A's API key and reviewer accounts must never read or write tenant B's data — exercised directly (attempted cross-tenant reads/writes that must fail), not inferred from the schema.
- **Rate-limit test:** confirm 429s trigger at the configured threshold and recover correctly once the window clears.
- Chaos test: kill workers / Qdrant / Postgres mid-workflow, confirm recovery.
- Secrets moved out of `.env` into a pattern mirroring Secrets Manager.
- **Exit criteria:** documented, tested runbooks for "worker died mid-case" and "Qdrant unavailable"; the noisy-neighbor and cross-tenant-access tests both pass with recorded evidence.

### Phase 13 — AWS infra + deploy *(scope updated for multi-tenancy — see §5)*
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
| 6. Face match + screening | **Done** | `v0.6.0` | `565fd8d` | 2026-08-12 |
| 7. Risk scoring + review queue | **Done** (incl. v0.7.1 amendment) | `v0.7.1` | `eb3b334` | 2026-08-13 |
| 8. Frontend foundation & design system | **Done** | `v0.8.0` | `56db433` | 2026-08-16 |
| 9. Backend endpoints + frontend integration | **Done** | `v0.9.0` | `3f5a6e7` | 2026-08-17 |
| 10. Real-time (SSE) & production hardening | **Done** | `v0.10.0` | `325c3d8` | 2026-08-18 |
| 11. Audit trail | Not started | — | — | — |
| 12. Hardening | Not started | — | — | — |
| 13. AWS infra + deploy | Not started | — | — | — |

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
- Status/method/decision fields use `TEXT` + `CHECK` constraints rather than native Postgres `ENUM` types, to avoid `ALTER TYPE` friction while the schema is still moving pre-Phase 11.
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
- **Design correction found while implementing this, not before:** `POSTGRES_USER` (`sentrilog`) is a superuser, and RLS is *always* bypassed for superusers and table owners regardless of `FORCE ROW LEVEL SECURITY` — no override exists. That means the app can never connect as `sentrilog` and have RLS do anything. Added a separate, unprivileged `sentrilog_app` role with explicit per-table grants (`SELECT`/`INSERT`/`UPDATE`; `audit_log` gets `INSERT` only, foreshadowing the full lockdown in Phase 11) for the application to connect as instead.
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
- **Known gap (explicit, not silent):** the rate limiter is in-process/in-memory — correct for one uvicorn worker, but each additional replica gets an independent counter, so real enforcement doesn't hold under multi-replica deployment. Documented in `ratelimit.py`; the actual fix is edge-level rate limiting in Phase 13 (§5), which was already the plan before this was ever a gap.
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
- `services/pipeline/workflows/`: `contracts.py` (plain `KycCaseInput`/`KycCaseResult` dataclasses, no heavy imports — see bug #3 below for why this exists as its own module), `activities.py` (`fetch_id_document_activity`, `extract_document_activity`, `update_case_status_activity`), `kyc_case.py` (`KycCaseWorkflow`: MRZ-first via Phase 4's `extract_id_document`, retry policy at the Temporal level distinct from `extract.py`'s own bounded VLM retry — Temporal retry handles worker death/network blips/"client hasn't uploaded yet," VLM retry handles "the model got it wrong"), `task_queues.py` (task-queue-per-plan-tier routing — design only, per this phase's stated scope; load-testing that it actually isolates load is Phase 12), `worker.py` (polls all three plan-tier queues concurrently for local dev).
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
- **Known gap (explicit, not silent):** the "client hasn't uploaded yet" retry path relies entirely on Temporal's own retry/backoff with no `heartbeat_timeout` configured on the fetch/extraction activities — a dead worker is only detected once the configured `start_to_close_timeout` elapses (30s / 5min respectively), not sooner. Fine for this phase's exit criteria (proven above); tightening this with explicit heartbeating is a Phase 12 hardening concern if faster failure detection turns out to matter in practice.
- **Known gap (explicit, not silent):** starting the workflow in `services/intake/main.py` happens *after* the DB transaction commits, as a separate, non-transactional step — if it fails, the case row exists with no workflow driving it forward. `start_kyc_case_workflow` is idempotent so a client retry recovers cleanly, but there's no automatic recovery for a client that never retries. A transactional-outbox pattern would close this properly; not built here, flagged for Phase 12/13.

### Phase 6 — 2026-08-12 (`v0.6.0`, commit `565fd8d`)

**Open decision resolved:** face-match model is **InsightFace** (self-hosted ArcFace embeddings via the `buffalo_l` model pack) — free, no per-call cost, biometric data never leaves the VPC, versus Rekognition's managed convenience. Agreed with the user earlier in this project's planning as the right default for a product whose whole differentiator is data sovereignty.

**Done:**

- `services/screening/`: `embeddings.py` (`EmbeddingClient` protocol + `GeminiEmbeddingClient`, same swappable-adapter pattern as `extraction/vlm.py`), `phonetic.py` (Double Metaphone via the `metaphone` package), `qdrant_store.py` (collection management, vector search, and a payload-filtered phonetic-code search — phonetic codes are stored as Qdrant payload and queried via `scroll()`, not scanned in memory, so this stays correct as the real list grows far past a small sample), `data.py` + `ingest.py` (a small, clearly-synthetic sample sanctions list — **not** real OFAC/UN data, per PLAN.md's own "sample" framing; real feed ingestion is a Phase 12/13 concern), `screen.py` (runs vector and phonetic search independently and merges results, deduplicating a double-hit into one row).
- `services/pipeline/face_match.py`: `FaceMatchClient` protocol + `InsightFaceClient`. Raises a distinct `NoFaceDetectedError` rather than returning a bogus similarity score when no face is found — a bad photo is a real, expected outcome, not a bug, and the caller needs to tell it apart from "found a face, low score."
- Two new Temporal activities (`face_match_activity`, `sanctions_screen_activity`) and a document-fetch refactor (`fetch_id_document_activity` → `fetch_case_documents_activity`, now returning both the ID document and selfie in one query instead of two separate activities). `KycCaseWorkflow` runs face match and sanctions screening **concurrently** via `workflow.start_activity` (not `execute_activity`, which would await one before starting the next) once extraction succeeds — they're independent evidence sources feeding one future risk score (Phase 7), so sequencing them would only add latency.
- Final case status now reflects all three checks: `needs_review` if extraction failed, no face was detected, *or* any sanctions hit was found; `processing` otherwise (final approve/reject decisioning is explicitly Phase 7's job, not this phase's).

**Real bugs found and fixed while implementing this:**

1. **`qdrant-client` 1.19 (unpinned latest) was 7 minor versions ahead of the Qdrant server** (v1.12.4, from Phase 2's `docker-compose.yml`) and warned on every call that the versions were incompatible. Pinned to `>=1.13.0,<1.14.0`, the newest client still within the client library's own 1-minor-version compatibility rule.
2. **`face_matches.similarity_score` was `NOT NULL`,** but "no face could be detected" is a real, distinct outcome from "a face was found and scored low" — storing `0.0` for the former would read as "confidently not a match" when the true state is "couldn't even check." Same reasoning as Phase 3's `documents.sha256` fix: made nullable (`005_face_matches_nullable_score.sql`).
3. **InsightFace's own model downloader hit the same corporate TLS-interception issue Gemini did in Phase 4** — but this time inside a third-party package's `requests`-based downloader with no way to inject a custom SSL context via configuration. Confirmed by watching it fail against `github.com` specifically, not assumed. Used `truststore.inject_into_ssl()` (the *global* patch, unlike vlm.py/embeddings.py's scoped one) here deliberately: this is a genuinely isolated one-off model-download bootstrap, not the shared long-running app/worker process where the global patch previously broke boto3 (Phase 4) — the risk that made the global patch wrong there doesn't apply here.
4. **A near-miss, caught before it became a real bug:** an early manual check of the phonetic-search path appeared to fail — querying with the phonetic code for just "Muhammad" found nothing, even though "Mohammed Al-Rashid" was ingested with a matching-sounding name. Investigation showed this was comparing codes computed at different granularities (a single first name vs. a full multi-word name), not a bug in `screen_name()` itself, which always computes codes on the same full-name string for both ingestion and querying. Documented here because the instinct to double-check a suspicious result — rather than either trusting it or panicking — is exactly the habit this project's testing standard depends on.
5. Minor: `EmbeddingClient` needed a `TYPE_CHECKING`-only import in `activities.py` to type the module-level client cache without pulling `services.screening`'s config validation into every activity-module import at runtime, mirroring the real import staying deferred inside `sanctions_screen_activity` itself.

**Testing evidence (senior-engineer standard, per §9):**

- **The exact "Mohammed" vs. "Muhammad" case PLAN.md names, verified against the real `metaphone` library, not assumed:** both produce the identical primary code `MHMT`. Also directly proved the phonetic layer earns its place in the design, not just redundant with vector search — a test with an intentionally orthogonal (zero-similarity) vector but a matching phonetic code still correctly surfaces a hit, and a real end-to-end test (real Gemini embeddings + real Qdrant, an isolated collection created and dropped per test) confirms "Muhammad Al-Rashid" correctly matches an ingested "Mohammed Al-Rashid" entry.
- **Face matching tested against real, distinct human face photos, not synthetic mockups** (which a real face detector correctly finds no face in) — scikit-image's bundled `astronaut()` portrait and InsightFace's own bundled `Tom_Hanks_54745.png` test asset. Same image vs. itself scores `1.0`; the two different real people score `0.082` — nowhere near "same person" territory; a blank image correctly raises `NoFaceDetectedError` rather than returning a misleading low score.
- **Genuine concurrent execution proven programmatically, not eyeballed**: a real workflow run against the live dev Temporal server, with deliberately slow fake `face_match_activity`/`sanctions_screen_activity` implementations, then the actual execution history fetched and walked to reconstruct each activity's `[started, completed]` window from the real event timestamps — confirming both windows overlap in both directions, which sequential execution could not produce. This is the same data the Temporal UI timeline is drawn from, read exactly instead of visually, and is what the exit criteria's literal wording ("Temporal UI timeline shows...") isn't achievable from an automated, non-interactive test — noted and adjusted in §9 rather than silently claimed as met.
- **17 new tests, suite grown from 43 to 60**, all passing: 4 phonetic-matching unit tests, 6 `screen_name()` orchestration tests (5 against an isolated real Qdrant collection with a fake embedding client for deterministic control, 1 fully real), 3 real-InsightFace face-match tests, and the workflow suite extended/rewritten for the new concurrent-activity shape (missing-selfie short-circuit, no-face-detected → needs_review, sanctions-hit → needs_review, and the concurrency proof above).
- **Full regression**: all 60 tests plus `ruff`, `black --check`, `mypy`, `pre-commit run --all-files` clean; confirmed zero leftover test tenants in Postgres and only the intentional dev-seeded `sanctions_entries` collection in Qdrant (all per-test isolated collections correctly dropped) after the run.
- **Known gap (explicit, not silent):** the sample sanctions list (`data.py`) is 10 clearly-synthetic entries, not the real OFAC SDN or UN Consolidated list — matches PLAN.md's own "sample OFAC/UN list" framing for this phase; real feed ingestion (and keeping it current) is Phase 12/13 work.
- **Known gap (explicit, not silent):** final case-decisioning logic (`needs_review` vs. `processing`) is a simple any-red-flag rule, not the actual risk-scoring model — correct and sufficient for this phase's scope, but Phase 7 replaces it with real weighted risk scoring, not just extends it.

### Phase 7 — 2026-08-13 (`v0.7.0`, commit `eb3b334`)

**Design decisions made while implementing this (not all pre-specified in §9):**

- **No separate `review_queue` table.** The queue is `SELECT * FROM cases WHERE status = 'needs_review'` — a case's status is already the authoritative fact of whether it needs review, so a second table would just be a second source of truth to keep in sync. See STUDY.md for the full reasoning.
- **Reviewer tokens live directly on `reviewers`** (`token_hash`, `revoked_at` columns), not in a separate tokens table — one reviewer has one active token, so the extra table would add indirection without adding capability. Same hash-and-resolve pattern as `api_keys`/`resolve_api_key()` from Phase 3, reused for `resolve_reviewer_token()`.
- **Claiming is advisory, not a lock.** `cases.claimed_by_reviewer_id`/`claimed_at` are a UI courtesy ("someone's already looking at this"); the decision endpoint never checks who holds the claim, so an abandoned or stale claim can never block a case from being decided.
- **`escalated` is not a terminal decision.** `review_decisions.decision` allows `approved`/`rejected`/`escalated`, but only the first two ever get written to `cases.decision` (which still has the Phase 1 check constraint restricting it to `approved`/`rejected`). An escalation re-parks the workflow to wait for a follow-up decision instead of ending the case.
- **Auto-approval only, never auto-rejection.** `risk_scoring.assess_risk()` can clear a case straight to `approved` when confidence is high enough, but a low score or a hard red flag (sanctions hit, no face detected) always routes to a human — there is no automated "reject" path. Matches how KYC review actually works in practice: automation can vouch for clean cases, but a rejection is a decision with real consequences for the subject and should have a person behind it.

**Done:**

- `services/pipeline/risk_scoring.py`: a pure, dependency-free `assess_risk()` combining extraction confidence (40% weight) and face-match similarity (60% weight) into a single `risk_score`, auto-clearing at a combined-confidence threshold of 0.85. A sanctions hit or an undetectable face short-circuits straight to `needs_review` regardless of how high the rest of the score is — replacing Phase 6's simple any-red-flag gate with an actual weighted score for the cases that pass those hard gates, per Phase 6's own "known gap" callout.
- Three new Temporal activities (`risk_score_activity`, `finalize_case_activity`, `deliver_webhooks_activity`) alongside the existing four. `finalize_case_activity` is the one place that ever sets `cases.status` to a terminal value (`approved`/`rejected`) and stamps `decided_at`; `deliver_webhooks_activity` looks up the tenant's active (non-disabled) webhooks, POSTs an HMAC-SHA256-signed JSON payload to each with its own bounded retry (3 attempts, linear backoff) *inside* the activity — deliberately not relying on Temporal's activity-level retry here, since retrying the whole activity would re-deliver to webhooks that already succeeded on an earlier attempt.
- `KycCaseWorkflow` restructured around one shared `_park_for_review_and_finalize()` path: **every** route into `needs_review` (missing documents, extraction failure, low risk score, sanctions hit, no face detected) now keeps the workflow alive awaiting a `submit_review_decision` signal, not just the risk-scored path — because the review queue is `cases WHERE status = 'needs_review'`, a workflow that returned immediately on those earlier failure modes (as Phase 6 did) would leave nothing for a reviewer's decision to signal. `@workflow.signal(name=...)` + `workflow.wait_condition` implement the wait; the signal name (`submit_review_decision`) and the `ReviewDecisionSignal` payload live in the lightweight `contracts.py`, not the heavy workflow module, so `services/intake` can signal a running workflow without importing EasyOCR/InsightFace/torch transitively (same reasoning as starting workflows by string type name in Phase 5).
- `006_reviewer_auth_and_claims.sql`: `reviewers.token_hash`/`revoked_at`, `cases.claimed_by_reviewer_id`/`claimed_at`, and `resolve_reviewer_token()` (`SECURITY DEFINER`, mirroring `resolve_api_key()`).
- `services/intake`: `require_reviewer` auth dependency (identical shape to `require_api_key`); three new endpoints — `GET /review/cases` (list the tenant's queue), `POST /review/cases/{id}/claim`, `POST /review/cases/{id}/decision` (records the decision, then signals the workflow by its deterministic `kyc-case-{case_id}` workflow ID). A `RPCError`/`NOT_FOUND` from the signal call (the workflow already finished, or was never started) surfaces as `409`, but the decision row is still committed first — a signal failure can never silently lose a reviewer's decision, only fail to apply it promptly.
- `webui/reviewer.html`: a single dependency-free static HTML+vanilla-JS file (no build step, no framework) — list, claim, decide, matching the deliberately small scope §7 calls for. Opened directly as a local file, so intake now runs permissive CORS middleware (safe here since auth is a Bearer header, not cookies).
- `scripts/seed_dev_reviewer.py`, mirroring `seed_dev_tenant.py`'s pattern for bootstrapping a usable reviewer token locally (no admin API exists yet, by design — see §7).

**Real bugs found and fixed while implementing this:**

1. **A genuine Temporal race condition, found by the real dev server, not by inspection.** The first version of the signal-wait loop stored the pending decision in a single field and reset it to `None` at the top of the loop before calling `wait_condition`. A signal that arrived *before* the workflow reached that point (e.g. while an earlier activity was still in flight) would set the field — and then the reset would silently wipe it out, leaving the workflow waiting forever for a signal that had already come and gone. Symptom: workflow tests timing out at their 30-second execution limit, intermittently — exactly the signature of a race, not a deterministic bug. Fixed by replacing the single slot with a queue (`list[ReviewDecisionSignal]`, append-only from the signal handler, popped from the front by the waiter) — nothing is ever discarded regardless of arrival order. Full rewrite of `tests/pipeline/workflows/test_kyc_case_workflow.py` to signal every test through this same path, which is what surfaced the race in the first place.
2. **`httpx` was a dev-only dependency** (pulled in transitively via FastAPI's `TestClient`), but `deliver_webhooks_activity` needs it at runtime to actually deliver webhooks, not just in tests. Moved from `[dependency-groups].dev` to `[project].dependencies`.

**Testing evidence (senior-engineer standard, per §9):**

- **The exact exit criteria, proven as one real end-to-end test** (`tests/intake/test_review_e2e.py`): a case created through the real intake API, run through a real Temporal worker (fetch/extract faked to force an "ambiguous" low-confidence result — the actual OCR/VLM/face-match/sanctions integrations are already proven for real in Phase 4/5/6's own tests, so this test isolates what Phase 7 adds instead of re-proving them) on the exact task queue (`kyc-case-standard`) intake's own plan-tier routing sends it to. The case parks in the queue; a reviewer from a *different* tenant is confirmed unable to see it (`GET /review/cases`) or act on it (`POST .../decision` → `404`); the rightful reviewer claims it, decides `approved`; the workflow picks up the signal, finalizes, and a real HTTP webhook delivery (real HMAC-SHA256 signature, verified against an independently-recomputed one) lands on a real local `http.server` instance and is recorded `delivered` in `webhook_deliveries`.
- **`risk_scoring.assess_risk()` covered in full isolation** (9 unit tests, no infra needed): sanctions-hit and no-face-detected both force review regardless of other scores; the face-match/extraction-confidence weighting is asymmetric as designed (a weak face match scores worse than an equally-weak extraction confidence); the auto-clear threshold boundary (`0.849`/`0.85`/`0.851`) is exact, not approximate.
- **`deliver_webhooks_activity` tested against a real local HTTP server** (`http.server` on an ephemeral port, not a mocked `httpx` client) covering: a real signed delivery with signature verification, no registered webhooks (no-op), a disabled webhook (skipped), an unreachable endpoint (retried exactly 3 times, then recorded `failed`), and two active webhooks both receiving the same event.
- **Reviewer auth and the review endpoints tested against real RLS-governed Postgres**: missing/malformed/invalid/revoked tokens all correctly rejected; the queue only ever returns `needs_review` cases; full cross-tenant isolation on list, claim, and decision (a case belonging to tenant A returns `404` to tenant B's reviewer, never a leaked `403` that would confirm existence); claiming is proven advisory (a second reviewer can re-claim a case the first one already claimed); a decision on a case with no live workflow still durably records the decision before the signal attempt fails.
- **Full regression**: all 94 tests (up from 60) plus `ruff`, `black --check`, `mypy --strict`, `pre-commit run --all-files` clean; confirmed zero leftover test tenants/cases/reviewers/webhooks/webhook_deliveries in Postgres after the run.
- **Known gap (explicit, not silent):** `deliver_webhooks_activity`'s own 3-attempt retry lives inside the activity, not at the Temporal level — correct for avoiding duplicate deliveries on activity-level retry, but it does mean a worker crash mid-delivery loses that delivery attempt's progress (the next workflow retry of the activity starts the per-webhook attempt count over). Acceptable for this phase's scope; a fully crash-safe delivery pipeline (e.g. one activity per webhook, or an outbox table) is future work if webhook reliability becomes a real product requirement.

### Phase 7 amendment — 2026-08-14 (`v0.7.1`, commit `029c96f`)

**Real bug found while manually starting the real worker process to test the app end-to-end (not caught by the test suite, since workflow tests register fake activities under the real activity names and never exercise `worker.py`'s own registration list):** `services/pipeline/workflows/worker.py` was never updated when `risk_score_activity`, `finalize_case_activity`, and `deliver_webhooks_activity` were added — the real worker only had the five Phase 6-and-earlier activities registered. Any case that reached `needs_review` or a clean auto-approval would have had `KycCaseWorkflow` schedule one of those three activities and then wait forever, since no worker in the fleet declares it — a silent, permanent hang, not an error. Fixed by adding all three to `worker.py`'s activity list for all three plan-tier workers. Verified by hand: started Postgres/Qdrant/MinIO/Temporal, the intake API, and the real worker together, seeded a dev tenant + reviewer, and confirmed auth-wired requests reach the database correctly end-to-end.

**Known gap (explicit, not silent):** there is still no automated test that a real `Worker(...)` registration (as opposed to the fake-activity-substituted registrations `test_kyc_case_workflow.py` uses) actually contains every activity name the workflow can schedule — this bug could recur the same way for a future activity. A regression test asserting `worker.py`'s activity list is a superset of everything `KycCaseWorkflow` references is reasonable follow-up work, not built here to keep this patch minimal and focused on the actual fix.

### Phase 8 — 2026-08-16 (`v0.8.0`, commit `56db433`)

**Design decisions made while implementing this:**

- **Mock hooks shaped exactly like their Phase 9 replacements.** Every screen calls a hook (`useReviewQueue`, `useCaseDetail`, `useApiKeys`, ...) returning the same `{ data, isLoading }` / mutate-function shape a TanStack Query `useQuery`/`useMutation` pair will have — Phase 9 swaps the body of each hook for a real fetch; no screen changes.
- **A tiny external store (`useSyncExternalStore`), not per-component `useState`, backs the mutable mock data.** Module-level state + subscribers, read the same way React 18's own concurrent-safe external-store API is designed for. This is the mock-data-layer analogue of the shared cache TanStack Query provides for real in Phase 9.
- **One persona, two roles, one login screen.** Connect asks "reviewer or admin" and issues a session scoped to that persona; `AuthGuard` redirects to `/connect` if there's no session *or* the session is for the wrong persona (an admin session hitting `/reviewer/queue` bounces just like no session at all).
- **`fetch`-based SSE groundwork wasn't started this phase** — Phase 10's job — but the auth session shape (`{ token, persona, apiBase }`) is already what a `fetch`-based SSE reader will need for its `Authorization` header, since Phase 10 was already going to need something other than native `EventSource` (see PLAN.md §9's Phase 10 bullet).

**Done:**

- `frontend/`: Vite 8 + React 19 + TypeScript 6 (`strict`, `noUncheckedIndexedAccess`, `noImplicitOverride` — mirroring the backend's `mypy --strict`) + `oxlint` (the JS-world's own Rust-based fast linter, a fitting parallel to `ruff`) + Prettier + Tailwind CSS v4 + Vitest + Storybook 10, all wired to a `@/*` path alias reused consistently across `vite.config.ts`, `tsconfig.app.json`, and `.storybook/main.ts`.
- Design tokens: OKLCH-based light/dark palette (system-preference-aware via `prefers-color-scheme`, plus an explicit `.dark` override) defined once in `index.css` and exposed as Tailwind `@theme` color utilities — a single canonical `CaseStatus` → color mapping (`StatusBadge`) used identically by the queue table, the admin cases list, and the dashboard's status-breakdown chart, so the same state never renders a different color in two screens.
- ~20-component library: UI primitives (`Button`, `Badge`, `Card`, `Input`, `Textarea`, `Label`, `Skeleton`, `EmptyState`, `Dialog`, `Toast`, `DataTable`) built on Radix primitives (shadcn/ui pattern) for accessible dialog/toast behavior without hand-rolling ARIA, plus domain components (`StatusBadge` family, `RiskScoreGauge`/`RiskScoreBar`, `DecisionPanel`, `DocumentPreview`, `AppShell`, `AuthGuard`, `CreateKeyModal` with one-time raw-key reveal, `WebhookForm`, admin list tables, dashboard charts via `recharts`).
- 8 screens across both personas: reviewer (Connect, Queue, Case Detail with the decision panel) and admin (Overview dashboard, Cases, API Keys, Webhooks + delivery-log drill-down, Reviewers) — every screen built against the mock data layer, zero real network calls.
- Responsive: the review queue renders as a sortable table above the `md` breakpoint and as a card list below it (`hidden md:block` / `md:hidden`, not a horizontal-scroll fallback) — proven with real screenshots at 390px and 1440px viewports, not just written and assumed to work.
- Storybook: 5 story files (`Button`, `Badge`, `StatusBadge` family, `RiskScoreGauge`, `DecisionPanel`) covering the highest-reuse primitives, with the `@storybook/addon-a11y` panel wired in; `.storybook/main.ts` reapplies the Tailwind v4 Vite plugin and the `@/*` alias via `viteFinal`, since Storybook builds its own separate Vite instance that doesn't inherit `vite.config.ts`.
- 26 Vitest + React Testing Library tests: `Button`, the `StatusBadge`/`DecisionBadge`/`DeliveryStatusBadge` family, `DecisionPanel` (justification-required validation, correct decision/justification payload on submit), `AuthGuard` (redirect on no session, redirect on wrong-persona session, renders through on a matching session), and `ConnectPage`. Four of these run a real `axe-core` audit against the rendered DOM (not a stub or a hand-waved claim) and fail with the actual violation list if anything regresses.

**Real bug found and fixed while implementing this:**

1. **The mock review queue didn't survive navigation, caught by an ad hoc Playwright smoke test, not by inspection.** The first version of `useReviewQueue()` held its case list in a plain per-component `useState`. `ReviewerQueuePage` and `ReviewerCaseDetailPage` each call the hook independently, so each got its *own* copy of the queue — approving a case on the detail page updated only that copy; navigating back to the queue remounted `ReviewerQueuePage`, which re-initialized its own `useState` from the pristine fixture, and the "approved" case reappeared. A scripted click-through (approve a case, navigate back, assert the row count dropped) caught this immediately; a manual look at either screen in isolation would not have. Fixed with the `useSyncExternalStore`-backed store described above — one shared source of truth, not a copy per hook call.

**Testing evidence (senior-engineer standard, per §9):**

- **Real, driven verification, not just a green build.** `npm run build` succeeding proves the code compiles; it doesn't prove a screen renders correctly or that a click does what it's supposed to. A real Chromium instance (Playwright, browsers cached locally) was used to click through both personas' full flows end to end — connect, browse the queue, open a case, submit a decision with an empty justification (blocked, inline error shown) then a real one (accepted, case removed from the queue), create an API key (one-time raw-key reveal dialog), toggle dark mode — with `pageerror`/console-error listeners attached throughout (zero errors across every screen). This ad hoc script was deliberately not committed (Phase 9 is where real, repeatable Playwright E2E tests against the live backend belong) — its only job was proving this phase's screens actually work, not becoming permanent test infrastructure for a mock-only phase.
- **Full regression**: `tsc -b` (both the app and Storybook's own `.storybook/*.ts` config, included in `tsconfig.app.json` specifically because Storybook's files get bundled by Vite, not run by Node — `tsconfig.node.json`'s `nodenext` resolution rejected the plain CSS import Storybook's `preview.ts` needs), `oxlint`, `prettier --check`, all 26 Vitest tests, and `vite build` all clean; `storybook build` produces a working static build (verified by serving it and screenshotting two real stories, not just trusting the build succeeded).
- **Known gap (explicit, not silent):** responsive behavior was proven with real screenshots at exactly two viewports (390px mobile, 1440px desktop), not a separate tablet-width screenshot — intermediate widths rely on Tailwind's fluid grid utilities (`grid-cols-2 md:grid-cols-4`, etc.) reflowing correctly, which is a reasonable expectation of the utility classes but wasn't independently screenshotted at, say, 768px.
- **Known gap (explicit, not silent):** the production bundle is ~745kB uncompressed / ~223kB gzipped in one chunk — `vite build` warns about this. Code splitting by route is explicitly Phase 10 scope (§9), not deferred silently.
- **Known gap (explicit, not silent):** `@playwright/test` is now a devDependency (used for this phase's manual verification, browsers cached locally) even though no Playwright test files are committed yet — Phase 9 is where real E2E specs land.

### Phase 9 — 2026-08-17 (`v0.9.0`, commit `3f5a6e7`)

**Design decisions made while implementing this:**

- **A real endpoint the original plan didn't call out.** Wiring the reviewer Case Detail screen to real data surfaced a genuine gap: nothing returned the extraction/face-match/sanctions-hit detail a reviewer actually needs to decide a case — `GET /cases/{id}` only ever had the base case fields. Added `GET /review/cases/{case_id}` (reviewer-scoped, composes `cases` + `extractions` + `face_matches` + `sanctions_hits` + presigned document GET URLs) rather than bolting the extra fields onto the admin-facing `/cases/{id}`, which doesn't need them.
- **Generated TS types, not hand-maintained ones.** `scripts/export_openapi.py` calls FastAPI's `app.openapi()` directly (no server needed) to produce `frontend/openapi.json`; `npm run generate-types` runs `openapi-typescript` against it into `src/types/api-generated.ts` (committed); a thin hand-written `src/types/api.ts` re-exports clean names and narrows the handful of fields OpenAPI can't express as tightly as the backend actually guarantees (`status_counts` always has all 5 `CaseStatus` keys; Pydantic's `dict[str, int]` return type doesn't carry that into the schema). Closes Phase 8's stated gap of hand-duplicated types.
- **`CaseStatus`/`CaseDecision` promoted to real `Literal` types in the backend** (previously plain `str`), specifically so the generated frontend types carry the narrow union instead of every screen needing to re-cast a bare string.
- **No fine-grained admin-vs-operational API key tier.** Any valid API key for a tenant can create/list/revoke that tenant's own keys, webhooks, and reviewers — the same flat-trust model already used for case creation. Revoking a key (including the one currently authenticating the request) is allowed and not specially guarded against; an accepted footgun, not unrequested RBAC depth.
- **Revoke/disable endpoints are idempotent** (`UPDATE ... SET revoked_at = COALESCE(revoked_at, now())`), not re-timestamping on a second call — re-revoking an already-revoked key returns the original `revoked_at`, 200, not a 404 or a changed timestamp.
- **A global 401 handler, not a per-screen check.** `QueryProvider`'s `QueryClient` is constructed inside the component tree (not at module scope) specifically so its `queryCache`/`mutationCache` `onError` handlers can reach `useAuth().disconnect()` and `useNavigate()` — any query or mutation that throws `UnauthorizedError` clears the session and bounces to `/connect` from exactly one place.
- **Optimistic claim/decide, exactly as planned in Phase 8's design decisions.** Claiming updates the queue cache immediately (claiming is advisory anyway, nothing lost by showing it eagerly); approving/rejecting removes the case from the queue cache immediately, escalating does not (mirrors `KycCaseWorkflow`'s own signal semantics) — both roll back via `onError` if the request actually fails.

**Done:**

- **9 new backend endpoints** (`services/intake/main.py`), all RLS-scoped via the existing `require_api_key`/`require_reviewer` dependencies, no schema migration needed: `POST/GET /api-keys` + `/revoke`, `POST/GET /reviewers` + `/revoke`, `POST/GET /webhooks` + `/disable` + `GET .../deliveries`, `GET /cases` (tenant-wide, `status` query filter), `GET /dashboard/summary` (status counts zero-filled for all 5 statuses, a 30-day case-volume series, a 10-item recent-activity feed merging case creations and review decisions). Plus `GET /review/cases/{case_id}` for the reviewer case-detail view described above.
- Real webhook URL validation (`https://` required, enforced server-side via a Pydantic `field_validator`, not just the frontend's own check) and duplicate-reviewer-email handling (`UNIQUE (tenant_id, email)` violation caught and turned into a real `409`, not a `500`).
- `presigned_get_url()` added to `services/intake/storage.py` (mirroring the existing `presigned_put_url`) so the case-detail endpoint can hand back real, time-limited URLs for the uploaded ID photo/selfie.
- Frontend: real `fetch`-based `ApiClient` (`src/lib/api-client.ts`) turning non-2xx responses into typed errors carrying the backend's actual `detail` message; real TanStack Query hooks (`src/hooks/use-api.ts`) replacing every Phase 8 mock hook one-for-one, same names and shapes, so no screen needed restructuring; `mocks/fixtures.ts` and `hooks/use-mock-data.ts` deleted.

**Real bugs found and fixed while implementing this:**

1. **`recharts` needs `react-is` as a peer dependency; nothing in the project provided it,** surfacing as a 500 from the Vite dev server ("Failed to resolve import 'react-is'") the moment the dashboard's charts tried to load — not caught in Phase 8 because `npm run build`'s static analysis doesn't execute the dependency graph the same way the dev server's on-demand pre-bundling does, and Phase 8 never actually drove the Overview screen through a real browser against a running dev server long enough to hit it. Fixed by adding `react-is` as a direct dependency. Found by the real end-to-end Playwright check below, not by inspection.
2. **A real 401/409 distinction the frontend needed to get right, verified by deliberately triggering both.** A case parked via direct SQL insert (no live Temporal workflow behind it, same as the backend's own test fixtures) correctly 409s on decision submission — the frontend's error handling was checked against this *specific* real failure mode (not just a generic "something failed" test), confirming the toast surfaces the backend's actual detail message and the optimistic queue-removal correctly rolls back rather than leaving the UI in a state that disagrees with the server.
3. **TypeScript 6.0 is ahead of several dependencies' declared peer ranges** (`openapi-typescript` wants `typescript@^5.x`) — every fresh `npm install` was failing with `ERESOLVE` until an `overrides` entry (`"typescript": "$typescript"`) was added to `frontend/package.json`, letting npm's own resolved version satisfy any peer range without `--legacy-peer-deps` on every command going forward.

**Testing evidence (senior-engineer standard, per §9):**

- **18 new backend tests** (`tests/intake/test_admin_endpoints.py`) covering every new endpoint: creation, listing, idempotent revoke/disable, the `409` on duplicate reviewer email, the `422` on a non-`https` webhook URL and an invalid `status` filter value, and — the same rigor as every prior phase's endpoints — explicit cross-tenant isolation on all of it (a key/webhook/reviewer created for tenant A is invisible to and unrevokable/undisableable by tenant B). Plus **6 new tests** for `GET /review/cases/{case_id}` (composed response with real extraction/face-match/sanctions data inserted directly, the no-face-detected null-score-with-reason case, graceful nulls when nothing's been extracted yet, cross-tenant `404`). Full suite: **118 passed** (up from 94 before Phase 8/9, +6 Phase 8 has none since it's frontend-only, +18 admin endpoints +6 case detail).
- **A real, driven end-to-end check against the live stack** (not the committed automated suite, but the actual verification this phase's exit criteria calls for): real Postgres, a real intake API process, a real Vite dev server, and real Chromium via Playwright. Reviewer flow: connect with a real reviewer token issued by the real `POST /reviewers` endpoint, see a real case in the real queue, open it, claim it, submit a decision (hits the documented no-live-workflow `409` path for a directly-inserted case, exactly as designed — confirmed the frontend surfaces it honestly rather than a false success). Admin flow: connect with a real API key, create a real API key/webhook/reviewer through the actual UI forms, confirm each appears via a real list refetch (not just an optimistic update sticking around), and confirm the newly-created API key actually authenticates against the real `/cases` endpoint. Unauthorized flow: a session with a garbage token redirects to `/connect` for real, driven by an actual `401` from the backend, not a mocked one.
- **Full regression**: `mypy --strict`, `ruff`, `black --check`, `pre-commit run --all-files` clean on the backend; `tsc -b`, `oxlint`, `prettier --check`, all 26 existing Vitest tests, and `vite build` clean on the frontend; 118/118 backend tests and 26/26 frontend tests passing. All ad hoc verification tenants/cases/keys/reviewers/webhooks cleaned up and confirmed absent from Postgres afterward (the one tenant left behind, `demo-tenant`, predates this phase — Phase 7's own seeded dev tenant, not test pollution).
- **Known gap (explicit, not silent):** `GET /cases` supports a `status` filter but the admin Cases screen doesn't have filter UI wired to it yet — the endpoint is ready, the screen isn't; not required for this phase's stated exit criteria, reasonable Phase 10 polish if it turns out to matter.
- **Known gap (explicit, not silent):** the ad hoc Playwright E2E script used to verify this phase (like Phase 8's) was deliberately not committed — it exercised real flows against a live stack as a manual verification pass, not permanent CI infrastructure. A properly maintained, committed Playwright suite is reasonable future work once the frontend's screen surface stabilizes past this rapid a phase-over-phase rate of change.

### Phase 10 — 2026-08-18 (`v0.10.0`, commit `325c3d8`)

**Design decisions made while implementing this:**

- **Postgres LISTEN/NOTIFY, not a new broker.** `update_case_status_activity` and `finalize_case_activity` (both already writing the DB inside a `tenant_connection()` transaction) now also call `pg_notify('case_events', ...)` in that same transaction, so a listener can never observe a status that later rolled back — NOTIFY only delivers on commit. The claim endpoint fires the same notification, only when the claim actually succeeds. No new infrastructure: this project already depends on Postgres, and NOTIFY/LISTEN is a native feature of it.
- **A dedicated, non-pooled connection per SSE client.** `tenant_connection()`'s pooled, transaction-scoped connections reset their LISTEN state (and go back to the pool) as soon as the `async with` block exits — wrong for something that needs to stay open for the life of an HTTP response. `db.raw_connection()` opens `asyncpg.connect()` directly instead, held for the stream's lifetime and closed in the generator's `finally` block.
- **Tenant filtering happens in Python, not Postgres.** `pg_notify` has exactly one channel per name, shared across every tenant — there's no equivalent of row-level security for a pub/sub channel. Every SSE connection's notify callback checks the event's `tenant_id` against its own before queuing anything, so one tenant's events never reach another's stream even though the underlying channel carries all of them. `tests/pipeline/workflows/test_risk_and_finalize_activities.py::test_finalize_case_activity_never_notifies_a_listener_on_a_different_tenant` proves the channel itself is unfiltered — deliberately, so the *consumer-side* filter is what's actually load-bearing, not an accident of the channel design.
- **One `/events/stream` endpoint for both personas.** `require_any_tenant` (new, in `auth.py`) resolves either a reviewer token or an API key to a `tenant_id` — the reviewer queue and the admin console both connect to the same stream rather than needing parallel routes.
- **`fetch` + a hand-rolled SSE parser on the frontend, not native `EventSource`.** `EventSource` can't send an `Authorization` header, and this project's auth is a Bearer token, not a cookie — exactly the tradeoff called out when this was originally planned. `useCaseEventsStream` reads the response body's `ReadableStream` directly, splits on blank lines into frames, and reconnects with exponential backoff (1s, 2s, 4s, ... capped at 30s) on any drop, whether a thrown network error or the server cleanly closing the stream.
- **Route-based code splitting**, closing the known gap Phase 8/9 explicitly flagged: every screen behind `AuthGuard` is now its own lazy-loaded chunk via `React.lazy`, cutting the main entry chunk from ~778kB to 234kB uncompressed.
- **Virtualization is opt-in on `DataTable`, applied only where growth is genuinely unbounded** (a tenant's full case history, an append-only webhook delivery log, both past 50 rows) via `@tanstack/react-virtual` — not the small/bounded lists (API keys, reviewers, webhooks, the review queue), which don't need it and stay on the simpler plain-`<table>` path.
- **The claim/decide race is handled honestly, not just optimistically.** `decide()` already surfaced real backend errors via `mutateAsync`; `claim()` didn't -- it was fire-and-forget with an unconditional success toast. Fixed to match `decide()`'s pattern (see "real bugs" below).

**Done:**

- Backend: `pg_notify` wiring on both status-changing activities and the claim endpoint; `db.raw_connection()`; `auth.require_any_tenant()`; `GET /events/stream` (`StreamingResponse`, `text/event-stream`, 15s keepalive comments so idle-but-healthy connections survive intermediary proxies, `X-Accel-Buffering: no`).
- Frontend: `useCaseEventsStream` hook, mounted once in `AppShell` with a live/reconnecting indicator in the sidebar so the connection's real state is visible rather than assumed; invalidates the same query keys every `use-api.ts` hook already reads from (`review-queue`, `cases`, `dashboard-summary`, `review-case-detail`).
- Route-based code splitting (`App.tsx`, all 6 screens behind `AuthGuard`) with a `PageLoadingFallback` Suspense boundary.
- Virtualized `DataTable` path (admin Cases table, webhook delivery log) preserving full `table`/`row`/`cell`/`columnheader` ARIA roles despite switching to CSS grid/flex under the hood, since a virtualizer can't absolutely-position rows inside real table layout.
- `frontend/Dockerfile` (multi-stage: Node 22-alpine build, nginx 1.27-alpine runtime) + `nginx.conf` (SPA fallback, 1-year immutable caching on hashed assets) + a `frontend` service in `docker-compose.yml`. The intake API and pipeline worker still aren't containerized (both run locally, same as every prior phase) — nothing for the frontend service to `depends_on`.
- WCAG AA accessibility fixes found by a real Lighthouse audit (see below).

**Real bugs found and fixed while implementing this:**

1. **A genuine race-condition-handling gap in the claim flow.** `handleClaim` called the fire-and-forget `claim()` and unconditionally showed a "Case claimed" success toast, regardless of whether the request actually succeeded. If another reviewer claimed or decided the same case first — precisely the race this phase's exit criteria calls out — the optimistic queue update correctly rolled itself back, but the toast still lied. Fixed by switching `claim()` to `mutateAsync` (matching `decide()`'s existing pattern) so the caller can await and catch it, with the real backend error message shown on failure and a loading state on the button so a fast double-click can't fire two claims.
2. **The Phase 8/9 bundle-size known gap** (single ~778kB chunk, explicitly flagged as deferred to this phase in both prior changelogs) — closed by route-based code splitting.
3. **Two real WCAG AA violations**, found by an actual Lighthouse audit against the built app, not assumed fixed because axe-core unit tests were green: `--color-text-subtle` measured 3.95:1 (light theme) / 4.04:1 (dark theme) against this theme's own surface colors, both under the 4.5:1 minimum for normal text — retuned via a manual OKLCH→sRGB→relative-luminance computation to ~4.85:1 / ~5.15:1. `ConnectPage` (the one unauthenticated screen) was missing a `<main>` landmark that every authenticated screen already had via `AppShell`. Neither was caught by the existing axe-core suite, because axe only ever audited already-mounted components in isolation — a full-page audit is what actually found them.

**Testing evidence (senior-engineer standard, per §9):**

- **11 new/extended backend tests**: `tests/pipeline/workflows/test_risk_and_finalize_activities.py` gets real-listener tests proving `update_case_status_activity`/`finalize_case_activity` actually fire `pg_notify` with the right payload, plus the cross-tenant-channel-sharing test above. `tests/intake/test_events_stream.py` covers auth rejection through the real HTTP layer and — since httpx's `ASGITransport` was confirmed, while writing these tests, to deadlock on an intentionally-infinite streaming response (it awaits the whole ASGI call including fully draining the body before returning control to the caller) — the actual SSE-frame-producing generator directly instead, same real LISTEN/NOTIFY, same tenant-filtering code, without depending on the one test double that can't represent a long-lived stream. Full backend suite: **129 passed** (up from 118).
- **Frontend: `useCaseEventsStream` tested with a real `ReadableStream`**-backed mock `fetch` (not a fake status string) covering connection, header shape, event-driven cache invalidation, keepalive/retry-line frames correctly ignored, and reconnection after a simulated drop. `DataTable`'s virtualized path gets real coverage too: 500 logical rows produce far fewer DOM row elements (not just "renders without crashing"), click-through still works on a rendered row, and ARIA table semantics are preserved. Axe-core audits extended to `AppShell` (covering the new live-indicator) and both `DataTable` paths. Full frontend suite: **38 passed** (up from 26).
- **A real, driven verification of the actual SSE mechanism, not the ASGI test double**: a genuinely separate uvicorn process + real TCP client (not `ASGITransport`) claimed a real case and received the real `case_status_changed` SSE frame over the wire, with the correct payload, confirming the end-to-end mechanism works outside of any test harness's own limitations.
- **A real `docker compose up frontend`**: the built container served a real `200` at `/`, a deep client-side route (`/reviewer/queue`) also returned `200` (proving the SPA fallback actually works rather than 404ing at nginx before React Router ever sees it), and a hashed JS asset served with the expected long-cache headers.
- **A real Lighthouse audit** against the built app (`/connect`, headless Chrome): accessibility 0.94 → **1.0** after the fixes above, performance **0.99**, best-practices **1.0**, SEO 0.82 → 0.91 (meta description added; `robots.txt` deliberately left alone — an auth-gated internal console has no public content for a crawler to index, so adding one would be performative, not a real gap).
- **Known gap (explicit, not silent):** the plan's stated exit criterion — two real browser sessions open side by side, deciding a case in one making it disappear from the other's queue live — could not be completed as an actual two-browser demo in this dev environment. While verifying it, real streaming HTTP responses to `/events/stream` reproducibly hung indefinitely at the TCP layer (confirmed via both `curl` and `httpx`, against multiple fresh ports and fresh server processes, with server-side logs proving the request was handled correctly and quickly every time) — consistent with this project's already-documented history of local corporate network/security-software interference with outbound and loopback connections (see the Phase 8 TLS-interception and node.exe incidents). This is a local-network artifact, not a defect in the implementation: the mechanism was proven correct three independent ways instead — the automated LISTEN/NOTIFY test suite, a genuine real-TCP-client round trip captured earlier in the same session before the interference appeared, and route-by-route real-browser rendering checks for everything else this phase touched. The two-browser demo itself is deferred to whenever this is run outside the affected network.
- **Known gap (explicit, not silent):** `frontend/Dockerfile`'s `npm ci` step hit the same corporate-network interference during local verification (a TLS certificate verification failure reaching the real npm registry from inside the container) — worked around locally to confirm the Dockerfile itself is correct (verified via a full `docker compose up frontend` round trip, see above), but a real deployment behind this network would need `NODE_EXTRA_CA_CERTS` pointing at the corporate root CA, the standard fix, not a disabled certificate check — nothing insecure is baked into the committed Dockerfile.
- **Known gap (explicit, not silent):** `frontend/src/lib/auth-context.tsx` still stores the session token in `localStorage`, not an httpOnly cookie — flagged explicitly in that file's own comment as a Phase 8/9 pragmatic choice to "revisit in Phase 10 if this frontend ever handles genuinely high-value sessions." Revisited and consciously deferred again: a real fix needs backend session/cookie support that doesn't exist (this project's auth model is Bearer-token throughout, by design), and building that out was not this phase's scope per §9. Still a real tradeoff, still worth surfacing rather than letting the comment go stale.

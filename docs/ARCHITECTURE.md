# Architecture

What exists today, at `v0.12.1`. The target architecture is in [improvements.md](../improvements.md)
§4; the free deployment is in [DEPLOY.md](DEPLOY.md). Rationale: [DECISIONS.md](DECISIONS.md). Call
chains: [FLOW.md](FLOW.md).

## 1. Stack

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, asyncpg (no ORM), pydantic-settings |
| Orchestration | Temporal, `temporalio/auto-setup:1.24.2` in dev, Python SDK |
| Database | Postgres 16, row-level security, schema in `infra/docker/postgres-init/*.sql` (no migration tool) |
| Vector search | Qdrant v1.12.4, client pinned `>=1.13,<1.14` |
| Object storage | S3 API via boto3 SigV4; `adobe/s3mock:4.9.0` in dev and CI |
| Extraction | EasyOCR → MRZ (ICAO 9303 checksums) → Gemini `gemini-2.5-flash` fallback |
| Face match | InsightFace `buffalo_l`, CPU |
| Screening | Gemini `gemini-embedding-001` (768 dimensions) + Metaphone |
| Frontend | Vite, React 19, TypeScript strict, TanStack Query, Tailwind v4, Radix, Vitest, Storybook |
| Observability | structlog JSON, OpenTelemetry with a console exporter only |
| Tooling | uv, ruff, black, mypy strict, pytest, pre-commit, GitHub Actions |

## 2. Modules

| Path | Owns |
|---|---|
| `services/common/` | Audit hash formula, observability, secrets provider, settings base |
| `services/intake/` | HTTP API, API-key and reviewer auth, rate limit, presigned URLs, workflow start, SSE |
| `services/pipeline/` | Extraction, OCR, face match, risk scoring, audit writer |
| `services/pipeline/workflows/` | `kyc_case` workflow, activities, task queues, worker entrypoint |
| `services/screening/` | Sanctions data, ingest, embeddings, phonetic codes, Qdrant store |
| `frontend/` | Operator console: reviewer queue and tenant admin |
| `webui/` | Phase 7's single-file console, superseded by `frontend/` |
| `infra/docker/postgres-init/` | The schema, applied in filename order |
| `scripts/` | Seeds, chaos and load harnesses, chain verification, readiness gate, OpenAPI export |

## 3. Deploy units

| Unit | Command | Containerised today? |
|---|---|---|
| Intake API | `uvicorn services.intake.main:app` | No |
| Pipeline worker | `python -m services.pipeline.workflows.worker [--tier …] [--max-concurrent-activities N]` | No |
| Console | nginx serving the Vite build (`frontend/Dockerfile`) | Yes |
| Sanctions ingest | `python -m services.screening.ingest` (one-shot) | No |

The console has no build-time configuration. Users enter the API URL on `/connect`, and it is stored
in `localStorage`.

## 4. Multi-tenancy: enforced twice

1. **Application:** an API key or reviewer token resolves to a `tenant_id` before any other table is
   touched, through `SECURITY DEFINER` functions (`resolve_api_key`, `resolve_reviewer_token`).
2. **Database:** `tenant_id` is on every table and RLS is forced on 11 tables. Each transaction calls
   `set_config('app.tenant_id', $1, true)`. A session that never sets it sees zero rows.

The app connects as `sentrilog_app` (no `BYPASSRLS`). Superusers bypass RLS with no override, so
connecting as `sentrilog` would silently switch this boundary off.

Cross-tenant access returns **404**, because a 403 would confirm an ID exists. A role failure inside
your own tenant returns **403**, because there is nothing to conceal.

## 5. Workflow isolation

Task queues are `kyc-case-{standard|pro|enterprise}`, routed by the tenant's `plan_tier`. Workflow IDs
are `kyc-case-{case_id}`, which makes starting a workflow idempotent. One worker process serves all
tiers unless `--tier` narrows it.

## 6. Audit trail

`audit_log` is insert-only for the app role (it holds only `SELECT, INSERT`) and hash-chained per
tenant. Intake and pipeline share one formula (`services/common/audit_hash.py`) and serialise writers
with `pg_advisory_xact_lock`. `scripts/verify_audit_chain.py` walks the chain. It proves tampering is
detectable, not impossible, and the chain still lives only in the database it protects (P-8 in
improvements.md).

## 7. Data stores

| Store | Holds | Note |
|---|---|---|
| Postgres `sentrilog` | Tenants, keys, reviewers, cases, documents, extractions, face matches, hits, decisions, audit log, webhooks | System of record |
| Postgres `temporal`, `temporal_visibility` | Workflow state | Same server as the app, which shapes the failure mode (§9) |
| Qdrant `sanctions_entries` | 10 synthetic entries | Rebuildable with ingest; no payload index |
| S3 bucket | ID documents and selfies | Presigned PUT/GET, 900 s expiry; no encryption locally |

## 8. Real-time

Activities and the claim endpoint call `pg_notify('case_events', …)` inside their transaction.
`GET /events/stream` holds one **unpooled** `LISTEN` connection per browser tab and filters by tenant
in Python, with a keep-alive every 15 s. `LISTEN` does not work through a transaction-mode pooler,
which is what forces Redis in the paid target ([improvements.md](../improvements.md) §7.3).

## 9. Measured limits (Phase 12, on a 28-core laptop)

| Measure | Result |
|---|---|
| Quiet tenant p95 under a 48-case burst, I/O work | Pooled 3.74×, partitioned **1.14×** |
| Same, CPU work | Pooled 5.44×, partitioned 2.51× (one process, one GIL) |
| Extraction throughput | Linear to concurrency 32, then flat: the `asyncio.to_thread` default pool |
| MRZ short-circuit | 58 % of cases in the load simulation cost no API call |
| VLM retries at a 15 % bad-response rate | ~26 % more billed calls |
| Temporal start to answering RPCs | ~50 s |
| Postgres down for 12 s mid-case | Case resumes and is approved, **only because Temporal shares that Postgres** |
| Qdrant down mid-case | Case parks in `needs_review`; never auto-approved |

These measure design properties. They do not give a capacity figure for any deployed hardware.

## 10. Known structural gaps

No Dockerfile for Python processes · no `/health` · CORS `*` · in-process rate limiter · workflow start
is not transactional with case creation (P-10) · upload completion is a 20-minute retry loop (P-11) ·
console-only tracing · no migration tool · retry budgets are backwards relative to how dependencies
fail (see [runbooks/](runbooks/README.md)).

# FLOW — Entry Points, Execution Order, AI Decision Points

How the code runs at `v0.12.1`. Every change to an execution path updates this file. A new session
that finds it stale runs the onboarding pass in §5 and repairs it.

## 1. Entry points

| Entry point | File | Triggered by |
|---|---|---|
| HTTP API | `services/intake/main.py` | Tenant integrations, operator console |
| Worker | `services/pipeline/workflows/worker.py` | Temporal task queues |
| Console | `frontend/src/main.tsx` | Browser |
| Schema | `infra/docker/postgres-init/*.sql` | First Postgres init (compose), or CI's psql step |
| Sanctions ingest | `services/screening/ingest.py` | By hand, once per environment |
| Seeds | `scripts/seed_dev_tenant.py`, `scripts/seed_dev_reviewer.py` | By hand |
| Harnesses | `scripts/chaos_test.py`, `load_test_*.py`, `resilience_test_worker.py` | By hand |
| Chain check | `scripts/verify_audit_chain.py` | By hand |

### HTTP routes

| Auth | Routes |
|---|---|
| API key | `POST/GET /cases`, `GET /cases/{id}` · `POST/GET /api-keys`, `POST /api-keys/{id}/revoke` · `POST/GET /reviewers`, `POST /reviewers/{id}/revoke` · `POST/GET /webhooks`, `POST /webhooks/{id}/disable`, `GET /webhooks/{id}/deliveries` · `GET /dashboard/summary` |
| Reviewer token | `GET /review/cases`, `GET /review/cases/{id}`, `POST /review/cases/{id}/claim`, `POST /review/cases/{id}/decision` |
| Stream | `GET /events/stream` |

## 2. Execution chains

### A. Case submission

`POST /cases` → resolve API key to tenant → rate limit (in-process, per key) → validate declared
content type and size against `MAX_UPLOAD_BYTES`, malware-scan hook (stub) → transaction under the
tenant GUC: `INSERT … ON CONFLICT (tenant_id, idempotency_key) DO NOTHING`, then document rows → commit
→ `start_kyc_case_workflow` (idempotent on `case_id`, routed by `plan_tier`) → audit row, skipped on a
replay → respond with presigned PUT URLs (900 s).

**Known gap (P-10):** the workflow starts after the commit, non-transactionally. A failure between the
two leaves a case with no workflow. A client retry with the same key recovers it.

### B. Pipeline: the `kyc_case` workflow

```text
fetch_case_documents ─ fail, or no id_document ──────────────► park
        │
extract_document (20 min window; retries while the upload is pending)
        │  read_mrz_lines (EasyOCR) → checksum valid → confidence 0.99, no model call
        │  otherwise → Gemini VLM, bounded retry fed with the validation error
        ├─ needs_review, or activity failed ─────────────────► park
        │
        ├─ no selfie ────────────────────────────────────────► park
        │
   ┌────┴─────┐ concurrent
face_match  sanctions_screen (vector + phonetic)
   └────┬─────┘
risk_score
        ├─ hard failure, sanctions hit, no face, combined < 0.85 ► park
        │
finalize_case(approved) → deliver_webhooks → done

park: update_case_status(needs_review) → wait for reviewer signal
      → finalize_case(decision) → deliver_webhooks → done
```

Every activity writes audit rows through `audit.audited(...)`. Status changes and finalisation also
call `pg_notify('case_events', …)` in the same transaction.

### C. Human review

`GET /review/cases` (cases `WHERE status = 'needs_review'`; the queue is a status, not a table) →
`POST /claim` (advisory, `require_decision_maker`) → `POST /decision` (`require_decision_maker`):
write `review_decisions`, audit row, then signal the workflow → the signal is queued, so a decision
that arrives early is not lost → `escalated` is popped and the workflow keeps waiting →
`approved` or `rejected` finalises and delivers webhooks.

The audit row is written as soon as the decision commits, whether or not the signal then succeeds, and
a failed signal is logged as `decision_signal_failed`.

### D. Real-time

Commit with `pg_notify` → each `GET /events/stream` client's dedicated `LISTEN` connection receives it
→ filtered by tenant in Python → SSE → the console's `fetch`-based parser updates the query cache.

### E. Webhooks

`deliver_webhooks_activity` → POST with an HMAC signature (10 s timeout, 3 attempts inside the activity
to avoid duplicates from activity-level retries) → outcome recorded in `webhook_deliveries`.

### F. Audit

`audit.record` → `pg_advisory_xact_lock` for the tenant → read the chain head → `compute_row_hash` →
insert. `scripts/verify_audit_chain.py` recomputes the chain per tenant.

## 3. AI decision-point registry

Every place model output influences behaviour, with its guardrail. Adding one without registering it
here fails review.

| ID | Where | Model | Output | Guardrail | Validated? |
|---|---|---|---|---|---|
| AI-1 | `extraction/extract.py` | Gemini `gemini-2.5-flash` | ID fields as schema JSON | Skipped when the MRZ checksum passes; Pydantic validation; bounded retry; exhausted → review; `model_version` recorded | Two live calls; no accuracy measurement |
| AI-2 | `pipeline/ocr.py` | EasyOCR | MRZ text | ICAO 9303 check digits, so a misread fails the checksum and falls back to AI-1 | Checksum logic tested; **real OCR cannot run on the dev laptop** |
| AI-3 | `pipeline/face_match.py` | InsightFace `buffalo_l` | Cosine similarity | No face → review, not a low score; weighted into AI-5 | Two photographs only |
| AI-4 | `screening/screen.py` | Gemini `gemini-embedding-001` | Vector search, threshold 0.55 | Metaphone runs independently; any hit → review; a failed screen → review, never "0 hits" | Threshold unmeasured; 10 synthetic names |
| AI-5 | `pipeline/risk_scoring.py` | None (deterministic) | `0.6·face + 0.4·extraction ≥ 0.85` auto-approves | Consumes AI-1 and AI-3; never auto-rejects; hard flags override the score | **No validation set behind any constant** |

Standing rule: a model can clear a case only through AI-5's gate. It cannot reject one, and it cannot
override a hard flag.

## 4. Dependency direction

`intake` → `common`, and `pipeline/workflows/contracts` and `task_queues` only (not the pipeline's
config) · `pipeline/workflows/activities` → `pipeline/*`, `screening/*` (lazily), `common` ·
`kyc_case.py` touches activities only through `imports_passed_through` · nothing imports `scripts/`
except `scripts/` itself.

## 5. Onboarding pass

When this file is stale, run this without changing code:

> You are a staff engineer joining this project. Using docs/ as the claimed architecture, verify it
> against reality. Walk the stack and versions; list every entry point; trace one case from
> `POST /cases` through the workflow to a webhook, naming each transaction boundary and each audit row;
> trace one decision from the console to the workflow signal; trace how `tenant_id` is resolved and
> enforced on each path; list every external integration and how each is constructed; flag drift
> between code and docs; repair this file and list the 10 files a new developer should read first.

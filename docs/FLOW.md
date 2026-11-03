# FLOW — Entry Points, Execution Order, AI Decision Points

How the code runs after Phase 13 (`v0.13.2`). Every change to an execution path updates this file. A new session
that finds it stale runs the onboarding pass in §5 and repairs it.

## 1. Entry points

| Entry point | File | Triggered by |
|---|---|---|
| HTTP API | `services/intake/main.py` | Tenant integrations, operator console |
| Worker | `services/pipeline/workflows/worker.py` | Temporal task queues |
| Console | `frontend/src/main.tsx` | Browser |
| Schema | `infra/docker/postgres-init/*.sql` | First Postgres init (compose), or CI's psql step |
| Sanctions ingest | `services/screening/ingest.py` | By hand: builds a versioned snapshot, then repoints the alias (`--activate` rolls back) |
| Evaluation harness | `services/eval/__main__.py` | By hand, and the `eval` CI job on every push |
| E2E seed | `scripts/seed_e2e.py` | The `e2e` CI job, before Playwright |
| Seeds | `scripts/seed_dev_tenant.py`, `scripts/seed_dev_reviewer.py` | By hand |
| Harnesses | `scripts/chaos_test.py`, `load_test_*.py`, `resilience_test_worker.py` | By hand |
| Chain check | `scripts/verify_audit_chain.py` | By hand |

### HTTP routes

| Auth | Routes |
|---|---|
| API key | `POST/GET /cases`, `GET /cases/{id}` · `POST/GET /api-keys`, `POST /api-keys/{id}/revoke` · `POST/GET /reviewers`, `POST /reviewers/{id}/revoke` · `POST/GET /webhooks`, `POST /webhooks/{id}/disable`, `GET /webhooks/{id}/deliveries` · `GET /dashboard/summary` · `GET/PUT /policy`, `GET /policy/versions` |
| Reviewer token | `GET /review/cases`, `GET /review/cases/{id}`, `POST /review/cases/{id}/claim`, `POST /review/cases/{id}/decision` |
| Stream | `GET /events/stream` |

## 2. Execution chains

### A. Case submission

`POST /cases` → resolve API key to tenant → rate limit (in-process, per key) → validate declared
content type and size against `MAX_UPLOAD_BYTES`, malware-scan hook (stub) → transaction under the
tenant GUC: `INSERT … ON CONFLICT (tenant_id, idempotency_key) DO NOTHING`, with `policy_version` set to
the tenant's newest policy inside that same `INSERT` (0 when it has none), then document rows → commit
→ `start_kyc_case_workflow` (idempotent on `case_id`, routed by `plan_tier`) → audit row, skipped on a
replay → respond with presigned PUT URLs (900 s).

**Known gap (P-10):** the workflow starts after the commit, non-transactionally. A failure between the
two leaves a case with no workflow. A client retry with the same key recovers it.

### B. Pipeline: the `kyc_case` workflow

```text
fetch_case_documents ─ fail, or no id_document ──────────────► park
        │
extract_document (20 min window; retries while the upload is pending)
        │  read_mrz_lines (EasyOCR, boxes merged into rows) → parse_td3_tolerant → every
        │  check digit valid → confidence 0.99, no model call
        │  otherwise → Gemini VLM, bounded retry fed with the validation error
        ├─ needs_review, or activity failed ─────────────────► park
        │
        ├─ no selfie ────────────────────────────────────────► park
        │
   ┌────┴─────┐ concurrent
face_match  sanctions_screen (n-gram vector, exact search, through the snapshot alias)
   └────┬─────┘
risk_score (reads the case's pinned policy_version and the extracted expiry/nationality)
        ├─ hard failure, sanctions hit, no face, or any policy rule ► park
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
insert. `scripts/verify_audit_chain.py` recomputes the chain per tenant, re-canonicalising each payload
it reads back (JSONB returns keys in its own order; D36).

### G. Sanctions list lifecycle

`ingest --source ofac` → download (or `--from-dir`) → keep individuals and their aliases (D34) →
normalise and de-duplicate per entity → embed into a new collection named for the list version and
model → count check → `list_snapshots` row (insert-only) → repoint the `sanctions_current` alias.
Screening reads only through the alias; each hit and each screen's audit payload carries the
`list_version`, and a screen with no hits still records it.

### H. Evaluation

`python -m services.eval <suite>` → load a dataset and check its SHA-256 → run the production code
path (`gather_candidates`/`decide_hits`, `extract_from_mrz`, `InsightFaceClient`, `evaluate_policy`) →
metrics → `eval_runs` row (`--record`), `services/eval/results/*.json` (`--write-results`), Markdown
(`--report`) → exit 1 if a gate in `report.py` fails.

## 3. AI decision-point registry

Every place model output influences behaviour, with its guardrail. Adding one without registering it
here fails review.

| ID | Where | Model | Output | Guardrail | Validated? |
|---|---|---|---|---|---|
| AI-1 | `extraction/extract.py` | Gemini `gemini-2.5-flash` | ID fields as schema JSON | Skipped when the MRZ passes; Pydantic validation; bounded retry; exhausted → review; `model_version` recorded | Measurable with `eval extraction --vlm` (synthetic pages only); not run in CI, which has no key |
| AI-2 | `pipeline/ocr.py` + `extraction/mrz.py` | EasyOCR | MRZ text | Every ICAO check digit; line-1 shape and issuing state; the document number is never searched (D35); a failure falls back to AI-1 | `eval extraction`: field F1 and wrong-but-accepted reported per run (`results/extraction.json`) |
| AI-3 | `pipeline/face_match.py` | InsightFace `buffalo_l` | Cosine similarity | No face → review, not a low score; weighted into AI-5 | `eval face`: TAR at FAR 1e-2 and 1e-3 on LFW DevTest (`results/face.json`) |
| AI-4 | `screening/screen.py` | None: character n-gram hashing (D30) | Exact vector search, threshold from the harness | Any hit → review; a failed screen → review, never "0 hits"; the list version is recorded | `eval screening`: 95% recall gate in CI; FPR reported (`results/screening.json`) |
| AI-5 | `pipeline/risk_scoring.py` | None (deterministic) | The tenant's pinned policy, version 0 = `0.6·face + 0.4·extraction ≥ 0.85` | Hard stops ahead of any policy; no policy field can reject (D11, D37) | `eval risk`: auto-clear precision and leak rates for the built-in policy, composed from the other suites |

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

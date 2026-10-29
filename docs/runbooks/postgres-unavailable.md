# Runbook: Postgres unavailable

Postgres holds every case, document, decision and audit row. Both the intake API and the
pipeline worker connect to it through plain `asyncpg` pools (`services/*/db.py`) with **no
application-level retry** — Temporal's activity retry is the only safety net on the worker side,
and the intake API has none at all.

## What the system does on its own

**This is the least forgiving dependency in the system, and the behaviour differs sharply
between the API and the worker.**

### Intake API

Every endpoint fails for the duration of the outage. `POST /cases` returns a 500 once the pool
can't serve a connection; clients should retry. Because case creation sends an
`Idempotency-Key`, a client retrying after recovery gets the same case rather than a duplicate
(`create_case`'s `ON CONFLICT (tenant_id, idempotency_key) DO NOTHING` path).

### Pipeline worker

Depends entirely on which activity was in flight:

| Activity | Policy | Outcome if Postgres is gone |
|---|---|---|
| `extract_document`, `face_match` | `_LONG_RETRY_POLICY` | Survives an outage up to the 20-minute `schedule_to_close_timeout` |
| `update_case_status`, `risk_score`, `finalize_case`, `deliver_webhooks`, `fetch_case_documents`, `sanctions_screen` | `_SHORT_RETRY_POLICY` | **3 attempts, exhausted ~3 seconds in** |

A `_SHORT_RETRY_POLICY` activity that exhausts its attempts raises `ActivityError`. Unlike the
sanctions and face-match calls — which the workflow deliberately catches — `update_case_status`,
`risk_score` and `finalize_case` are awaited with no handler, so **the workflow itself fails**
and the case is left at whatever status it last held. A Postgres restart of more than a few
seconds will therefore strand in-flight cases in `pending` or `processing`.

That is the single most important thing to know during this incident: *recovering Postgres does
not recover the cases.* Those workflows are already in a failed terminal state and need an
explicit reset.

## Detecting it

- Intake API 500s across the board; `asyncpg` connection errors in the structured logs.
- Temporal UI (http://localhost:8080) shows workflows in **Failed** state with an
  `ActivityError` for one of the short-retry activities.
- Cases stuck off the terminal statuses:

  ```sql
  SELECT id, status, created_at
  FROM cases
  WHERE status IN ('pending', 'processing')
    AND created_at < now() - interval '30 minutes'
  ORDER BY created_at;
  ```

## Recovery

1. Restore Postgres and confirm it accepts connections.

   ```bash
   docker compose up -d postgres
   docker compose logs --tail=50 postgres
   docker compose exec postgres pg_isready -U sentrilog
   ```

2. **Restart the pipeline worker and the intake API.** `asyncpg` pools do not reliably
   re-establish themselves after the server they were connected to goes away; the pool is
   created once at startup (`init_pool()`), so the cleanest recovery is a process restart rather
   than waiting for the pool to heal.

   ```bash
   # worker
   uv run python -m services.pipeline.workflows.worker
   # intake API
   uv run uvicorn services.intake.main:app
   ```

3. **Re-drive the failed workflows.** They will not resume on their own.

   ```bash
   temporal workflow list --query "ExecutionStatus='Failed'"
   temporal workflow reset --workflow-id kyc-case-<case_id> --type FirstWorkflowTask
   ```

   The workflow is idempotent on `case_id` (`start_kyc_case_workflow` swallows
   `WorkflowAlreadyStartedError`), and every activity either overwrites its own row or appends,
   so a re-drive does not double-decide a case. It *does* append fresh audit rows, which is
   correct — the second run genuinely happened.

4. Verify the audit chain survived the outage. Partial writes shouldn't be possible (every
   audit append is one transaction), but after an unclean shutdown it is worth proving rather
   than assuming:

   ```bash
   uv run python scripts/verify_audit_chain.py
   ```

5. Confirm nothing is still stranded, using the query above.

## Known gaps

- **Short-retry activities give up in ~3 seconds**, which is shorter than almost any real
  Postgres restart, failover or `pg_upgrade`. In practice any genuine outage strands in-flight
  cases. See `README.md` in this directory for why this wasn't widened in Phase 12.
- **No transactional outbox.** Both `create_case` (starting the workflow) and
  `submit_review_decision` (signalling it) perform their Temporal call *outside* the database
  transaction, a gap documented since Phase 5. During a Postgres incident the interesting
  variant is the reverse of the usual one: a decision row that committed just before the outage
  may have a signal that never reached a running workflow. `submit_review_decision` logs
  `decision_workflow_missing` when it notices this directly; otherwise a reset (step 3) replays
  the decision from `review_decisions`.
- **The intake API has no retry or circuit breaker at all**, so clients absorb the full outage.
  Edge-level retry/rate limiting is Phase 13 (API Gateway/ALB) scope.

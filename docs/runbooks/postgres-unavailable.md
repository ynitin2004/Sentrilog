# Runbook: Postgres unavailable

Postgres holds every case, document, decision and audit row. Both the intake API and the
pipeline worker connect to it through plain `asyncpg` pools (`services/*/db.py`) with **no
application-level retry** — Temporal's activity retry is the only safety net on the worker side,
and the intake API has none at all.

## What the system does on its own

### Intake API

Every endpoint fails for the duration of the outage. `POST /cases` returns a 500 once the pool
can't serve a connection; clients must retry. Because case creation sends an `Idempotency-Key`,
a client retrying after recovery gets the same case rather than a duplicate (`create_case`'s
`ON CONFLICT (tenant_id, idempotency_key) DO NOTHING` path).

### Pipeline worker — measured, and it depends on where Temporal's store lives

**In this deployment, an in-flight case survives a Postgres outage and finishes on its own.**
Verified rather than reasoned about: `scripts/chaos_test.py postgres` starts a case, confirms it
is genuinely mid-workflow, stops the `postgres` container for 12 seconds — four times the
`_SHORT_RETRY_POLICY` budget — restarts it, and the case reaches `approved` with the workflow
`COMPLETED` and no operator action.

The reason is the topology, and it is worth understanding before trusting the result:
`docker-compose.yml` points **Temporal's own persistence at this same Postgres**. So the outage
takes the workflow engine down too, and a frozen Temporal cannot dispatch activity tasks or
record their failures. Nothing burns a retry budget while the database is gone — the workflow
simply stops, then continues where it left off. Paradoxically, sharing the store makes this
failure *more* survivable, not less.

**On production topology this is expected to be different**, and it is the case to plan for.
With Temporal on its own store (Phase 13), the engine stays healthy while the application
database is gone, so it keeps dispatching activities that immediately fail:

| Activity | Policy | Outcome if only the app database is gone |
|---|---|---|
| `extract_document`, `face_match` | `_LONG_RETRY_POLICY` | Survives an outage up to the 20-minute `schedule_to_close_timeout` |
| `update_case_status`, `risk_score`, `finalize_case`, `deliver_webhooks`, `fetch_case_documents`, `sanctions_screen` | `_SHORT_RETRY_POLICY` | **3 attempts, exhausted ~3 seconds in** |

A `_SHORT_RETRY_POLICY` activity that exhausts its attempts raises `ActivityError`. Unlike the
sanctions and face-match calls — which `KycCaseWorkflow` deliberately catches —
`update_case_status`, `risk_score` and `finalize_case` are awaited with no handler, so the
workflow itself would fail and leave the case at whatever status it last held. *Recovering
Postgres would not recover those cases*; they would be in a failed terminal state needing the
explicit reset in step 3.

**This split-store case is not yet tested** — see the known gap at the bottom. Treat the table
above as the design's expected behaviour, not as a measured result, and re-run the chaos
scenario against the real topology once Phase 13 separates the two stores.

## Detecting it

- Intake API 500s across the board; `asyncpg` connection errors in the structured logs.
- Temporal UI (http://localhost:8080) shows workflows in **Failed** state with an
  `ActivityError` for one of the short-retry activities. (If Temporal shares the database, the
  UI is down too for the duration — its absence is itself the signal.)
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

2. **Restart the pipeline worker and the intake API.** `asyncpg` pools do not re-establish
   themselves after the server they were connected to goes away; the pool is created once at
   startup (`init_pool()`), so a process restart is the cleanest recovery rather than waiting
   for the pool to heal. The chaos test models exactly this step with a
   `close_pool()`/`init_pool()` cycle, and the case does not finish without it.

   ```bash
   # worker
   uv run python -m services.pipeline.workflows.worker
   # intake API
   uv run uvicorn services.intake.main:app
   ```

3. **Check whether any workflows actually failed**, and re-drive those that did. In this
   deployment the expected answer is none — they resume on their own.

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

- **The production-shaped failure is untested.** Because Temporal shares the application's
  Postgres in `docker-compose.yml`, `scripts/chaos_test.py postgres` can only exercise "the
  entire data tier vanished", not "the application database vanished while the workflow engine
  kept running". The second is the one where the ~3-second retry budget actually matters, and it
  is the shape production will have. Giving Temporal its own store is a Phase 13 infrastructure
  decision; the chaos scenario should be re-run against it then, and this runbook corrected
  from measurement rather than from reading the retry policies.
- **Short-retry activities give up in ~3 seconds**, which is shorter than almost any real
  Postgres restart, failover or `pg_upgrade`. Left as-is in Phase 12 (see `README.md` in this
  directory) because widening it is a workflow-timing change, not a configuration tweak.
- **No transactional outbox.** Both `create_case` (starting the workflow) and
  `submit_review_decision` (signalling it) perform their Temporal call *outside* the database
  transaction, a gap documented since Phase 5. During a Postgres incident the interesting
  variant is the reverse of the usual one: a decision row that committed just before the outage
  may have a signal that never reached a running workflow. `submit_review_decision` logs
  `decision_workflow_missing` when it notices this directly; otherwise a reset (step 3) replays
  the decision from `review_decisions`.
- **The intake API has no retry or circuit breaker at all**, so clients absorb the full outage.
  Edge-level retry/rate limiting is Phase 13 (API Gateway/ALB) scope.

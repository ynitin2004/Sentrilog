# Runbook: worker died mid-case

A pipeline worker process (`services/pipeline/workflows/worker.py`) exits — OOM kill, deploy,
crash, host loss — while cases are being processed.

## What the system does on its own

**This one genuinely self-heals, and that is the whole reason the pipeline is built on
Temporal.** Workflow state lives in the Temporal server, not in the worker process. A dead
worker loses no progress:

1. The activity the worker was running stops heartbeating.
2. Temporal waits out the activity's `start_to_close_timeout` (30s for most, 5 minutes for
   `extract_document`/`face_match`), then marks that attempt failed.
3. The attempt is retried under the activity's retry policy, dispatched to **any** worker
   polling that task queue — the original process never has to come back.
4. If no worker is polling, the task simply waits in the queue. Work resumes when one starts.

Phase 5 proved this for real rather than assuming it:
[`scripts/resilience_test_worker.py`](../../scripts/resilience_test_worker.py) registers a
deliberately slow activity so the process can be killed mid-run and restarted, confirming the
workflow continues.

The caveat is the retry budget, not the durability. An activity under `_SHORT_RETRY_POLICY`
(3 attempts) that was already on its last attempt when the worker died has no attempts left,
and the workflow fails — see [postgres-unavailable.md](postgres-unavailable.md) step 3 for the
re-drive procedure, which is identical here.

## Detecting it

- No `worker_starting` log line from a process that should be running. The structured logs
  (Phase 12) emit it with the task queues being polled:

  ```json
  {"event": "worker_starting", "task_queues": ["kyc-case-standard", "..."], "service": "pipeline-worker"}
  ```

- Temporal UI (http://localhost:8080) → the task queue shows no registered pollers, and
  scheduled activities accumulate.
- Cases sitting in `pending`/`processing` longer than normal (query in
  [postgres-unavailable.md](postgres-unavailable.md)).
- The audit trail distinguishes this failure from every other one: a worker killed mid-activity
  leaves a `.started` row with no matching `.completed` or `.failed`, which is exactly why
  Phase 11 records both ends rather than only the outcome.

  ```sql
  SELECT s.case_id, s.event_type, s.created_at
  FROM audit_log s
  WHERE s.event_type LIKE '%.started'
    AND NOT EXISTS (
      SELECT 1 FROM audit_log e
      WHERE e.case_id = s.case_id
        AND e.id > s.id
        AND e.event_type IN (
          replace(s.event_type, '.started', '.completed'),
          replace(s.event_type, '.started', '.failed')
        )
    )
    AND s.created_at < now() - interval '15 minutes'
  ORDER BY s.created_at;
  ```

## Recovery

1. Start a worker. That is usually the entire fix.

   ```bash
   uv run python -m services.pipeline.workflows.worker
   ```

   Queued activities are picked up immediately and the affected workflows continue from where
   they stopped.

2. Check whether any workflows actually *failed* rather than merely stalling (they'd have
   exhausted their retry budget during the gap):

   ```bash
   temporal workflow list --query "ExecutionStatus='Failed'"
   ```

   Re-drive those with `temporal workflow reset` as in
   [postgres-unavailable.md](postgres-unavailable.md) step 3.

3. If the worker died from resource exhaustion, cap its concurrency rather than letting it take
   the same amount of work again — `--max-concurrent-activities` was added in Phase 12 for
   exactly this, and `--tier` lets one process own a single plan tier:

   ```bash
   uv run python -m services.pipeline.workflows.worker --tier standard --max-concurrent-activities 10
   ```

## Known gap

The shipped default is one process polling **all** plan-tier queues, so losing it stops every
tier at once; per-tier pools (the `--tier` flag) are available but are not what
`docker compose`/the documented command runs by default. Per-tier worker pools with independent
sizing are Phase 13 (AWS autoscaling) scope — see the noisy-neighbor findings in the Phase 12
changelog in `PLAN.md`.

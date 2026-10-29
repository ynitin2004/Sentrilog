# Runbook: Qdrant unavailable

Qdrant backs sanctions screening (`services/screening/qdrant_store.py`). It is reached only by
`sanctions_screen_activity`.

## What the system does on its own

**It fails safe, and you should expect cases to pile up in the review queue rather than break.**

1. `sanctions_screen_activity` can't reach Qdrant and raises.
2. Temporal retries it under `_SHORT_RETRY_POLICY` — 3 attempts, exhausted ~3 seconds later.
3. `KycCaseWorkflow` **catches** the resulting `ActivityError` (`kyc_case.py`, the
   `except ActivityError: sanctions_result = None` branch) and sets
   `hard_failure_reason = "sanctions screening activity failed"`.
4. The case is parked for human review with that reason. Risk scoring still runs, with
   `sanctions_hit_count=0`.

The property that matters for compliance: **a case whose sanctions screen never actually ran is
never auto-approved.** It goes to a human, with the reason recorded. Nothing silently treats
"screening didn't happen" as "screening found nothing" — the workflow distinguishes them
precisely because `risk_score_activity` cannot (it has no state for "never ran" distinct from
"0 hits").

**Verified, not reasoned about.** `scripts/chaos_test.py qdrant` stops the real `qdrant`
container with a case in flight and asserts the case lands in `needs_review`. It passes. This is
the one dependency failure in this directory whose documented behaviour has been confirmed
end-to-end against the real container, in the real topology, with no caveats attached.

## Detecting it

- Review queue volume climbs, with cases whose parked reason is
  `sanctions screening activity failed`.
- Structured logs (Phase 12) from the worker carry `trace_id`/`span_id`; filter for
  `sanctions_screen.failed` audit events or the activity's own failures.
- The audit trail records it durably — one `sanctions_screen.failed` row per attempt:

```sql
SELECT case_id, created_at, payload
FROM audit_log
WHERE event_type = 'sanctions_screen.failed'
  AND created_at > now() - interval '1 hour'
ORDER BY created_at DESC;
```

- Directly: `curl -sf http://localhost:6333/readyz` (adjust for the deployed address).

## Recovery

1. Restore Qdrant.

   ```bash
   docker compose up -d qdrant
   docker compose logs --tail=50 qdrant
   ```

2. Confirm the collection still exists and has points — a restarted container with a lost volume
   answers health checks happily while returning zero matches, which would silently screen every
   subsequent case against an empty list:

   ```bash
   curl -s http://localhost:6333/collections/sanctions_entries | python -m json.tool
   ```

   If `points_count` is 0 or the collection is missing, re-ingest before doing anything else:

   ```bash
   uv run python -m services.screening.ingest
   ```

3. **Cases parked during the outage do not re-screen themselves.** They are sitting in the
   review queue awaiting a human, and their workflows have moved on. Either let reviewers decide
   them normally (the sanctions reason is recorded, so the decision is made with that context),
   or re-submit them as new cases if a real screening result is required for the file.

   Find them:

   ```sql
   SELECT c.id, c.subject_name, c.created_at
   FROM cases c
   JOIN audit_log a ON a.case_id = c.id
   WHERE a.event_type = 'sanctions_screen.failed'
     AND c.status = 'needs_review'
   ORDER BY c.created_at;
   ```

## Known gap

An outage shorter than the ~3 seconds `_SHORT_RETRY_POLICY` allows is absorbed; anything longer
parks every in-flight case. For a dependency whose restart takes tens of seconds, that means a
burst of parked cases from a blip that a slightly longer retry window would have ridden out. The
tradeoff was left as-is in Phase 12 (see `README.md` in this directory) — widening it is a
workflow-timing change, not a configuration tweak.

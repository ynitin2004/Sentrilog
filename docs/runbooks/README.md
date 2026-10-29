# Runbooks

Operational procedures for the failure modes Phase 12 examined. Each runbook states what the
system actually does (traced to the code that decides it), how to recognise the failure, and how
to recover.

| Runbook | Failure |
|---|---|
| [worker-died-mid-case.md](worker-died-mid-case.md) | A pipeline worker process dies while cases are in flight |
| [qdrant-unavailable.md](qdrant-unavailable.md) | Qdrant (sanctions vector search) is unreachable |
| [postgres-unavailable.md](postgres-unavailable.md) | Postgres is unreachable |

## The retry policies these all depend on

Every behaviour below follows from two policies in
[`services/pipeline/workflows/kyc_case.py`](../../services/pipeline/workflows/kyc_case.py):

| Policy | Definition | Used by |
|---|---|---|
| `_SHORT_RETRY_POLICY` | `maximum_attempts=3`, Temporal defaults otherwise (1s initial, 2× backoff) — **exhausted roughly 3 seconds after the first failure** | `fetch_case_documents`, `sanctions_screen`, `update_case_status`, `risk_score`, `finalize_case`, `deliver_webhooks` |
| `_LONG_RETRY_POLICY` | 5s initial, 2× backoff, 2min cap, **no attempt limit** — bounded only by `schedule_to_close_timeout=20 minutes` | `extract_document`, `face_match` |

**The asymmetry is worth knowing before an incident:** the activities that merely talk to our own
database give up after ~3 seconds, while the ones calling external model APIs retry for up to 20
minutes. That is backwards relative to how these dependencies actually fail — a Postgres restart
or failover is both more common and more quickly self-resolving than a Gemini outage, yet it is
the database-writing activities that will not survive one. Phase 12 recorded this rather than
changing it, because widening the DB retry window changes workflow timing characteristics that
the Phase 5 design settled deliberately; see the Phase 12 changelog in `PLAN.md`.

No application code retries database or Qdrant calls (`services/*/db.py` and
`services/screening/qdrant_store.py` are plain clients). Temporal's activity retry is the entire
safety net, which is why these runbooks are written in terms of its policies.

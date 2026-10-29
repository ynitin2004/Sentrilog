# Runbooks

Operational procedures for the failure modes Phase 12 examined. Each runbook states what the
system actually does (traced to the code that decides it), how to recognise the failure, and how
to recover.

| Runbook | Failure | Verified how |
|---|---|---|
| [worker-died-mid-case.md](worker-died-mid-case.md) | A pipeline worker process dies while cases are in flight | Phase 5, `scripts/resilience_test_worker.py` (kill and restart a real worker mid-activity) |
| [qdrant-unavailable.md](qdrant-unavailable.md) | Qdrant (sanctions vector search) is unreachable | Phase 12, `scripts/chaos_test.py qdrant` — **passes, no caveats** |
| [postgres-unavailable.md](postgres-unavailable.md) | Postgres is unreachable | Phase 12, `scripts/chaos_test.py postgres` — passes, but only for "the whole data tier vanished"; see that runbook's first known gap |

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
the database-writing activities that have the smaller budget to survive one. Phase 12 recorded
this rather than changing it, because widening the DB retry window changes workflow timing
characteristics that the Phase 5 design settled deliberately; see the Phase 12 changelog in
`PLAN.md`.

**One important correction from actually measuring it.** The 3-second budget only bites when
Temporal is still running to spend it. In this deployment Temporal's persistence is the *same*
Postgres, so a database outage freezes the engine too, no retries are consumed, and an in-flight
case resumes and completes on its own — confirmed by `scripts/chaos_test.py postgres` across a
12-second outage. The strand scenario the policies imply belongs to the split-store topology
production will have, and is therefore still untested. A passing chaos test here does **not**
mean the retry budget is adequate; see the first known gap in
[postgres-unavailable.md](postgres-unavailable.md).

No application code retries database or Qdrant calls (`services/*/db.py` and
`services/screening/qdrant_store.py` are plain clients). Temporal's activity retry is the entire
safety net, which is why these runbooks are written in terms of its policies.

# Decision Traces

[DECISIONS.md](DECISIONS.md) says what was decided. This file says **what fails if the decision is
broken**. A decision with no guard is marked ⚠, so the gap stays visible.

**Verified** means the last recorded run. The full suite ran green on CI at `v0.12.1` (2026-10-30), so
every test guard below carries that date. Scripts run by hand carry the date they were recorded in
[PLAN.md](../PLAN.md). How to run each: [WALKTHROUGH.md](WALKTHROUGH.md).

## Tenancy and auth

| ID | Decision | Guard | Verified |
|---|---|---|---|
| D2 | Shared DB + RLS | `tests/intake/test_authz_matrix.py`: `test_api_key_routes_404_on_another_tenants_resource`, `test_collection_routes_never_include_another_tenants_rows`, `test_dashboard_summary_counts_only_the_callers_own_cases` | CI, 2026-10-30 |
| D3 | Unprivileged app role | CI's `DATABASE_URL` is `sentrilog_app`, and `scripts/wait_for_dependencies.py` connects as it. ⚠ Nothing fails if `services/` is pointed at the superuser in a deployment | CI, 2026-10-30 |
| D4 | `SECURITY DEFINER` lookups | `tests/intake/test_cases.py`, `test_review.py`: the auth paths work under RLS as the app role | CI, 2026-10-30 |
| D5 | Hashed API keys | `test_a_reviewer_token_is_rejected_on_api_key_routes`, `test_an_api_key_is_rejected_on_reviewer_routes`, `test_revoked_credentials_are_rejected_everywhere`. ⚠ The choice of hash itself has no guard | CI, 2026-10-30 |
| D6 | 404 vs 403 | `test_cross_tenant_get_returns_404_not_403`, `test_an_auditor_may_read_but_not_claim_or_decide`, `test_an_auditors_blocked_decision_leaves_no_trace` | CI, 2026-10-30 |

## Pipeline

| ID | Decision | Guard | Verified |
|---|---|---|---|
| D1 | Temporal | `tests/pipeline/workflows/test_kyc_case_workflow.py` on a real server; `scripts/resilience_test_worker.py` kills a real worker mid-activity | CI, 2026-10-30; script at Phase 5 |
| D7 | MRZ first | `tests/pipeline/extraction/test_mrz.py`, `test_extract.py` | CI, 2026-10-30 |
| D8 | VLM behind a protocol | `test_document_not_visible_is_treated_as_a_failed_attempt_not_a_valid_empty_result`; `scripts/smoke_test_gemini_extraction.py` is the live check. ⚠ "Synthetic only" is policy, with no technical guard | CI, 2026-10-30 |
| D9 | Self-hosted faces | `tests/pipeline/test_face_match.py` on the real model | CI, 2026-10-30 |
| D10 | Vector + phonetic | `test_mohammed_muhammad_share_a_primary_code`, `test_phonetic_match_surfaces_a_hit_vector_search_alone_would_miss`, `test_real_gemini_and_qdrant_end_to_end` (**skips on CI**, runs locally with a key). ⚠ The 0.55 threshold has no recall measurement | CI, 2026-10-30; live test local |
| D11 | Never auto-reject | `tests/pipeline/test_risk_scoring.py`: `test_sanctions_hit_forces_review_regardless_of_other_scores`, `test_sanctions_hit_takes_priority_over_missing_face`; `test_sanctions_hit_parks_case`, `test_no_face_detected_parks_case`, `test_low_risk_score_without_hard_flags_parks_case` | CI, 2026-10-30 |
| D12 | Queue is a status | `test_review_queue_is_tenant_scoped`; `tests/intake/test_review_e2e.py::test_ambiguous_case_parks_reviewer_decides_workflow_completes_webhook_delivered` | CI, 2026-10-30 |
| — | Screening and face match run concurrently | `test_face_match_and_sanctions_screen_run_concurrently_not_sequentially` (checks execution history) | CI, 2026-10-30 |
| — | Hard failures park, not crash | `test_fetch_document_hard_failure_parks_not_crash`, `test_extraction_activity_hard_failure_parks_case`; `scripts/chaos_test.py qdrant` | CI, 2026-10-30; script at Phase 12 |
| — | Tiers get distinct queues | `tests/pipeline/workflows/test_task_queues.py`; `scripts/load_test_noisy_neighbor.py` | CI, 2026-10-30; script at Phase 12 |

## Audit, delivery, real-time

| ID | Decision | Guard | Verified |
|---|---|---|---|
| D13 | Hash chain | `tests/pipeline/test_audit.py`: `test_concurrent_writes_for_the_same_tenant_never_fork_the_chain`, `test_verify_chain_detects_a_tampered_historical_row`; `test_idempotency_key_replay_does_not_duplicate_the_audit_row`; `scripts/verify_audit_chain.py` | CI, 2026-10-30 |
| D14 | `LISTEN/NOTIFY` | `tests/intake/test_events_stream.py`: `test_generator_delivers_a_matching_tenant_event`, `test_generator_never_delivers_another_tenants_event` | CI, 2026-10-30 |
| D15 | Signed webhooks | `tests/pipeline/workflows/test_deliver_webhooks_activity.py` against a real HTTP server, including an unreachable endpoint recorded as failed | CI, 2026-10-30 |

## Engineering practice

| ID | Decision | Guard | Verified |
|---|---|---|---|
| D16 | Secrets seam | `tests/common/test_secrets.py`, `test_settings_secrets.py` | CI, 2026-10-30 |
| D17 | Lazy clients | `tests/pipeline/workflows/test_activity_client_construction.py` imports in a fresh subprocess with an empty credential; confirmed failing on the old code | CI, 2026-10-30 |
| D18 | s3mock | `test_upload_flow_end_to_end_via_presigned_url` checks where the bytes went. ⚠ Says nothing about real S3 | CI, 2026-10-30 |
| D19 | Local = CI | `.pre-commit-config.yaml` and `ci.yml` run the same mypy paths. ⚠ Nothing enforces that they stay equal, and no branch protection requires the check | CI, 2026-10-30 |

## Deployment (D20–D25): no guards yet

All ⚠, because nothing is built. The intended guards, from [DEPLOY.md](DEPLOY.md):

| ID | Intended guard |
|---|---|
| D20 | `scripts/live_smoke.py` against the public URL; UptimeRobot on `/health` |
| D21 | A timed Plan B dry run before an interview |
| D22 | `scripts/chaos_test.py postgres` run on the deployed topology |
| D23 | The S3 operation check against Garage, then the live smoke |
| D24 | A first-case timing recorded in the trace |
| D25 | A new row in [CASE-TRACES.md](CASE-TRACES.md) each day |

## Open gaps worth knowing in a discussion

- **No threshold has a measurement behind it** (0.55, 0.85, 0.6/0.4). The eval harness is roadmap Phase 13.
- **The split-store Postgres failure is untested.** The passing chaos test covers only the shared-store topology.
- **No frontend CI job,** so a console regression cannot fail a build.
- **No branch protection,** so a green check is informative, not required.

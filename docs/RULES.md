# Engineering Rules

Changing a rule needs a [DECISIONS.md](DECISIONS.md) entry, and the Quiz Rule in `CLAUDE.md` applies.

## 1. Prohibitions

- No connecting as the superuser from `services/`. Running as `sentrilog` silently disables RLS.
- No tenant data access outside `tenant_connection()` and its GUC.
- No 403 for a cross-tenant resource; return 404.
- No credential as a plain `str`, and no reading one from `os.environ` in application code. Use
  `SecretStr` through the provider seam.
- No third-party client built at module scope. Use a lazy accessor like `_get_vlm_client`, or a
  credential becomes an import-time requirement for every activity.
- No heavy import at module scope in anything `activities.py` imports. `easyocr` stays deferred.
- No second copy of the audit hash formula.
- No UPDATE or DELETE on `audit_log`, and no state transition without an audit row.
- No hand-written duplicate of API types in the frontend. Generate them from the OpenAPI schema.
- No real ID document or selfie through Gemini's free tier. Synthetic images only.
- No credentials, real names or real documents in git. The repository is public.
- No new broker, cache or store without a revisit trigger having fired ([improvements.md](../improvements.md) §7).

## 2. Reliability contracts

- Client retries are certain, so case creation is idempotent on `(tenant_id, idempotency_key)`, and a
  replay writes no second audit row.
- Workflow starts are idempotent on `case_id`.
- Every failure path ends in a human's queue or a recorded state. A case is never stranded silently,
  and a screen that never ran is never read as "0 hits".
- Every `needs_review` case goes through `_park_for_review_and_finalize`, so a live workflow can
  receive the decision signal.
- Failure states are visible: webhook outcomes are recorded in `webhook_deliveries`.
- Logs are JSON with `trace_id` and `span_id`. Tracing interceptors go on both client and worker.
- Readiness is checked by performing the real operation, not by probing a port.

## 3. AI boundaries

- Every model output is validated against a Pydantic schema, with bounded retry that feeds the
  validation error back. Exhausted retries mean `needs_review`.
- Prefer the deterministic path: a checksum-valid MRZ beats a model and costs nothing.
- **A case may be auto-approved but never auto-rejected.** A sanctions hit or undetected face always
  goes to a human, whatever the combined score says.
- Every extraction records `model_version`, and audit rows record the input hash.
- Screening is tuned for recall: a false negative is a compliance failure, a false positive is work.
- Every place model output influences behaviour is registered in [FLOW.md](FLOW.md) §3.
- A threshold is a guess until the eval harness measures it. Say so wherever one is cited.

## 4. Testing gates

A phase is not done on happy-path-works. Full standard: [PLAN.md](../PLAN.md) §9.

- Real infrastructure over mocks. Fake only what bills money or cannot be deterministic, and say which.
- Negative and failure cases: malformed input, retry exhaustion, partial writes, process death, replay.
- Authorization is a table of every route, because scattered checks cannot answer "is every route covered?"
- Verify a test can fail: run it against the old code, or against a tampered row.
- Verify cleanup by querying afterwards. Trusted cleanup fails quietly.
- A harness that measures nothing is worse than none. Check that it actually exercised the scenario.
- Run the suite with a key and with `GEMINI_API_KEY=""` before pushing.
- Anything skipped is written down as a known gap.

## 5. CI

- Local gates equal CI gates. A pre-commit hook that differs from CI only delivers news after a push.
- CI applies the real schema files and runs as `sentrilog_app`.
- A failing pipeline is fixed, not bypassed. No suppressions added to make a check pass.
- `.github/workflows/ci.yml` stays on plain `uv run <tool>`; the `python -m` form is for this machine only.

## 6. Git and workflow

- Branch per phase: `phase-N-<short-name>`. At least 4 logical commits per phase.
- `feat(phaseN): …` for the main commit; `fix`, `chore`, `test`, `docs` for the rest.
- Git identity through per-command environment variables, never `git config`.
- `merge --no-ff` into `main`, tag `vX.Y.Z`.
- Confirm with the user before any push. PR first, then merge and tag.
- Never skip hooks.
- `PLAN.md`'s changelog is history; closed gaps are struck through and annotated, not deleted.
- Every working session ends by updating `docs/MEMORY.md`.

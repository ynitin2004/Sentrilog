# Sentrilog — KYC / AML Document Intelligence Pipeline — Project Plan

> **Project name:** Sentrilog
> **Repository:** <https://github.com/ynitin2004/Sentrilog>
> **License:** Skipped for Phase 1 (no LICENSE file — proprietary by default absent one; revisit before any external release).

## 1. Overview

**Hook:** Automated identity verification — OCR + a vision-language model extract and validate ID documents, match faces, and screen against sanctions lists with fuzzy matching, producing an auditable risk score.

**Why it matters:** Sits on the fintech × AI seam (Onfido, Persona, Sumsub). It's a multi-stage async pipeline where each stage can fail, retry, or escalate to a human — with a compliance-grade audit trail. This is a production system, not a prototype: every design choice below is made with regulatory auditability and operational resilience as first-class requirements, not afterthoughts.

## 2. Hard parts (the actual engineering problems)

- **VLM extraction with structured output** — force a JSON schema out of a document image; validate + retry.
- **Fuzzy sanctions screening** — "Mohammed" vs "Muhammad": phonetic + vector similarity vs OFAC/UN, tuned for recall.
- **Human-in-the-loop** — low-confidence cases pause the pipeline and resume on the reviewer's decision.
- **Immutable audit trail** — every decision, model version, and input hash stored for regulators.

## 3. Architecture

```
Client → Intake API (FastAPI)
           ├─→ presigned PUT → Encrypted S3 (raw ID + selfie)
           └─→ start Temporal workflow (case_id)

Temporal Workflow "kyc_case"
  1. OCR (PaddleOCR/Textract) + VLM extract → structured JSON
  2. validate against Pydantic schema
       fail → retry (bounded) → still fail → route to review_queue (reason: extraction_failure)
  3. parallel:
       a. face_match(selfie, id_photo) → similarity score
       b. sanctions_screen(name, dob, nationality) → vector + phonetic hits
  4. risk_score = f(confidence, face_score, sanctions_hits)
  5. decision:
       clear        → APPROVED
       low_conf/hit → review_queue → wait_for_signal(reviewer_decision) → APPROVED/REJECTED
  6. write immutable audit record (every transition, not just the end state)
```

Face match and sanctions screening run **in parallel** — they're independent evidence sources feeding one risk score, and running them sequentially only adds latency.

### Production stack

| Layer | Tech |
|---|---|
| Intake | FastAPI · encrypted S3 · presigned uploads |
| Extract | PaddleOCR / Textract · VLM (structured output) · face embeddings |
| Screen | Vector DB (Qdrant) · phonetic match · OFAC / UN feeds |
| Orchestrate | Temporal · review queue · retry + escalate |
| Record | Postgres (append-only) · audit log · model versioning |

**Temporal over Celery:** native support for long-running workflows that pause for external signals (the reviewer decision), built-in per-activity retry policies, and full execution history that doubles as half the audit trail. Trade-off: an extra stateful system to run/pay for (Temporal Cloud vs. self-hosted) — worth it here given the compliance requirements.

## 4. Core data model (sketch)

```sql
cases(id, status, created_at, subject_name, subject_dob, risk_score, decision, decided_at)
documents(id, case_id, s3_key, doc_type, sha256, uploaded_at)
extractions(id, case_id, document_id, model_version, raw_json, confidence, valid)
face_matches(id, case_id, similarity_score, model_version)
sanctions_hits(id, case_id, list_source, matched_name, match_score, method[vector|phonetic])
review_decisions(id, case_id, reviewer_id, decision, justification, decided_at)
audit_log(id, case_id, event_type, actor, model_version, input_hash, payload, prev_row_hash, created_at)
-- audit_log is append-only: INSERT-only grants, hash-chained via prev_row_hash for tamper evidence
```

## 5. Infrastructure (AWS — deferred to Phase 10)

| Concern | Choice | Why |
|---|---|---|
| Compute | ECS Fargate for API/workers; Temporal self-hosted on Fargate *or* Temporal Cloud | Fargate avoids node-patching ops burden; Temporal Cloud trades $ for removing a stateful system from on-call |
| Storage | S3 (SSE-KMS), separate buckets per data class | Blast-radius isolation |
| DB | RDS Postgres, Multi-AZ, encrypted, PITR | Needed both for recovery and "prove nothing was altered" audits |
| Vector DB | Qdrant on ECS or Qdrant Cloud | Self-host fine at this scale; managed removes an op burden |
| Secrets | Secrets Manager + customer-managed KMS CMKs | Auditors ask about key ownership specifically |
| Networking | Private subnets, no public ingress except ALB, VPC endpoints for S3/KMS/Secrets Manager | Keeps PII traffic off the public internet internally |
| IAM | Per-service roles, least privilege | A face-match worker shouldn't read the audit table's KMS key |

IaC: Terraform, module-per-layer (`network/`, `data/`, `compute/`, `security/`), one state per environment, stricter apply gate (PR + plan artifact + manual approval) for `prod`.

## 6. Open decisions (need your call before relevant phase)

1. **PaddleOCR (self-hosted) vs Textract (managed)** — cost/ops vs. speed-to-ship.
2. **Temporal self-hosted vs Temporal Cloud** — ops burden vs. cost.
3. **Data retention period** — regulatory/jurisdictional, not engineering; drives S3 lifecycle policy.
4. **Face match model** — Rekognition (managed, data leaves VPC) vs. self-hosted ArcFace/InsightFace (full control, more MLOps).

## 7. Project naming shortlist

| Name | Rationale |
|---|---|
| **Custos** | Latin "guardian" — short, evokes the gatekeeper role of KYC |
| **Veridex** | Veri(ty) + index — verification + searchable audit record |
| **Attestly** | "Attest" is literal compliance vocabulary |
| **Provenance** | Names exactly what the audit trail is |
| **Sentrilog** | Sentry + log — leans into the immutable-trail angle |
| **ClearChain** | "Clear" (risk-cleared) + "chain" (hash-chained audit log) |

Leaning toward **Custos** or **Veridex**.

## 8. Delivery plan — 10 phases

Phases 1-9 build and prove the system locally (Docker Compose standing in for S3/RDS/Qdrant). AWS is touched only in Phase 10, so cloud debugging and logic debugging never happen at the same time.

**Workflow convention for every phase:**
- Branch per phase: `phase-N-<short-name>`, PR into `main`.
- Merge only when the phase's exit criteria **and** the testing standard below both pass.
- Tag `main` after merge: `git tag v0.<N>.0 && git push --tags`.
- Commit convention: `feat(phaseN): <what>` for the main commit, `fix/chore/test(phaseN): ...` for follow-ups.
- Update this file's **Status** table and **Changelog** (§10) as the last commit of the phase.

**Testing standard (applies to every phase, not just the ones with obvious business logic):**
A phase is not "done" on happy-path-works. Before merging, verify at the level expected of a senior engineer on a compliance-grade system:

- **Negative and edge cases**, not just the golden path — malformed input, empty/oversized payloads, network/service unavailability, concurrent access where relevant.
- **Failure-mode tests specific to this system**: retry exhaustion, partial writes, worker/process death mid-operation, replay/idempotency where a case could be processed twice.
- **Regression check** — re-run the previous phase's test suite, not just the new phase's; nothing earlier should silently break.
- **Static checks green**: lint (`ruff`), formatting (`black --check`), types (`mypy`), and `pre-commit run --all-files` all pass with zero suppressions added to make them pass.
- **Test evidence recorded**: what was tested and the result gets a short note in the Changelog entry for that phase — not just "tests pass," but *which* scenarios were exercised, so a reviewer six months from now can tell what's actually covered.
- Anything skipped (e.g., load testing deferred to Phase 9) is stated explicitly as a known gap, not silently omitted.

### Phase 1 — Repo & project scaffolding
- `git init`, connect remote to `github.com/ynitin2004/Sentrilog`, `.gitignore`, `README.md` (problem statement + architecture diagram). No `LICENSE` file for now (decision: skip, revisit later).
- Layout: `services/intake/`, `services/pipeline/`, `services/screening/`, `infra/terraform/`, `docs/`.
- Tooling: `uv`, `ruff` + `black` + `mypy`, pre-commit hooks.
- CI skeleton: GitHub Actions — lint + no-op test on every PR.
- **Exit criteria:** `pre-commit run --all-files` and CI both pass on the empty scaffold.
- **Phase 1 testing note:** no business logic exists yet, so "testing" means proving the scaffold itself is trustworthy — pre-commit hooks actually catch a deliberately introduced lint error, CI runs on a real PR (not just locally), and the repo structure/tooling versions are pinned (not "latest") so the build is reproducible.

### Phase 2 — Local infra (Docker Compose)
- `docker-compose.yml`: Postgres, Qdrant, MinIO, Temporal dev server (+ its Postgres/Elasticsearch).
- `make up` / `make down`; seed script for the Postgres schema.
- **Exit criteria:** `make up` brings up all services healthy; empty tables visible via `psql`.

### Phase 3 — Intake API
- FastAPI: `POST /cases` → case row + presigned PUT URLs for ID + selfie.
- Encryption on MinIO (or explicitly note deferral to real KMS in Phase 10).
- Integration test: upload sample ID image, confirm bucket object + case row.
- **Exit criteria:** curl/Postman flow uploads a file; case row has the correct S3 key.

### Phase 4 — Extraction (OCR + VLM structured output)
- Pydantic `IDDocument` schema.
- OCR pass (PaddleOCR to start) + schema-constrained VLM call.
- Bounded retry (2-3 tries) with validation-error injected into the retry prompt.
- Confidence score attached to output.
- **Exit criteria:** unit tests cover valid extraction, malformed-image retry path, and exhausted-retries → `needs_review` flag.

### Phase 5 — Temporal workflow wiring
- `kyc_case` workflow calls extraction as a Temporal activity; retry policy at the Temporal level.
- **Exit criteria:** kill the worker mid-run, restart it, workflow resumes without losing progress.

### Phase 6 — Face match + sanctions screening (parallel)
- Face match activity: embedding similarity between selfie and ID photo.
- Sanctions screening activity: sample OFAC/UN list into Qdrant; vector + phonetic match, including "Mohammed/Muhammad" test case.
- **Exit criteria:** Temporal UI timeline shows both activities executing concurrently, not sequentially.

### Phase 7 — Risk scoring + human review queue
- Risk scoring combining confidence + face score + sanctions hits.
- `review_queue` table + minimal API (list pending, submit decision).
- Workflow signal handler unblocks on reviewer submission.
- **Exit criteria:** an ambiguous test case parks in the queue, a manual decision resolves it, workflow completes.

### Phase 8 — Immutable audit trail
- `audit_log` with `prev_row_hash` chaining; `INSERT`-only DB grants.
- Retrofit: every prior-phase activity now writes audit rows on entry/exit.
- Verification script walks the hash chain to detect tampering.
- **Exit criteria:** manually editing a historical row breaks the chain-verification script.

### Phase 9 — Hardening: security, observability, tests
- Structured logging + OpenTelemetry tracing.
- Load test the extraction stage specifically (bottleneck/cost center).
- Chaos test: kill workers / Qdrant / Postgres mid-workflow, confirm recovery.
- Secrets moved out of `.env` into a pattern mirroring Secrets Manager.
- **Exit criteria:** documented, tested runbooks for "worker died mid-case" and "Qdrant unavailable."

### Phase 10 — AWS infra + deploy
- Terraform modules: network, RDS, S3 (KMS), Qdrant, Temporal per decisions made.
- GitHub Actions: build → push → `terraform plan` on PR → manual-approval `apply`.
- Staging first, smoke-tested end-to-end, then prod.
- **Exit criteria:** a real case flows through staging in AWS, audit chain verifies, rollback plan documented before prod go-live.

## 9. Status

| Phase | Status | Tag | Commit | Date |
|---|---|---|---|---|
| 1. Repo & scaffolding | **Done** | `v0.1.0` | `5dc28ed` | 2026-07-20 |
| 2. Local infra | Not started | — | — | — |
| 3. Intake API | Not started | — | — | — |
| 4. Extraction | Not started | — | — | — |
| 5. Temporal wiring | Not started | — | — | — |
| 6. Face match + screening | Not started | — | — | — |
| 7. Risk scoring + review queue | Not started | — | — | — |
| 8. Audit trail | Not started | — | — | — |
| 9. Hardening | Not started | — | — | — |
| 10. AWS infra + deploy | Not started | — | — | — |

## 10. Changelog

### Phase 1 — 2026-07-20 (`v0.1.0`, commit `5dc28ed`)

**Done:**

- Repo initialized and connected to <https://github.com/ynitin2004/Sentrilog> (`main` branch).
- Layout scaffolded: `services/{intake,pipeline,screening}`, `infra/terraform/`, `docs/`, `tests/`.
- Python tooling: `uv`-managed `pyproject.toml`, `ruff`, `black`, `mypy` (strict), `pytest`, pre-commit hooks.
- GitHub Actions CI (`.github/workflows/ci.yml`): installs `uv`, runs ruff/black/mypy/pytest on every push and PR to `main`.
- README.md written with architecture, stack table, and repo layout.
- No `LICENSE` file added (explicit decision — proprietary by default, revisit before any external release).

**Testing evidence (senior-engineer standard, per §8):**

- `uv run ruff check .`, `uv run black --check .`, `uv run mypy services tests`, `uv run pytest -v` all run clean locally against the full scaffold.
- Pre-commit hooks were **proven, not assumed**: a throwaway file with an unused variable and a missing type annotation was deliberately added, confirmed that `ruff` flagged the unused variable (F841) and auto-fixed an unused import, and `mypy` flagged the missing annotation — then the probe file was deleted and never staged/committed.
- `pre-commit run --all-files` passes clean on the real, committed file set.
- **Known gap (explicit, not silent):** the first two pushes to `main` (`5dc28ed`, `a94443f`) both show CI as `failure` on GitHub, but not for a code/workflow reason — the GitHub API reports *"The job was not started because your account is locked due to a billing issue."* The runner never started; nothing in `ci.yml` has been validated on GitHub's infra yet. **Action item (owner: repo owner, not engineering):** resolve the GitHub account billing lock, then re-push (or re-run) to get a real CI signal before Phase 2 work lands.

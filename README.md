# Sentrilog

**Sentrilog** is a KYC/AML document intelligence pipeline: automated identity verification that extracts and validates ID documents (OCR + a vision-language model), matches faces, screens against sanctions lists with fuzzy matching, and produces an auditable risk score for every case.

This is built as a production system from the start — a multi-stage async pipeline where each stage can fail, retry, or escalate to a human reviewer, backed by a compliance-grade, tamper-evident audit trail.

## Why this is hard

- **VLM extraction with structured output** — forcing a JSON schema out of a document image, with validation and bounded retry.
- **Fuzzy sanctions screening** — e.g. "Mohammed" vs "Muhammad": phonetic + vector similarity against OFAC/UN lists, tuned for recall.
- **Human-in-the-loop** — low-confidence cases pause the pipeline and resume on a reviewer's decision, potentially days later.
- **Immutable audit trail** — every decision, model version, and input hash is stored, hash-chained, and provably tamper-evident for regulators.

## Architecture

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

| Layer | Tech |
|---|---|
| Intake | FastAPI · encrypted S3 · presigned uploads |
| Extract | PaddleOCR / Textract · VLM (structured output) · face embeddings |
| Screen | Vector DB (Qdrant) · phonetic match · OFAC / UN feeds |
| Orchestrate | Temporal · review queue · retry + escalate |
| Record | Postgres (append-only) · audit log · model versioning |

## Project layout

```
services/
  intake/      # FastAPI intake API, presigned uploads
  pipeline/    # Temporal workflows/activities: extraction, face match, screening, risk scoring
  screening/   # Sanctions list ingestion + vector/phonetic matching
infra/
  terraform/   # AWS infrastructure (Phase 10)
docs/          # Design notes, runbooks
PLAN.md        # Full project plan: architecture, data model, 10-phase delivery plan, status
```

## Status

This project is being delivered in 10 phases, local-first (Docker Compose standing in for AWS), with AWS infrastructure introduced only in the final phase. See [PLAN.md](PLAN.md) for the full plan, current phase status, and changelog.

## Development

Requires [uv](https://docs.astral.sh/uv/) for Python tooling.

```bash
uv sync                        # install dependencies
uv run pre-commit install      # set up git hooks
uv run pre-commit run --all-files
```

CI (GitHub Actions) runs lint, type-check, and tests on every PR.

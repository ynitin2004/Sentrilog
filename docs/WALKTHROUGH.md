# WALKTHROUGH — running and verifying Sentrilog

For a developer or interviewer with the repository, Docker Desktop and `uv`. Commands use the
`uv run python -m` form this Windows machine needs; on Linux, plain `uv run <tool>` also works.
Expected results are the recorded ones at `v0.12.1`. They were not re-run when this document was written.

## 1. Local stack

```bash
cp .env.example .env            # add a Gemini key from https://aistudio.google.com/ for real extraction
docker compose up -d --wait     # postgres, qdrant, s3, temporal, temporal-ui, frontend
uv sync --all-groups
uv run python scripts/wait_for_dependencies.py
uv run python -m services.screening.ingest     # needs a Gemini key; one embedding per entry
```

Temporal takes about 50 s to answer RPCs, which is why the readiness script performs real operations
rather than probing ports. Temporal UI: <http://localhost:8080>. Console: <http://localhost:8081>.

Run the API and the worker on the host, because `docker-compose.yml` does not containerise them:

```bash
uv run python -m uvicorn services.intake.main:app --port 8000
uv run python -m services.pipeline.workflows.worker
```

On this machine, Application Control blocks torch, so a real OCR call fails on the host. The test suite
is arranged so that this does not affect collection.

## 2. Static gates (what CI runs)

```bash
uv run python -m ruff check .
uv run python -m black --check .
uv run python -m mypy services tests scripts
uv run pre-commit run --all-files
```

Expected: all clean, with no suppressions added.

## 3. The test suite, twice

```bash
uv run python -m pytest -q
GEMINI_API_KEY="" OTEL_TRACES_EXPORTER=none uv run python -m pytest -q
```

| Run | Expected |
|---|---|
| With a key | 192 passed |
| Empty key (CI's configuration) | 191 passed, 1 skipped (`test_real_gemini_and_qdrant_end_to_end`) |

Only about 40 tests are pure units; the rest use real Postgres, Temporal, Qdrant and an S3 API. To prove
the suite's premise, use `sentrilog_app` in `DATABASE_URL`: the superuser would bypass RLS.

## 4. A case by hand

```bash
uv run python scripts/seed_dev_tenant.py acme acme     # prints an API key once
uv run python scripts/seed_dev_reviewer.py <tenant-id> reviewer@example.com reviewer   # prints a token once
```

`POST /cases` with `Authorization: Bearer <key>` and an `Idempotency-Key` header returns presigned PUT
URLs. Upload a **synthetic** ID image and selfie to them, sending exactly the `Content-Type` that was
declared. Then watch the workflow in Temporal UI.

To see the review loop, open the console, enter `http://localhost:8000` and the reviewer token on
`/connect`, and decide the case. The decision signals the workflow, which finalises it and delivers webhooks.

## 5. Failure behaviour

| Command | Proves | Recorded result |
|---|---|---|
| `uv run python scripts/chaos_test.py qdrant` | A screen that never ran is never auto-approved | Case parks in `needs_review` |
| `uv run python scripts/chaos_test.py postgres` | Recovery from a 12 s outage, **for the shared-store topology only** | Case reaches `approved` |
| `uv run python scripts/resilience_test_worker.py` | A killed worker loses no progress | Workflow resumes on restart |
| `uv run python scripts/load_test_noisy_neighbor.py` | Tier isolation | I/O 3.74× → 1.14×; CPU 5.44× → 2.51× |
| `uv run python scripts/load_test_extraction.py` | Throughput ceiling and API cost | Flat at 32; 58 % of cases with no API call |

Chaos scenarios stop real containers, so do not run them against a stack you are using. Afterwards,
confirm the tenant count is unchanged: cleanup is verified, not trusted.

## 6. Audit chain

```bash
uv run python scripts/verify_audit_chain.py            # all tenants
uv run python scripts/verify_audit_chain.py <tenant-id>
```

Expected: every chain intact. Editing a historical row as the superuser in a scratch environment must
make this report the break. The script hardcodes the local superuser DSN today.

## 7. Frontend

```bash
cd frontend
npm ci
npm run typecheck && npm run lint && npm test && npm run build
npm run storybook        # http://localhost:6006
```

After a backend contract change, regenerate the types:

```bash
uv run python scripts/export_openapi.py
cd frontend && npm run generate-types
```

## 8. CI

Workflow: `.github/workflows/ci.yml`, job `lint-and-test`. Latest recorded result on a real runner:
191 passed, 1 skipped. The `ci-watch` skill follows a run. GitHub emails on failure by default, not on
success, so check the Actions tab.

## 9. Deployment

See [DEPLOY.md](DEPLOY.md). Nothing there is built yet.

# WALKTHROUGH — running and verifying Sentrilog

For a developer or interviewer with the repository, Docker Desktop and `uv`. Commands use the
`uv run python -m` form this Windows machine needs; on Linux, plain `uv run <tool>` also works.
Expected results are the ones recorded at `v0.13.2` (2026-11-03), each re-run for that release.

## 1. Local stack

```bash
cp .env.example .env            # add a Gemini key from https://aistudio.google.com/ for real extraction
docker compose up -d --wait     # postgres, qdrant, s3, temporal, temporal-ui, frontend
uv sync --all-groups
uv run python scripts/wait_for_dependencies.py
uv run python -m services.screening.ingest --source ofac --from-dir services/eval/data/ofac-sdn-20261103
#   -> "built sanctions-ofac-sdn-20261103-e568aca69f-char-ngram-2-3-1024: 16162 entries", ~30 s, no API key
#   --source ofac alone downloads today's list; --list shows snapshots; --activate COLLECTION rolls back
```

Temporal takes about 50 s to answer RPCs, which is why the readiness script performs real operations
rather than probing ports. Temporal UI: <http://localhost:8080>. Console: <http://localhost:8081>.

Run the API and the worker on the host, because `docker-compose.yml` does not containerise them:

```bash
uv run python -m uvicorn services.intake.main:app --port 8000
uv run python -m services.pipeline.workflows.worker
```

torch loads on this machine as of 2026-11-03, so real OCR runs on the host, at about 8 s a page.
The import is lazy, so if torch is ever blocked again only OCR stops; collection is unaffected.

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
| With a key | 243 passed |
| Empty key (CI's configuration) | 242 passed, 1 skipped (`test_real_gemini_and_qdrant_end_to_end`) |

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

Expected: every chain intact ("OK: all audit chains intact"; 24 tenants in the dev database on
2026-11-03). Editing a historical row as the superuser in a scratch environment must make this report
the break. Before Phase 13 it also reported every *finalised* case as broken, a verifier bug (D36). The
script hardcodes the local superuser DSN today.

## 7. Frontend

```bash
cd frontend
npm ci
npm run typecheck && npm run lint && npm test && npm run build     # 38 unit tests
npm run storybook        # http://localhost:6006
```

The end-to-end flow needs the API and a worker running (§1), then:

```bash
uv run python scripts/seed_e2e.py frontend/e2e/.fixture.json      # the real workflow parks a case
cd frontend && npx playwright install chromium && npx playwright test   # 1 passed
cd .. && uv run python scripts/seed_e2e.py --cleanup frontend/e2e/.fixture.json
```

On a Windows checkout `npm run format:check` fails on line endings alone (`core.autocrlf`); CI's Linux
checkout does not.

After a backend contract change, regenerate the types:

```bash
uv run python scripts/export_openapi.py
cd frontend && npm run generate-types
```

## 8. CI

Workflow: `.github/workflows/ci.yml`, four jobs: `lint-and-test` (the suite, 242 + 1 skipped expected),
`eval` (the harness and its gates), `frontend`, and `e2e`. The last three were added in Phase 13 and had
not yet run on GitHub when it closed. Latest recorded `lint-and-test` result on a real runner: 191
passed, 1 skipped (`v0.12.1`). The `ci-watch` skill follows a run. GitHub emails on failure by default, not on
success, so check the Actions tab.

## 9. Evaluation harness

```bash
uv run python -m services.eval screening --compare        # all four embeddings, with and without reranker: ~20 min
uv run python -m services.eval screening                  # the shipped configuration only: ~2 min
uv run python -m services.eval face                       # LFW DevTest; first run downloads 173 MB and embeds ~40 min here
uv run python -m services.eval extraction                 # 300 MRZ parses + 100 OCR pages: ~13 min here
uv run python -m services.eval risk [--policy FILE]       # instant; reads the results of face and screening
```

Add `--write-results` to update `services/eval/results/*.json`, `--record` for an `eval_runs` row, and
`--report PATH` for Markdown. Recorded 2026-11-03:

| Suite | Expected |
|---|---|
| Screening (shipped) | n-gram, threshold 0.73, phonetic off: 95.67 % recall, 13.45 % FPR |
| Face | TAR 95.0 % at FAR 1e-3, 95.4 % at 1e-2; 0 no-face images |
| Extraction | parse F1 0.981 (27 wrong but accepted); OCR F1 0.950 on 24 of 100 accepted (6 wrong) |
| Risk, built-in | precision 99.95 %, review burden 84.05 %, leaks 0 % impostor / 0.85 % listed |
| Risk, `services/eval/policies/example-balanced.json` | precision 99.95 %, review burden 23.16 %, leaks 0 % / 4.09 % |

Exit code 1 means a gate in `services/eval/report.py` failed.

## 10. Deployment

See [DEPLOY.md](DEPLOY.md). Nothing there is built yet.

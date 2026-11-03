---
name: verify-gates
description: Run every local Sentrilog quality gate the way CI does, including the empty-Gemini-key run. Use before committing or pushing, before closing a phase, or when asked whether the code is green.
---

# Verify gates

Use the `python -m` form. Application Control blocks the `.venv` executables on this machine.

1. Dependencies: `docker compose up -d --wait`, then `uv run python scripts/wait_for_dependencies.py`.
2. Static checks:
   - `uv run python -m ruff check .`
   - `uv run python -m black --check .`
   - `uv run python -m mypy services tests scripts`
   - `uv run pre-commit run --all-files`
3. Suite with the local key: `uv run python -m pytest -q`.
4. Suite as CI runs it: `GEMINI_API_KEY="" OTEL_TRACES_EXPORTER=none uv run python -m pytest -q`.
5. If `frontend/` changed, from `frontend/`: `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`, the same as CI's `frontend` job. If the review path changed, run the Playwright flow as `docs/WALKTHROUGH.md` §7 describes, which needs the API and a worker running.
6. If screening, extraction, face match or risk scoring changed, run the affected `python -m services.eval` suite (WALKTHROUGH §9). A change to a screening threshold or model also needs `--write-results` and `tests/eval/test_shipped_config.py` passing.

Compare with the latest counts in `docs/WALKTHROUGH.md` §3. Any drop needs an explanation, and new tests should raise the count.

After the suite, confirm that it left nothing behind: compare the tenant count before and after rather than trusting the fixtures.

Report the actual numbers. Report a failure with its output. Do not add suppressions to make a check pass.

# MEMORY — Living State

Read this first; update it last each session.

## Current state (2026-11-03)

- **Released and pushed:** `v0.13.0` (13a), `v0.13.1` (13b), `v0.13.2` (13c), merged into `main`
  with `--no-ff`; `main` at `b64f237` on GitHub (it also merges PR #2, the docs branch). Record:
  [PHASE-LOG.md](PHASE-LOG.md).
- **Tests:** 243 passed with a key; 242 passed and 1 skipped with `GEMINI_API_KEY=""` (CI's
  configuration). ruff, black, mypy (112 files), pre-commit clean. Frontend: 38 unit tests, and the
  Playwright flow passes against the live local stack.
- **CI:** all four jobs green on GitHub for `b64f237` (run 41615147116, 2026-11-03):
  `lint-and-test`, `frontend`, `e2e` and `eval`, the last three for the first time. The eval gates
  held on Linux.
- **Screening** is measured: n-gram vectors at 0.73 against the real OFAC list, 95.67% recall and
  13.45% FPR on our noise model. The dev Qdrant alias `sanctions_current` points at the n-gram
  collection.
- **Risk:** per-tenant versioned policies exist; no tenant has one. The built-in policy sends 84% of
  cases to review.
- **Deployed anywhere:** no ([DEPLOY.md](DEPLOY.md) §4 is unbuilt).
- **Summary:** the control plane is strong; the data plane is now measured, and the measurements say
  where it is weak (MRZ names, OCR document numbers, hard-negative names); deployment does not exist.

## Open gates

| Gate | Blocked on | Owner |
|---|---|---|
| Deployment: [DEPLOY.md](DEPLOY.md) §4 | Approval to change code (quiz first) | Engineering |
| Oracle account and an A1 instance | Signup (card for identity verification), regional capacity | User |
| `GEMINI_API_KEY` repository secret | Unset by choice; the real-Gemini test and VLM eval skip on CI | User |
| Branch protection on `main` | Repository setting | User |
| MRZ name and OCR document number are accepted wrongly at 0.99 confidence | A cross-check against the printed zone or the VLM (D35 revisit) | Engineering |
| No tenant risk policy chosen | A design partner's appetite; `eval risk --policy` measures one | User + Engineering |
| Exact vector search has no guard | D33 ⚠ | Engineering |
| Split-store Postgres failure untested | Temporal leaving the shared Postgres (roadmap Phase 14) | Engineering |

## Next actions

1. User: consider branch protection on `main` requiring all four jobs.
2. User: Oracle account and A1 attempt ([DEPLOY.md](DEPLOY.md) §5), which can take weeks.
3. Engineering, after the quiz: [DEPLOY.md](DEPLOY.md) §4, or roadmap Phase 14.

## Environment facts that bit

- **Application Control** blocks `.venv/Scripts/*.exe` (os error 4551), including `uvicorn`: a
  backgrounded `uv run uvicorn` dies silently and the API is just absent. Use `uv run python -m <tool>`.
- **torch loads now** (2.13.0+cpu, 2026-11-03); it used to be blocked. Real OCR runs on the host at
  ~8 s a page, and InsightFace embeds LFW at ~1.5 s an image on this CPU.
- **The host is memory-starved** (~0.4 GB free, commit charge near its limit). Docker Desktop stopped
  under it mid-run; if Qdrant or Postgres refuses connections, check `docker ps` first. Do not run two
  harness suites at once.
- **The console encoding is cp1252.** Printing "≥" killed a 20-minute harness run after it finished;
  `services/eval` now writes results first and forces UTF-8 stdout.
- **Bash heredocs with apostrophes** in Python source fail here; write a script file and run it.
- **An empty Gemini key is a different code path** (`genai.Client(api_key="")` raises).
- **The local Temporal holds old workflows** for deleted test tenants; the worker logs foreign-key
  errors for them. Harmless, and absent on CI's fresh server.
- **`core.autocrlf=true`** makes `frontend/` files show as modified and fail `prettier --check`
  locally; content is unchanged and CI's Linux checkout is unaffected. Eval data is `-text` in
  `.gitattributes` so its hashes hold.
- **MinIO is withdrawn; s3mock ignores signatures.** **Temporal takes ~50 s to answer RPCs.**
- **Oracle free tier** numbers disagree between sources; check the console.

## Standing rules from the user

- `PLAN.md` stays as it was written. Phases from 13 on are recorded in `docs/PHASE-LOG.md`.
- Real infrastructure over mocks; state known gaps explicitly; verify test cleanup, never trust it.
- At least 4 logical commits per phase; `merge --no-ff` into `main`; tag `vX.Y.Z`.
- Confirm before pushing to GitHub. PR first, then merge and tag.
- Everything here concerns Sentrilog. Other local projects are references for the expected level of
  work only.

## History

- 2026-07-20 → 2026-08-20: Phases 1–11 ([PLAN.md](../PLAN.md) §11).
- 2026-10-29: Phase 12 hardening, `v0.12.0`. 2026-10-30: Phase 12.1, CI green, `v0.12.1`.
- 2026-11-02: `improvements.md`, the owner-doc system, `DEPLOY.md` and project skills; pushed as the
  branch `docs/project-docs-system`.
- 2026-11-03: roadmap Phase 13 as 13a/13b/13c (`v0.13.0`–`v0.13.2`, local only): evaluation harness,
  real OFAC list, measured screening threshold, MRZ reading fixes, per-tenant policies, frontend CI,
  first Playwright flow, and a Phase 11 audit-verifier bug fixed. Pushed; CI green on all four jobs.

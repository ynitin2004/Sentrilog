# MEMORY — Living State

Read this first; update it last each session.

## Current state (2026-11-02)

- **Released:** `v0.12.1`, `main` at `1a2b2fc`. Phases 1–12 and 12.1 are done ([PLAN.md](../PLAN.md) §10).
- **CI:** green since run 21, the first success in the repository's history. 192 tests pass locally
  with a key; 191 pass and 1 skips with an empty key, locally and on CI.
- **Deployed anywhere:** no. `infra/terraform/` holds only a README, and no Python process has a
  Dockerfile. The strategy is written ([DEPLOY.md](DEPLOY.md)); none of it is built.
- **Roadmap:** [improvements.md](../improvements.md) is written. It reshapes Phase 13 and adds
  Phases 14–19. No roadmap item has started.
- **Summary:** the control plane (tenancy, audit, workflow, tests) is strong; the data plane (models,
  sanctions list, thresholds) is a demo; the deployment plane does not exist yet.

## Uncommitted in the working tree

- `improvements.md`, `CLAUDE.md`, the new `docs/*.md` files and `.claude/skills/`, plus corrected
  lines in the READMEs that were stale.
- The modified `frontend/` files in `git status` are line-ending noise from `core.autocrlf=true`, apart
  from the README line edited here.

## Open gates

| Gate | Blocked on | Owner |
|---|---|---|
| Deployment: the code changes in [DEPLOY.md](DEPLOY.md) §4 | Approval to change code (a major change, so quiz first) | Engineering |
| Oracle account and an A1 instance | Signup, including a card for identity verification; regional capacity | User |
| `GEMINI_API_KEY` repository secret | User chose to leave it unset on 2026-10-30, so the real-Gemini test skips on CI | User |
| Branch protection on `main` requiring `lint-and-test` | Repository setting | User |
| Frontend has no CI job and no committed end-to-end test | P-17 in improvements.md | Engineering |
| Every threshold is unvalidated (0.55 screening, 0.85 auto-clear, 0.6/0.4 weights) | Eval harness, roadmap Phase 13 | Engineering |
| Sanctions list is 10 synthetic names | Real feed, roadmap Phase 13 | Engineering |
| Split-store Postgres failure is untested | Only arises once Temporal moves off the shared Postgres | Engineering |

## Next actions

1. User: decide whether to commit these documents.
2. User: create the Oracle account and try for an A1 instance ([DEPLOY.md](DEPLOY.md) §5), since
   capacity is the one risk that can take weeks.
3. Engineering, after the quiz: build [DEPLOY.md](DEPLOY.md) §4 in the listed order.
4. Then start roadmap Phase 13 (eval harness), which everything later is measured against.

## Environment facts that bit

- **Application Control** blocks `.venv/Scripts/*.exe` (os error 4551) and `torch_python.dll`. Use
  `uv run python -m <tool>`. Real OCR cannot run on this host outside a container.
- **An empty Gemini key is a different code path.** `genai.Client(api_key="")` raises immediately.
  An eagerly built client once made `activities.py` unimportable and took four unrelated test modules
  down at collection, and only CI could see it because `.env` always has a key.
- **MinIO's images are withdrawn;** anonymous registry tokens are refused. LocalStack's `2026.*` tags
  exit demanding a licence token. `adobe/s3mock:4.9.0` replaced it for dev and CI, and it ignores
  signatures, so it proves nothing about signing.
- **s3mock's persistence** (`root` plus a volume) returns InternalError under Docker Desktop. It is
  not a production store.
- **Temporal auto-setup takes ~50 s to answer RPCs** and binds its own hostname, not loopback, so a
  port probe or a `localhost` check is misleading. `scripts/wait_for_dependencies.py` does the real
  operations.
- **`make` is not installed** on this machine, so the Makefile targets are unverified through `make`.
- **Oracle free tier:** sources in 2026 report the A1 allowance halved to 2 OCPU / 12 GB, and they
  disagree on the idle-reclaim thresholds. Check the console, not a blog.
- **The dev database holds ~35 leftover test tenants** from before Phase 11. Harmless, but ad-hoc
  queries return noise.

## Standing rules from the user

- `PLAN.md` stays as it is; it is the record the user will use in project discussions. New material
  goes in other files.
- Real infrastructure over mocks; state known gaps explicitly; verify test cleanup, never trust it.
- At least 4 logical commits per phase; `merge --no-ff` into `main`; tag `vX.Y.Z`.
- Confirm before pushing to GitHub. PR first, then merge and tag.
- Everything here concerns Sentrilog. Other local projects may be shown as references for the
  expected level of work; their product content does not belong in this repository.

## History

- 2026-07-20 → 2026-08-20: Phases 1–11 (details in [PLAN.md](../PLAN.md) §11).
- 2026-10-29: Phase 12 hardening, `v0.12.0`.
- 2026-10-30: Phase 12.1, CI green for the first time, `v0.12.1`.
- 2026-11-02: `improvements.md`, the owner-doc system, `DEPLOY.md` and project skills written.

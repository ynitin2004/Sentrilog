# Sentrilog — Claude Session Protocol

KYC/AML document intelligence pipeline: multi-tenant intake API, durable Temporal workflow
(extraction, face match, sanctions screening, risk scoring), human review, tamper-evident audit
log, React operator console. Repository: <https://github.com/ynitin2004/Sentrilog> (public).

## Read order (every session, before any work)

1. `docs/MEMORY.md` — current state, open gates, next actions. Always first.
2. The document that owns the area you are touching (index below).
3. `docs/FLOW.md` — before changing anything on an execution path.

Before ending a working session, update `docs/MEMORY.md`. It is the last action, not an optional one.

## Documentation index

| File | Owns | Update when |
|---|---|---|
| `PLAN.md` | The 13-phase delivery plan, status table and per-phase changelog with test evidence | Each phase closes (append; history is not rewritten) |
| `STUDY.md` | Concepts each phase introduced, explained | Each phase closes |
| `improvements.md` | Roadmap to a production product: HLD, problems P-1…P-18, Phases 13–19, roadmap decisions | The roadmap changes |
| `README.md` | What the project is and how to start | The first-run steps change |
| `docs/MEMORY.md` | Living state: where things stand, open gates, environment facts that bit | End of every session |
| `docs/ARCHITECTURE.md` | Stack, modules, deploy units, tenancy, data stores, measured limits | Any structural change |
| `docs/RULES.md` | Prohibitions, reliability contracts, AI boundaries, testing gates, git workflow | A rule changes (needs a DECISIONS entry) |
| `docs/DECISIONS.md` | Every decision: why, alternatives, revisit trigger | Before implementing a new decision |
| `docs/DECISION-TRACES.md` | Decision → the test or script that fails if it is broken | A decision or its guard changes |
| `docs/FLOW.md` | Entry points, execution chains, AI decision-point registry | Any change to an execution path |
| `docs/DEPLOY.md` | $0 deployment strategy, runbook, cost table, what is not built yet | Deployment changes |
| `docs/WALKTHROUGH.md` | How to run and re-verify every gate, with expected output | A command or expected result changes |
| `docs/DESIGN.md` | Operator console: UX states, real-time rules, accessibility | Any user-facing surface work |
| `docs/CASE-TRACES.md` | One row per live smoke case | Written by the smoke script; never hand-edited |
| `docs/evidence/` | Archived evidence from real runs | Every evidence-producing run |
| `docs/runbooks/` | Failure-mode runbooks | A failure mode is re-tested or changes |

Rule: if a fact about the product is not in these files, it is not decided. No project knowledge
lives only in chat history.

## The Quiz Rule (before any major change)

Before implementing a **major change**, ask the user 3–5 short questions and wait for the answers
before writing code. This is the user's codebase and their interview material, so the quiz is for
their understanding. Cover what the change affects, what breaks if it is wrong, how to roll it
back, and what it costs.

A change is **major** if it involves any of: a new dependency · a schema change in
`infra/docker/postgres-init/` · a new or changed endpoint contract · anything in
`services/pipeline/extraction/`, `services/pipeline/risk_scoring.py`, or the screening threshold ·
tenancy, RLS or auth logic · the audit hash or chain · a workflow change that alters event history
· deleting or rewriting more than ~100 lines · spending money or adding an external service · a
deviation from `docs/ARCHITECTURE.md` or `docs/RULES.md`.

After the quiz, record the change in `docs/DECISIONS.md` if it sets a precedent and update
`docs/FLOW.md` if it touches an execution path.

## This machine

- An endpoint Application Control policy blocks `.venv/Scripts/*.exe`. Run tools as
  `uv run python -m pytest`, `-m mypy`, `-m ruff`, `-m black`. CI on Linux keeps `uv run <tool>`;
  do not "fix" the workflow to match.
- The same policy blocks `torch_python.dll`, so real OCR cannot run on this host outside a container.
- Git identity comes from one-off environment variables on each command, never `git config`:
  `GIT_AUTHOR_NAME=ynitin GIT_AUTHOR_EMAIL=ynitin2004@gmail.com GIT_COMMITTER_NAME=ynitin GIT_COMMITTER_EMAIL=ynitin2004@gmail.com`.
- Before pushing, run the suite twice: normally, and with `GEMINI_API_KEY=""`, which is CI's real
  configuration and a path a local `.env` hides.

## Project skills

`.claude/skills/`: `session-start`, `verify-gates`, `ci-watch`, `phase-close`, `decision`,
`live-smoke`, `deploy`.

## Hard rules (full list in docs/RULES.md)

Every tenant query runs under the tenant GUC as `sentrilog_app`, never the superuser · cross-tenant
access returns 404 · no credential outside `SecretStr` and the provider seam · third-party clients
behind lazy accessors · an audit row for every state transition · a case is never auto-rejected,
and a hard red flag always goes to a human · only synthetic documents reach Gemini's free tier ·
local gates equal CI gates · no commit or push without the user's say-so.

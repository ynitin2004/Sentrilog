---
name: phase-close
description: Close a Sentrilog delivery phase, covering evidence, docs, merge and tag. Use when a phase's work is finished or the user asks to wrap up, merge or release a phase.
---

# Phase close

Do these in order. Stop and report if a gate fails.

1. **Exit criteria.** Check each one against real evidence. Code being present does not meet a criterion.
2. **Gates.** Run the `verify-gates` skill, then re-run earlier suites as regression. Run any phase-specific chaos or load scenario.
3. **Commits.** At least 4 logical commits on `phase-N-<short-name>`. Pass the git identity per command, never with `git config`:
   `GIT_AUTHOR_NAME=ynitin GIT_AUTHOR_EMAIL=ynitin2004@gmail.com GIT_COMMITTER_NAME=ynitin GIT_COMMITTER_EMAIL=ynitin2004@gmail.com git commit ...`
4. **`docs/PHASE-LOG.md`.** Add a status row and a changelog entry: design decisions, measured findings, real bugs found, test evidence naming the scenarios, and explicit known gaps. Append only. `PLAN.md` is never edited: the user keeps it as written.
5. **`STUDY.md`.** Add the concepts the phase introduced.
6. **Owner docs.** Update whichever changed: `docs/DECISIONS.md` and `docs/DECISION-TRACES.md` (every new decision has a guard, or a ⚠), `docs/FLOW.md` (execution paths and the AI registry), `docs/ARCHITECTURE.md`, `docs/WALKTHROUGH.md` (expected counts), `docs/runbooks/`.
7. **Merge.** `git merge --no-ff` into `main`, with a message that says what was wrong and what was proven. Tag `v0.N.0`.
8. **Ask before pushing.** Prefer a PR first, then merge and tag. Then follow the run with the `ci-watch` skill.
9. **`docs/MEMORY.md` last:** state, open gates, next actions, history.

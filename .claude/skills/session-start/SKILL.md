---
name: session-start
description: Rebuild Sentrilog project context at the start of a working session. Use when beginning work in this repository, or when the user asks where the project stands or what to do next.
---

# Session start

1. Read `docs/MEMORY.md` in full.
2. Run `git status --short` and `git log --oneline -5`. Ignore `frontend/` entries that are only line-ending noise (`core.autocrlf=true`); check with `git diff --stat`.
3. Read the document that owns the area of the task, using the index in `CLAUDE.md`. Read `docs/FLOW.md` before changing an execution path.
4. Tell the user in a few lines: the current release, the open gates relevant to the task, and any drift you found between the docs and the code.

Rules while working:

- If a fact is not in the docs, it is not decided. Ask, or record it in `docs/DECISIONS.md`.
- Do not treat code being present as proof that a gate is met. Check the evidence.
- A major change, as defined in `CLAUDE.md`, needs the Quiz Rule before any code is written.
- `PLAN.md` is the user's record for project discussions. Do not restructure it.
- End the session by updating `docs/MEMORY.md`.

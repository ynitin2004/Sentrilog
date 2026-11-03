---
name: deploy
description: Deploy Sentrilog on the $0 Oracle plan or the laptop tunnel fallback, check it, or roll it back. Use when asked to deploy, stand up a demo URL, prepare for an interview demo, or roll back.
---

# Deploy

`docs/DEPLOY.md` is the source of truth. Read it first.

**Check what exists.** The items in §4 of that document were not built when this skill was written. If `Dockerfile` or `deploy/compose.prod.yml` is missing, stop and offer to build §4 under the Quiz Rule. Do not improvise a deployment around them.

**Choose.**

- Always-on URL: Plan A (§5). The user creates the Oracle account, DuckDNS names and repository secrets. Do not ask for credentials in chat.
- An interview soon, or no Oracle capacity: Plan B (§6).

**Confirm before acting.** Deploying and changing a public host are outward-facing, so get the user's go-ahead each time, and again for anything touching the running database.

**After each runbook step, run its check.** Do not move on from a failed step. Finish with `/health`, `/ready`, the `live-smoke` skill and `scripts/verify_audit_chain.py`.

**Never:**

- Use the dev credentials on a public host, including the `sentrilog_app` dev password from `002_multi_tenancy.sql`.
- Publish Postgres, Temporal, Qdrant or the Temporal UI.
- Send real documents through the Gemini free tier.
- Exceed the Always Free shape limit shown in the Oracle console.
- Publish a demo API key.

**Rollback.** Redeploy the previous image tag. A schema change needs a restore from backup (§8).

**Afterwards.** Record what ran, measured memory use and any surprises in `docs/DEPLOY.md` and `docs/MEMORY.md`, and replace every *verify* note you confirmed with the fact you found.

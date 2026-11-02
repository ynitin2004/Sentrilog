---
name: ci-watch
description: Follow a GitHub Actions run for Sentrilog to completion and diagnose failures from the real logs. Use after a push, or when asked about CI status or why CI failed.
---

# CI watch

Repository: `ynitin2004/Sentrilog`. The `gh` CLI may be missing here, so these steps use the REST API from a Python script in the session scratchpad.

**Authentication.** Read the token at runtime with `git credential fill` (`protocol=https`, `host=github.com`). Never print it, put it on a command line, or write it to disk. Anonymous access allows 60 requests an hour, which a polling loop exhausts.

**Following a run.** `GET /repos/{repo}/actions/runs?head_sha=<sha>` gives the run, and `GET /actions/runs/{id}/jobs` gives each step's status. Run the watcher in the background, polling every 20 seconds or so, and stop when the status is `completed`.

**Reading a failure.**

1. Find the first failed step. Later steps report nothing after a fatal one, and nothing can read as fine.
2. Fetch `GET /actions/jobs/{id}/logs` without following the redirect: it returns a 302 to blob storage, and that storage rejects a bearer header. Fetch the redirect target without authentication.
3. Fix the cause. Do not bypass the check.

**Common causes here.**

- It passes locally and fails on CI: run the suite with `GEMINI_API_KEY=""`. CI has no key.
- Collection errors: a client or setting being built at import time.
- Connection errors right after start: Temporal takes about 50 s, so check the readiness step ran.
- An RLS test fails unexpectedly: check which role `DATABASE_URL` uses.

**Re-running** is `POST /actions/runs/{id}/rerun`. This is a visible action on a shared repository, so run it only when asked.

GitHub emails on failure by default, not on success.

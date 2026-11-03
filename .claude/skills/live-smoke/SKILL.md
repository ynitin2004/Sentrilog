---
name: live-smoke
description: Run one real Sentrilog case end to end against a local or deployed API and record the trace. Use before a demo, after a deploy, or when asked to prove the system works for real.
---

# Live smoke

**`scripts/live_smoke.py` does not exist yet** (`docs/DEPLOY.md` §4, item 9). Until it does, follow `docs/WALKTHROUGH.md` §4 by hand, say that it was manual, and do not add a row to `docs/CASE-TRACES.md`.

The check must cover:

1. Readiness: `scripts/wait_for_dependencies.py`.
2. `POST /cases` with an idempotency key, and a replay that returns the same case.
3. Presigned PUTs of **synthetic** images. Real documents must never go through Gemini's free tier.
4. Polling to `approved` or `needs_review`.
5. If parked, a reviewer decision, then confirmation that the workflow completed.
6. A delivery row in `webhook_deliveries`, if a webhook is registered.
7. `scripts/verify_audit_chain.py` for the tenant.
8. A trace in `docs/evidence/case-traces/` and a row in `docs/CASE-TRACES.md`, both written by the script.

Cost: VLM calls if the MRZ path does not apply; screening is local (n-gram vectors) and free. Real OCR runs on this laptop at about 8 s a page.

Report which path the case took, its timings and the chain result. A check that ran the path partially must say which steps were skipped.

# Operator Console: Design & UX

The console in `frontend/` has two sides: a **reviewer** side (connect, queue, case detail) and a
**tenant admin** side (overview, cases, API keys, webhooks, reviewers). Code layout:
[frontend/README.md](../frontend/README.md).

## 1. Principles

- Information-dense and calm. A reviewer decides on identity and sanctions evidence, so the evidence
  comes first.
- Accessibility is a gate: real axe-core audits run inside the component tests.
- Design tokens live in the Tailwind config; components follow the shadcn/ui pattern over Radix primitives.
- Components are developed in isolation in Storybook.

## 2. Every screen has four states

Loading (skeleton), empty (explains what would appear), error (specific and recoverable), and
permission-denied. A 401 clears the session from one global handler rather than a per-screen check.
The other statuses the console handles are 404, 409, 422 and 429.

## 3. Reviewer flow

1. **Connect:** enter the API URL and a credential. There is no build-time API URL.
2. **Queue:** live-updating list of `needs_review` cases, virtualized for long lists.
3. **Case detail:** documents, extraction, face match, sanctions hits and risk score.
4. **Decide:** a decision with a justification. Auditors can read but cannot claim or decide; the API
   enforces this with a 403.

## 4. Real-time rules

- The queue updates over SSE with no polling. `fetch` plus a hand-written parser is used because native
  `EventSource` cannot send an `Authorization` header.
- Reconnect with backoff after a drop.
- Claim and decide are optimistic, with rollback on failure. A claim is advisory: it means "someone is
  looking at this" and is never a lock.
- Two reviewers racing is a normal case, handled in the UI rather than as an error page.

## 5. Honesty in the UI

- A missing value is not a clean result. "Screening did not run" must never look like "no hits", and
  "no face detected" must never look like a low score.
- Scores are shown as evidence for a human, never as a verdict.
- An API key or reviewer token is revealed exactly once, at creation.

## 6. Known gaps

- Session token in `localStorage` (P-5, P-8 in [improvements.md](../improvements.md)).
- No committed end-to-end test and no frontend CI job (P-17).
- The admin Cases screen had no status filter UI at Phase 9, though the endpoint supports one.
- Responsive behaviour was checked by screenshot at 390 px and 1440 px only.

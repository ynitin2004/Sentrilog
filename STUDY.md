# Sentrilog — Study Notes

A running log of every non-trivial concept introduced while building this, written so you can learn it alongside the implementation rather than just watch code appear. Organized by phase. Each entry: **what it is**, **why we needed it here** (the specific bug or requirement that forced it), and **where to read more**.

This file grows every phase — treat it as a second changelog, one for concepts instead of commits.

---

## Phase 2 concepts

### Row-Level Security (RLS)

**What:** A Postgres feature where the database itself filters which rows a query can see/write, based on a policy — enforced at the engine level, not just in application code.

**Why here:** With many tenants sharing one Postgres instance, app-layer `WHERE tenant_id = ...` filtering is one missed clause away from a cross-tenant data leak. RLS makes that structurally impossible — even a buggy or malicious query against `cases` physically cannot see another tenant's rows, because the *database* rewrites the query.

**The gotcha we hit:** RLS is **silently bypassed for superusers and table owners**, with no override — not even `FORCE ROW LEVEL SECURITY` changes that. Since `POSTGRES_USER` is a superuser by default, the app had to connect as a separate, unprivileged role (`sentrilog_app`) for RLS to do anything at all. This is the single most important Postgres security fact in this whole project.

**Read more:** <https://www.postgresql.org/docs/current/ddl-rowsecurity.html>

### `SET LOCAL` / `set_config()`

**What:** Postgres session/transaction-scoped configuration variables — a way to stash a value (like "which tenant is this connection allowed to see") that a query can read back via `current_setting(...)`.

**Why here:** RLS policies need to know the current tenant *without* it being a literal in the SQL (that would mean building SQL strings by hand — an injection risk). `SET LOCAL` sets a variable for the rest of the current transaction only, so a pooled connection can never leak one tenant's context onto the next request that reuses it.

**The gotcha we hit:** `SET`/`SET LOCAL` do **not** accept bind parameters (`$1`) — only literal values. Writing `SET LOCAL app.tenant_id = $1` is a syntax error. The fix is `SELECT set_config('app.tenant_id', $1, true)` — `set_config()` is a real function, so it *does* accept parameters, and the third argument (`true`) makes it transaction-local, equivalent to `SET LOCAL`.

**Read more:** <https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADMIN-SET>

### `SECURITY DEFINER` functions

**What:** A Postgres function that runs with the *privileges of whoever defined it*, not whoever calls it — the one deliberate, narrow way to bypass RLS or other restrictions for a specific, controlled operation.

**Why here:** Authenticating an API key means looking up `api_keys` by its hash *before* we know which tenant we're dealing with — but RLS on `api_keys` requires the tenant to already be known. Classic chicken-and-egg. `resolve_api_key()` is `SECURITY DEFINER`, so it runs as its (superuser) owner and can see the whole table, but it only *returns* `tenant_id`/`api_key_id`/`revoked_at` — never exposing more than that one lookup needs.

**Read more:** <https://www.postgresql.org/docs/current/sql-createfunction.html> (search "SECURITY DEFINER")

### Append-only tables + hash chaining (tamper evidence)

**What:** A table that only ever gets `INSERT`s (enforced via revoked `UPDATE`/`DELETE` grants), where each row stores a hash of its own content plus the previous row's hash (`prev_row_hash` → `row_hash`). Altering any historical row breaks every hash after it — the same idea Git and blockchains use for integrity, applied to a regular table.

**Why here:** "The audit log wasn't tampered with" needs to be provable to a regulator, not just asserted. A plain `UPDATE`-able table can't prove that; a hash chain can — you can independently walk it and verify nothing changed.

**Read more:** no single canonical doc — the general technique is sometimes called a "hash chain" or "tamper-evident log"; Certificate Transparency logs are a well-known real-world example: <https://certificate.transparency.dev/howctworks/>

### Docker Compose healthchecks (`--wait`)

**What:** `docker compose up -d --wait` blocks until every service with a `healthcheck:` reports `healthy`, and fails fast if one doesn't — instead of the default behavior, which just reports "container started" even if the process inside is still initializing.

**Why here:** We hit this directly — Temporal's container was "running" long before it was actually ready to accept connections. A healthcheck is a real prove-it-works check (in our case, `tctl cluster health`), not just "is the process alive."

**Read more:** <https://docs.docker.com/reference/compose-file/services/#healthcheck>

---

## Phase 3 concepts

### Presigned URLs (S3/MinIO)

**What:** A time-limited, cryptographically signed URL that grants permission to upload (or download) one specific object, without the client ever having real S3 credentials.

**Why here:** The intake API should never see raw ID-document bytes pass through it — that's more PII exposure surface than necessary. Instead, the client uploads *directly* to S3/MinIO using a URL we hand it; the signature (in the query string) is what authorizes the `PUT`, valid only for that exact key, for a limited time (15 minutes here).

**Read more:** <https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/generate_presigned_url.html>

### Idempotency keys

**What:** A client-supplied token that makes "do this request" safe to retry — the server recognizes a repeated key and returns the *original* result instead of doing the work twice.

**Why here:** Network retries are a certainty, not an edge case — a client's HTTP call can time out *after* our server already created the case, and the client has no way to know that. Without idempotency, its retry creates a duplicate. The constraint is `UNIQUE (tenant_id, idempotency_key)` — scoped per tenant deliberately, so two different clients can coincidentally use the same key string without colliding (this was one of our actual tests).

**Read more:** <https://docs.stripe.com/api/idempotent_requests> — Stripe's is the industry-reference implementation of this pattern.

### API-key hashing (why SHA-256, not bcrypt)

**What:** Storing `SHA-256(raw_key)` instead of the raw key, so a database leak doesn't hand out working credentials.

**Why here — and why not bcrypt/argon2:** Those slow, salted hashes exist specifically to defend against *low-entropy, human-chosen* passwords (where an attacker can feasibly guess candidates). An API key here is a 256-bit random token — brute-forcing it is infeasible regardless of hash speed, so a fast hash is the *correct* choice, not a shortcut. Using bcrypt on a high-entropy token doesn't add security, it just adds latency.

**Read more:** <https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html> (see the discussion of when slow hashing is/isn't appropriate)

### Rate limiting (fixed-window)

**What:** Counting requests per key within a rolling time window, rejecting with `429 Too Many Requests` (+ a `Retry-After` header telling the client when to try again) once a threshold is hit.

**Why here — and the known limitation:** Our implementation is in-process memory — correct for one running server, but each additional replica gets its *own* independent counter, so real enforced limits under multiple replicas are effectively `limit × replica count`. This is a documented, deliberate gap: real enforcement belongs at the infrastructure edge (API Gateway/load balancer), which is already the Phase 10 plan.

**Read more:** <https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429> · rate-limiting algorithms overview: <https://blog.cloudflare.com/counting-things-a-lot-of-different-things/>

### FastAPI dependency injection (`Depends`)

**What:** FastAPI's pattern for declaring "this endpoint needs X" (an authenticated tenant, a DB connection, etc.) as a function parameter with a default of `Depends(some_function)` — FastAPI calls `some_function` for you and injects the result.

**Why here:** `require_api_key` (auth) is a dependency every protected endpoint declares, rather than every endpoint hand-rolling its own header-parsing/lookup logic.

**The gotcha:** linters that don't know FastAPI (like ruff's bugbear rule `B008`) flag `Depends(...)` in a default argument as a bug — it *looks* like the classic Python "mutable default argument" mistake, but it's actually required by how FastAPI works. Fixed by allowlisting it, not disabling the whole rule.

**Read more:** <https://fastapi.tiangolo.com/tutorial/dependencies/>

### asyncpg connection pooling

**What:** A pool of reusable database connections (`asyncpg.create_pool`) instead of opening a new TCP connection per request — connections are expensive to establish, so the pool hands one out, gets it back when the request finishes, and reuses it for the next request.

**Why here:** Combined with RLS, this is exactly why `SET LOCAL`/`set_config` had to be *transaction-scoped*: a pooled connection previously used for Tenant A's request could be handed to Tenant B's request next — if the tenant context leaked across that reuse, that's a cross-tenant data breach. Transaction-scoped config resets automatically, making that impossible.

**Read more:** <https://magicstack.github.io/asyncpg/current/api/index.html#connection-pools>

### pytest-asyncio event loop scoping

**What:** Async tests need an event loop to run on. By default, pytest-asyncio gives *each test function its own loop* — but a database connection pool created once (in a session-scoped fixture) is tied to the loop it was created on.

**The bug we actually hit:** `RuntimeError: Task ... attached to a different loop` — the pool's loop and each test's loop didn't match. Fixed by forcing both fixtures and tests to share one session-scoped loop (`asyncio_default_fixture_loop_scope = "session"`).

**Read more:** <https://pytest-asyncio.readthedocs.io/en/latest/concepts.html#event-loops>

### Least-privilege DB roles (why the app can't `DELETE`)

**What:** `sentrilog_app` (the role the application connects as) was granted `SELECT`/`INSERT`/`UPDATE` on most tables — deliberately **no `DELETE`, anywhere**.

**Why here:** An app that can create case records but never delete them is part of the audit-integrity story — if the app itself can't delete, "someone deleted evidence" can only mean "someone used elevated/superuser access," which is a much smaller, more auditable set of people/processes. This surfaced as a real bug when our *test cleanup* tried to delete test data using the app's own role and got `permission denied` — the fix was a separate, explicitly-labeled admin connection for test teardown only, never for application code.

**Read more:** <https://www.postgresql.org/docs/current/ddl-priv.html> · principle of least privilege: <https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html>

---

## Phase 7 concepts

### Temporal signals + `wait_condition` (human-in-the-loop)

**What:** A signal is an async message delivered into a *running* workflow from the outside world (here: the intake API's decision endpoint calling `handle.signal(name, payload)`). A `@workflow.signal`-decorated method is the handler; `await workflow.wait_condition(fn)` suspends the workflow (durably — it can wait for hours or days without holding any resources) until `fn()` becomes true.

**Why here:** `KycCaseWorkflow` needs to pause indefinitely once a case reaches `needs_review`, then resume exactly where it left off once a reviewer decides — without polling, without a separate scheduler, and without losing any progress if the worker process restarts while waiting (that's the whole point of *durable* execution). Signals + `wait_condition` are the mechanism Temporal gives you for that instead of hand-rolling a poll loop or a message queue.

**The real bug this surfaced:** the first version stored the pending decision in a single field and reset it to `None` at the top of the wait loop ("start fresh each time"). That's wrong: a signal can arrive and set the field *before* the workflow even reaches the wait loop (e.g. while an earlier activity is still running) — and the reset would silently wipe it out, leaving the workflow waiting forever for a signal that already came. The fix was a queue (`list[ReviewDecisionSignal]`) that's only ever appended to and popped from, never reset. This is a general lesson, not a Temporal quirk: any time you're consuming an event that might arrive before you start listening for it, "reset then wait" is a race — "append then check what's there" isn't.

**Read more:** <https://docs.temporal.io/develop/python/message-passing#signals> · <https://docs.temporal.io/develop/python/message-passing#wait-condition>

### The queue is a status value, not a table

**What:** There's no `review_queue` table. "The queue" is simply `SELECT * FROM cases WHERE status = 'needs_review'`.

**Why here:** A separate queue table would need to be kept in sync with `cases.status` on every transition — two sources of truth for the same fact, with all the drift-and-bugs risk that implies. Since a case's status is already the authoritative record of whether it needs review, querying it directly *is* the queue. This only works because "claimed" is deliberately advisory (a `claimed_by_reviewer_id` column, not a lock) — a real exclusive-claim system would need more than a status filter.

### HMAC webhook signatures

**What:** Before sending a webhook payload, compute `HMAC-SHA256(shared_secret, request_body)` and send it as a header (`X-Sentrilog-Signature`). The receiver recomputes the same HMAC over the raw body it received and compares — a match proves the payload came from us (or someone with the secret) and wasn't altered in transit.

**Why here:** A webhook URL is public-ish (anyone who guesses or leaks it can `POST` to it), so the receiving side needs a way to distinguish a genuine Sentrilog delivery from a forged one. A shared secret + HMAC is the standard pattern (Stripe, GitHub, and most webhook providers all do this) — the alternative, trusting the payload just because it arrived at the right URL, means anyone who finds that URL can fabricate case decisions.

**Read more:** <https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries> — GitHub's docs walk through the same pattern used here.

---

## Phase 8 concepts

### `useSyncExternalStore` for a shared mock data layer

**What:** A React 18 hook that subscribes a component to any external (non-React) mutable state source — you give it a `subscribe(callback)` function and a `getSnapshot()` function, and React re-renders the component whenever the snapshot changes. It's the same primitive React itself uses internally for things like the browser's online/offline status.

**Why here:** Phase 8 has no real backend yet, so the reviewer queue's data has to live *somewhere* in memory that survives navigating between screens. A naive `useState` inside a custom hook doesn't do that — every component that calls the hook gets its own independent copy, because `useState`'s initial value only runs once *per component instance*, not once globally. `useSyncExternalStore` reads from one shared, module-level store instead, so every component calling `useReviewQueue()` sees (and can mutate) the same underlying data — which is exactly the role a shared client-side cache (Phase 9's TanStack Query) plays for real once there's a real backend to cache. This project actually hit the bug the naive version causes (see PLAN.md's Phase 8 changelog) before fixing it this way.

**Read more:** <https://react.dev/reference/react/useSyncExternalStore>

### Radix primitives + Tailwind (the shadcn/ui pattern)

**What:** Radix UI ships unstyled, fully-accessible interactive primitives (dialogs, dropdowns, toasts) that handle the hard parts — focus trapping, `Escape`-to-close, ARIA attributes, portal rendering — while leaving all visual styling to you. "shadcn/ui" isn't a component library you install; it's a pattern of wrapping these primitives in your own small styled components (this project's `src/components/ui/`) using Tailwind classes, so you own the code and can change anything without fighting a library's API.

**Why here:** Building a dialog's focus-trap and keyboard handling correctly from scratch is genuinely hard to get right (and easy to get *wrong* in ways that only show up for keyboard/screen-reader users) — Radix has already solved it. Tailwind utility classes on top keep the design-token system (`index.css`'s `@theme` colors) as the single source of visual truth, the same way the backend keeps `risk_scoring.py`'s weights as the single source of the risk-scoring formula rather than duplicating the number in multiple places.

**Read more:** <https://www.radix-ui.com/primitives/docs/overview/introduction>

### Automated accessibility testing with axe-core

**What:** `axe-core` is a real accessibility-rule engine (the same one browser DevTools' own "Lighthouse"/"Accessibility" panels use) that scans rendered DOM for actual WCAG violations — missing labels, insufficient color contrast, invalid ARIA usage — and returns a structured list of what's wrong and why, not just a pass/fail score.

**Why here:** "We built it with semantic HTML and ARIA attributes" is a claim; running a real audit against the actual rendered output on every test run is evidence. This project calls `axe-core` directly (`src/test/a11y.ts`) rather than through a matcher-wrapper library, after finding that wrapper's type definitions didn't match this project's (very new) Vitest version — a small real-world lesson that a convenience wrapper is only worth it while it's actually compatible with what it's wrapping.

**Read more:** <https://github.com/dequelabs/axe-core>

---

## Phase 9 concepts

### Generating TypeScript types from the backend's own OpenAPI schema

**What:** FastAPI already knows the exact shape of every request/response it serves — `app.openapi()` returns that as a JSON Schema document without needing a running server. `openapi-typescript` turns that document into TypeScript `interface`s. The pipeline here is `scripts/export_openapi.py` (calls `app.openapi()` → `frontend/openapi.json`, gitignored as a regeneratable intermediate) → `npm run generate-types` (→ `src/types/api-generated.ts`, committed) → a thin hand-written `src/types/api.ts` that re-exports friendlier names and narrows the small number of fields OpenAPI can't express as precisely as the backend actually guarantees.

**Why here:** Hand-maintaining a parallel set of TypeScript interfaces next to the Pydantic schemas is exactly the kind of duplication this project avoids everywhere else (the backend already keeps risk-scoring weights, status enums, and validation rules in one place each). The generated types make a backend schema change a compile error on the frontend instead of a silent runtime mismatch. The one piece of friction worth knowing: Pydantic fields typed as plain `str` produce `string` in the generated types, not a narrow union — this project hit that directly (`CaseResponse.status` needed to become a real `Literal[...]` type in `schemas.py`, matching the DB's `CHECK` constraint, before the generated frontend type became the specific `CaseStatus` union instead of `string`).

**Read more:** <https://openapi-ts.dev/introduction>

### TanStack Query: optimistic updates and cache-driven UI

**What:** TanStack Query treats server data as a cache with a lifecycle (`useQuery` to read it, `useMutation` to change it) rather than data you copy into `useState` and manage by hand. Its optimistic-update pattern has three hooks working together: `onMutate` cancels any in-flight refetches, snapshots the current cache value, and writes the new value in immediately (before the server has responded); `onError` rolls the cache back to that snapshot if the request actually fails; `onSettled` invalidates the query afterward so the next read resyncs with whatever the server actually has, regardless of whether the optimistic guess was right.

**Why here:** A reviewer claiming or deciding a case should feel instant — waiting on a network round-trip before the queue list updates makes the UI feel laggy for something that succeeds the overwhelming majority of the time. This project's `useReviewQueue` hook (`src/hooks/use-api.ts`) applies this to claim (always optimistic — claiming is advisory, nothing is lost if it's wrong) and decide (approve/reject remove the case from the cached queue immediately; escalate does not, mirroring the same signal semantics the backend's `KycCaseWorkflow` already uses) — with a real rollback path exercised against a real `409` (a case inserted directly via SQL, with no live workflow behind it to accept the decision).

**Read more:** <https://tanstack.com/query/latest/docs/framework/react/guides/optimistic-updates>

### Handling auth expiry from one place, not every screen

**What:** Instead of every component that calls a protected endpoint checking for a `401` itself, the `QueryClient` is constructed *inside* the React tree (in a `QueryProvider` component, not at module scope) so its constructor can close over `useAuth()` and `useNavigate()`. The `queryCache`/`mutationCache` each get a global `onError` handler: if the error is an `UnauthorizedError`, it clears the session and redirects to `/connect`, from exactly one place, no matter which of the dozen or so hooks triggered it.

**Why here:** A token can go stale for reasons that have nothing to do with any single screen (it was revoked from another tab, it simply expired) — handling that per-screen means either duplicating the same check nine times or, more likely, forgetting it on the tenth screen added later. Building the `QueryClient` inside the tree rather than as a module-level singleton (the more common tutorial pattern) is the specific trick that makes this possible, since a module-level client can't reach hooks at all.

**Read more:** <https://tanstack.com/query/latest/docs/framework/react/guides/query-invalidation>

---

## Phase 10 concepts

### Postgres LISTEN/NOTIFY for real-time push

**What:** `NOTIFY channel, 'payload'` broadcasts a small text message to every client currently `LISTEN`ing on that channel name, on the same Postgres server. It's pub/sub built into the database itself — no message queue, no broker, nothing to run or operate beyond Postgres, which this project already depends on. Delivery is fire-and-forget and only happens on transaction commit: a `NOTIFY` inside a transaction that later rolls back is simply never sent.

**Why here:** The alternative to real-time updates is polling — every connected browser re-fetching the queue every few seconds "just in case" something changed. That wastes requests when nothing changed and adds latency when something did. `NOTIFY`/`LISTEN` inverts it: the database tells you the instant something changes, because the write that changed it is the same write that sends the notification (see `update_case_status_activity`/`finalize_case_activity` in `services/pipeline/workflows/activities.py`). The one thing it *doesn't* give you for free is per-tenant filtering — a channel has no concept of row-level security, so every `LISTEN`er on `case_events` receives every tenant's events, and the intake API's `GET /events/stream` endpoint has to filter by `tenant_id` itself before ever queuing a message for a specific client.

**Read more:** <https://www.postgresql.org/docs/current/sql-notify.html>

### Server-Sent Events (SSE)

**What:** A one-way, long-lived HTTP response with `Content-Type: text/event-stream` — the server keeps the connection open and writes small text frames (`data: ...\n\n`) whenever it has something new to send, and the browser (or, here, a hand-rolled `fetch` + `ReadableStream` reader) keeps reading them as they arrive. Unlike WebSockets, it's plain HTTP in one direction only, which is all a "tell the browser when a case's status changes" feature actually needs.

**Why here:** The browser's built-in `EventSource` API is the normal way to consume SSE, but it can't send custom headers — no way to attach `Authorization: Bearer <token>`, which is how every other endpoint in this project authenticates. Rather than inventing a separate cookie- or query-string-based auth path just for this one endpoint (and putting a bearer token in a URL, which risks it ending up in server/proxy logs), `useCaseEventsStream` (`frontend/src/hooks/use-case-events.ts`) consumes the stream with a plain `fetch` call and parses `text/event-stream` frames out of the response body manually. The tradeoff: reconnection isn't automatic like `EventSource` gives you for free, so the hook implements its own exponential backoff.

**Read more:** <https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events>

### Virtualized lists

**What:** Instead of rendering one DOM element per row in a list, a virtualizer renders only the rows currently near the visible viewport (plus a small overscan buffer) and absolutely-positions them inside a container sized to the *full* list's total height. Scrolling swaps which rows are mounted rather than growing the DOM without bound.

**Why here:** A `<table>` can't be virtualized directly — absolutely positioning only a handful of rows requires each row to be independently placeable, which native table layout (rows sized and positioned relative to each other by the browser) doesn't allow. `DataTable`'s virtualized path (`frontend/src/components/ui/data-table.tsx`) switches to CSS grid/flex `div`s instead, which is the standard workaround — but that trade loses the *automatic* accessibility semantics a real `<table>`/`<tr>`/`<td>` gives a screen reader for free, unless you explicitly restore them. Real ones: `role="table"`, `role="row"`, `role="cell"`, `role="columnheader"` on the right elements. This project only reaches for it where a list is genuinely unbounded (a tenant's full case history, an append-only webhook delivery log) — applying it to small, naturally-bounded lists (a handful of API keys) would add real complexity for no benefit.

**Read more:** <https://tanstack.com/virtual/latest/docs/introduction>

### Route-based code splitting

**What:** Instead of one JavaScript bundle containing every screen's code, each route's component is wrapped in `React.lazy(() => import('./SomePage'))`, which tells the bundler (Vite, here) to put that screen in its own separate file, fetched only when a user actually navigates to it. A `<Suspense>` boundary with a fallback covers the brief gap between "user clicked a link" and "that chunk's JS finished downloading."

**Why here:** Phase 8/9 shipped one ~778kB bundle containing the reviewer console, the entire admin console (API keys, webhooks, reviewer management), and `recharts` (a sizeable charting library used only by the admin dashboard) — a reviewer who never touches the admin screens was still downloading all of it. Splitting by route means a reviewer's first load only fetches the reviewer queue's code; the admin bundle (and `recharts` specifically) only loads if and when someone actually visits `/admin/overview`.

**Read more:** <https://react.dev/reference/react/lazy>

---

## Phase 11 concepts

### Hash-chained audit logs (tamper-evidence, not tamper-prevention)

**What:** Every audit row stores a `row_hash` computed over its own fields *plus* the previous row's `row_hash` (its `prev_row_hash`). This links every row into a chain: row 2's hash depends on row 1's hash, row 3's depends on row 2's, and so on. Change anything about a historical row — its payload, its timestamp, even by one byte — and that row's own hash no longer matches what it should be, which means it no longer matches what the *next* row recorded as `prev_row_hash`. The break is detectable by simply recomputing hashes and comparing (`scripts/verify_audit_chain.py`).

**Why here:** This is deliberately *tamper-evidence*, not *tamper-prevention* — nothing here stops someone with direct database access (a compromised superuser credential, an insider with production access) from editing a row. What it guarantees is that if they do, it's provable after the fact: the chain breaks at exactly the row they touched, and every row after it. That's the actual property a regulator cares about for a KYC/AML audit trail — not "this system is impossible to tamper with" (no software-only mechanism can promise that against someone with full DB access), but "if it was tampered with, we can prove it and point to exactly where." This project also had to solve a real concurrency problem to make the chain safe to write to from multiple processes at once — see `services/pipeline/audit.py`'s `pg_advisory_xact_lock` comment for why a naive "read the last hash, then insert" is a race.

**Read more:** <https://en.wikipedia.org/wiki/Hash_chain>

### Row-level security scopes *every* connection, including your own tooling

**What:** Once RLS is enabled on a table with a policy like `USING (tenant_id = current_setting('app.tenant_id')::uuid)`, *every* query against it — from the application, from a script, from a `psql` session — is filtered by that policy, unless the connecting role is a superuser or the table's owner (RLS is a no-op for those, by design). There's no such thing as "RLS applies to normal requests but not to my one-off script" unless the script explicitly connects as a role RLS doesn't apply to.

**Why here:** `scripts/verify_audit_chain.py`'s first draft connected using the application's own `sentrilog_app` role — the same role every API request uses — and silently reported "chain OK" for a tenant that actually had real audit rows, because a connection with no `app.tenant_id` ever set matches *no* tenant's rows under that RLS policy. Zero rows found isn't the same as zero rows existing, and the script had no way to tell the difference from the outside; it just looked like success. The fix — connecting as the Postgres superuser role instead, the same one this project's own test cleanup already uses to bypass RLS — is the correct shape for this specific tool: an audit-chain verifier is inherently an out-of-band, elevated-access operation, not a normal tenant-scoped request, and it should look like one.

**Read more:** <https://www.postgresql.org/docs/current/ddl-rowsecurity.html>

---

## Coming up later (named now so you know to watch for them)

- **OpenTelemetry tracing** (Phase 12) — following one request across multiple services/processes.
- **Terraform modules & remote state** (Phase 13) — infrastructure as version-controlled code.

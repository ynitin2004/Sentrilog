# Sentrilog — Improvements Plan

**From a portfolio pipeline to a production KYC/AML product.**

This document is the engineering plan for everything after Phase 12. It is written to be read top to bottom once, then used as a reference: §1–§3 define terms and the baseline, §4 is the target system design, §5 is every known problem with its fix, its bypass, and the alternatives that were rejected, §6 is the phased roadmap, §7–§9 are the AI, messaging and integration designs in detail, §10 is the decision log.

Everything stated about the *current* system is taken from the code and from measurements made in Phases 5–12.1, not from the original plan. Where a number is an estimate rather than a measurement it says so.

---

## 0. How to read this

- **Read §2 first if any term is unfamiliar.** The rest of the document uses them without re-explaining.
- **Every problem in §5 has four parts:** what is wrong, the proper fix, the *bypass* (the fastest thing that unblocks a customer without the proper fix), and the alternatives that were considered with the verdict and reasoning. A bypass is not a recommendation — it is a documented escape hatch for when a design partner is waiting.
- **Verdicts are decisions, not surveys.** Each one names a choice and the condition under which it should be revisited.
- **Phases in §6 continue PLAN.md's numbering** (13 onward) so the status table and changelog keep one timeline. Phase 13 as written in PLAN.md ("AWS infra + deploy") is reshaped here: an evaluation harness has to exist *before* deployment, because without it nothing deployed can be tuned or defended.

---

## 1. Where the project actually is

Measured at `v0.12.1` (2026-10-30).

| Dimension | State |
|---|---|
| Backend | 10,461 lines of Python across `services/`, `scripts/`, `tests/`; FastAPI intake, Temporal pipeline, Qdrant screening |
| Frontend | 5,372 lines of TypeScript (1,253 generated from OpenAPI); React 19, TanStack Query, SSE, 38 tests, typecheck and lint clean |
| Tests | 192 backend tests (191 + 1 skipped on CI), all against real Postgres/Temporal/Qdrant/S3; 4,644 test lines vs 4,160 service lines |
| CI | Green since run 21 (first ever), ~2 min, four service containers |
| Deployed anywhere | **No.** `infra/terraform/` is a README |
| Tenancy | Shared Postgres, RLS forced on 11 tables, `SECURITY DEFINER` auth lookup, 404-not-403 cross-tenant |
| Audit | Per-tenant hash chain, advisory-locked, shared formula module, verified by `scripts/verify_audit_chain.py` against a tampered row |
| Workflow | One Temporal workflow, 8 activities, parallel face-match + screening, signal queue for reviewer decisions, per-tier task queues |
| Extraction | EasyOCR MRZ → ICAO 9303 checksum → Gemini VLM fallback with bounded, error-fed retry |
| Screening | 10 invented names in Qdrant, vector + Metaphone phonetic, threshold 0.55 |
| Face match | Real InsightFace `buffalo_l`, CPU, tested on two photographs |
| Risk | `0.6·face + 0.4·extraction ≥ 0.85` auto-clears; any sanctions hit or no face → review. **No validation set behind any constant.** |
| Measured limits | Noisy-neighbour p95: pooled 3.74× / partitioned 1.14× (I/O), 5.44× / 2.51× (CPU). Extraction throughput flat at 32 concurrent (`asyncio.to_thread` default pool). MRZ short-circuits 58% of cases to zero API calls. 15% bad-VLM-response rate costs 26% more billed calls, not 15% |
| Security posture | Dev-grade and documented as such: CORS `*`, token in localStorage, seeded bearer tokens for reviewers, malware scan stubbed, no encryption at rest, in-process rate limiter |

The honest one-line summary: **the control plane (tenancy, audit, workflow, tests) is senior-grade; the data plane (models, lists, thresholds) is a demo; the deployment plane does not exist.**

---

## 2. Definitions

Terms used throughout. Domain first, then systems, then AI.

### 2.1 Domain

- **KYC (Know Your Customer):** verifying that a person is who they claim to be, usually from an identity document plus a live photo, before a regulated business (bank, exchange, lender) can serve them.
- **AML (Anti-Money Laundering):** the broader obligation to detect and report suspicious actors. KYC is one input; screening and ongoing monitoring are others.
- **CDD / EDD:** Customer Due Diligence (standard checks) and Enhanced Due Diligence (deeper checks for higher-risk subjects, e.g. a PEP).
- **Sanctions list:** a government-published list of people and entities one may not do business with. OFAC SDN (US), UN Consolidated, EU Consolidated, UK HMT are the major ones. They change daily.
- **PEP (Politically Exposed Person):** someone holding or close to public office; higher-risk by regulation, not necessarily sanctioned.
- **Adverse media:** news or public records suggesting a subject is involved in crime, fraud or corruption. Regulators expect it to be screened alongside lists.
- **Screening vs monitoring:** screening is at onboarding; monitoring is re-screening existing customers when lists change.
- **False positive / false negative, in this domain:** a false positive sends a clean person to a human (cost); a false negative lets a sanctioned person through (regulatory failure). This asymmetry is why screening is "tuned for recall".
- **MRZ (Machine Readable Zone):** the two or three lines of `<`-padded text on passports and ID cards, specified by ICAO 9303, with check digits. Free, deterministic extraction when present and legible.
- **VLM (Vision-Language Model):** a model that takes an image and text and returns text; used here to read documents the MRZ path cannot.
- **Liveness:** proving the selfie is of a present, live person rather than a photo of a photo or a replayed video. Not built.
- **V-CIP (Video Customer Identification Process):** India's RBI-mandated video-call KYC for certain institutions. Relevant to one of the candidate wedges in §3.
- **Audit trail / tamper-evidence:** a record of every decision that can be shown to a regulator, where any after-the-fact edit is detectable. Tamper-*evident* (edits are visible) is different from tamper-*proof* (edits are impossible); this system is the former.

### 2.2 Systems

- **Multi-tenancy:** one deployment serving many client organisations, each unable to see the others' data. Here: shared database, `tenant_id` on every row, enforced by **RLS**.
- **RLS (Row-Level Security):** Postgres policies that filter every query by a session variable. `FORCE` makes it apply to the table owner too.
- **Idempotency key:** a client-supplied token on a write so a retried request does not create a duplicate. Enforced by `UNIQUE (tenant_id, idempotency_key)`.
- **Durable workflow (Temporal):** code whose execution state is persisted by a server so it survives process death and can wait days for an external signal. Activities are the side-effecting steps; the workflow is the deterministic orchestration.
- **Task queue:** the named channel a Temporal worker polls. Partitioning by plan tier is how one tenant's burst is kept off another's capacity.
- **Transactional outbox:** writing "an event happened" into a table *in the same transaction* as the business row, and having a separate relay publish it. Solves "the row committed but the message was never sent."
- **Message broker vs event log:** a broker (RabbitMQ, SQS) delivers each message to a consumer and forgets it; an event log (Kafka, Redis Streams) retains messages so they can be replayed and read by many independent consumers. The distinction decides most of §7.
- **Pub/Sub:** fire-and-forget fan-out to whoever is listening right now. No durability. Right for "refresh your screen," wrong for "deliver this webhook."
- **BFF (Backend for Frontend):** a thin server that holds the session cookie and talks to the API on the browser's behalf, so the browser never holds a long-lived token.
- **OIDC / SAML / SSO:** the protocols by which a company's identity provider (Okta, Azure AD, Google) vouches for a user to a third-party app. Enterprise buyers require it.
- **WORM (Write Once Read Many):** storage that cannot be modified or deleted for a retention period. S3 Object Lock is the AWS implementation.
- **SLO / SLA:** an internal target (SLO) vs a contractual promise with penalties (SLA). This document sets SLOs.
- **p95:** the latency that 95% of requests beat. The number that matters for user experience; averages hide tails.

### 2.3 AI

- **Embedding:** a fixed-length vector representing text or an image such that similar things are near each other. The index and the query must use the *same* model.
- **Vector search / ANN:** finding the nearest embeddings to a query vector, approximately, fast. Qdrant does this.
- **Reranker (cross-encoder):** a model that scores a (query, candidate) *pair* jointly, more accurately but more slowly than embedding distance. Used on the top-k from vector search to cut false positives.
- **RAG (Retrieval-Augmented Generation):** retrieve relevant documents, put them in a model's context, generate an answer grounded in them. **Sentrilog has retrieval (Qdrant) but no generation grounded in it, so it is not using RAG today.**
- **Multimodal RAG:** the same loop where the retrieved context includes images (reference document templates, specimens) and the generator is a VLM that can look at them.
- **Late-interaction retrieval (ColBERT / ColPali):** representing a document as *many* vectors (one per token or image patch) and scoring by max-similarity per query token. More precise for dense documents than one vector per page; Qdrant supports it as multivectors.
- **Cite-or-abstain:** a generation rule where every claim must point at a retrieved source, and the model says "no evidence" rather than guessing. The only acceptable mode for anything a regulator may read.
- **Eval harness:** a fixed dataset plus scoring code that turns "is the model better?" into a number that can gate a release. Without one, every threshold is a guess.
- **Structured output:** forcing a model to return JSON matching a schema. Already used for extraction via Pydantic validation and retry.
- **MCP (Model Context Protocol):** an open protocol by which an AI assistant discovers and calls tools exposed by a server. A product can *expose* an MCP server (so assistants can use it) and a team can *consume* MCP servers (so its own tooling can reach GitHub, databases, etc.).

---

## 3. Product thesis and the wedge

### 3.1 What the market already has

Persona, Onfido (Entrust), Sumsub, Veriff, Jumio sell document verification, face match, sanctions/PEP screening and a review console as a bundle, with hundreds of supported document types and global coverage. A small team cannot win on coverage or on "the pipeline".

### 3.2 What this repository already has that they do not lead with

A **per-tenant, hash-chained, model-versioned audit trail**, a **durable human-in-the-loop workflow** whose every transition is recorded at both ends, and a **test culture that measures rather than asserts**. Nobody sells "AI-assisted compliance decisions you can hand to a regulator" as the headline. That is the wedge.

### 3.3 The thesis

> **Sentrilog is the compliance review workbench where every AI suggestion is evidence.** The pipeline gets the case to a reviewer with a draft decision, cited sources, and a risk policy the tenant wrote; the reviewer accepts, edits or rejects; and the complete chain — model versions, list snapshot, policy version, retrieved sources, human action — is tamper-evident and exportable.

The pipeline remains, because the workbench needs cases flowing through it, but it is no longer the product. The product is the review experience and its auditability.

### 3.4 Candidate wedges (pick one with design partners, not in a document)

| Wedge | Why a small team can win | Risk |
|---|---|---|
| **Regulated-audit niche** (lenders / exchanges that failed an audit) | Audit trail is the differentiator; pain is acute and budgeted | Must find them; sales-led |
| **Indian NBFC / fintech** (RBI V-CIP, CKYC, DigiLocker, Aadhaar offline XML, PAN) | Regional regulation beats global incumbents; team is local | Crowded (HyperVerge, Signzy, IDfy); V-CIP needs video, not built |
| **Mid-market crypto / web3 onboarding** | Price-sensitive, API-first, tolerant of a young vendor | Churn and regulatory volatility |
| **Embedded KYC for vertical SaaS** (property, gig platforms, marketplaces) | Buyers want an API and a white-label console, not a compliance department | Each vertical has its own doc types |

The roadmap in §6 is wedge-agnostic through Phase 15 and assumes a wedge has been chosen by Phase 16.

---

## 4. Target architecture (HLD)

### 4.1 Component diagram

```
                                  ┌──────────────────────────────────────────────────────────┐
                                  │                        AWS account (one per env)          │
  Tenant systems                  │                                                          │
  ┌──────────────┐  API key       │   ┌────────────┐     ┌─────────────────────────────────┐ │
  │ Client app   │───────────────▶│   │  WAF + ALB │────▶│  Intake API (ECS Fargate, N×)   │ │
  └──────────────┘                │   └────────────┘     │  FastAPI · OIDC · Redis RL     │ │
  ┌──────────────┐  OIDC session  │          ▲           └──┬─────────┬─────────┬──────────┘ │
  │ Review       │───────────────▶│          │              │         │         │            │
  │ console SPA  │  (httpOnly)    │          │        outbox│   SSE   │  signal │            │
  └──────────────┘                │          │              ▼         ▼         ▼            │
  ┌──────────────┐  MCP (stdio/   │   ┌────────────┐  ┌──────────┐ ┌───────┐ ┌───────────┐   │
  │ Analyst's AI │  streamable    │   │ Sentrilog  │  │ Postgres │ │ Redis │ │ Temporal  │   │
  │ assistant    │───HTTP)───────▶│   │ MCP server │  │ RDS M-AZ │ │ Elasti│ │ Cloud     │   │
  └──────────────┘                │   │ (sidecar)  │  │ + Proxy  │ │ Cache │ │ (or self) │   │
                                  │   └────────────┘  └────┬─────┘ └───┬───┘ └─────┬─────┘   │
                                  │                        │           │           │          │
  Presigned PUT/GET               │   ┌────────────┐  ┌────┴───┐  ┌────┴──────────┴────────┐  │
  ┌──────────────┐                │   │ S3 docs    │  │ Outbox │  │  Pipeline workers      │  │
  │ Browser /    │───────────────▶│   │ SSE-KMS    │  │ relay  │  │  (ECS, one pool / tier)│  │
  │ client       │                │   │ Object Lock│  └────────┘  │  standard│pro│enterprise│  │
  └──────────────┘                │   └─────┬──────┘              └──┬────┬────┬────┬────┬──┘  │
                                  │         │ S3 event                │    │    │    │    │     │
                                  │         ▼                         │    │    │    │    │     │
                                  │   ┌────────────┐  ┌───────────┐   │    │    │    │    │     │
                                  │   │ SQS upload │─▶│ Upload    │───┘    │    │    │    │     │
                                  │   │ -complete  │  │ consumer  │ signal │    │    │    │     │
                                  │   └────────────┘  └───────────┘        │    │    │    │     │
                                  │   ┌────────────┐                       │    │    │    │     │
                                  │   │ GuardDuty  │ malware tag on object  │    │    │    │     │
                                  │   │ S3 scan    │                        │    │    │    │     │
                                  │   └────────────┘                        ▼    ▼    ▼    ▼     │
                                  │                               ┌──────────┐┌──────┐┌────────┐│
                                  │                               │ Bedrock  ││Qdrant││ Face   ││
                                  │                               │ VLM +    ││Cloud ││ (CPU,  ││
                                  │                               │ embeds   ││      ││ in-pool││
                                  │                               │ (VPC EP) ││      ││ )      ││
                                  │                               └──────────┘└──────┘└────────┘│
                                  │   ┌─────────────────────────────────────────────────────┐   │
                                  │   │ Sanctions feed ingester (Temporal schedule, daily)  │   │
                                  │   │ OpenSanctions/OFAC/UN → versioned snapshot → Qdrant │   │
                                  │   └─────────────────────────────────────────────────────┘   │
                                  │   ┌──────────────┐ ┌───────────────┐ ┌─────────────────┐    │
                                  │   │ OTel collector│ │Secrets Manager│ │ Audit anchor    │    │
                                  │   │ → Grafana/DD  │ │ + KMS CMKs    │ │ (chain head →   │    │
                                  │   └──────────────┘ └───────────────┘ │  S3 Object Lock)│    │
                                  │                                       └─────────────────┘    │
                                  └──────────────────────────────────────────────────────────┘
```

What changed from today, in one line each:

- **Redis** appears, for three jobs only: distributed rate limiting, SSE fan-out, and short-lived caches (§7.3).
- **Outbox + relay** replaces "start the workflow after the commit and hope" (§5, P-10).
- **S3 → SQS → signal** replaces the 20-minute "document not yet uploaded" retry loop (§7.4).
- **Temporal on its own store** (Temporal Cloud by default) closes the untested split-store failure from the Phase 12 chaos test.
- **One worker pool per tier** turns the `--tier` flag into the shipped default, closing the measured CPU leak (2.51×).
- **Bedrock behind the existing `VLMClient` / `EmbeddingClient` protocols** keeps PII inside the VPC; Gemini stays for local development.
- **An MCP server sidecar** exposes the review and audit surface to AI assistants (§9).
- **Nothing is Kafka.** See §7.1 for why, and for the conditions under which that changes.

### 4.2 Request flows

#### Flow A — case submission

```
Client                 ALB/WAF        Intake API              Redis    Postgres             Outbox relay        Temporal
  │ POST /cases          │               │                      │         │                    │                   │
  │ Idempotency-Key      │               │                      │         │                    │                   │
  │─────────────────────▶│──────────────▶│                      │         │                    │                   │
  │                      │   WAF rate    │ resolve API key      │         │                    │                   │
  │                      │   (per IP)    │ (SECURITY DEFINER)   │────────▶│                    │                   │
  │                      │               │ rate limit per key   │         │                    │                   │
  │                      │               │──────────────────────▶ INCR    │                    │                   │
  │                      │               │◀────────── allowed ──│         │                    │                   │
  │                      │               │ BEGIN; set_config(tenant)      │                    │                   │
  │                      │               │ INSERT cases ON CONFLICT DO NOTHING                 │                   │
  │                      │               │ INSERT documents ×2 │         │                    │                   │
  │                      │               │ INSERT outbox('case.created') ─ same tx            │                   │
  │                      │               │ INSERT audit_log('case_created')                   │                   │
  │                      │               │ COMMIT ──────────────────────▶│                    │                   │
  │                      │               │ presign PUT ×2 (local, no network)                 │                   │
  │◀── 201 {case_id, upload_urls} ───────│                      │         │                    │                   │
  │                      │               │                      │         │  poll outbox       │                   │
  │                      │               │                      │         │◀───────────────────│ start_workflow    │
  │                      │               │                      │         │                    │──(id=case_id)────▶│
  │                      │               │                      │         │  mark published    │                   │
  │                      │               │                      │         │◀───────────────────│                   │
```

The response no longer waits on Temporal. If Temporal is down, the case is created and the workflow starts when it returns — the relay is the retry. `start_workflow` is idempotent on `case_id` (already true), so a relay crash between "start" and "mark" is harmless.

#### Flow B — upload completion and document safety

```
Browser          S3                 GuardDuty         SQS              Upload consumer        Temporal workflow
  │ PUT (presigned)│                   │                │                   │                     │
  │───────────────▶│ ObjectCreated     │                │                   │                     │
  │                │──────────────────▶│ scan           │                   │                     │
  │                │                   │ tag object     │                   │                     │
  │                │◀──────────────────│ (NO_THREATS /  │                   │                     │
  │                │                   │  THREATS_FOUND)│                   │                     │
  │                │ ObjectTagging event ───────────────▶│                   │                     │
  │                │                   │                │──────────────────▶│ read tag            │
  │                │                   │                │                   │ clean → signal      │
  │                │                   │                │                   │  document_uploaded ─▶│ (was: retry
  │                │                   │                │                   │ threat → signal     │  every 5s–2m
  │                │                   │                │                   │  document_rejected ─▶│  for 20 min)
```

The workflow's `fetch_case_documents` becomes "wait for both `document_uploaded` signals, or a 20-minute timer, whichever first" — a `wait_condition`, not a retry policy. The existing `_LONG_RETRY_POLICY` on extraction remains for genuine model/network failure.

#### Flow C — the pipeline (unchanged shape, new grounding)

```
Temporal workflow (per tier queue)                          External
  │
  ├─ wait: document_uploaded ×2  (Flow B)  ─ or 20m timer → park(reason="upload timeout")
  │
  ├─ extract_document ──────────────── EasyOCR MRZ → checksum OK? → done (58% of cases, $0)
  │       └─ else: embed image ─────── Bedrock multimodal embedding
  │                retrieve templates ─ Qdrant 'doc_templates' (top-3 + specimen check)
  │                VLM with context ── Bedrock VLM, structured output, bounded retry
  │                                     specimen near-duplicate? → park(reason="specimen image")
  │
  ├─ parallel ─┬─ face_match ──────── InsightFace (CPU) [+ liveness, later]
  │            └─ sanctions_screen ── embed name → Qdrant (list_version=N) → rerank top-20 → hits
  │                                    + phonetic codes → hits
  │                                    + adverse media RAG → memo with citations (Phase 16)
  │
  ├─ risk_score ───────────────────── tenant policy (version P) → score, reason, needs_review
  │
  ├─ needs_review? ─┬─ no  → finalize(approved) → deliver_webhooks (child workflow, long retry)
  │                 └─ yes → update_status(needs_review)
  │                          draft_review_memo ── RAG over case + policy + similar decisions (Phase 16)
  │                          wait: reviewer_decision signal (days, if needed)
  │                          finalize(decision) → deliver_webhooks
  │
  └─ every activity: audit_log .started / .completed|.failed with model_version, list_version, policy_version
```

#### Flow D — reviewer decision (console)

```
Browser (SPA)        Intake API (OIDC session cookie)     Postgres        Redis pub/sub     Temporal
  │ POST /review/cases/{id}/decision                        │                 │                │
  │────────────────▶│ session → reviewer, role               │                 │                │
  │                 │ require_decision_maker (403 if auditor)│                 │                │
  │                 │ BEGIN; INSERT review_decisions; audit  │────────────────▶│                │
  │                 │ COMMIT                                 │                 │                │
  │                 │ signal submit_review_decision ──────────────────────────────────────────▶│
  │◀── 200 ─────────│                                        │                 │                │
  │                 │                                        │  finalize activity updates cases│
  │                 │                                        │◀────────────────────────────────│
  │                 │                                        │ NOTIFY (trigger) → relay → PUBLISH case_status_changed
  │                 │◀───────────────────────────────────────────────────────│                │
  │◀── SSE frame ───│ (every API replica subscribed)         │                 │                │
```

Today the API `LISTEN`s on Postgres directly. That stops working behind RDS Proxy (which does not support `LISTEN`/`NOTIFY`), so fan-out moves to Redis Pub/Sub with a tiny relay; Postgres stays the source of truth.

#### Flow E — analyst's AI assistant via MCP

```
Claude / other assistant        Sentrilog MCP server (sidecar)       Intake API
  │ tools/list                     │                                   │
  │───────────────────────────────▶│ get_case, list_review_queue,      │
  │                                │ get_audit_trail, screen_name,     │
  │                                │ submit_decision, export_evidence  │
  │ tools/call get_audit_trail     │                                   │
  │───────────────────────────────▶│ same bearer/OIDC credential ─────▶│ RLS, role check, audit row
  │◀── structured result ──────────│◀──────────────────────────────────│ (actor = "mcp:<user>")
```

The MCP server owns no logic and no data access; it is a typed adapter over the public API, so every tenancy and role rule applies unchanged and every call is in the audit chain with an `mcp:` actor prefix.

### 4.3 Data stores

| Store | Role | Why this and not the alternative |
|---|---|---|
| **Postgres (RDS Multi-AZ, PITR, RDS Proxy)** | System of record: tenants, cases, documents, extractions, hits, decisions, audit_log, outbox, policies, list snapshots | Already designed around RLS; nothing else gives row-level tenancy this cheaply. Proxy because many tenants × many short connections exhausts Postgres without pooling |
| **Temporal (Cloud, or self-hosted on its own RDS)** | Workflow state | Must be a *separate* store from the application DB — the Phase 12 chaos test only passed because they were shared, which hides the real failure mode |
| **S3 (SSE-KMS, per-data-class buckets, Object Lock on audit anchors)** | Raw documents, exports, audit anchors | Already the design; Object Lock is what makes "tamper-evident" survive a database restore |
| **Qdrant (Cloud or ECS)** | Sanctions embeddings (versioned collections), document templates (multivector), adverse-media chunks | Already in use; multivector support is what makes multimodal late-interaction retrieval possible without a second vector store. pgvector would serve sanctions alone but not ColPali-style retrieval |
| **Redis (ElastiCache)** | Rate limits, SSE fan-out, embedding/rerank cache, OIDC session index | Three distinct jobs; none of them want durability; all of them want sub-millisecond shared state across API replicas |
| **SQS** | Upload-complete events from S3 | Exactly one producer (S3) and one consumer; a broker, not a log, is the right shape |
| **Not present: Kafka, RabbitMQ, OpenSearch** | — | §7.1 |

### 4.4 Deployment topology

- **Environments:** `dev` (compose, unchanged), `staging`, `prod`. One Terraform state each; `prod` apply gated on plan artifact + manual approval.
- **Compute:** ECS Fargate for the intake API (2+ tasks, target-tracking on CPU and ALB request count), the outbox relay (1 task), the upload consumer (1 task), the MCP sidecar, and **one ECS service per plan tier** for pipeline workers (`--tier standard|pro|enterprise`, each with its own `--max-concurrent-activities` and scaling on Temporal task-queue backlog). InsightFace runs on CPU inside the worker image; a GPU worker service is a Phase 19 option, not a Phase 14 requirement.
- **Network:** private subnets, ALB the only public ingress, VPC endpoints for S3, KMS, Secrets Manager, Bedrock, SQS. No NAT path for PII.
- **Secrets:** Secrets Manager behind the existing `SecretsProvider` seam (`services/common/secrets.py`); KMS customer-managed keys per data class.
- **Observability:** OTel collector sidecar; traces + logs + metrics to Grafana Cloud (free tier to start) or Datadog (already configured as an integration here; switch when revenue justifies). Per-tenant dashboards tagged by `tenant_id` as PLAN.md §5 specifies.

### 4.5 Service level objectives (targets, not yet measured in production)

| Metric | Target | Why this number |
|---|---|---|
| `POST /cases` p95 | < 300 ms | Removing the synchronous Temporal start (outbox) is what makes this reachable |
| Auto-clear pipeline p95 (MRZ path) | < 20 s | OCR + face match on CPU; no model API in the loop |
| Auto-clear pipeline p95 (VLM path) | < 90 s | One VLM call with bounded retry + retrieval |
| Review-queue freshness (SSE) | < 2 s from DB commit | Redis pub/sub replaces LISTEN; same target as today |
| Availability (API) | 99.9% monthly | Multi-AZ RDS + 2 API tasks; Temporal Cloud carries its own SLA |
| Audit chain verification | 100% of tenants, nightly, zero breaks | Existing script, scheduled; a break pages |
| Screening recall on the eval set | ≥ 0.98 at the shipped threshold | Set by the harness in Phase 13; the number is a placeholder until measured |

---

## 5. Problems, fixes, bypasses, and the alternatives rejected

Numbered **P-1…P-18**. "Bypass" means the fastest path that lets a design partner proceed *this week*; it is always inferior to the fix and is listed so the trade is explicit rather than improvised under pressure.

### P-1 · The VLM path cannot legally touch real documents

**What is wrong.** Extraction calls Gemini on a free-tier key, and `.env.example` itself says "only send synthetic test images, never real ID/selfie data." Consumer-tier terms permit training on inputs. The core feature is unusable for a customer by its own documentation.

**Fix.** Route production inference through a provider with contractual zero data retention, inside the VPC, behind the existing `VLMClient` protocol (`services/pipeline/extraction/vlm.py`). Keep Gemini free tier for local development only, selected by settings.

**Bypass.** Google AI Studio *paid* tier (not free) changes the data-use terms; a paid key and a settings change unblocks a pilot in a day. Still crosses the public internet and leaves AWS; acceptable for a pilot with synthetic-adjacent data, not for production.

**Alternatives.**

| Option | Data stays in VPC | Zero retention | Structured output | Rework | Cost/1k pages (est.) |
|---|---|---|---|---|---|
| Gemini free tier (today) | No | **No** | Yes | — | $0 |
| Gemini paid / Vertex AI | No / Vertex: GCP VPC-SC | Yes (paid), yes (Vertex) | Yes | Low; second cloud for PII | ~$1–3 |
| **Claude or Nova on Bedrock** | **Yes (VPC endpoint)** | **Yes, by policy** | Yes | Low: one new `VLMClient` impl | ~$3–8 (Claude), ~$1 (Nova) |
| Azure OpenAI | Azure VNet | Yes | Yes | Third cloud | ~$3–8 |
| Self-hosted open VLM (Qwen2.5-VL / Llama 3.2 Vision) on GPU | Yes | Yes (ours) | Yes, less reliably | High: GPU fleet, MLOps | GPU-hours; cheap at volume, expensive at low volume |

**Verdict: Bedrock, with the specific model chosen by the Phase 13 eval harness, not by this document.** It keeps every byte of PII inside the AWS account the rest of the system already lives in, it needs one adapter class, and the choice between the expensive-accurate and cheap-fast model is exactly the question an eval harness answers. Self-hosting is the Phase 19 cost lever once volume justifies a GPU fleet; the protocol seam means it is a drop-in. Vertex would be the choice only if the deployment target were GCP. *Revisit if* Bedrock's model availability in the chosen region lags, or if extraction F1 on the harness is below a self-hosted model's by more than the cost difference justifies.

### P-2 · No evaluation harness; every threshold is a guess

**What is wrong.** `0.85` auto-clear, `0.6/0.4` weights, `confidence = 1 − 0.15·(attempt−1)`, sanctions threshold `0.55` — none has a dataset behind it. Nobody can say what the false-reject rate is, which is the first question a buyer asks and the first thing a regulator probes.

**Fix.** A versioned evaluation dataset and a scoring harness (`services/eval/`), run in CI as a non-blocking report and as a blocking gate on the metrics that matter:

- *Extraction:* field-level precision/recall/F1 per document type; MRZ-path vs VLM-path split; cost per correct field.
- *Screening:* recall and false-positive rate at the shipped threshold over name pairs (true matches from list aliases; hard negatives from near-miss names); reranker lift.
- *Face match:* TAR at FAR = 1e-3 over a labelled pair set; separate "no face" detection accuracy.
- *Risk policy:* auto-clear precision (how many auto-approved cases a human would have rejected) and review burden (fraction sent to humans) per policy version.
- *Reviewer copilot (Phase 16):* acceptance rate of drafts, citation validity rate, abstain rate.

**Datasets.** Public ID-document sets (MIDV-500, MIDV-2020) for extraction and layout; a synthetic ID generator (already partly exists in the Phase 4 test tooling) for volume; OpenSanctions alias data for name-pair construction; labelled face pairs from public research sets for TAR/FAR. Every dataset is versioned, hashed, and its hash recorded in the eval run row.

**Bypass.** A 200-case hand-labelled set from a design partner's historical (anonymised) data and a notebook that prints the five numbers above. Enough to tune one tenant's policy; not enough to gate releases.

**Alternatives.**

| Option | Verdict |
|---|---|
| Keep tuning by intuition | Rejected: unmeasurable, indefensible to a buyer |
| Third-party eval SaaS (LLM-eval vendors) | Rejected for v1: the metrics here are classical (F1, TAR/FAR), not LLM-judge metrics; a vendor adds cost and a data-sharing question for PII |
| **Own harness, pytest-driven, results in a `eval_runs` table** | **Chosen:** the project already runs real-infra tests in CI; the harness is the same pattern with a dataset fixture and a metrics assertion |

### P-3 · One global risk formula

**What is wrong.** `risk_scoring.py` is three constants. Every compliance team has a different appetite and different jurisdictional rules (a PEP is a hard stop for one tenant and a review flag for another).

**Fix.** A per-tenant, versioned **policy** document, validated by Pydantic, evaluated by a pure function, with `policy_version` recorded on every `risk_score.completed` audit row and on the case. Shape:

```yaml
version: 3
auto_clear:
  min_combined: 0.85
  weights: { face_match: 0.6, extraction: 0.4 }
hard_stops:
  - sanctions_hit            # any hit → review
  - no_face_detected
  - specimen_image_detected  # Phase 17
  - pep_match: review        # or: reject
document_rules:
  expired: reject
  expiring_within_days: { days: 30, action: review }
jurisdictions:
  blocked: [ "XX" ]
```

**Bypass.** Make the three constants per-tenant columns on `tenants`. Two migrations, no evaluator. Buys one quarter.

**Alternatives.**

| Option | Verdict |
|---|---|
| Constants per tenant | Bypass only: cannot express hard stops or document rules |
| **Declarative YAML/JSON policy + Pydantic + pure evaluator** | **Chosen:** versioned, diffable, testable with the harness, no new runtime |
| OPA / Rego | Rejected for now: a second language and runtime for rules that are, today, a dozen fields. Adopt if a customer needs logic the schema cannot express |
| Hosted rules engines | Rejected: policy *is* the product's IP and must be in the audit chain |

### P-4 · Sanctions screening is ten invented names with no version identity

**What is wrong.** `services/screening/data.py` is a demo list; `ingest.py` is a one-shot script; `sanctions_hits` and `audit_log` record `model_version` but not *which list snapshot* was screened. "Was this person listed on that date?" is unanswerable, and lists change daily.

**Fix.** A daily **Temporal schedule** runs an ingester that pulls the feed, writes an immutable `list_snapshots` row (`id`, `source`, `published_at`, `content_hash`, `entry_count`), upserts into a **versioned Qdrant collection** (`sanctions_v{snapshot_id}` or a `list_version` payload filter), and flips an alias. Every screen records `list_version`; re-screening of existing customers (monitoring) becomes "run screen for every open customer against the new snapshot, diff hits" — a second scheduled workflow.

**Bypass.** Pull the OFAC SDN CSV by hand into `ingest.py`, add a `list_version` column defaulting to the file's publication date. Real data, manual refresh, versioned. One day of work.

**Alternatives.**

| Feed | Coverage | Format | Cost | Verdict |
|---|---|---|---|---|
| OFAC SDN direct | US only | XML/CSV, stable | Free | Fallback / cross-check |
| UN Consolidated direct | UN | XML | Free | Fallback |
| **OpenSanctions** | OFAC + UN + EU + UK + ~100 more, **plus PEPs**, deduplicated with stable entity IDs | JSON/CSV, daily | Free for non-commercial; **commercial licence required** for a product | **Chosen for breadth and PEP coverage; budget the licence** |
| Commercial screening data vendors (Dow Jones, LSEG World-Check) | Broadest, adverse media included | API | $$$ | Later, if a customer requires a specific vendor |

**Also:** add a **cross-encoder reranker** over the top-20 vector candidates before applying the threshold. This is the single highest-leverage accuracy change available in screening and it is not RAG — see §8.4.

### P-5 · Reviewer identity is a seeded bearer token

**What is wrong.** No login, no SSO, no MFA, no session expiry; tokens live in `localStorage`. B2B compliance buyers will not pilot without SSO, and `auth.py` assumes opaque tokens throughout.

**Fix.** OIDC for the console: the SPA does the authorization-code-with-PKCE flow against an IdP; the API validates the ID token/session and maps `sub` → `reviewers` row. Session in an **httpOnly, SameSite=Strict cookie** issued by the API (a minimal BFF role), token never in JS. Enterprise tenants federate their own IdP (SAML/OIDC) through the provider. API keys for machine clients stay exactly as they are.

**Bypass.** Keep bearer tokens but add expiry, rotation endpoint, and move the SPA's storage to a cookie set by the API. Removes the XSS-exfiltration risk; does not give SSO. One week.

**Alternatives.**

| Provider | Enterprise SSO federation | Self-host | Cost at 1k reviewers | Verdict |
|---|---|---|---|---|
| **Cognito** | SAML + OIDC federation | No (managed) | Low; AWS-native, IAM-integrated | **Chosen: stays in the AWS account, cheapest, adequate** |
| Keycloak | Full | Yes | Ops burden | Rejected: one more stateful service on a small team |
| Auth0 / Okta CIC | Full, best DX | No | Higher | Alternative if Cognito's hosted UI and federation setup costs sales cycles |
| WorkOS | Enterprise SSO as a product | No | Per-connection | Adopt *alongside* Cognito if enterprise onboarding becomes the bottleneck — it is built for exactly that |

### P-6 · The thirteen-phase waterfall built breadth before a customer

**What is wrong.** Twelve phases of horizontal scope with no external user. This is a process decision and it is the most expensive one in the repository.

**Fix.** Phases 13–15 (§6) are the minimum to put a real case from a real design partner through the system. Phase 16 onward is gated on having one.

**Bypass.** None. Talk to five prospective customers before Phase 16.

### P-7 · Nothing is deployed; `infra/terraform` is a README

**Fix.** Phase 14. Terraform, module per layer, one state per environment, as PLAN.md §5 already specifies. Nothing in that plan needs to change except Temporal's store (P-9) and the Redis addition (§7.3).

**Bypass.** For a demo to a design partner: a single EC2 host running `docker compose` with a real domain, TLS via Caddy, and real Bedrock credentials. Two days. Not a production topology and not where the audit anchors should live, but it is a URL someone can click.

**Alternatives.** ECS Fargate (planned) vs EKS vs a PaaS (Fly.io, Railway, Render).

| Option | Verdict |
|---|---|
| **ECS Fargate** | **Chosen:** no node patching, per-service scaling, IAM per task, matches PLAN.md; adequate until a GPU fleet is needed |
| EKS | Rejected for now: control-plane cost and Kubernetes operational surface for a small team; revisit with GPU workers (Phase 19) |
| PaaS | Rejected: PII residency, VPC endpoints, KMS ownership and Object Lock are the compliance story; a PaaS makes them someone else's story |

### P-8 · Security posture is dev-grade

Each item has its own fix; they are listed together because they ship together in Phase 14.

| Item | Today | Fix | Bypass |
|---|---|---|---|
| CORS | `allow_origins=["*"]` | Allow-list the console origin per environment | Same, one line |
| Session storage | `localStorage` | httpOnly cookie (P-5) | Cookie without SSO |
| Malware scan | Stub returning `True` | GuardDuty Malware Protection for S3 (managed, in-account) gates the `document_uploaded` signal (Flow B) | ClamAV Lambda on `ObjectCreated` |
| Encryption at rest | None locally | SSE-KMS with per-data-class CMKs; RDS encrypted; Qdrant volume encrypted | — |
| Rate limiting | In-process fixed window | WAF per-IP at edge + Redis sliding window per API key in app (§7.3) | WAF alone |
| Pagination | `LIMIT 200` | Keyset pagination (`created_at, id`) on every list endpoint; the frontend `DataTable` already virtualises | Raise the limit; not a fix |
| CSP / headers | None | Strict CSP, HSTS, frame-ancestors none | — |
| Secrets | `.env` | Secrets Manager via existing provider seam | — |
| Audit anchoring | Chain lives only in the DB it protects | Nightly: write each tenant's chain-head hash to an S3 Object Lock bucket; verification script checks DB head against the anchored head | — |

### P-9 · Temporal shares the application database

**What is wrong.** The Phase 12 chaos test passed *because* a Postgres outage froze Temporal too, so no retry budget was spent. Production will have them separate, where the ~3-second `_SHORT_RETRY_POLICY` budget on DB-writing activities genuinely would strand cases. That scenario is untested.

**Fix.** Separate store (Temporal Cloud, or self-hosted on its own RDS). Then **re-run `scripts/chaos_test.py postgres` against the split topology** and widen `_SHORT_RETRY_POLICY` for database-writing activities to survive an RDS failover (~60–120 s), accepting the workflow-timing change the Phase 12 changelog deferred.

**Alternatives.**

| Option | Verdict |
|---|---|
| **Temporal Cloud** | **Chosen:** removes a stateful system from a small team's on-call; the untested failure mode becomes someone else's SLA. Cost is per action; model it in Phase 14 |
| Self-hosted on own RDS | Fallback if cost or data-residency rules out Cloud; adds a second RDS and the Temporal server to operate |
| Keep shared DB | Rejected: ships the untested failure mode |

### P-10 · Workflow start is not transactional with case creation

**What is wrong.** `create_case` commits, then calls `start_kyc_case_workflow`. A crash between the two leaves a `pending` case nothing will ever drive. Documented as a known gap since Phase 5.

**Fix.** Transactional outbox: `outbox(id, tenant_id, aggregate, event, payload, created_at, published_at)` written in the same transaction; a relay task polls unpublished rows (`FOR UPDATE SKIP LOCKED`), starts the workflow, marks published. Idempotent on `case_id` at the Temporal side, so at-least-once is safe.

**Bypass.** A reconciliation job every five minutes: `SELECT id FROM cases WHERE status='pending' AND created_at < now() - interval '2 min'` and start a workflow for each. Same correctness, worse latency, zero schema change. A day.

**Alternatives.**

| Option | Verdict |
|---|---|
| Reconciliation job | Bypass only |
| **Outbox table + polling relay** | **Chosen:** no new infrastructure, exactly-once-effective with the existing idempotent start |
| Debezium CDC → Kafka → consumer | Rejected: three new components to solve a problem a table solves |
| Start workflow *before* commit, compensate on failure | Rejected: creates workflows for cases that never existed |

### P-11 · Upload completion is a 20-minute retry loop

**What is wrong.** `fetch_case_documents` fails with "document not yet uploaded" and Temporal retries under `_LONG_RETRY_POLICY` until the object appears. Correct, but it burns retries, pollutes the audit trail with `.failed` rows for a non-failure, and cannot distinguish "slow upload" from "never uploaded".

**Fix.** S3 `ObjectCreated` (after the GuardDuty tag, Flow B) → SQS → a small consumer that signals the workflow `document_uploaded(doc_type, sha256)`. The workflow waits on a condition with a timer. The sha256 arrives for free and populates `documents.sha256`, which is nullable today because nothing computes it.

**Alternatives.** EventBridge → Lambda → signal (fine; SQS chosen for simpler local emulation and a visible backlog), or polling S3 from the API (what we are replacing).

### P-12 · Worker topology leaks CPU across tiers

**What is wrong.** Measured: per-tier queues cut I/O-bound interference to 1.14× but CPU-bound only to 2.51×, because one process runs every tier's workers under one GIL. The `--tier` flag exists; nothing deploys with it.

**Fix.** One ECS service per tier (§4.4), each scaling on its own queue backlog. Also raise the extraction ceiling (measured flat at 32 concurrent) by installing a sized `ThreadPoolExecutor` via `loop.set_default_executor` in `worker.py`, with the size a CLI flag — the number comes from Phase 14 instance sizing, as the Phase 12 changelog said.

**Bypass.** Three `docker compose` services with `--tier` each. An afternoon; gives the isolation locally and in the single-host demo.

### P-13 · Face match has no liveness and two test photos

**Fix (Phase 17, optional by wedge).** Passive liveness (single-frame anti-spoofing model) on the selfie before embedding; a labelled spoof/live eval set in the harness. Active liveness (challenge-response video) only if the wedge is V-CIP. **Alternatives:** AWS Rekognition Face Liveness (managed, in-account, per-check cost) vs an open anti-spoofing model (free, needs eval). Verdict: Rekognition Liveness for v1 if the wedge needs liveness at all — it is a solved commodity and not where differentiation lives.

### P-14 · No adverse-media or PEP screening

**Fix.** Phase 16, §8.2. PEP comes with the OpenSanctions feed (P-4). Adverse media is a RAG service with cite-or-abstain.

### P-15 · The reviewer sees evidence but no draft

**Fix.** Phase 16, §8.1 — the product's core feature.

### P-16 · Stale documentation

**What is wrong.** `README.md` names PaddleOCR/Textract (the code uses EasyOCR); `frontend/README.md` says "Phase 8 (current): mock data layer"; `infra/terraform/README.md` says "Implemented in Phase 10"; `docs/README.md` cites a Phase 9 runbook. Four wrong claims in the four files a reader opens first.

**Fix.** Today. Not a phase.

### P-17 · Frontend has no CI job and no end-to-end tests

**Fix.** A `frontend` job in `ci.yml` (typecheck, lint, vitest) and a Playwright suite against the real API in the existing service-container topology — Playwright is already a devDependency with zero tests. Phase 13.

### P-18 · No usage metering or billing

**Fix.** Phase 18. A `usage_events` table written by the outbox relay (one row per case, per VLM call, per screen) → nightly aggregation → Stripe metered billing. Not before a customer.

---

## 6. Phased roadmap

Each phase keeps PLAN.md's conventions: a branch, ≥ 4 logical commits, measured exit criteria, a changelog entry with known gaps, a tag. Durations assume one engineer full-time plus the AI-assisted workflow the project already uses; they are estimates.

### Phase 13 — Evaluation harness, real data, honest docs (2–3 weeks)

The phase that makes every later phase measurable.

- `services/eval/` with datasets (versioned, hashed), metrics, `eval_runs` table, CI report job.
- OFAC + OpenSanctions ingest with `list_snapshots` and `list_version` on hits and audit rows (P-4 fix, feed part).
- Cross-encoder reranker behind a `Reranker` protocol; measured lift on the screening set.
- Per-tenant policy document, evaluator, `policy_version` in the audit chain (P-3).
- Frontend CI job + first Playwright flow (connect → queue → decide) (P-17).
- Fix the four stale READMEs (P-16).
- **Exit criteria:** extraction F1, screening recall/FPR, face TAR@FAR and auto-clear precision all reported by CI with numbers; the shipped sanctions threshold is the one the harness chose, with the FPR it implies written in PLAN.md.

### Phase 14 — Production platform (4–6 weeks)

- Terraform: network, data (RDS Multi-AZ + Proxy, ElastiCache, Qdrant, S3 + KMS + Object Lock), compute (ECS services per §4.4), security (IAM per task, Secrets Manager), one state per env.
- Temporal Cloud (P-9), then **re-run the postgres chaos test on the split topology** and set the DB-activity retry budget from what it shows.
- Redis: rate limiter behind the existing `InMemoryRateLimiter` interface; SSE fan-out relay (§7.3).
- Outbox + relay (P-10); S3 → SQS → signal with GuardDuty gating (P-11, Flow B); `documents.sha256` populated.
- Bedrock `VLMClient` and `EmbeddingClient` implementations, model chosen by the harness (P-1).
- OIDC via Cognito, httpOnly session, CORS allow-list, CSP, keyset pagination (P-5, P-8).
- OTel → Grafana Cloud or Datadog; per-tenant dashboards; nightly chain verification + audit anchoring to Object Lock.
- Load test the deployed topology with the existing `scripts/load_test_*.py` and record the new noisy-neighbour and ceiling numbers.
- **Exit criteria:** a real case submitted over the public internet by a design partner's integration reaches a decision; all §4.5 SLOs measured for a week in staging; chaos postgres passes in the split topology; chain verification green nightly.

### Phase 15 — Compliance core and monitoring (3 weeks)

- Scheduled re-screening of open customers on each new list snapshot; diff → `needs_review` with reason `new_list_hit`.
- PEP handling via policy hard stops.
- Evidence export: a signed, hash-verified bundle (PDF + JSON) per case for regulators, including policy and list versions.
- Webhook delivery as a child workflow with long retry and a dead-letter state visible in the console.
- **Exit criteria:** a list update produces a monitoring hit on a previously-approved case within the schedule window; an exported bundle re-verifies offline with the public verification script.

### Phase 16 — The reviewer workbench: copilot and adverse media (5–6 weeks) — *requires a chosen wedge and one design partner*

- Text RAG service (§8.1): index tenant policy docs, past decisions, case evidence; draft memo with citations; cite-or-abstain; every draft audited with model and index versions; reviewer accept/edit/reject recorded.
- Adverse-media RAG (§8.2): source ingestion (licensed news API or OpenSanctions-adjacent datasets), subject-scoped retrieval, memo with citations.
- Console: draft panel, citation hover, "why this score" from the policy evaluator, keyboard-first review.
- Harness additions: draft acceptance rate, citation validity, abstain rate.
- **Exit criteria:** on the design partner's cases, reviewer median time-to-decision drops by a measured amount with no change in reviewer-vs-reviewer agreement; zero uncited claims in sampled memos.

### Phase 17 — Multimodal document grounding and fraud (4–5 weeks)

- Template corpus (MIDV + partner-supplied specimens) → multimodal embeddings → Qdrant multivector collection (§8.3).
- Extraction prompt receives top-k templates as visual context; harness measures F1 lift per document type.
- Specimen near-duplicate detection → policy hard stop.
- Optional by wedge: liveness (P-13).
- **Exit criteria:** measured extraction F1 improvement on the VLM path for at least three document types; specimen detection recall on a held-out set.

### Phase 18 — Distribution: MCP server, SDKs, metering (3 weeks)

- Sentrilog MCP server sidecar (§9.1) with the six tools, OAuth-backed.
- Python and TypeScript SDKs generated from the OpenAPI spec (the frontend already generates its types this way).
- Usage events → metered billing (P-18); status page.
- **Exit criteria:** an analyst completes a review from Claude Desktop via MCP, and the audit chain shows the `mcp:` actor rows.

### Phase 19 — Scale and cost (ongoing)

- GPU worker service and self-hosted VLM behind the same protocol, switched by tenant volume; measured cost-per-case versus Bedrock.
- Sized executors per activity class; per-tier autoscaling policies tuned from production backlog metrics.
- Revisit §7.1's Kafka trigger conditions with real numbers.

---

## 7. Messaging and state: what is used for what, and why not Kafka

### 7.1 The decision

**No Kafka. No RabbitMQ. Temporal for anything that must happen; Postgres outbox for anything that must happen *because* a row committed; SQS for the one S3 integration; Redis Pub/Sub for anything that merely refreshes a screen.**

The reasoning is about *shape*, not fashion:

| Need in this system | Durable? | Replay? | Fan-out to many consumers? | Ordering? | Right tool |
|---|---|---|---|---|---|
| Drive a case through 8 steps over days, survive crashes, wait for a human | Yes | No (history is the replay) | No | Per case | **Temporal** (have it) |
| Start the workflow iff the case row committed | Yes | No | No | No | **Outbox + relay** |
| Know an upload finished and was scanned | Yes | No | One consumer | No | **S3 → SQS** |
| Deliver a webhook with retries and a dead letter | Yes | No | No | Per webhook | **Temporal child workflow** |
| Refresh every open console when a case changes | **No** | No | Yes, ephemeral | No | **Redis Pub/Sub** |
| Rate limit per API key across N API replicas | No (expiring) | No | Shared counter | No | **Redis** |
| Daily list ingest, nightly chain verification | Yes | No | No | No | **Temporal schedules** |
| Analytics / audit export to a warehouse | Yes | Yes | Many | Per tenant | *Not needed yet* — see trigger below |

Kafka earns its place when several independent consumers need to replay the same ordered event history (analytics, search indexing, a second product team) or when sustained throughput passes what a Postgres outbox comfortably relays (order of a few thousand events/second). Neither is true, and the first customer does not change that. RabbitMQ would be chosen over SQS only for routing-key fan-out or on-prem deployment; neither applies.

**Revisit Kafka (or Kinesis / Redis Streams as the cheaper step) when any of these is true:** a third independent consumer of case events exists; outbox relay lag exceeds 5 s at p95 under load; or a customer requires a replayable event feed as a product feature.

### 7.2 Comparison, for the record

| | Kafka / MSK | RabbitMQ / Amazon MQ | SQS | Redis Streams | Postgres outbox | Temporal |
|---|---|---|---|---|---|---|
| Durability | Yes, retained log | Yes, until consumed | Yes, until consumed (14 d max) | Yes, in Redis (memory/AOF) | Yes, in the DB | Yes, in its store |
| Replay | Yes | No | No | Yes (bounded) | Yes (it is a table) | History per workflow |
| Ordering | Per partition | Per queue | FIFO queues only | Per stream | By `id` | Per workflow |
| Ops burden | High | Medium | None | Low (have Redis) | None | Cloud: none |
| Fit here | Over-built | Wrong shape | One job | Fallback for events | Core | Core |

### 7.3 Redis: exactly three jobs

1. **Rate limiting.** Sliding-window counter per API key via a small Lua script (atomic `INCR` + `EXPIRE` on bucketed keys). Replaces `InMemoryRateLimiter` behind the same `check(key) -> (allowed, retry_after)` interface, so the existing tests and the injectable clock pattern carry over. WAF still handles per-IP abuse at the edge before a request reaches the app, as PLAN.md §5 specifies — the two layers answer different questions.
2. **SSE fan-out.** A Postgres trigger still `NOTIFY`s on `cases` status change; one small relay task `LISTEN`s on a direct (non-Proxy) connection and `PUBLISH`es to Redis; every API replica subscribes. The browser-facing hook (`use-case-events.ts`) does not change. Why not Redis as the source: the DB is the source of truth and the trigger already exists; why not keep `LISTEN` in the API: RDS Proxy does not support it, and N replicas each holding a raw connection defeats the Proxy.
3. **Caches with TTL.** Embedding results for repeated names (screening the same subject twice), reranker scores, and the OIDC session index. All regenerable; Redis loss costs latency, never correctness.

What Redis is **not** used for: anything in the audit chain, anything a workflow waits on, idempotency (that is the Postgres unique constraint and must stay there).

### 7.4 SQS: one integration

S3 cannot signal Temporal. SQS is the buffer between `ObjectCreated`/`ObjectTagging` and the consumer that calls `handle.signal("document_uploaded", ...)`. Standard queue; the consumer is idempotent (signalling twice is harmless because the workflow condition checks "both present"). A dead-letter queue catches malformed events. Locally, `docker-compose.yml` gets an SQS emulator (ElasticMQ is the lightest) and the consumer polls it; the S3 event itself is simulated by the test uploading and then posting the event, which is what the chaos-test pattern already does for Temporal signals.

---

## 8. AI design: RAG where it earns its place

### 8.0 The rule

An AI feature enters this system only if (a) the harness can score it, (b) its output is attributable to retrieved sources or it abstains, and (c) a human is between it and a decision until (a) proves otherwise. Everything below follows that rule. Nothing below replaces the deterministic paths (MRZ, checksums, list matching) — it grounds the probabilistic ones.

### 8.1 Reviewer copilot — text RAG (Phase 16)

**Purpose.** When a case lands in `needs_review`, the reviewer opens it to a *draft decision with citations*, not a blank justification box.

**Corpus (per tenant, RLS-scoped).**
- The tenant's own compliance policy documents (uploaded PDFs/markdown), chunked by section.
- The case's full evidence: extraction, face score, hits with list version, the audit trail.
- Past decided cases of the same tenant: justification text + outcome, embedded, so "cases like this were usually rejected because X" is retrievable.

**Retrieval.** Hybrid: BM25 (Postgres full-text) + vector (Qdrant, per-tenant collection or payload filter) → reciprocal-rank fusion → cross-encoder rerank → top-8 chunks.

**Generation.** A VLM/LLM on Bedrock with a fixed prompt: produce a decision recommendation, a justification of ≤ 200 words, and a list of citations `{chunk_id, quote}`. Hard rule: every sentence either carries a citation to a retrieved chunk or the model emits `INSUFFICIENT_EVIDENCE` for that point. Post-generation validation rejects any citation whose quote is not a substring of the chunk.

**Audit.** The draft, its model version, the retrieval index version, the chunk IDs, and the reviewer's action (accept / edit-with-diff / reject) are all `audit_log` rows. This is the feature's moat: the AI's contribution is evidence in the same chain as the human's.

**Harness metrics.** Acceptance rate, edit distance when edited, citation validity (automatic), abstain rate, and reviewer agreement before vs after. The feature is on by default only when acceptance ≥ an agreed floor on the design partner's data.

**Frameworks.** Hand-rolled over the existing `EmbeddingClient` protocol, Qdrant and Postgres FTS; no LangChain/LlamaIndex. Reason: the pipeline is ~200 lines, the audit requirements are unusual (every retrieval must be recorded), and a framework's abstractions sit between us and that record. Revisit if the number of distinct RAG pipelines exceeds three.

### 8.2 Adverse media — text RAG (Phase 16)

**Purpose.** "Is there public reporting that this subject is involved in financial crime?" with sources.

**Corpus.** A licensed news/records source (budget item; OpenSanctions' adjacent datasets cover some), ingested continuously, chunked per article, embedded, stored in Qdrant with `published_at`, `source`, `url` payload.

**Retrieval.** Subject-scoped: query by name variants (the phonetic and transliteration logic that already exists) + DOB/nationality filters where present → top-30 → rerank → top-10.

**Generation.** A screening memo: for each retrieved article, *does it refer to this subject* (entity disambiguation is the hard part — "John Smith" has many) and *what does it allege*, with the quote. Cite-or-abstain. The memo never changes the risk score automatically; it is evidence for the policy's `adverse_media: review` hard stop and for the reviewer.

**Harness.** A labelled set of (subject, article, is-same-entity, is-adverse) pairs; precision on entity match is the number to watch — false attribution is a reputational and legal problem.

### 8.3 Multimodal document grounding — multimodal RAG (Phase 17)

**Purpose.** Extraction today is zero-shot: the VLM sees one image and a schema. Grounding it with the *right reference template* improves accuracy on the long tail of document types and enables authenticity checks.

**Corpus.** Reference templates: specimen images per country / document type / series; a text card per template (field layout, MRZ format, security features, known forgery tells, validity period). Sources: MIDV-500 / MIDV-2020 (public, research licence — check terms for commercial use), PRADO-style public specimen references, and design-partner-supplied specimens under NDA.

**Indexing — the decision.**

| Option | How it works | Precision on dense documents | Infra | Verdict |
|---|---|---|---|---|
| Single-vector multimodal embedding (Bedrock Titan Multimodal, SigLIP/CLIP-class) | One vector per image, one per text card, shared space | Good for "which document type is this" | Qdrant single vector; Bedrock in-VPC | **v1:** the question at this stage is *type and series identification*, which single vectors answer well |
| Late interaction (ColPali / ColQwen) | Hundreds of patch vectors per page, MaxSim scoring | Better for "which *region* matches" | Qdrant multivector; a GPU or patience at index time | **v2 (Phase 19 option):** adopt if field-level grounding needs region retrieval |
| OCR text → text embedding | Lossy; discards layout and security features | Weak | Cheapest | Rejected: the point is visual grounding |

**Query.** Embed the uploaded ID (same model as the index) → top-3 templates by vector → payload filter by MRZ-inferred country when the MRZ was readable → pass the template *images* and text cards to the VLM alongside the upload: "This is likely a `{type}` `{series}`; fields are at …; verify `{security features}`; extract only what is printed."

**Second use, same index.** Specimen near-duplicate detection: if the upload's distance to any *specimen* image is below a tight threshold, it is almost certainly a downloaded sample, not a real document → policy hard stop. This is a fraud check for nearly zero marginal cost.

**Harness.** Retrieval: recall@3 on type/series identification over a held-out template set. Generation: field-level F1 on the VLM path with vs without grounding, per document type — the lift must be measured separately from retrieval quality, because a wrong template retrieved confidently makes extraction *worse*, and that failure mode is invisible in an end-to-end number.

**What you learn building it, in order:** that index and query embedders must be identical to the byte; that single-vector vs multivector is a precision/infra trade, not a quality ranking; that visual few-shot context changes VLM behaviour more than prompt wording; that retrieval and generation need separate evals; and that a retrieval system's worst failure is a confident wrong neighbour, not a miss.

### 8.4 Not RAG, but higher leverage: the reranker

Sanctions matching is entity resolution, not question answering. A cross-encoder reranker (`bge-reranker-v2-m3` class, CPU-servable) over the top-20 vector candidates, scored on (query name, candidate name + aliases + DOB/nationality), with the threshold then set by the harness, will reduce false positives more than any generative step. It ships in Phase 13 because the harness exists to prove it.

### 8.5 Model and prompt versioning

Every AI call records: provider, model ID, prompt template hash, retrieval index version, and (for RAG) chunk IDs. `audit_log.model_version` already exists; it becomes a structured JSON field. The eval harness refuses to compare runs with different prompt hashes unless explicitly told to.

---

## 9. MCP

### 9.1 Sentrilog as an MCP server (Phase 18)

A sidecar process exposing a typed tool surface over the public API. No logic, no direct database access — every call carries the user's OIDC token or API key, so RLS, the auditor/reviewer role check and the audit chain apply unchanged. Actor is recorded as `mcp:<sub>`.

| Tool | Maps to | Role |
|---|---|---|
| `list_review_queue(status?, limit?)` | `GET /review/cases` | reviewer, admin, auditor |
| `get_case(case_id)` | `GET /review/cases/{id}` | reviewer, admin, auditor |
| `get_audit_trail(case_id)` | new `GET /cases/{id}/audit` (read-only, verified chain segment) | all |
| `screen_name(name, dob?, nationality?)` | `POST /screen` (new, stateless, metered) | api key |
| `submit_decision(case_id, decision, justification)` | `POST /review/cases/{id}/decision` | reviewer, admin — **never** auditor; the existing 403 applies |
| `export_evidence(case_id)` | Phase 15 bundle | admin, auditor |

Transport: streamable HTTP for hosted assistants, stdio for local. Auth: OAuth 2.1 as the MCP spec describes for remote servers, backed by Cognito. The product value is distribution — a compliance analyst's existing assistant can work the queue — and the safety value is that *nothing* an assistant does bypasses the chain.

### 9.2 MCP servers the team should consume

| Server | Status here | Use |
|---|---|---|
| **GitHub** (official) | Not connected — CI logs were only readable via a token workaround in Phase 12.1 | PRs, CI runs and logs, issues, from the agent |
| **Postgres** | Not connected | Inspect RLS state, audit chains, eval runs on dev/staging |
| **Qdrant** (official) | Not connected | Inspect collections, debug a screening miss, verify a snapshot flip |
| **Playwright** (Microsoft) | Not connected; Playwright installed with zero tests | Author and run the Phase 13 e2e suite |
| **Sentry** | Not connected; no error aggregation exists | Phase 14 prerequisite |
| **AWS** (awslabs servers: IaC, cost, IAM) | Not connected | Phase 14 Terraform review, cost modelling |
| **Datadog, Linear, Slack** | Configured in this environment, **unauthorised** | Authorise before Phase 14: observability, tracking, alerts |
| Temporal | *Not listed* — no official server confirmed; do not assume one | Use the Temporal CLI/UI |

---

## 10. Decision log (ADR summary)

One line per decision so the reasoning survives the people who made it.

| ID | Decision | Chosen | Over | Because | Revisit when |
|---|---|---|---|---|---|
| D-1 | Orchestration | Temporal (keep) | Celery, Step Functions, Kafka sagas | Durable waits for human signals; history is half the audit trail | Never for this product |
| D-2 | Workflow store | Temporal Cloud | Self-host, shared RDS | Removes a stateful system; closes the untested split-store failure | Cost per action exceeds self-host + on-call cost |
| D-3 | Event backbone | None (outbox + SQS + Redis) | Kafka, RabbitMQ | No replay or multi-consumer need; wrong shape | Third consumer, >5 s relay lag, replay as a feature |
| D-4 | Shared state | Redis (ElastiCache) | Postgres-only, Memcached | Sub-ms shared counters and pub/sub across replicas; RDS Proxy drops LISTEN | — |
| D-5 | VLM | Bedrock, model by harness | Gemini free (today), Vertex, Azure, self-host | PII stays in the account; one adapter | Self-host when GPU cost < API cost at volume |
| D-6 | Embeddings | Bedrock (text + multimodal) | Gemini embedding (today), OSS | In-VPC; same reason | Same |
| D-7 | Vector DB | Qdrant (keep) | pgvector, OpenSearch | Multivector for Phase 17; already integrated | pgvector if multimodal is dropped |
| D-8 | Reranker | OSS cross-encoder, CPU | API rerankers | Cheap, in-VPC, enough at this k | Latency at p95 > 500 ms |
| D-9 | Sanctions feed | OpenSanctions (licensed) + OFAC direct | Direct feeds only, commercial vendors | Breadth + PEPs; stable IDs | Customer mandates a vendor |
| D-10 | Identity | Cognito OIDC; WorkOS if enterprise SSO drags | Keycloak, Auth0 | AWS-native, cheap, federates | Enterprise onboarding becomes the sales bottleneck |
| D-11 | Risk policy | Declarative per-tenant YAML + evaluator | Constants, OPA | Versioned, auditable, testable | Customer logic exceeds the schema |
| D-12 | Compute | ECS Fargate | EKS, PaaS | No nodes; IAM per task; PII controls | GPU fleet |
| D-13 | IaC | Terraform | Pulumi, CDK | PLAN.md; module-per-layer; widest hiring pool | — |
| D-14 | Observability | OTel → Grafana Cloud → Datadog later | X-Ray/CloudWatch only | Vendor-neutral exporters already in place | Revenue |
| D-15 | Malware scan | GuardDuty S3 Malware Protection | ClamAV Lambda | Managed, in-account, tags objects | Cost at volume |
| D-16 | Audit anchoring | Chain head → S3 Object Lock nightly | Public transparency log, blockchain | Simple, auditable, no third party | Regulator asks for external anchoring |
| D-17 | RAG framework | None; hand-rolled over existing protocols | LangChain, LlamaIndex | Audit requirements; small surface | >3 distinct pipelines |
| D-18 | Multimodal retrieval | Single-vector v1, late-interaction v2 | ColPali from day one | Type identification first; infra later | Field-level region grounding needed |
| D-19 | Liveness | Rekognition Liveness, only if wedge needs it | OSS anti-spoofing | Commodity; not differentiating | Cost or residency |
| D-20 | Upload completion | S3 → SQS → signal | Polling with retry (today), EventBridge+Lambda | Removes 20-min retry loop; sha256 for free | — |

---

## 11. Explicitly not doing

- **Not** replacing Temporal with anything.
- **Not** introducing Kafka, RabbitMQ or a data warehouse before a third consumer exists.
- **Not** letting any AI output change a case's outcome without a human, until the harness shows auto-accept precision a tenant's policy explicitly allows.
- **Not** building V-CIP video, active liveness or on-prem deployment unless the chosen wedge requires it.
- **Not** supporting a second cloud for PII.
- **Not** adding a feature that the eval harness cannot score.

---

## Appendix A — Current file → change map

| File | Change | Phase |
|---|---|---|
| `services/intake/main.py` | Outbox write in `create_case`; remove synchronous workflow start; keyset pagination; CORS allow-list; OIDC session; `/cases/{id}/audit`, `/screen` | 14, 18 |
| `services/intake/ratelimit.py` | `RedisRateLimiter` behind the same interface | 14 |
| `services/intake/auth.py` | OIDC validation path alongside API keys; role mapping from IdP claims | 14 |
| `services/intake/scanning.py` | Remove stub; GuardDuty tag check moves to upload consumer | 14 |
| `services/pipeline/workflows/kyc_case.py` | `document_uploaded` wait condition; webhook child workflow; policy version in inputs; draft-memo activity | 14–16 |
| `services/pipeline/workflows/activities.py` | Bedrock clients; grounded extraction; reranked screening; `list_version`/`policy_version` on audit rows | 13–17 |
| `services/pipeline/workflows/worker.py` | Sized default executor flag; per-tier default in deployment | 14 |
| `services/pipeline/risk_scoring.py` | Replaced by `services/policy/` evaluator | 13 |
| `services/screening/data.py`, `ingest.py` | Replaced by feed ingester + `list_snapshots` | 13 |
| `services/screening/screen.py` | Reranker stage; `list_version` on hits | 13 |
| `services/common/secrets.py` | `SecretsManagerProvider` | 14 |
| `services/common/observability.py` | OTLP exporter alongside console | 14 |
| `services/eval/` (new) | Harness, datasets, metrics | 13 |
| `services/rag/` (new) | Copilot, adverse media, document grounding | 16–17 |
| `services/mcp/` (new) | MCP sidecar | 18 |
| `infra/terraform/` | Everything | 14 |
| `docker-compose.yml` | Redis, ElasticMQ, three worker services with `--tier` | 13–14 |
| `.github/workflows/ci.yml` | Frontend job, eval report job, Playwright | 13 |
| `frontend/` | OIDC flow, cookie session, draft panel, citations, pagination | 14, 16 |
| `README.md`, `frontend/README.md`, `infra/terraform/README.md`, `docs/README.md` | Correct the stale claims | now |

## Appendix B — Cost model placeholders (per 10,000 cases/month, estimates to be replaced by Phase 14 measurements)

| Item | Driver | Rough range |
|---|---|---|
| VLM extraction | ~42% of cases reach the VLM (58% MRZ short-circuit, measured); ~1.26 calls per VLM case at a 15% bad-response rate (measured) | $20–60 on Bedrock depending on model |
| Embeddings + rerank | One name embedding per case; rerank on CPU | < $5 |
| Temporal Cloud | ~12 actions per case | Vendor pricing; model before Phase 14 |
| RDS Multi-AZ + Proxy, ElastiCache, Qdrant | Fixed | Dominant at low volume; size for 10× headroom |
| S3 + KMS | Two objects per case, retention by policy | Small |
| GuardDuty S3 scan | Per GB scanned | Small |
| OpenSanctions licence | Fixed | Budget line |

The point of the table is that **fixed infrastructure dominates until roughly the tens of thousands of cases per month**, which is why Phase 14 should size for headroom rather than optimise per-case cost, and why self-hosting models is Phase 19, not Phase 14.

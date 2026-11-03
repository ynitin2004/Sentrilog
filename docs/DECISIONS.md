# Decisions

Every settled decision and why, so that one is not re-argued by accident. New decisions are appended
through the Quiz Rule in `CLAUDE.md`. Each entry gives the decision, the reason, and what would reopen it.

- **Part 1 (D1–D19):** decisions already in the code, gathered from the [PLAN.md](../PLAN.md) changelog.
- **Part 2 (D20–D25):** the $0 deployment, accepted as a plan and not built.
- **Part 3 (D26–D39):** Phase 13: what was chosen before measuring, and what measuring changed.
- **Part 4 (R1–R20):** the proposed roadmap. Full reasoning is in [improvements.md](../improvements.md) §5–§10.

## Part 1: In the code

### D1 — Temporal for orchestration, not Celery
Cases wait days for a reviewer, and Temporal has durable waits for external signals, per-activity retry
policy, and an execution history that doubles as half the audit trail. The cost is one more stateful
system. **Revisit:** not for this product.

### D2 — Shared database with RLS, no schema-per-tenant or sharding
`tenant_id` is denormalized onto every table so policies need no join. RLS is forced as defence in depth
over application scoping, and proven with real cross-tenant attempts. **Revisit:** a client's
data-residency requirement that a shared database cannot meet.

### D3 — A separate unprivileged `sentrilog_app` role
Found while implementing RLS: superusers bypass it with no override. The app therefore never connects as
`sentrilog`, and CI runs as `sentrilog_app` so a green run means what it appears to.

### D4 — Auth lookups through `SECURITY DEFINER` functions
Resolving a key must happen before a tenant is known, which RLS would block. Narrow functions answer that
one question and nothing else.

### D5 — Hashed per-tenant API keys with SHA-256, not bcrypt or OAuth
Keys are high-entropy random strings, so a fast hash is correct, and a slow hash would tax every request.
OAuth2 client credentials were the alternative for enterprise clients. **Revisit:** an enterprise client
requires them.

### D6 — Cross-tenant 404, same-tenant role failure 403
A 403 on another tenant's resource confirms the ID is real. An auditor can already see the case in their
own queue, so there a 403 hides nothing and a 404 would mislead.

### D7 — MRZ first, VLM as fallback
A checksum-valid MRZ is free and verifiable. The Phase 12 load simulation had 58 % of cases costing no API
call. **Revisit:** not while documents carry an MRZ.

### D8 — Gemini free tier behind a `VLMClient` protocol, synthetic data only
Free for a build with no budget and swappable by design. Free-tier data terms rule out real documents.
**Revisit:** replaced by R5 before any real document is processed.

### D9 — Self-hosted InsightFace for face match, not Rekognition
Biometric data stays in the deployment, at the cost of more MLOps. **Revisit:** liveness needs (R19).

### D10 — Vector and phonetic screening together, tuned for recall
Vectors catch names that read differently and mean the same; Metaphone catches names that sound alike and
embed apart. "Mohammed" and "Muhammad" share the primary code `MHMT`. A false negative is a compliance
failure. **Revisit:** the threshold when the harness can measure it (R8).

### D11 — Auto-approve only, never auto-reject
A low score or hard red flag goes to a human. An automated rejection of a person is a heavier decision than
this system has earned. **Revisit:** a tenant's explicit policy, backed by measured precision.

### D12 — The review queue is a status, not a table
`cases WHERE status = 'needs_review'` cannot fall out of sync with the case. Claims are advisory, and
`escalated` is not terminal.

### D13 — Hash-chained audit log, insert-only grants, one shared formula
Two services write one per-tenant chain, so a second copy of the formula would fork it on a one-byte drift.
Writers serialise on an advisory lock. This gives tamper evidence, not tamper prevention. **Revisit:**
anchoring outside the database (R16).

### D14 — Postgres `LISTEN/NOTIFY` for real-time, no broker
Postgres already does it, and the notification commits with the data. It costs one unpooled connection per
tab, plus tenant filtering in Python. **Revisit:** connection pooling arrives (R4).

### D15 — HMAC-signed webhooks with recorded outcomes
An async, possibly multi-day pipeline is unusable if integrators must poll. Retries live inside the activity
to avoid duplicate deliveries from activity-level retries.

### D16 — Secrets through a provider seam and `SecretStr`
Secrets leak through tracebacks and settings dumps more than through deliberate prints. Moving to a real
secrets manager should change which provider is returned and nothing else.

### D17 — Third-party clients behind lazy accessors
`genai.Client(api_key="")` raises immediately, so a module-scope client made a credential an import-time
requirement for every activity. Found only by CI. A worker serving webhooks should not need an extraction key.

### D18 — `adobe/s3mock` for dev and CI, not MinIO or LocalStack
MinIO's images are withdrawn, and LocalStack's current tags require a licence token. It was checked against
every S3 operation the code performs. It is an emulator and ignores signatures, so it proves nothing about
IAM, SSE-KMS or signing. **Revisit:** real S3 or Garage in deployment.

### D19 — Local gates equal CI gates; CI runs real services
CI failed 19 straight times partly because pre-commit and CI checked different paths. CI now applies the real
schema, runs four real services and gates on real readiness operations. `OTEL_TRACES_EXPORTER=none` keeps
logs readable, and the one test that bills an API skips rather than fakes. mypy uses `explicit_package_bases`
because `scripts/` has no `__init__.py`.

## Part 2: The $0 deployment

Accepted as a plan on 2026-11-02 and **not built**. Details and sources: [DEPLOY.md](DEPLOY.md).

### D20 — Oracle Always Free A1 with Docker Compose, for a hiring demo
Constraints from the user: $0/month, no domain, always on. It is the only free host found with enough memory
for the worker and Temporal, and it never sleeps. Alternatives rejected: Fly.io (no free tier), sleeping free
PaaS tiers, 1 GB micro instances, and a managed free split (four accounts, three code changes). Signup asks
for a card for verification. **Revisit:** a design partner or revenue (then R12), or the free tier is cut again.

### D21 — Cloudflare quick tunnels from the laptop as the fallback
Needs no account or card and works while Oracle has no capacity. Ephemeral, and reported not to support SSE.
**Revisit:** Plan A is live, after which this stays as a backup.

### D22 — Temporal stays on the application's Postgres in the free deployment
A separate store would need a second database on the same disk, with no real isolation gained. The Phase 12
chaos result therefore describes this deployment accurately. **Revisit:** R2 at the paid target, where the
chaos test must be re-run.

### D23 — Garage on the box for object storage, R2 as the fallback
S3 API, arm64 image, documented presigned URLs, and no billing exposure. It needs a public hostname and
path-style addressing. Unproven with this code, so it must pass the Phase 12.1 operation check first.
**Revisit:** that check fails, or off-box durability becomes worth a second account.

### D24 — Models baked into the image
The first demo case must not wait on a model download, and a container must not depend on an upstream release
host at runtime. The cost is a larger image. **Revisit:** image size blocks the free registry or the build runner.

### D25 — A daily live smoke as monitor and evidence
One run of the real path proves more than a port check, leaves a trace, and adds activity on the VM. Costs one
Gemini embedding per run, more if the document takes the VLM path. **Revisit:** free-tier limits are hit.

## Part 3: Phase 13 (2026-11-03)

### D26 — Local ONNX embeddings and reranker via `fastembed`, not Gemini or `sentence-transformers`
The real OFAC list is about 40,000 names (19,484 SDN rows plus 20,204 aliases, downloaded 2026-11-03). Embedding
them through Gemini's free tier hits rate limits on every ingest and every evaluation run, and makes runs
unrepeatable. `sentence-transformers` needs torch, which Application Control blocks on the dev host.
`fastembed` runs ONNX models on the `onnxruntime` already in the lockfile; a probe on this host loaded
`paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions) and `ms-marco-MiniLM-L-6-v2` and ran both. Gemini stays
available behind `EmbeddingClient`. **Revisit:** the harness shows a hosted model beats the local one by more than
its cost, or R6 (Bedrock) is built.

**Update, 2026-11-03: D26 was overturned by the harness it made possible.** Its example model,
`paraphrase-multilingual-MiniLM-L12-v2`, was the worst of four measured (D30). `fastembed` stays: it is how
the harness compares models, and the reranker seam (D32) runs on it.

### D27 — OFAC SDN only for Phase 13; OpenSanctions waits for Phase 15
OFAC data is US-government public domain. OpenSanctions is free only for non-commercial use, and its PEP data
belongs with the PEP screening work in Phase 15. **Revisit:** Phase 15 starts, or a licence is bought.

### D28 — Generated and pinned datasets, not MIDV in CI
Synthetic ID documents with known ground truth, name pairs built from OFAC aliases with hard near-miss
negatives, and a small public face-pair set fetched by script and pinned by hash. MIDV-500/2020 is several GB
with research-only terms. Every metric names the dataset and its hash, so no number is read as more than it is.
**Revisit:** a design partner supplies labelled real data, or the synthetic F1 is suspected of flattering the
extractor.

### D29 — Phase 13 ships as 13a, 13b and 13c
13a: harness, real OFAC list, reranker, threshold chosen by the harness. 13b: per-tenant policy and
`policy_version` in the audit chain. 13c: frontend CI and the first Playwright flow. Each has its own branch,
at least 4 commits, a changelog entry and a tag. 13a goes first because 13b's policy is tuned against it.
**Revisit:** never; this is sequencing.

### D30 — Character n-gram vectors for screening, chosen by the harness over three embedding models
Measured on the pinned OFAC snapshot and the 3,500-query set (`services/eval/screening.py`), at the 95% recall
target: character 2–3-gram feature hashing (1,024 dimensions, no model) **13.45% FPR** at threshold 0.73;
`BAAI/bge-small-en-v1.5` 23.05%; `all-MiniLM-L6-v2` 38.6%; `paraphrase-multilingual-MiniLM-L12-v2` never reaches
95% recall at all (89.2% at best, with 99.8% FPR). A sentence model is trained on meaning, and a name has almost
none; n-grams track spelling, which is what transliteration, typos and reordering preserve. Also deterministic
across platforms (pure Python hashing), so CI and a laptop compute the same recall. **Revisit:** a name-trained
model (or Bedrock, R6) beats it on the harness by more than two standard errors.

### D31 — The phonetic leg is off at the shipped threshold (amends D10)
D10 ran Double Metaphone beside vector search on the reasoning that it catches what vectors miss. With n-gram
vectors, on the real list, it does not: the best point with it on is 95.40% recall at 14.80% FPR, against 95.67%
at 13.45% with it off. Whole-name metaphone codes collide across the 16,162 listed names more often than they
rescue a miss the n-grams did not already catch. The code keeps the leg (`PHONETIC_ENABLED`), and every
comparison reports the best point both ways (`best_by_phonetic` in `services/eval/results/screening.json`). D10's principle — run independent methods and
measure — stands; its specific claim did not survive measurement. **Revisit:** a list or query mix where the
phonetic leg's recall gain shows up in the harness.

### D32 — The reranker is built and measured, and does not ship
A cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`) over the n-gram candidates lowered FPR from 13.45% to 12.70% at
the same recall target. Over 2,000 negatives one standard error of a 13% rate is about 0.75 points, so the lift is
one standard error, at 2.2× the latency and a second model. The rule now in `select_shipped()`: a reranker ships
only if it beats the plain configuration by more than two standard errors. Rule 2 of that function was added after
the first comparison was read, and says so in its docstring. **Revisit:** a name-trained cross-encoder, or a
larger negative set that can resolve a smaller lift.

### D33 — Exact nearest-neighbour search, not HNSW
An approximate index can miss a true neighbour, differently on each build, so a compliance decision and every
measured recall would depend on index randomness. At ~16,000 vectors a full scan costs milliseconds.
`VECTOR_SEARCH_EXACT=true`. **Revisit:** a list large enough that the scan shows in screening latency.

### D34 — Screen individuals only
7,529 of the 19,484 SDN rows are individuals; with their aliases, 16,162 indexed names. Entity, vessel and
aircraft names can only produce false positives against a person and would triple ingest and evaluation time.
Business screening would use them. **Revisit:** KYB.

### D35 — Read the MRZ tolerantly, but never guess inside the document number
The harness found real OCR defeating every rendered data page, for three reasons, each now fixed in production
code with a test: EasyOCR splits a line at long `<` runs (rows are merged, `ocr._rows`); end padding stranded the
final check digit (`mrz.fit_filler` widens the filler run instead); and letter/digit confusions in fixed-type
positions (`mrz.parse_td3_tolerant`, resolved per field by check digit). It also found why the obvious next step is
wrong: the composite check digit weights the document number exactly as the number's own check does, so any
compensating substitution there passes both. Searching that field accepted 8 wrong numbers out of 14 reads; it is
now never searched, and a failed check goes to the VLM. Line 1 has no check digit at all, so its shape and issuing
state are validated, and the given names end at the next `<<`. **Revisit:** a VIZ cross-check of the document
number exists to replace the VLM fallback.

### D36 — The audit verifier canonicalises the payload it reads (bug fix; formula unchanged)
`scripts/verify_audit_chain.py` hashed the JSONB text Postgres returns, but rows are hashed over sorted-key JSON,
and JSONB orders keys shortest first. Every finalised case therefore read as tampered — present since Phase 11,
found by verifying the Phase 13 end-to-end case. The writer, the formula and every stored hash are unchanged; only
the verifier's reading was wrong. **Revisit:** never; the regression test pins it.

### D37 — Per-tenant policy: versioned, insert-only, pinned to the case at creation
`risk_policies` rows are never edited; a change is the next version. `cases.policy_version` is set inside the
`INSERT` that creates the case and never re-read, so a policy change cannot alter how an in-flight case is judged
(STUDY.md, Phase 13). The risk activity reads the version itself, so the workflow code and its event history did
not change. The schema has no field that can reject a case (D11) or switch off a hard stop. Version 0 is the
pre-policy behaviour, exactly, including not checking expiry. **Revisit:** a customer needs logic the schema
cannot express (R11, OPA).

### D38 — The end-to-end test parks its case through the real workflow
`scripts/seed_e2e.py` creates a case with no documents and starts the real workflow, which parks it for review.
Writing `status = 'needs_review'` directly would have let the test pass with no workflow listening for the
decision. The test passes only when `finalize_case_activity` has run. **Revisit:** a full-upload flow, once the
VLM path can run in CI.

### D39 — Recall target 95%, fixed before any result was read
The harness chooses the lowest false-positive rate among configurations meeting it. 95% is a starting bar on our
own noise model, not a regulatory number. **Revisit:** a design partner's risk appetite, or real labelled traffic.

## Part 4: Proposed roadmap

Proposed in [improvements.md](../improvements.md) §10, where these are numbered D-1…D-20. None are implemented
except where the first column says "keep".

| ID | Decision | Chosen | Over | Revisit when |
|---|---|---|---|---|
| R1 | Orchestration | Temporal (keep) | Celery, Step Functions, Kafka sagas | Never for this product |
| R2 | Workflow store | Temporal Cloud | Self-host, shared RDS | Cost per action exceeds self-host + on-call |
| R3 | Event backbone | None (outbox + SQS + Redis) | Kafka, RabbitMQ | Third consumer, >5 s relay lag, replay as a feature |
| R4 | Shared state | Redis (ElastiCache) | Postgres-only, Memcached | — |
| R5 | VLM | Bedrock, model chosen by the harness | Gemini free, Vertex, Azure, self-host | Self-host when GPU cost < API cost at volume |
| R6 | Embeddings | Bedrock | Gemini, OSS | Same as R5 |
| R7 | Vector DB | Qdrant (keep) | pgvector, OpenSearch | pgvector if multimodal is dropped |
| R8 | Reranker | OSS cross-encoder on CPU | API rerankers | p95 > 500 ms |
| R9 | Sanctions feed | OpenSanctions (licensed) + OFAC direct | Direct feeds only, vendors | A customer mandates a vendor |
| R10 | Identity | Cognito OIDC; WorkOS if enterprise SSO drags | Keycloak, Auth0 | Enterprise onboarding is the bottleneck |
| R11 | Risk policy | Declarative per-tenant YAML + evaluator | Constants, OPA | Customer logic exceeds the schema |
| R12 | Compute | ECS Fargate | EKS, PaaS | GPU fleet |
| R13 | IaC | Terraform | Pulumi, CDK | — |
| R14 | Observability | OTel → Grafana Cloud → Datadog later | CloudWatch only | Revenue |
| R15 | Malware scan | GuardDuty S3 Malware Protection | ClamAV Lambda | Cost at volume |
| R16 | Audit anchoring | Chain head → S3 Object Lock nightly | Transparency log, blockchain | A regulator asks for external anchoring |
| R17 | RAG framework | None; hand-rolled over existing protocols | LangChain, LlamaIndex | More than 3 distinct pipelines |
| R18 | Multimodal retrieval | Single-vector v1, late-interaction v2 | ColPali from day one | Field-level grounding needed |
| R19 | Liveness | Rekognition Liveness, only if the wedge needs it | OSS anti-spoofing | Cost or residency |
| R20 | Upload completion | S3 → SQS → signal | Polling with retry (today), EventBridge + Lambda | — |

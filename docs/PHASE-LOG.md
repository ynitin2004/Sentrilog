# Phase Log — roadmap phases from Phase 13

[PLAN.md](../PLAN.md) is the record of Phases 1–12.1 and stays as it was written. Phases from the
roadmap in [improvements.md](../improvements.md) §6 are recorded here, in the same form: a status
row per phase and an append-only changelog with design decisions, measured results, real bugs, test
evidence and explicit known gaps.

Roadmap Phase 13 replaces PLAN.md's original "Phase 13 — AWS infra + deploy", which is now roadmap
Phase 14. PLAN.md's status row for it still reads "Not started", which is true of the AWS work.

## Status

| Phase | Status | Tag | Commit | Date |
|---|---|---|---|---|
| 13a. Evaluation harness, real sanctions list | **Done** | `v0.13.0` | `2affc31` | 2026-11-03 |
| 13b. Per-tenant risk policy | **Done** | `v0.13.1` | `380496a` | 2026-11-03 |
| 13c. Frontend CI + first E2E flow | **Done** | `v0.13.2` | `c31e8de` | 2026-11-03 |

## Changelog

### Phase 13a — Evaluation harness, real sanctions list (`v0.13.0`, 2026-11-03)

Roadmap Phase 13 in [improvements.md](../improvements.md) §6, which replaced this file's original "AWS infra + deploy" Phase 13 (AWS is now roadmap Phase 14). Split into 13a/13b/13c (D29). Every threshold in the pipeline had been a guess; this phase built the harness that measures them, and then let it overrule the guesses — including several of this phase's own.

**What was built:**

- `services/eval/`: four suites over pinned, hashed datasets, each running the production code path rather than a copy of it; an `eval_runs` table (insert-only) recording dataset hash, full result and git SHA; committed results in `services/eval/results/`; a Markdown report; and an `eval` CI job with two blocking gates (screening recall ≥ 95%, face TAR@FAR=1e-3 ≥ 93%). Everything else is reported, not gated, so the gates keep meaning something.
- The real OFAC SDN list, individuals only (D34): 7,529 parties and their aliases, 16,162 indexed names. Each ingest is an immutable snapshot (`list_snapshots`) in its own Qdrant collection behind the `sanctions_current` alias; every hit and every screen's audit payload records `list_version`, including screens with no hits. Rolling back is repointing the alias, and nothing is deleted.
- A `Reranker` protocol with a cross-encoder implementation; `fastembed` for local ONNX models; a character-n-gram embedding that needs no model at all.

**Measured, and what each measurement overturned:**

| Suite | Dataset | Result |
|---|---|---|
| Screening | `screening-queries-v1` (1,500 altered listed names, 1,000 synthetic and 1,000 hard negatives; sha256 `5ef15c4e…`) against the 2026-11-03 OFAC snapshot | **95.67% recall, 13.45% FPR** at threshold **0.73** (2.20% on synthetic names, 24.70% on names sharing a listed token), 28 ms per screen |
| Face match | LFW DevTest, 500 genuine pairs, 1,197,943 cross-identity impostor pairs | **TAR 95.0% at FAR 1e-3** (threshold 0.203), 95.4% at 1e-2; 0 of 1,549 images without a detected face |
| Extraction, MRZ parse | 300 synthetic TD3 identities, 2% OCR-style corruption | **F1 0.981**; 284 accepted, 16 to fallback, **27 wrong but accepted**, all in the name |
| Extraction, MRZ OCR | 100 rendered data pages through real EasyOCR | **F1 0.950**; 24 accepted, 76 to fallback, **6 wrong but accepted** (document number F1 0.75) |
| Risk, built-in policy | Composed from the three above at stated priors | Auto-clear precision **99.95%**, impostor leak **0%**, listed-person leak 0.85%, **review burden 84%** |

1. **The model chosen for D26 was the worst one measured.** `paraphrase-multilingual-MiniLM-L12-v2` never reached 95% recall (89.2% at best, at 99.8% FPR). Sentence models embed meaning and names have none; character n-grams, with no model, beat all three (D30). The old threshold, 0.55, belonged to Gemini embeddings: with any of these models it would have flagged nearly everyone.
2. **The phonetic leg costs more than it catches** with n-gram vectors on the real list: best point with it on, 95.40% / 14.80%; off, 95.67% / 13.45% (D31, amends D10).
3. **The reranker's lift is one standard error.** 13.45% → 12.70% FPR, where one standard error over 2,000 negatives is ~0.75 points, at 2.2× latency. Not shipped; `select_shipped()` now requires a two-standard-error lift (D32). That rule was added after the first comparison was read, and the docstring says so.
4. **Approximate search was replaced by exact search** before the threshold was chosen (D33). The n-gram numbers were identical under both, but a compliance threshold should not depend on index randomness.

The shipped configuration (`services/screening/config.py`) is the one the harness chose, and `tests/eval/test_shipped_config.py` fails if the two drift. The FPR it implies, on our noise model: 13.45%.

**Real bugs the harness found in production code** (all in the MRZ path):

1. **OCR never produced an MRZ from a rendered page — 0 of 30.** EasyOCR splits a line at long runs of `<` filler, and `_assemble_mrz_lines` only accepted single boxes. Boxes are now merged into rows (`ocr._rows`). Real passports with short names split the same way.
2. **End padding stranded the final check digit.** A short OCR line was padded at the end, leaving the composite digit mid-line. `mrz.fit_filler` widens the filler run instead.
3. **Letter/digit confusions in fixed-type positions** (a check digit `0` read `O`, `7` read `Z`) failed otherwise-correct reads. `parse_td3_tolerant` resolves each check-digit field separately, keeping a field that passes as read and taking the unique fewest-substitution reading otherwise.
4. **My first version of (3) was dangerous, and the harness said so:** it also searched the document number, and accepted 8 wrong numbers out of 14 reads. The composite check digit weights the document number exactly as its own check does, so it gives no independent protection (STUDY.md). The document number is now never searched (D35).
5. **A printed header could be parsed as MRZ line 1**, which has no check digit. Line 1 must now have the TD3 shape and a real ICAO issuing state (`country_codes.py`; the specimen state `UTO` is deliberately absent), and the given names end at the next `<<`, so filler noise is no longer part of a name.

**Bugs in my own work along the way:** a harness run that finished after 20 minutes and then died printing `≥` to a cp1252 console, losing its results (results are now written before anything prints); a backgrounded `uv run uvicorn` silently blocked by Application Control, so the first Playwright run talked to nothing; a Docker Desktop crash under memory pressure (0.4 GB free) that took Qdrant down mid-comparison.

**Known gaps (explicit):**

- **The noise model is ours.** Recall is robustness to five transforms we wrote, not a production miss rate. The hard negatives (24.7% FPR) are where a real deployment's review load would come from.
- **The MRZ name has no check digit** and remains the weak field (parse path: 27 wrong-but-accepted). The OCR path's document number is protected only by one mod-10 digit, so ~10% of its misreads pass; 6 of 24 accepted OCR reads were wrong. These are accepted at confidence 0.99. The fix is a cross-check against the printed zone or the VLM, not more MRZ heuristics.
- **The OCR numbers are on a Consolas render**, not OCR-B; real documents will read differently.
- **LFW is press photos**, not a document portrait against a selfie. TAR@FAR measures identity separation, not live-capture error.
- **The risk suite is composed** from the others at stated priors, MRZ-path cases only. VLM-path confidences are not measured in CI (no key).
- **Exact search has no guard** (D33 ⚠).
- **The eval job's first CI run pays ~30 min of face embedding**; later runs use the cached embeddings.

### Phase 13b — Per-tenant risk policy (`v0.13.1`, 2026-11-03)

- **`risk_policies`**: per-tenant, versioned, insert-only, under RLS. `PUT /policy` creates the next version; `GET /policy` and `GET /policy/versions` read them. A concurrent writer gets 409, not a silent overwrite.
- **A case is pinned to its version at creation**, inside the `INSERT` (`cases.policy_version`), and never re-reads it. The risk activity loads that version itself and writes it into the hashed audit payload — so no workflow code or event history changed, and in-flight cases are unaffected (the Temporal answer in STUDY.md, Phase 13).
- **What a policy can say:** auto-clear weights and threshold, a face-match floor, expired and soon-expiring documents, nationalities that always go to review. **What it cannot:** reject anything (D11), or switch off the sanctions and no-face hard stops. Neither has a field to express it, and `extra="forbid"` turns an attempt into a 422.
- **Version 0 is exactly the old behaviour**, including not checking expiry, so cases decided before policies existed are explained by it.
- **Measured with the harness** (`python -m services.eval risk --policy services/eval/policies/example-balanced.json`): an example policy — auto-clear at 0.60, face floor 0.35 (the highest impostor score measured was 0.199), 30-day expiry window — cuts review from 84% to **23%** at the same 99.95% precision and 0% impostor leak. The listed-person leak rises to **4.1%**, which is the screening miss rate: once the face gate opens, sanctions recall is the only defence. That is the strongest argument for the 95% recall target.
- **Tests:** 7 endpoint tests (versions only grow, invalid policies write nothing, tenant isolation, pinning across two policy changes, insert-only role), 8 evaluator tests (no reject, hard stops hold under the most permissive policy, the built-in policy is the old one), and an activity test where a case pinned to a strict v1 is decided by it although a lenient v2 exists.
- **Known gaps:** no policy is adopted for any tenant; the console has no policy screen (the endpoints and generated types exist); policy changes are not themselves written to `audit_log`, whose rows require a case — the insert-only `risk_policies` table is their history.

### Phase 13c — Frontend CI and the first end-to-end flow (`v0.13.2`, 2026-11-03)

- **`frontend` CI job:** typecheck, lint, format, unit tests and a production build. The console had no CI at all (P-17).
- **`e2e` CI job and `frontend/e2e/review-decision.spec.ts`:** a real browser connects as a reviewer, opens the queue, claims a case and approves it, against the real API, worker, Temporal and Postgres. `scripts/seed_e2e.py` has the **real workflow** park the case (a case with no documents parks itself) rather than writing `needs_review` into the table (D38), so the test passes only when the decision has gone through the Temporal signal and `finalize_case_activity` has set `approved/approved`. Passed locally in 2.6 s; the audit chain for that tenant verifies.
- **A real bug, found by this flow:** `verify_audit_chain.py` reported the finished case as tampered. **It had reported every finalised case that way since Phase 11.** It hashed the JSONB text Postgres returns, which orders keys shortest first, while rows are hashed with sorted keys — so `{"status", "decision"}` never matched. Phase 11's tests used payloads whose two orders agree. The formula and every stored hash were always correct; only the verifier's reading was wrong. Fixed by re-canonicalising what it reads; the regression test stores the real finalize and webhook payloads and asserts the premise (that Postgres hands back a different order) as well as the result. All 24 tenant chains in the dev database now verify (D36).
- **Known gaps:** both jobs have run locally only; one flow, on the decision path — the upload path needs the VLM, which CI cannot call; `format:check` fails on a Windows checkout because `core.autocrlf` rewrites line endings (CI's Linux checkout is unaffected).

**Test evidence for Phase 13 as a whole:** 243 passed with a Gemini key; 242 passed, 1 skipped (the real-Gemini screening test) with `GEMINI_API_KEY=""`, which is CI's configuration. ruff, black, mypy (112 files) and every pre-commit hook clean. Frontend: typecheck, lint, 38 unit tests, Playwright 1/1.

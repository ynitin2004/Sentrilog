# Deployment — $0 strategy

Constraints: **$0/month, no domain, hiring demo and portfolio traffic** (a few cases a day), always on
with no cold start, synthetic documents only.

**Status: nothing here is built or tested yet.** §4 lists the code that must exist first. Free-tier
facts were checked on 2026-11-02, mostly from third-party sources that sometimes disagree. Lines
marked *verify* must be confirmed in the vendor's console before you rely on them.

## 1. The verdict

**Plan A, the always-on URL.** One Oracle Cloud Always Free Ampere A1 VM runs the same container
topology as development, behind Caddy with Let's Encrypt, on free DuckDNS hostnames. Postgres,
Temporal, Qdrant and the object store all live on the box.

**Plan B, the demo-day bridge.** The stack runs on the laptop in containers and is exposed through
Cloudflare quick tunnels for the length of an interview. It is also the fallback while Oracle has no
A1 capacity, which can last weeks.

**The paid target is unchanged:** ECS Fargate and managed services in
[improvements.md](../improvements.md) §4.4. This plan is the bridge to a clickable URL. It is not a
production topology.

### One requirement that is not free of friction: a card at signup

Oracle asks for a credit or debit card during signup to verify identity, even for an Always Free
account (*verify* at signup). Staying on Always Free resources should not produce a charge, but "no
card anywhere" is not achievable with Plan A. Plan B needs no card at all. If no card is acceptable,
Plan B is the whole deployment and there is no always-on URL.

## 2. Why this and not something else

| Option | Verdict | Reason |
|---|---|---|
| **Oracle A1 + containers** | **Chosen** | The only free host found with enough memory for the worker (torch + InsightFace), Temporal and the data stores together, and it never sleeps |
| Fly.io | Rejected | No free tier for new accounts in 2026 |
| Render and similar free PaaS | Rejected | Free services sleep (*verify*), and Temporal alone takes ~50 s to answer RPCs, so a sleeping host fails every demo. No small free instance fits the worker |
| GCP / AWS free micro instances | Rejected | About 1 GB of RAM (*verify*); the worker alone needs more |
| Managed free split: Neon or Supabase, Qdrant Cloud, Temporal Cloud credits, R2 | Rejected for now | Four accounts and code changes: Temporal TLS, a Qdrant `api_key`, `statement_cache_size=0` plus a separate direct DSN for `LISTEN`. Temporal's credits also run out. It is the evacuation path if the VM is lost |
| AWS Fargate ([improvements.md](../improvements.md) P-7) | Deferred | Correct for production, not free. Revisit with a design partner or revenue |

**A useful side effect of staying on one Postgres:** Temporal shares it, exactly as in development, so
the Phase 12 chaos result actually describes this deployment. `LISTEN/NOTIFY` also keeps working
because there is no pooler. The split-store failure stays untested, and it matters only for the paid
target.

## 3. Topology

```text
Browser / API client
   │ HTTPS (Let's Encrypt)
   ▼
Caddy on the VM (:443)
   ├─ sentrilog-console.duckdns.org ─► frontend (existing nginx image)
   ├─ sentrilog-api.duckdns.org     ─► api (uvicorn, one process)
   └─ sentrilog-s3.duckdns.org      ─► garage (S3 API, presigned PUT/GET)

Private Docker network, nothing else published:
   api ────► postgres:16 ◄──── temporal (temporal + temporal_visibility databases)
   api ────► temporal ◄─────── worker (all tiers, one process)
   worker ─► postgres, qdrant, garage, Gemini free tier
   Temporal UI: bound to localhost, reached through an SSH tunnel.

Cron on the VM: backup · verify_audit_chain · live_smoke (daily)
Outside the VM: UptimeRobot → /health · GitHub Actions arm64 → GHCR → SSH deploy
```

**Why the object store needs its own public hostname.** Presigned URLs are signed for the host in
`S3_ENDPOINT_URL`, and the client uploads to that host directly. So it must be publicly reachable, and
the same public URL must be used to sign. The worker uses the same setting and reaches it through Caddy.

**Why path-style addressing.** A virtual-hosted URL would be
`sentrilog-documents.sentrilog-s3.duckdns.org`, which needs a wildcard certificate. Path-style avoids
that, but the code cannot select it today (§4).

### Memory budget (estimates; measure on the box)

| Process | Estimate |
|---|---|
| worker (torch, InsightFace, EasyOCR loaded) | 2.5–3 GB |
| temporal | ~600 MB |
| api | ~300 MB |
| postgres | ~256 MB |
| qdrant | ~200 MB |
| garage | ~100 MB |
| temporal-ui, caddy, frontend | ~110 MB |
| **Total** | **~4.5 GB of 12 GB** |

All of it shares 2 OCPUs, and face match is the CPU hot spot. That is fine at demo volume and is not
a capacity claim. Phase 12's load numbers came from a 28-core laptop and do not transfer.

## 4. Code that must exist first (not built)

Build in this order. The Quiz Rule applies to the set before work starts.

| # | Change | Where | Why |
|---|---|---|---|
| 1 | Backend `Dockerfile`: `uv sync --frozen --no-dev`, non-root, selects `api` / `worker` / `ingest`, bakes `buffalo_l` and EasyOCR `en` into the image, adds any system libraries `opencv-python` needs | `Dockerfile`, `docker/entrypoint.sh` | No Python process is containerised today (`docker-compose.yml:107-110`). Models otherwise download on the first case |
| 2 | CPU-only torch index | `pyproject.toml` | On x86 Linux the lockfile pulls the CUDA stack (`uv.lock:2596-2604`), adding gigabytes. Matters for Plan B; check whether arm64 is affected when building |
| 3 | `GET /health` (liveness) and `GET /ready` (Postgres and Temporal) | `services/intake/main.py` | The intake API has no probe target |
| 4 | `CORS_ALLOW_ORIGINS` setting, default `*` | `services/intake/config.py`, `main.py:90-95` | Console and API are different origins; a public URL should not stay on `*` |
| 5 | `S3_ADDRESSING_STYLE` setting | both `storage.py` files, `scripts/wait_for_dependencies.py` | §3 |
| 6 | `SENTRILOG_ADMIN_DSN` in place of the hardcoded superuser DSN | `scripts/verify_audit_chain.py:30` | So the nightly chain check can run on the VM |
| 7 | `deploy/compose.prod.yml`, `deploy/Caddyfile`, `deploy/env.prod.example`, `deploy/secrets.prod.example.json` | `deploy/` | Secrets use the existing `JsonFileSecretsProvider` (`SENTRILOG_SECRETS_PROVIDER=file`) |
| 8 | `scripts/backup.sh` and a timed restore drill | `scripts/` | The VM holds the only copy of the data |
| 9 | `scripts/live_smoke.py`: API key → `POST /cases` → presigned PUTs → poll → decide if parked → webhook row → audit chain → trace | `scripts/` | Proves the deployment and writes [CASE-TRACES.md](CASE-TRACES.md) |
| 10 | `.github/workflows/deploy.yml`: build on `ubuntu-24.04-arm`, push to GHCR tagged by SHA, deploy over SSH after CI passes | `.github/workflows/` | Free for public repositories |

**Garage must pass the Phase 12.1 check before it is trusted.** Run every S3 operation the code
performs against it: presigned PUT, presigned GET, `head_object`, `get_object`, and a `404` code on a
missing key, since `object_exists()` keys off exactly that code. Its compatibility docs list presigned
URLs as implemented, but no one has tested it with this code. If it fails, use Cloudflare R2 (§7).

## 5. Runbook — Plan A

1. **Account.** Choose the home region deliberately, because it is fixed at signup. Nearby regions
   give lower latency to you; community reports suggest Frankfurt or Singapore have more spare
   capacity (*verify*).
2. **Instance.** `VM.Standard.A1.Flex`, 2 OCPU / 12 GB (*verify the current limit in the console; do
   not exceed it, since reports say over-allocation can get instances deleted*), Ubuntu 24.04. On
   "Out of host capacity", retry other availability domains with an OCI CLI loop and use Plan B
   meanwhile.
3. **Network.** Open 22, 80 and 443 in the VCN security list, and also in the host firewall: Oracle's
   Ubuntu images commonly block inbound traffic with iptables rules of their own. Persist the change.
4. **Names.** Create the three DuckDNS names with a cron updater. Caddy obtains certificates once they
   resolve.
5. **Secrets.** Copy `deploy/env.prod` and `secrets.prod.json` (mode 0600). Generate real values; the
   dev values are public in this repository.
6. **Start.** `docker compose -f deploy/compose.prod.yml up -d --wait`. Schema files run from
   `infra/docker/postgres-init/` on first init.
7. **Harden the app role.** `002_multi_tenancy.sql` creates `sentrilog_app` with a hardcoded dev
   password, so run `ALTER ROLE sentrilog_app PASSWORD '…'` right after first init and update the
   secrets file.
8. **Object store.** Set up the Garage layout, bucket and key. Set `S3_REGION` to Garage's configured
   region, because SigV4 signs the region.
9. **Seed.** Run the `ingest` one-shot (`--source ofac`: downloads the list, ~30 s, no API key), then
   `scripts/seed_dev_tenant.py` and `scripts/seed_dev_reviewer.py`. Each prints its credential once.
10. **Verify.** `/health`, `/ready`, `scripts/wait_for_dependencies.py`, then `scripts/live_smoke.py`.
    Done means a trace in `docs/evidence/case-traces/` with a verified chain.
11. **Schedule.** Nightly backup and `verify_audit_chain.py`; daily `live_smoke.py`; a 5-minute
    UptimeRobot check on `/health`.
12. **Idle check.** After a week, confirm the instance is not near the reclaim thresholds. Reports put
    the idle rule at CPU, network and memory all under roughly 15–20 % for 7 days (*verify*). The
    estimated memory use should keep it above that, and the point is to measure it.

**Rollback.** Images are tagged by git SHA, so redeploy the previous tag. Schema changes are not
reversed this way, so restore from backup (§8) if one was applied.

## 6. Runbook — Plan B

Run the worker in a container, not on the host: Application Control blocks torch here, so OCR cannot
run on the host. That makes item 1 of §4 a prerequisite for Plan B too.

1. Start the dependencies and the backend containers. Development's `s3mock` is fine here because it
   ignores signatures, and its data is throwaway.
2. Run `cloudflared tunnel --url …` three times, for the API, the console and S3. Use the
   `cloudflare/cloudflared` image if Application Control blocks the binary.
3. Set `S3_ENDPOINT_URL` to the S3 tunnel URL and restart the API and worker.
4. Open the console tunnel and enter the API tunnel URL on `/connect`.

**What not to claim about Plan B:**

- The hostnames change on every run, and laptop sleep ends the demo.
- Cloudflare documents quick tunnels as not supporting Server-Sent Events (*verify*). If so, live queue
  updates will not work, so refresh the page, and show real-time behaviour only on localhost or Plan A.
- Quick tunnels are for testing and carry no uptime guarantee.

## 7. Choices inside the plan

| Choice | Alternative | Why |
|---|---|---|
| **Garage** on the box | Cloudflare R2 (10 GB free) | One box and no billing exposure. R2 also wants a payment method on file (*verify*). **R2 is the fallback** if Garage fails §4's check, and it would also give durability off the VM |
| **Self-hosted Temporal and Qdrant** | Temporal Cloud credits, Qdrant Cloud free 1 GB | No code changes, and credits expire. Both clouds are the evacuation path |
| **DuckDNS + Caddy** | A ~$10/year domain with Cloudflare | Meets the $0 constraint. A domain later adds a free WAF and edge rate limiting; it is the first thing worth paying for |
| **Baked models** | Download on first use | The first demo case must not wait on a model download |
| **Gemini free tier** | Bedrock | Meets $0. **Synthetic documents only**, because free-tier data terms differ. This is P-1 in improvements.md and it stays open |
| **Console logs** | Grafana Cloud free | No OTLP exporter exists yet. Adding one is the first observability follow-up, and the free tier is reported at 10k series and 50 GB of logs and traces with 14-day retention (*verify*) |

## 8. Backups

The VM is one failure domain, so a backup that stays on its disk does not survive losing the instance.

- **Nightly on the VM:** `pg_dump`, a Qdrant snapshot and the Garage data directory, with 7-day rotation.
- **Off the VM:** copy the dump to the laptop weekly, or use Oracle's free volume backups (*verify the
  allowance*).
- **Drill:** restore into a scratch database once, compare row counts, run `verify_audit_chain.py`
  against the restored copy, and record the time here.

The Qdrant data can always be rebuilt with `services.screening.ingest`; the audit chain cannot.

## 9. Cost

| Item | Monthly | What would make it non-zero |
|---|---|---|
| Oracle A1 VM + 200 GB block storage | $0 | Upgrading the account or exceeding Always Free limits (*verify current limits*) |
| DuckDNS, Let's Encrypt | $0 | — |
| GitHub Actions arm64 runners, GHCR | $0 | The repository going private |
| Gemini free tier | $0 | Moving to a paid key. Free-tier rate limits apply with no SLA |
| UptimeRobot free | $0 | — |
| Cloudflare quick tunnels (Plan B) | $0 | — |
| **Total** | **$0** | Optional first spend: a ~$10/year domain |

## 10. Known gaps of this deployment

- **One VM:** no high availability. A reboot is an outage, and losing the instance loses anything not
  backed up off it.
- **The instance can be reclaimed or the free tier can change.** Oracle has already cut it once, per
  2026 reports.
- **Rate limiting is app-level only and in-process.** That is correct with one uvicorn process and
  gives no edge protection.
- **CPU work is not isolated between tiers:** one worker process, measured at 2.51× p95 degradation.
  Irrelevant at demo volume, but do not claim otherwise.
- **Dev-grade security from [improvements.md](../improvements.md) P-8 remains,** apart from CORS and
  the role password: reviewer tokens in `localStorage`, malware scan stubbed, no encryption at rest
  beyond what the platform gives.
- **Presigned PUTs do not sign a content length.** `MAX_UPLOAD_BYTES` checks the size the client
  declares, and the store does not enforce it. Keep API keys private; do not publish a demo key.
- **Not verifiable until built:** the arm64 image, Garage compatibility, Oracle capacity, certificate
  issuance, real memory use.

## Sources (checked 2026-11-02)

- [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm), [reported 2026 A1 reduction](https://infoq.com/news/2026/07/oracle-cloud-free-tier-limits/), [reclamation rules](https://oracommit.blogspot.com/2026/02/understanding-oci-always-free-compute.html), [capacity retry approach](https://dev.to/alexpua/how-i-stopped-fighting-oracles-out-of-host-capacity-and-let-a-script-catch-a-free-arm-server-for-41a6)
- [GitHub arm64 runners for public repositories](https://github.blog/changelog/2025-08-07-arm64-hosted-runners-for-public-repositories-are-now-generally-available/)
- [R2 presigned URLs](https://developers.cloudflare.com/r2/data-access/s3-api/presigned-urls/), [R2 setup and payment method](https://tech-insider.org/how-to-set-up-cloudflare-r2-2026/)
- [Garage S3 compatibility](https://git.adnab.me/Deuxfleurs/garage.git/tree/doc/book/reference-manual/s3-compatibility.md?h=v0.7.1), [arm64 image](https://hub.docker.com/r/dxflrs/arm64_garage)
- [Qdrant pricing comparison](https://qdrant.tech/pricing/comparison/), [Temporal pricing](https://temporal.io/pricing), [Grafana Cloud free plan](https://costbench.com/software/observability/grafana-cloud/free-plan/), [Fly.io pricing](https://temps.sh/blog/fly-io-pricing-2026)

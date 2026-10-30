"""Block until Postgres, Qdrant, Temporal and S3 are all actually answering.

Why this exists rather than a sleep or a Compose healthcheck:

  * On CI, GitHub Actions only waits for service containers that declare a `--health-cmd`.
    Temporal's auto-setup image has no usable one (its own healthcheck needs `tctl` pointed at
    the container's own hostname, which can't be expressed in a service `options` string
    without the runner's shell expanding it first), so without this the test step can start
    before the workflow engine is serving and fail with a connection error that looks like a
    code bug.
  * A TCP probe isn't enough for two of the four. Temporal binds :7233 well before it will
    answer RPCs, and Qdrant answers HTTP before its storage is ready -- so each check here is
    the real operation the application performs, not a port knock.

Locally the same command turns `docker compose up -d` into something you can wait on:

    docker compose up -d && uv run python scripts/wait_for_dependencies.py
"""

import argparse
import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

# Same shim the other scripts in here use: run as `uv run python scripts/wait_for_dependencies.py`,
# Python puts scripts/ on sys.path rather than the repo root, so `services` isn't importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402
import boto3  # noqa: E402
from botocore.client import Config  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402
from temporalio.api.workflowservice.v1 import GetSystemInfoRequest  # noqa: E402
from temporalio.client import Client  # noqa: E402

from services.intake.config import settings as intake_settings  # noqa: E402
from services.screening.config import settings as screening_settings  # noqa: E402


async def _poll(
    name: str, check: Callable[[], Awaitable[None]], timeout_seconds: float, interval: float
) -> bool:
    """Retries `check` until it stops raising, printing only the last failure.

    Printing every attempt would bury the result; printing none would leave a timeout with no
    explanation of what the dependency was actually saying.
    """
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        attempts += 1
        try:
            await check()
        except Exception as exc:  # noqa: BLE001 - every dependency raises its own type here
            last_error = exc
            await asyncio.sleep(interval)
            continue
        print(f"  ok      {name} (after {attempts} attempt(s))")
        return True
    print(f"  TIMEOUT {name} after {timeout_seconds:.0f}s, {attempts} attempt(s)")
    print(f"          last error: {type(last_error).__name__}: {last_error}")
    return False


async def _check_postgres() -> None:
    # The app role, not the superuser: this also proves 002_multi_tenancy.sql has been applied,
    # since sentrilog_app doesn't exist until it runs.
    conn = await asyncpg.connect(dsn=intake_settings.database_url.get_secret_value())
    try:
        await conn.fetchval("SELECT 1 FROM tenants LIMIT 1")
    finally:
        await conn.close()


async def _check_qdrant() -> None:
    def _list() -> None:
        QdrantClient(url=screening_settings.qdrant_url).get_collections()

    await asyncio.to_thread(_list)


async def _check_temporal() -> None:
    client = await Client.connect(
        intake_settings.temporal_address, namespace=intake_settings.temporal_namespace
    )
    await client.workflow_service.get_system_info(GetSystemInfoRequest())


async def _check_s3() -> None:
    def _head() -> None:
        client = boto3.client(
            "s3",
            endpoint_url=intake_settings.s3_endpoint_url,
            aws_access_key_id=intake_settings.s3_access_key,
            aws_secret_access_key=intake_settings.s3_secret_key.get_secret_value(),
            region_name=intake_settings.s3_region,
            config=Config(signature_version="s3v4"),
        )
        # head_bucket, not list_buckets: the bucket existing is the precondition the upload
        # path actually needs, and a running server with no bucket is a configuration failure
        # worth catching here rather than inside a test.
        client.head_bucket(Bucket=intake_settings.s3_bucket)

    await asyncio.to_thread(_head)


_CHECKS: dict[str, Callable[[], Awaitable[None]]] = {
    "postgres": _check_postgres,
    "qdrant": _check_qdrant,
    "temporal": _check_temporal,
    "s3": _check_s3,
}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(_CHECKS),
        help="check just this dependency; repeatable. Default: all four.",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="per-dependency, seconds")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between attempts")
    args = parser.parse_args()

    names = args.only or sorted(_CHECKS)
    print(f"waiting for: {', '.join(names)} (up to {args.timeout:.0f}s each)")

    # Sequential, not gathered: these are mostly waiting on the same cold start, and a
    # sequential run produces output in a fixed order that's readable in a CI log.
    results = [await _poll(n, _CHECKS[n], args.timeout, args.interval) for n in names]
    if all(results):
        print("all dependencies ready")
        return 0
    print("one or more dependencies never became ready", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

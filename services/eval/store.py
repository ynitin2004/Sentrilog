"""Writes one eval_runs row per suite run (007_list_snapshots_and_eval_runs.sql). The row holds
the dataset's hash and the full result, so any number quoted later can be traced to the data
and code that produced it."""

import json
import os
import subprocess
from typing import Any

import asyncpg

from services.screening.config import settings


def git_sha() -> str | None:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def dataset_identity(suite: str, payload: dict[str, Any]) -> tuple[str, str]:
    if suite == "screening":
        return payload["dataset"]["name"], payload["dataset"]["sha256"]
    if suite == "extraction":
        return payload["dataset"], payload["dataset_sha256"]
    if suite == "face":
        from .datasets import read_manifest
        from .face import MANIFEST_DIR

        manifest = read_manifest(MANIFEST_DIR / "MANIFEST.json")
        return payload["dataset"], manifest["files"]["lfw.tgz"]["sha256"]
    # risk is composed from the others; its identity is the hash of its own inputs.
    from .datasets import sha256_bytes

    return "composed", sha256_bytes(json.dumps(payload["inputs"], sort_keys=True).encode())


async def record_run(suite: str, payload: dict[str, Any]) -> None:
    name, sha = dataset_identity(suite, payload)
    conn = await asyncpg.connect(settings.database_url.get_secret_value())
    try:
        await conn.execute(
            "INSERT INTO eval_runs (suite, dataset_name, dataset_sha256, config, metrics, git_sha) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            suite,
            name,
            sha,
            json.dumps({"suite": suite}),
            json.dumps(payload, default=str),
            git_sha(),
        )
    finally:
        await conn.close()

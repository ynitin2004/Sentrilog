"""Dataset identity. A metric means nothing without the exact data it was measured on, so every
dataset the harness reads has a manifest recording its SHA-256, and loading one checks it.
A dataset that no longer matches its manifest fails loudly rather than producing a number for
data nobody can name (D28).
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"


class DatasetMismatchError(Exception):
    pass


@dataclass(frozen=True)
class DatasetRef:
    name: str
    sha256: str
    rows: int


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return manifest


def verify_files(directory: Path) -> dict[str, Any]:
    """Checks every file the directory's MANIFEST.json lists against its recorded hash."""
    manifest = read_manifest(directory / "MANIFEST.json")
    for filename, expected in manifest["files"].items():
        actual = sha256_file(directory / filename)
        if actual != expected:
            raise DatasetMismatchError(
                f"{directory.name}/{filename}: sha256 {actual} does not match manifest {expected}"
            )
    return manifest


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    """Writes rows with a fixed key order and LF endings, so the same rows always hash the same
    on every OS, and returns that hash."""
    data = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)
    encoded = data.encode("utf-8")
    path.write_bytes(encoded)
    return sha256_bytes(encoded)


def read_jsonl(path: Path, *, expected_sha256: str) -> list[dict[str, Any]]:
    data = path.read_bytes()
    actual = sha256_bytes(data)
    if actual != expected_sha256:
        raise DatasetMismatchError(
            f"{path.name}: sha256 {actual} does not match the recorded {expected_sha256}"
        )
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line]

"""Face match evaluation: true accept rate at a fixed false accept rate (TAR@FAR) for the
production InsightFace client, over Labeled Faces in the Wild's DevTest pairs (D28).

LFW is fetched by script and pinned by SHA-256, never committed: its images are photographs of
real people, collected from news media for research. The archive is cached outside the
repository (EVAL_CACHE_DIR, default ~/.cache/sentrilog-eval).

Two impostor sets are reported, because they answer different questions:

- the 500 official mismatched pairs, comparable with published LFW numbers, but able to resolve
  a FAR no finer than 1/500 = 2e-3;
- every cross-identity pair among the DevTest images, which resolves FAR = 1e-3 and below. These
  pairs are not independent (each image appears in many), so the TAR at a very low FAR has a
  wider error than the count suggests.

LFW is not KYC data. Its faces are celebrity press photos, not a document portrait against a
phone selfie, so this measures the model's identity separation on the faces it was published
against -- not the live-capture error a KYC deployment would see. Every report says so.

    uv run python -m services.eval face [--max-pairs N]
"""

import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from services.pipeline.face_match import InsightFaceClient, NoFaceDetectedError

from .datasets import DATA_DIR, read_manifest, sha256_file
from .metrics import tar_at_far

MANIFEST_DIR = DATA_DIR / "lfw-devtest"
FARS = (1e-2, 1e-3)


def cache_dir() -> Path:
    import os

    return Path(os.environ.get("EVAL_CACHE_DIR", Path.home() / ".cache" / "sentrilog-eval")) / "lfw"


def ensure_files() -> dict[str, Path]:
    """Downloads anything missing, then checks every file against the pinned hash."""
    import ssl

    import httpx
    import truststore

    manifest = read_manifest(MANIFEST_DIR / "MANIFEST.json")
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for filename, meta in manifest["files"].items():
        path = directory / filename
        if not path.exists():
            context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with httpx.Client(verify=context, timeout=600, follow_redirects=True) as client:
                with client.stream("GET", meta["url"]) as response:
                    response.raise_for_status()
                    with path.open("wb") as f:
                        for chunk in response.iter_bytes(1 << 20):
                            f.write(chunk)
        actual = sha256_file(path)
        if actual != meta["sha256"]:
            raise RuntimeError(f"{filename}: sha256 {actual} does not match {meta['sha256']}")
        paths[filename] = path
    return paths


def read_pairs(path: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """pairsDevTest.txt: a count N, then N same-person lines "name i j", then N
    different-person lines "name1 i name2 j". Returns image keys like "Abdullah_Gul/13"."""
    lines = path.read_text(encoding="utf-8").split("\n")
    n = int(lines[0])
    genuine, impostor = [], []
    for line in lines[1 : 1 + n]:
        name, i, j = line.split("\t")
        genuine.append((f"{name}/{int(i)}", f"{name}/{int(j)}"))
    for line in lines[1 + n : 1 + 2 * n]:
        a, i, b, j = line.split("\t")
        impostor.append((f"{a}/{int(i)}", f"{b}/{int(j)}"))
    return genuine, impostor


def _member_key(member_name: str) -> str | None:
    # lfw/Abdullah_Gul/Abdullah_Gul_0013.jpg -> Abdullah_Gul/13
    parts = member_name.split("/")
    if len(parts) != 3 or not parts[2].endswith(".jpg"):
        return None
    index = parts[2][len(parts[1]) + 1 : -4]
    return f"{parts[1]}/{int(index)}" if index.isdigit() else None


def read_images(archive: Path, keys: set[str]) -> dict[str, bytes]:
    """One streaming pass over the 173 MB archive, keeping only the images the pairs use."""
    images: dict[str, bytes] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            key = _member_key(member.name)
            if key in keys:
                f = tar.extractfile(member)
                if f is not None:
                    images[key] = f.read()
    return images


@dataclass
class FaceResult:
    dataset: str
    pairs_genuine: int
    pairs_impostor_official: int
    images: int
    no_face_images: int
    tar_at_far: dict[str, float]
    threshold_at_far: dict[str, float]
    tar_at_far_official: dict[str, float]
    cross_identity_impostor_pairs: int
    genuine_percentiles: dict[str, float]
    impostor_percentiles: dict[str, float]
    genuine_scores: list[float]
    impostor_scores_official: list[float]
    caveat: str


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {f"p{q}": float(np.percentile(values, q)) for q in (1, 5, 25, 50, 75, 95, 99)}


_MODEL = "buffalo_l"


def _embed_all(
    images: dict[str, bytes], *, archive_sha256: str
) -> tuple[dict[str, np.ndarray], int]:
    """Embeds every image once, through a cache keyed by the archive's hash and the model.

    Embedding ~1,700 images costs half an hour on a slow CPU, and the result is a pure function
    of (archive, model, image), so it is cached in EVAL_CACHE_DIR, which CI persists between
    runs. A missing key in the cache means "no face detected", stored as such.
    """
    path = cache_dir() / f"embeddings-{_MODEL}-{archive_sha256[:16]}.npz"
    cached: dict[str, np.ndarray] = {}
    if path.exists():
        with np.load(path) as data:
            cached = {k: data[k] for k in data.files}
    no_face_marker = np.zeros(0)
    missing = [k for k in sorted(images) if k not in cached]
    if missing:
        client = InsightFaceClient(_MODEL)
        for key in missing:
            try:
                cached[key] = client.embed(images[key])
            except NoFaceDetectedError:
                cached[key] = no_face_marker
        # numpy's stub types **kwds as its own keyword flags, not array names.
        savez: Any = np.savez
        savez(path, **cached)
    embeddings = {k: v for k, v in cached.items() if k in images and v.size}
    no_face = sum(1 for k in images if k in cached and not cached[k].size)
    return embeddings, no_face


def evaluate(*, max_pairs: int | None = None) -> FaceResult:
    files = ensure_files()
    genuine_pairs, impostor_pairs = read_pairs(files["pairsDevTest.txt"])
    if max_pairs is not None:
        genuine_pairs, impostor_pairs = genuine_pairs[:max_pairs], impostor_pairs[:max_pairs]
    keys = {k for pair in genuine_pairs + impostor_pairs for k in pair}
    images = read_images(files["lfw.tgz"], keys)

    embeddings, no_face = _embed_all(images, archive_sha256=sha256_file(files["lfw.tgz"]))

    def score(a: str, b: str) -> float | None:
        if a not in embeddings or b not in embeddings:
            return None  # counted under no_face_images; in production this routes to review
        return float(np.dot(embeddings[a], embeddings[b]))

    genuine = [s for a, b in genuine_pairs if (s := score(a, b)) is not None]
    impostor_official = [s for a, b in impostor_pairs if (s := score(a, b)) is not None]

    ordered = sorted(embeddings)
    matrix = np.stack([embeddings[k] for k in ordered])
    identity = np.array([k.split("/")[0] for k in ordered])
    similarity = matrix @ matrix.T
    upper = np.triu(np.ones_like(similarity, dtype=bool), k=1)
    cross = similarity[upper & (identity[:, None] != identity[None, :])]

    tar, thresholds, tar_official = {}, {}, {}
    for far in FARS:
        label = f"{far:g}"
        tar[label], thresholds[label] = tar_at_far(genuine, cross.tolist(), far)
        # Only report an official-set FAR the set can actually resolve.
        if far * len(impostor_official) >= 1:
            tar_official[label], _ = tar_at_far(genuine, impostor_official, far)

    return FaceResult(
        dataset="lfw-devtest",
        pairs_genuine=len(genuine),
        pairs_impostor_official=len(impostor_official),
        images=len(images),
        no_face_images=no_face,
        tar_at_far=tar,
        threshold_at_far=thresholds,
        tar_at_far_official=tar_official,
        cross_identity_impostor_pairs=int(cross.size),
        genuine_percentiles=_percentiles(genuine),
        impostor_percentiles=_percentiles(impostor_official),
        genuine_scores=[round(s, 4) for s in genuine],
        impostor_scores_official=[round(s, 4) for s in impostor_official],
        caveat=(
            "LFW press photos, not document-portrait-vs-selfie captures: identity separation "
            "of the model, not a KYC live-capture error rate."
        ),
    )


def as_dict(result: FaceResult) -> dict[str, Any]:
    return asdict(result)

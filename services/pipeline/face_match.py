"""Face match client, behind a swappable interface -- same reasoning as vlm.py/embeddings.py.
InsightFace (self-hosted ArcFace embeddings) is the active choice per PLAN.md §6: full data
control (biometric data never leaves the VPC), no per-call cost, vs. Rekognition's managed
convenience. Confirmed against the real model, not assumed -- see PLAN.md Phase 6 changelog.
"""

import io
from typing import Protocol

import numpy as np
from insightface.app import FaceAnalysis
from PIL import Image


class NoFaceDetectedError(Exception):
    """Raised when InsightFace can't find a face in an image at all -- distinct from 'found a
    face but it doesn't match', which is a similarity score, not an error. A selfie or ID photo
    with no detectable face is a real, expected failure mode (bad photo, occluded face, wrong
    file), not a bug, and the caller needs to tell the two apart to route correctly.
    """


class FaceMatchClient(Protocol):
    def compare_faces(self, image_a: bytes, image_b: bytes) -> float:
        """Returns a similarity score in [-1, 1] (cosine similarity of face embeddings;
        practically always in [0, 1] for the same detector/model). Raises NoFaceDetectedError
        if either image has no detectable face."""
        ...


class InsightFaceClient:
    def __init__(self, model_name: str = "buffalo_l") -> None:
        self._app = FaceAnalysis(name=model_name)
        # ctx_id=-1 selects CPU -- no GPU assumed available in this environment or in prod
        # deployment target (ECS Fargate has no GPU by default per PLAN.md §5).
        self._app.prepare(ctx_id=-1)

    def _embed(self, image_bytes: bytes) -> np.ndarray:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        # InsightFace/OpenCV expect BGR channel order, not RGB.
        bgr = np.array(image)[:, :, ::-1].copy()
        faces = self._app.get(bgr)
        if not faces:
            raise NoFaceDetectedError("no face detected in image")
        # If multiple faces are detected (e.g. a busy background), the largest bounding box is
        # the most plausible subject -- a reasonable default, not a guarantee of correctness;
        # this is a heuristic, not a claim that face selection is a solved problem here.
        largest = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        # insightface has no type stubs, so normed_embedding is Any -- np.asarray gives mypy a
        # concrete return type to check callers against, without changing the runtime value.
        return np.asarray(largest.normed_embedding)

    def compare_faces(self, image_a: bytes, image_b: bytes) -> float:
        embedding_a = self._embed(image_a)
        embedding_b = self._embed(image_b)
        # normed_embedding is already L2-normalized, so the dot product IS the cosine
        # similarity -- no separate normalization step needed.
        return float(np.dot(embedding_a, embedding_b))

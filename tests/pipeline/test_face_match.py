"""Real InsightFace tests -- no fakes here, unlike vlm.py/embeddings.py's fake-client pattern,
because the thing actually worth testing is the model's real behavior (does it correctly
detect faces, does the same person score high, do different people score low), not an
orchestration layer around it. face_match.py has no separate orchestration to fake around --
that lives in the Temporal activity, tested separately.

Uses two real, distinct human face photos: scikit-image's bundled astronaut() portrait and
insightface's own bundled Tom_Hanks_54745.png test asset -- deliberately not two of our own
synthetic drawn images, since a real face detector correctly finds no face in a text-only
mockup (that's the "no face detected" test below), so a real photo is required to exercise the
actual detection + embedding + comparison path at all.
"""

import io
from pathlib import Path

import insightface
import pytest
from PIL import Image
from skimage import data

from services.pipeline.face_match import InsightFaceClient, NoFaceDetectedError

_TOM_HANKS_PATH = Path(insightface.__file__).parent / "data" / "images" / "Tom_Hanks_54745.png"


def _array_to_jpeg_bytes(arr) -> bytes:  # type: ignore[no-untyped-def]
    img = Image.fromarray(arr).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def client() -> InsightFaceClient:
    return InsightFaceClient()


@pytest.fixture(scope="module")
def astronaut_bytes() -> bytes:
    return _array_to_jpeg_bytes(data.astronaut())  # type: ignore[no-untyped-call]  # scikit-image has no stubs


@pytest.fixture(scope="module")
def blank_image_bytes() -> bytes:
    img = Image.new("RGB", (400, 300), color="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_same_person_scores_very_high(client: InsightFaceClient, astronaut_bytes: bytes) -> None:
    score = client.compare_faces(astronaut_bytes, astronaut_bytes)
    assert score > 0.99  # identical image, identical embedding -- should be ~1.0, not just "high"


@pytest.mark.skipif(
    not _TOM_HANKS_PATH.exists(), reason="insightface's bundled test image moved/renamed"
)
def test_different_real_people_score_low(client: InsightFaceClient, astronaut_bytes: bytes) -> None:
    tom_hanks_bytes = _TOM_HANKS_PATH.read_bytes()
    score = client.compare_faces(astronaut_bytes, tom_hanks_bytes)
    assert score < 0.4  # two different people must not be anywhere near "same person" territory


def test_no_face_detected_raises_not_returns_a_bogus_score(
    client: InsightFaceClient, astronaut_bytes: bytes, blank_image_bytes: bytes
) -> None:
    with pytest.raises(NoFaceDetectedError):
        client.compare_faces(astronaut_bytes, blank_image_bytes)

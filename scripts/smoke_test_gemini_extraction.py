"""One-off manual smoke test proving the real Gemini integration works end to end -- not part
of the automated suite (which uses a fake VLMClient) since this costs real API quota and isn't
deterministic. Run by hand: uv run python scripts/smoke_test_gemini_extraction.py

Uses a synthetically drawn image with fabricated field values -- never real ID data, per the
free-tier data-usage policy noted in .env.example and PLAN.md Phase 4.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from services.pipeline.config import settings  # noqa: E402
from services.pipeline.extraction.extract import extract_with_vlm  # noqa: E402
from services.pipeline.extraction.vlm import GeminiVLMClient  # noqa: E402


def build_synthetic_id_image() -> bytes:
    img = Image.new("RGB", (600, 400), color="white")
    draw = ImageDraw.Draw(img)
    lines = [
        "SAMPLE IDENTIFICATION CARD (SYNTHETIC -- NOT A REAL DOCUMENT)",
        "",
        "Name: Jordan Alex Rivera",
        "Document Number: SYN-2026-00042",
        "Date of Birth: 1992-03-14",
        "Expiry Date: 2032-03-14",
        "Nationality: TESTLAND",
        "Document Type: national_id",
    ]
    y = 30
    for line in lines:
        draw.text((30, y), line, fill="black")
        y += 35
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main() -> None:
    client = GeminiVLMClient(api_key=settings.gemini_api_key, model=settings.gemini_model)
    image_bytes = build_synthetic_id_image()

    result = extract_with_vlm(
        client, image_bytes, "image/jpeg", max_retries=settings.extraction_max_retries
    )

    print(f"needs_review: {result.needs_review}")
    print(f"confidence:   {result.confidence}")
    print(f"method:       {result.method}")
    print(f"document:     {result.document}")
    if result.reason:
        print(f"reason:       {result.reason}")


if __name__ == "__main__":
    main()

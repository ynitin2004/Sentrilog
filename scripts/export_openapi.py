"""Exports the intake API's OpenAPI schema to frontend/openapi.json, without needing a live
server -- FastAPI/Starlette apps build their schema in-process via app.openapi().

Usage: uv run python scripts/export_openapi.py
Then: cd frontend && npm run generate-types
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.intake.main import app  # noqa: E402

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "frontend" / "openapi.json"


def main() -> None:
    OUTPUT_PATH.write_text(json.dumps(app.openapi(), indent=2))
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

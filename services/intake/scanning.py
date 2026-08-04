ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "application/pdf"}


def validate_upload_request(
    content_type: str, declared_size_bytes: int, max_bytes: int
) -> str | None:
    """Pre-upload validation of what the client *claims* it will upload. Returns an error
    message, or None if valid. This cannot inspect file bytes (the file goes straight to S3
    via presigned PUT, never through this service) -- it only catches obviously bad requests
    before a presigned URL is even issued.
    """
    if content_type not in ALLOWED_CONTENT_TYPES:
        return f"content_type must be one of {sorted(ALLOWED_CONTENT_TYPES)}"
    if declared_size_bytes <= 0:
        return "size_bytes must be positive"
    if declared_size_bytes > max_bytes:
        return f"size_bytes exceeds the {max_bytes}-byte limit"
    return None


async def scan_for_malware(s3_key: str) -> bool:
    """Stub: always reports clean. The real implementation (Phase 9/10) hooks this up to an
    actual scanner (e.g. ClamAV sidecar, or an S3-event-triggered Lambda) run against the
    object after upload completes. Defined now so the call site and its position in the
    pipeline exist before real files are flowing through it, per PLAN.md Phase 3 scope.
    """
    return True

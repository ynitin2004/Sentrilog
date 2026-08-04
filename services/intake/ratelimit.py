import time
from dataclasses import dataclass


@dataclass
class _Window:
    start: float
    count: int


class InMemoryRateLimiter:
    """Fixed-window rate limiter, keyed by API key ID. Process-local: correct for a single
    uvicorn worker, but each additional worker/replica would get its own independent counter,
    so real enforced limits under a multi-replica deployment are effectively (limit * replica
    count). Acceptable for Phase 3 (single local worker) -- real enforcement moves to the edge
    (API Gateway/ALB) in Phase 10, per PLAN.md §5, before multiple replicas make this matter.
    """

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}

    def check(self, key: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        window = self._windows.get(key)
        if window is None or now - window.start >= self._window_seconds:
            self._windows[key] = _Window(start=now, count=1)
            return True, 0
        if window.count < self._limit:
            window.count += 1
            return True, 0
        retry_after = int(self._window_seconds - (now - window.start)) + 1
        return False, retry_after

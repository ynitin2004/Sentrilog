import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Window:
    start: float
    count: int


class InMemoryRateLimiter:
    """Fixed-window rate limiter, keyed by API key ID. Process-local: correct for a single
    uvicorn worker, but each additional worker/replica would get its own independent counter,
    so real enforced limits under a multi-replica deployment are effectively (limit * replica
    count). Acceptable while a single local worker serves everything -- real enforcement moves
    to the edge (API Gateway/ALB) in Phase 13, per PLAN.md §5, before multiple replicas make
    this matter.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # clock is injectable purely so the window's *expiry* is testable: proving a limiter
        # recovers once its window closes otherwise means a test that really sleeps for
        # window_seconds (60s in this service's config). Production passes nothing and gets
        # time.monotonic, which is also the right clock here -- a wall-clock jump (NTP step,
        # DST) must not hand out a free window or stretch one.
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._windows: dict[str, _Window] = {}

    def check(self, key: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        window = self._windows.get(key)
        if window is None or now - window.start >= self._window_seconds:
            self._windows[key] = _Window(start=now, count=1)
            return True, 0
        if window.count < self._limit:
            window.count += 1
            return True, 0
        retry_after = int(self._window_seconds - (now - window.start)) + 1
        return False, retry_after

    def reset(self) -> None:
        """Drops all window state. For tests that share this module-level limiter across cases
        -- nothing in the request path calls it."""
        self._windows.clear()

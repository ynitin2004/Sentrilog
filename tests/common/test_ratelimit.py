"""Rate limiter unit tests (Phase 12), including the half that was missing: recovery.

The pre-existing coverage (tests/intake/test_cases.py) proved a 429 *triggers* at the threshold
through the real HTTP layer. What it couldn't prove is that the window ever closes again --
doing that against the real clock means a test that sleeps for the configured 60s window. These
drive an injected clock instead, which is why InMemoryRateLimiter grew a `clock` parameter in
this phase; production still gets time.monotonic.

Lives in tests/common/ rather than tests/intake/ deliberately: tests/intake/conftest.py has a
session-scoped autouse fixture that opens a real Postgres pool, and a pure in-memory limiter
has no business requiring a database to test.
"""

from services.intake.ratelimit import InMemoryRateLimiter


class _FakeClock:
    """A manually-advanced stand-in for time.monotonic."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_up_to_the_limit() -> None:
    limiter = InMemoryRateLimiter(limit=3, window_seconds=60, clock=_FakeClock())
    assert [limiter.check("k")[0] for _ in range(3)] == [True, True, True]


def test_blocks_once_the_limit_is_reached() -> None:
    limiter = InMemoryRateLimiter(limit=2, window_seconds=60, clock=_FakeClock())
    limiter.check("k")
    limiter.check("k")
    allowed, retry_after = limiter.check("k")
    assert allowed is False
    assert retry_after > 0


def test_recovers_once_the_window_closes() -> None:
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(limit=2, window_seconds=60, clock=clock)
    limiter.check("k")
    limiter.check("k")
    assert limiter.check("k")[0] is False

    clock.advance(60)
    allowed, retry_after = limiter.check("k")
    assert allowed is True
    assert retry_after == 0


def test_does_not_recover_one_tick_before_the_window_closes() -> None:
    # The boundary is the interesting part: a limiter that resets slightly early hands out a
    # free extra window on every cycle, which is exactly the bug a "does it recover?" test that
    # only checks well past the boundary would miss.
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60, clock=clock)
    limiter.check("k")
    clock.advance(59.9)
    assert limiter.check("k")[0] is False
    clock.advance(0.1)
    assert limiter.check("k")[0] is True


def test_retry_after_shrinks_as_the_window_elapses() -> None:
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60, clock=clock)
    limiter.check("k")

    _, retry_at_start = limiter.check("k")
    clock.advance(30)
    _, retry_midway = limiter.check("k")

    assert retry_at_start > retry_midway > 0


def test_keys_are_counted_independently() -> None:
    # The multi-tenant property: one API key exhausting its budget must not 429 another's
    # requests, since the limiter is keyed per api_key_id/reviewer_id.
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60, clock=_FakeClock())
    assert limiter.check("tenant-a-key")[0] is True
    assert limiter.check("tenant-a-key")[0] is False
    assert limiter.check("tenant-b-key")[0] is True


def test_reset_clears_all_windows() -> None:
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60, clock=_FakeClock())
    limiter.check("k")
    assert limiter.check("k")[0] is False
    limiter.reset()
    assert limiter.check("k")[0] is True

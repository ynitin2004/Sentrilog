"""Extraction-stage load test (Phase 12, PLAN.md §9): the pipeline's bottleneck and cost centre.

PLAN.md singles out extraction for its own load test because it is the only stage that is both
the slowest *and* metered -- every VLM attempt is a billed API call. Those are two different
risks and this script measures both:

  Throughput  How many cases per second the stage sustains, and where the ceiling actually is.
  Cost        Billed VLM calls per case, which is *not* one, because of two multipliers the
              design deliberately accepted (MRZ short-circuit, bounded retry on bad output).

Real code under test: `extract_id_document`, the whole `extract_with_vlm` retry-with-feedback
loop, `parse_td3`'s checksum validation, and Pydantic validation of every response. Stubbed:
the Gemini HTTP call itself (`FakeVLMClient`), because a run at this volume against the live
API would cost real money and its latency variance would drown the signal. The stub is a
drop-in `VLMClient`, so the orchestration it drives is the shipped orchestration.

`activities.extract_document_activity` wraps all three of its blocking calls in
`asyncio.to_thread`, which hands them to the interpreter's *default* `ThreadPoolExecutor`. That
pool is shared process-wide and sized `min(32, cpu_count + 4)` -- a number set by neither
`max_concurrent_activities` nor anything in this project's configuration. The
`--concurrency` sweep exists to find out whether that invisible pool, rather than the Temporal
slot limit an operator actually tunes, is what caps the stage.

Usage (needs nothing running -- no Temporal, no Docker):
    uv run python scripts/load_test_extraction.py
    uv run python scripts/load_test_extraction.py --cases 400 --concurrency 1,8,32,64
    uv run python scripts/load_test_extraction.py --mrz-hit-rate 0.0 --bad-response-rate 0.4
"""

import argparse
import asyncio
import os
import random
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.pipeline.extraction.extract import extract_id_document  # noqa: E402
from services.pipeline.extraction.mrz import parse_td3  # noqa: E402

# A checksum-valid TD3 MRZ, so the MRZ path is exercised for real rather than mocked past.
# Verified below at startup -- if parse_td3 ever stops accepting it the run aborts instead of
# silently reporting a 0% MRZ hit rate as though that were a measurement.
_VALID_MRZ = (
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
)

# Representative of a real ID photo, and large enough that per-case byte handling isn't free.
_IMAGE_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 400_000

_GOOD_RESPONSE = (
    '{"document_visible": true, "document": {"full_name": "Anna Maria Eriksson", '
    '"document_number": "L898902C3", "date_of_birth": "1974-08-12", '
    '"expiry_date": "2012-04-15", "nationality": "UTO", "document_type": "passport"}}'
)
# The failure mode the retry loop exists for, and the one the real API actually produced:
# a schema-valid response that honestly reports it couldn't read the document.
_BAD_RESPONSE = '{"document_visible": false}'


@dataclass
class Meter:
    """Billed-call accounting. Locked because `to_thread` runs these on real threads."""

    vlm_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_call(self) -> None:
        with self._lock:
            self.vlm_calls += 1


class FakeVLMClient:
    """A `VLMClient` that charges latency instead of money.

    `--bad-response-rate` is applied per *attempt*, not per case, which is the point: a case
    whose first attempt comes back unreadable is billed again for the retry, so the cost curve
    against that rate is superlinear rather than proportional.
    """

    def __init__(self, meter: Meter, latency_seconds: float, bad_response_rate: float) -> None:
        self._meter = meter
        self._latency = latency_seconds
        self._bad_rate = bad_response_rate
        self._rng = random.Random(1234)
        self._rng_lock = threading.Lock()

    def extract_json(
        self, image_bytes: bytes, mime_type: str, *, error_feedback: str | None = None
    ) -> str:
        self._meter.record_call()
        time.sleep(self._latency)  # a blocking HTTP call, which is what the real one is
        with self._rng_lock:
            roll = self._rng.random()
        return _BAD_RESPONSE if roll < self._bad_rate else _GOOD_RESPONSE


@dataclass
class Outcome:
    latency: float
    method: str
    needs_review: bool


async def _one_case(
    client: FakeVLMClient,
    semaphore: asyncio.Semaphore,
    *,
    use_mrz: bool,
    max_retries: int,
) -> Outcome:
    """One case through the stage, shaped exactly like `extract_document_activity` does it:
    blocking work pushed onto the default thread pool via `to_thread`."""
    async with semaphore:
        started = time.monotonic()
        result = await asyncio.to_thread(
            extract_id_document,
            client,
            _IMAGE_BYTES,
            "image/jpeg",
            mrz_lines=_VALID_MRZ if use_mrz else None,
            max_retries=max_retries,
        )
        return Outcome(time.monotonic() - started, result.method, result.needs_review)


def _percentile(samples: list[float], fraction: float) -> float:
    if not samples:
        return float("nan")
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


@dataclass
class Measurement:
    concurrency: int
    wall_seconds: float
    cases: int
    vlm_calls: int
    outcomes: list[Outcome]

    @property
    def throughput(self) -> float:
        return self.cases / self.wall_seconds if self.wall_seconds else float("nan")

    @property
    def calls_per_case(self) -> float:
        return self.vlm_calls / self.cases if self.cases else float("nan")

    @property
    def mrz_share(self) -> float:
        hits = sum(1 for o in self.outcomes if o.method == "mrz")
        return hits / len(self.outcomes) if self.outcomes else float("nan")

    @property
    def review_share(self) -> float:
        flagged = sum(1 for o in self.outcomes if o.needs_review)
        return flagged / len(self.outcomes) if self.outcomes else float("nan")


async def _measure(
    concurrency: int,
    cases: int,
    *,
    latency: float,
    mrz_hit_rate: float,
    bad_response_rate: float,
    max_retries: int,
) -> Measurement:
    meter = Meter()
    client = FakeVLMClient(meter, latency, bad_response_rate)
    semaphore = asyncio.Semaphore(concurrency)

    # Decided up front from a fixed seed so every concurrency level gets the identical case mix
    # -- otherwise the sweep would be comparing different workloads, not different concurrency.
    rng = random.Random(99)
    mrz_flags = [rng.random() < mrz_hit_rate for _ in range(cases)]

    started = time.monotonic()
    outcomes = await asyncio.gather(
        *(_one_case(client, semaphore, use_mrz=flag, max_retries=max_retries) for flag in mrz_flags)
    )
    wall = time.monotonic() - started
    return Measurement(concurrency, wall, cases, meter.vlm_calls, list(outcomes))


def _default_thread_pool_size() -> int:
    """What `asyncio.to_thread` actually gets, per CPython's ThreadPoolExecutor default."""
    return min(32, (os.cpu_count() or 1) + 4)


def _report(m: Measurement) -> None:
    latencies = [o.latency for o in m.outcomes]
    print(
        f"  {m.concurrency:>4}  "
        f"{m.throughput:7.1f}  "
        f"{statistics.median(latencies):7.2f}  "
        f"{_percentile(latencies, 0.95):7.2f}  "
        f"{m.calls_per_case:8.2f}  "
        f"{m.wall_seconds:7.1f}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=240)
    parser.add_argument(
        "--concurrency",
        default="1,4,8,16,32,64,128",
        help="Comma-separated levels to sweep. Spans the default thread pool's size on purpose.",
    )
    parser.add_argument(
        "--vlm-latency",
        type=float,
        default=0.25,
        help="Stubbed per-attempt VLM latency in seconds (real Gemini calls run ~1-3s).",
    )
    parser.add_argument(
        "--mrz-hit-rate",
        type=float,
        default=0.6,
        help="Share of documents with a checksum-valid MRZ. These cost zero API calls.",
    )
    parser.add_argument(
        "--bad-response-rate",
        type=float,
        default=0.15,
        help="Per-attempt chance the VLM returns an unreadable-document response, forcing a "
        "billed retry.",
    )
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    if not parse_td3(*_VALID_MRZ).valid:
        print("ABORT: the fixture MRZ no longer passes parse_td3 -- fix the fixture, not this.")
        return 1

    levels = [int(x) for x in args.concurrency.split(",") if x.strip()]
    pool_size = _default_thread_pool_size()
    print(
        f"cases per level: {args.cases}   mrz hit rate: {args.mrz_hit_rate:.0%}   "
        f"bad-response rate: {args.bad_response_rate:.0%}/attempt\n"
        f"stubbed VLM latency: {args.vlm_latency}s   max retries: {args.max_retries}\n"
        f"default to_thread pool: {pool_size} threads\n"
    )
    print("  conc   cases/s   p50 s   p95 s  calls/case  wall s")

    results: list[Measurement] = []
    for level in levels:
        m = await _measure(
            level,
            args.cases,
            latency=args.vlm_latency,
            mrz_hit_rate=args.mrz_hit_rate,
            bad_response_rate=args.bad_response_rate,
            max_retries=args.max_retries,
        )
        results.append(m)
        _report(m)

    best = max(results, key=lambda m: m.throughput)
    print("\n=== findings ===")
    print(
        f"  peak throughput      : {best.throughput:.1f} cases/s "
        f"at concurrency {best.concurrency}"
    )

    # Does raising concurrency past the thread pool still buy throughput? If not, the pool --
    # not the Temporal slot limit an operator tunes -- is the real ceiling.
    beyond = [m for m in results if m.concurrency > pool_size]
    at_or_below = [m for m in results if m.concurrency <= pool_size]
    if beyond and at_or_below:
        ceiling = max(at_or_below, key=lambda m: m.throughput)
        gain = max(m.throughput for m in beyond) / ceiling.throughput
        verdict = (
            "thread pool is the ceiling"
            if gain < 1.15
            else "concurrency beyond the pool still helps"
        )
        print(f"  past {pool_size} threads{'':<6}: {gain:.2f}x further gain -> {verdict}")

    sample = results[-1]
    print(f"  mrz short-circuit    : {sample.mrz_share:.0%} of cases billed zero API calls")
    print(
        f"  billed calls per case: {sample.calls_per_case:.2f} "
        f"(vs {1 - sample.mrz_share:.2f} if every VLM case succeeded first try)"
    )
    print(f"  flagged for review   : {sample.review_share:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

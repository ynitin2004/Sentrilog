"""Structured logging + OpenTelemetry tracing setup, shared by every service's own process
entrypoint (services/intake/main.py, services/pipeline/workflows/worker.py).

Console exporter only -- a real trace backend/UI (e.g. AWS X-Ray) is Phase 13 (AWS) scope, not
this one's. What this buys now: every span is real and correctly correlated across the intake
API -> Temporal -> pipeline worker process boundary (via Temporal's own OpenTelemetry
interceptor, registered on both the client and the worker in each service's entrypoint), and
every log line is structured JSON carrying the active trace_id/span_id when one exists -- just
not yet queryable in a trace UI.

Unlike audit_hash.py (Phase 11), this lives in services/common/ purely to avoid ~80 lines of
identical structlog/OTel boilerplate duplicated between intake and pipeline -- there's no
cross-service correctness invariant at stake the way there is for the audit hash formula, just
setup code every service's entrypoint calls once with its own service name.
"""

import logging
import os
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from structlog.typing import Processor


def _add_trace_context(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Correlates a log line with whatever span is active when it's emitted -- the standard way
    to jump from "this log line looks wrong" to "show me every other log line and span from the
    same request" without a dedicated trace backend to click through.
    """
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")
    return event_dict


def configure_logging(service_name: str, *, level: int = logging.INFO) -> None:
    """Structured JSON logs for both structlog.get_logger() calls and plain stdlib `logging`
    calls (uvicorn, asyncpg, and temporalio all log via stdlib) -- routing both through the same
    processor chain means one process never interleaves two different log formats on stdout.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_trace_context,
        structlog.stdlib.add_logger_name,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)

    # Bound once via contextvars so every log line this process ever emits carries it, without
    # threading service_name through every individual log call.
    structlog.contextvars.bind_contextvars(service=service_name)


class _LateBoundStdout:
    """Resolves `sys.stdout` at write time instead of at exporter construction.

    `ConsoleSpanExporter()` defaults to `out=sys.stdout`, capturing whatever object is bound to
    that name when `configure_tracing` runs. `BatchSpanProcessor` then flushes on a background
    thread and again from an `atexit` hook, both of which can fire *after* that captured stream
    is gone -- pytest's capture plugin closes its replacement stream at the end of a test, and
    interpreter shutdown closes the real one. The result was a `ValueError: I/O operation on
    closed file` traceback printed after an otherwise-clean test run.

    Dropping the write is the right answer here specifically because this is a console debug
    exporter: a span line lost during teardown costs nothing, and the only alternative at that
    point is the traceback. Nothing durable routes through here -- the tamper-evident record is
    Phase 11's `audit_log`, which is a transactional database write and does not tolerate loss.
    Phase 13's OTLP exporter writes to a socket rather than a borrowed stream and so doesn't
    share this failure mode.
    """

    def write(self, text: str) -> int:
        stream = sys.stdout
        if stream is None or stream.closed:
            return 0
        try:
            return stream.write(text)
        except ValueError:  # the stream closed between the check and the write
            return 0

    def flush(self) -> None:
        stream = sys.stdout
        if stream is None or stream.closed:
            return
        try:
            stream.flush()
        except ValueError:
            pass


def _console_export_disabled() -> bool:
    """Honours OTEL_TRACES_EXPORTER=none, which is OpenTelemetry's own standard variable for
    "produce spans but ship them nowhere" -- not a flag invented for this project.

    The provider is still installed when it's set, so spans are still created and
    _add_trace_context still stamps trace_id/span_id onto every log line; only the printing
    stops. CI sets it: with the console exporter on, a full test run buries its own failure
    output under a JSON span per HTTP request, which makes the one thing a CI log exists for --
    reading the traceback -- the hardest thing to find in it.
    """
    return os.getenv("OTEL_TRACES_EXPORTER", "").strip().lower() == "none"


_tracing_configured = False


def configure_tracing(service_name: str) -> None:
    """Idempotent: OpenTelemetry permits exactly one global TracerProvider and merely *warns*
    when a second is set, keeping the first. Several test modules import an app that calls this
    at import time, so without the guard a run logs that warning repeatedly while stacking
    another exporter's worth of background flushing on each call.
    """
    global _tracing_configured
    if _tracing_configured:
        return

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if not _console_export_disabled():
        # `out` is annotated IO[Any], but ConsoleSpanExporter only ever calls .write() and
        # .flush() on it -- the annotation is wider than the contract it actually uses.
        # Satisfying IO[Any] properly would mean implementing a dozen methods (read, seek,
        # fileno, the context-manager protocol) that this writer has no meaning for, so the
        # narrow ignore is the honest option.
        exporter = ConsoleSpanExporter(out=_LateBoundStdout())  # type: ignore[arg-type]
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracing_configured = True

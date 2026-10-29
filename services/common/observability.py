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


def configure_tracing(service_name: str) -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)

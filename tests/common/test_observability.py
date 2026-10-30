"""configure_tracing's exporter wiring.

Worth testing rather than eyeballing because both branches fail silently in opposite
directions: a provider with no exporter looks identical to a working one until you go looking
for the spans, and a provider that still exports buries a CI failure under its own output.
"""

import logging
from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.util._once import Once

from services.common import observability


@pytest.fixture
def unconfigured_tracing() -> Iterator[None]:
    """Hands each test a process that has never configured tracing, and puts the real one back
    afterwards.

    Three bits of global state are in play, and missing any one of them breaks the test in a
    confusing way rather than an obvious one. configure_tracing has its own idempotence flag.
    The OpenTelemetry API holds the provider in trace._TRACER_PROVIDER. And set_tracer_provider
    goes through a one-shot latch (trace._TRACER_PROVIDER_SET_ONCE) that, once tripped, makes
    every later call a no-op that only logs "Overriding of current TracerProvider is not
    allowed" -- so without resetting it the second test in this file silently inspects the
    first test's provider.
    """
    previous_flag = observability._tracing_configured
    previous_provider = trace._TRACER_PROVIDER
    previous_once = trace._TRACER_PROVIDER_SET_ONCE

    observability._tracing_configured = False
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    try:
        yield
    finally:
        observability._tracing_configured = previous_flag
        trace._TRACER_PROVIDER = previous_provider
        trace._TRACER_PROVIDER_SET_ONCE = previous_once


def _span_processor_count(provider: object) -> int:
    # The SDK keeps registered processors in a composite on _active_span_processor; there's no
    # public accessor, so this reaches for the private one rather than asserting on captured
    # stdout, which would depend on BatchSpanProcessor's background flush timing.
    active = provider._active_span_processor  # type: ignore[attr-defined]
    return len(active._span_processors)


def test_otel_traces_exporter_none_installs_a_provider_without_an_exporter(
    unconfigured_tracing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    observability.configure_tracing("test-service")

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    # The provider is still real: spans are still created, so log lines still get a trace_id.
    assert _span_processor_count(provider) == 0


def test_console_exporter_is_registered_by_default(
    unconfigured_tracing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    observability.configure_tracing("test-service")

    assert _span_processor_count(trace.get_tracer_provider()) == 1


@pytest.mark.parametrize("value", ["console", "", "NONE_OF_THE_ABOVE"])
def test_only_the_exact_value_none_disables_export(
    unconfigured_tracing: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A substring or truthiness check here would silently disable tracing for anyone who set
    OTEL_TRACES_EXPORTER to a real exporter name, which is the variable's normal use.
    """
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", value)
    observability.configure_tracing("test-service")

    assert _span_processor_count(trace.get_tracer_provider()) == 1


def test_trace_context_processor_adds_nothing_outside_a_span() -> None:
    """A log line emitted with no active span should carry no trace_id at all, rather than a
    zeroed-out one that looks like a real id in a log search.
    """
    event_dict = observability._add_trace_context(None, "info", {"event": "hello"})

    assert "trace_id" not in event_dict
    assert "span_id" not in event_dict


def test_configure_logging_leaves_one_handler_emitting_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both structlog and plain stdlib logging have to land on the same single handler -- the
    point of the foreign_pre_chain wiring is that uvicorn/asyncpg/temporalio lines come out in
    the same format, not that a second handler prints them in a second format.
    """
    observability.configure_logging("test-service")
    try:
        assert len(logging.getLogger().handlers) == 1
        logging.getLogger("some.stdlib.logger").warning("from stdlib")
        out = capsys.readouterr().out
        assert '"event": "from stdlib"' in out
        assert '"service": "test-service"' in out
        assert '"logger": "some.stdlib.logger"' in out
    finally:
        logging.getLogger().handlers = []

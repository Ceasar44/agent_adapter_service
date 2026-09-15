import asyncio
import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_adapter_service.observability.audit import AuditService
from agent_adapter_service.observability.logging import (
    bind_context,
    configure_logging,
    get_context,
)
from agent_adapter_service.observability.metrics import Metrics
from agent_adapter_service.observability.tracing import TraceManager


async def test_context_isolation_nesting_and_cancellation():
    async def worker(name):
        with bind_context(run_id=name):
            await asyncio.sleep(0)
            with bind_context(thread_id="thread"):
                assert get_context() == {"run_id": name, "thread_id": "thread"}
            assert get_context() == {"run_id": name}
        assert get_context() == {}

    await asyncio.gather(worker("one"), worker("two"))
    with pytest.raises(asyncio.CancelledError):
        with bind_context(run_id="cancelled"):
            raise asyncio.CancelledError()
    assert get_context() == {}


@pytest.mark.parametrize("json_output", [True, False])
def test_logging_is_idempotent_and_does_not_serialize_exceptions_or_extras(json_output):
    logger = logging.getLogger("agent_adapter_service")
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    output = io.StringIO()
    try:
        configure_logging(stream=output, json_output=json_output)
        configure_logging(stream=output, json_output=json_output)
        with bind_context(run_id="r1", request_id="q1", trace_id="t1", thread_id="th1"):
            try:
                raise RuntimeError("payment-secret")
            except RuntimeError:
                logger.exception("operation failed", extra={"token": "payment-secret"})
        lines = output.getvalue().splitlines()
        assert len(lines) == 1
        assert "payment-secret" not in lines[0]
        assert "RuntimeError" in lines[0]
        if json_output:
            assert json.loads(lines[0])["run_id"] == "r1"
    finally:
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def test_spans_propagate_without_exporting_exception_payloads():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    manager = TraceManager(provider)
    try:
        with bind_context(run_id="r1"), manager.span("run") as root:
            carrier = manager.inject()
            with pytest.raises(ValueError):
                with manager.span("saleor", {"token": "secret"}, parent=manager.extract(carrier)):
                    raise ValueError("payment-secret")
        child, parent = exporter.get_finished_spans()
        assert child.parent.span_id == root.get_span_context().span_id
        assert child.context.trace_id == parent.context.trace_id
        assert child.attributes["run_id"] == "r1"
        assert "token" not in child.attributes
        assert not child.events
        assert child.status.status_code.name == "ERROR"
        assert get_context() == {}
    finally:
        provider.shutdown()


def test_metrics_latency_errors_and_bounded_labels():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics = Metrics(provider)
    try:
        with metrics.measure("run", "untrusted-run-123"):
            pass
        with pytest.raises(ValueError):
            with metrics.measure("saleor", "secret"):
                raise ValueError()
        metrics.record("user-input", "sensitive", 0.01, "failure")
        data = reader.get_metrics_data()
        points = [
            (metric.name, point)
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            for point in metric.data.data_points
        ]
        assert {name for name, _ in points} == {
            "adapter.calls",
            "adapter.errors",
            "adapter.duration",
        }
        assert all(set(point.attributes) == {"surface", "status"} for _, point in points)
        assert "secret" not in repr(data)
        assert sum(p.value for n, p in points if n == "adapter.errors") == 2
    finally:
        provider.shutdown()


@pytest.mark.parametrize("surface", ["customer_agent", "frontend_tool", "admin_mcp"])
async def test_audit_sanitizes_before_repository_and_preserves_trace(surface):
    repository = SimpleNamespace(append=AsyncMock())
    service = AuditService(repository)
    with bind_context(trace_id="trace-1"):
        async with service.tool_call(
            actor="actor",
            surface=surface,
            tool_name="test_tool",
            arguments={"quantity": 2, "nested": {"token": "secret", "card_number": "1234"}},
        ):
            assert repository.append.await_count == 1
    events = [c.args[0] for c in repository.append.call_args_list]
    assert [e.result_status for e in events] == ["pending", "success"]
    assert events[0].trace_id == "trace-1"
    assert events[0].arguments_summary["quantity"] == 2
    assert "secret" not in repr(events) and "1234" not in repr(events)


async def test_audit_fails_closed_and_records_cancellation():
    repository = SimpleNamespace(append=AsyncMock(side_effect=RuntimeError("unavailable")))
    service = AuditService(repository)
    fields = dict(actor="actor", surface="customer_agent", tool_name="write")
    entered = False
    with pytest.raises(RuntimeError):
        async with service.tool_call(**fields):
            entered = True
    assert not entered
    repository.append = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        async with service.tool_call(**fields):
            raise asyncio.CancelledError()
    assert [c.args[0].result_status for c in repository.append.call_args_list] == [
        "pending",
        "failure",
    ]

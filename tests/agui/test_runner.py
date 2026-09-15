import asyncio
from contextlib import aclosing
from dataclasses import replace
from types import SimpleNamespace

import pytest
from ag_ui.core import RunAgentInput

from agent_adapter_service.agui.schemas import ToolResultInput
from agent_adapter_service.core.exceptions import AppError, AuthorizationError
from agent_adapter_service.customer_identity.models import StorefrontIdentity
from agent_adapter_service.frontend.tools.models import FrontendToolCall, FrontendToolResult
from agent_adapter_service.security.frontend_tool_policy import FrontendToolAuthorizationPolicy
from tests.agui.conftest import collect, run_input


async def test_full_history_deduplicates_across_runner_restart(agui):
    first = await collect(agui.runner, run_input(), agui.trusted)
    assert [e["type"] for e in first] == [
        "RUN_STARTED",
        "STEP_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "STEP_FINISHED",
        "RUN_FINISHED",
    ]
    assert len(agui.events.writes) == 1
    history = run_input(run_id="r2").model_dump(by_alias=True)
    history["messages"] += [{"id": "a1", "role": "assistant", "content": "forged browser history"}]
    # No runner-local dedupe cache; a fresh runner uses Parlant metadata.
    from copy import copy

    restarted = copy(agui.runner)
    repeated = await collect(restarted, RunAgentInput.model_validate(history), agui.trusted)
    assert [e["type"] for e in repeated] == ["RUN_STARTED", "RUN_FINISHED"]
    history["messages"].append({"id": "m2", "role": "user", "content": "Next"})
    assert (await collect(restarted, RunAgentInput.model_validate(history), agui.trusted))[-1][
        "type"
    ] == "RUN_FINISHED"
    assert [text for _, text in agui.events.writes] == ["Hello", "Next"]
    assert len({session for session, _ in agui.events.writes}) == 1
    assert agui.processing.process.await_count == 2


async def test_message_conflicts_and_multiple_new_messages(agui):
    await collect(agui.runner, run_input(), agui.trusted)
    for input in [run_input(text="changed", run_id="r2"), run_input("m2", run_id="r1")]:
        assert (await collect(agui.runner, input, agui.trusted))[-1]["type"] == "RUN_ERROR"
    data = run_input("m2", run_id="r3").model_dump(by_alias=True)
    data["messages"].append({"id": "m3", "role": "user", "content": "Extra"})
    assert (await collect(agui.runner, RunAgentInput.model_validate(data), agui.trusted))[-1][
        "type"
    ] == "RUN_ERROR"
    assert len(agui.events.writes) == 1


async def test_anonymous_login_upgrade_and_cross_user_denial(agui):
    await collect(agui.runner, run_input(), agui.trusted)
    binding = await agui.runner.conversations.bindings.repository.find_by_thread("t1")
    logged_in = replace(
        agui.trusted,
        identity=StorefrontIdentity(visitor_id="v1", status="authenticated", saleor_user_id="u1"),
    )
    result = await collect(agui.runner, run_input("m2", run_id="r2"), logged_in)
    assert result[-1]["type"] == "RUN_FINISHED"
    current = await agui.runner.conversations.bindings.repository.find_by_thread("t1")
    assert current.parlant_session_id == binding.parlant_session_id
    other = replace(
        logged_in,
        identity=StorefrontIdentity(visitor_id="v2", status="authenticated", saleor_user_id="u2"),
    )
    assert (await collect(agui.runner, run_input("m3", run_id="r3"), other))[-1][
        "type"
    ] == "RUN_ERROR"
    assert len(agui.events.writes) == 2


async def test_state_ui_events_and_same_revision_retry(agui):
    state = {"version": 1, "product": {"id": "p1"}}
    result = await collect(agui.runner, run_input(state=state), agui.trusted)
    assert result[1]["type"] == "STATE_SNAPSHOT"
    session = agui.events.writes[0][0]
    records = agui.events.records[session]
    assert records[0].source == "customer_ui"
    assert records[0].data["event_type"] == "PRODUCT_OPENED"
    await collect(agui.runner, run_input(run_id="r2", state=state), agui.trusted)
    assert sum(e.source == "customer_ui" for e in records) == 1
    changed = {"version": 1, "product": {"id": "foreign"}}
    assert (await collect(agui.runner, run_input(run_id="r3", state=changed), agui.trusted))[-1][
        "type"
    ] == "RUN_ERROR"


async def test_incremental_chunks_at_same_offset(agui):
    async def process(session, agent_id, *, run_id):
        event = agui.events.add(session, data={"message": "", "chunks": ["Hello"]})
        await asyncio.sleep(0.12)
        event.data["chunks"].extend([" world", None])
        event.data["message"] = "Hello world"

    agui.processing.process.side_effect = process
    output = await collect(agui.runner, run_input(), agui.trusted)
    assert [e["delta"] for e in output if e["type"] == "TEXT_MESSAGE_CONTENT"] == [
        "Hello",
        " world",
    ]
    assert sum(e["type"] == "TEXT_MESSAGE_END" for e in output) == 1


async def test_frontend_round_trip_updates_state_before_agent_continues(agui):
    receipt = None
    continued = asyncio.Event()

    async def process(session, agent_id, *, run_id):
        nonlocal receipt
        binding = await agui.runner.conversations.bindings.repository.find_by_thread("t1")
        active = await agui.runner.contexts.resolve(
            SimpleNamespace(
                session_id=session, agent_id=agent_id, customer_id=binding.parlant_customer_id
            )
        )
        receipt = await FrontendToolAuthorizationPolicy(agui.trusted.scope).dispatch(
            active.authorization,
            FrontendToolCall(
                call_id="call1",
                thread_id="t1",
                name="select_variant",
                arguments={"product_id": "p1", "variant_id": "v1"},
            ),
            active.dispatcher,
        )
        assert (await agui.runner.states.get("t1")).product.variant_id == "v1"
        assert any(e.source == "customer_ui" for e in agui.events.records[session])
        continued.set()

    agui.processing.process.side_effect = process
    output = []
    async with aclosing(agui.runner.run(run_input(), agui.trusted)) as stream:
        async for event in stream:
            output.append(event.type.value)
            if event.type == "TOOL_CALL_END":
                foreign = replace(
                    agui.trusted,
                    identity=StorefrontIdentity(visitor_id="other", status="anonymous"),
                )
                data = ToolResultInput(
                    thread_id="t1",
                    run_id="r1",
                    result=FrontendToolResult(
                        call_id="call1", status="success", resulting_state_version=1
                    ),
                    state={"version": 1, "product": {"id": "p1", "variant_id": "v1"}},
                )
                with pytest.raises(AuthorizationError):
                    await agui.runner.handle_tool_result(data, foreign)
                assert await agui.runner.handle_tool_result(data, agui.trusted)
                assert not await agui.runner.handle_tool_result(data, agui.trusted)
    assert continued.is_set() and receipt.status == "success"
    assert output == [
        "RUN_STARTED",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "STATE_SNAPSHOT",
        "RUN_FINISHED",
    ]
    assert not agui.runner._active and not agui.runner.contexts._active


@pytest.mark.parametrize("method", ["cancel", "timeout", "close_generator", "shutdown"])
async def test_cancellation_awaits_processing_and_releases_context(agui, method):
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def process(session, agent_id, *, run_id):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    agui.processing.process.side_effect = process
    if method == "timeout":
        agui.runner.run_timeout = 0.15
    if method == "close_generator":

        async def with_pause():
            async with aclosing(agui.runner.run(run_input(), agui.trusted)) as stream:
                async for event in stream:
                    if event.type == "TEXT_MESSAGE_CONTENT":
                        break

        async def emitting(session, agent_id, *, run_id):
            agui.events.add(session, data={"message": "Partial"})
            await process(session, agent_id, run_id=run_id)

        agui.processing.process.side_effect = emitting
        task = asyncio.create_task(with_pause())
    else:
        task = asyncio.create_task(collect(agui.runner, run_input(), agui.trusted))
    await started.wait()
    if method == "cancel":
        task.cancel()
    elif method == "shutdown":
        await agui.runner.close()
    if method in {"cancel", "shutdown"}:
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        if method == "timeout":
            assert result[-1]["type"] == "RUN_ERROR"
    assert cleaned.is_set()
    assert (
        not agui.runner._active and not agui.runner._reserved and not agui.runner.contexts._active
    )


async def test_concurrent_run_is_rejected_before_identity_mutation(agui):
    agui.processing.process.side_effect = lambda *a, **kw: None
    stream = agui.runner.run(run_input(), agui.trusted)
    await anext(stream)
    other = agui.runner.run(run_input(run_id="r2"), agui.trusted)
    with pytest.raises(AppError) as exc:
        await anext(other)
    assert exc.value.http_status == 409
    await stream.aclose()
    assert not agui.runner._reserved


async def test_sdk_failure_is_sanitized(agui):
    agui.processing.process.side_effect = RuntimeError("password=secret-token")
    result = await collect(agui.runner, run_input(), agui.trusted)
    assert result[-1]["type"] == "RUN_ERROR"
    assert "secret-token" not in str(result)


async def test_run_trace_reaches_saleor_and_rag_without_context_leak(agui, monkeypatch):
    import httpx
    from pydantic import BaseModel, SecretStr
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from agent_adapter_service.observability.tracing import traces
    from agent_adapter_service.observability.logging import get_context
    from agent_adapter_service.saleor.client import SaleorGraphQLClient
    from agent_adapter_service.mcp.clients.rag import RagMcpClient, RagMcpConfig
    from unittest.mock import AsyncMock

    exporter, provider = InMemorySpanExporter(), TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(traces, "tracer", provider.get_tracer("test"))
    sdk = SimpleNamespace(
        __aenter__=AsyncMock(), __aexit__=AsyncMock(),
        call_tool=AsyncMock(return_value=SimpleNamespace(is_error=False)),
    )
    class Response(BaseModel):
        count: int = 1

    operation = AsyncMock(return_value=Response())
    previous = agui.processing.process.side_effect
    async with httpx.AsyncClient() as http, RagMcpClient(RagMcpConfig(), client=sdk) as rag:
        saleor = SaleorGraphQLClient("http://saleor.invalid", SecretStr("secret"), http_client=http)
        async def process(session_id, agent_id, *, run_id):
            assert get_context()["run_id"] == run_id
            await saleor.call(operation)
            await rag.call_tool("list_collections", {})
            await previous(session_id, agent_id, run_id=run_id)
        agui.processing.process.side_effect = process
        events = await collect(agui.runner, run_input(), agui.trusted)
    assert events[-1]["type"] == "RUN_FINISHED"
    spans = exporter.get_finished_spans()
    run = next(s for s in spans if s.name == "agui.run")
    children = [s for s in spans if s.name in {"saleor.graphql", "mcp.rag_tool"}]
    assert len(children) == 2
    assert all(s.context.trace_id == run.context.trace_id for s in children)
    assert all(s.attributes["run_id"] == "r1" for s in children)
    trace_id = format(run.context.trace_id, "032x")
    assert operation.call_args.kwargs["headers"]["traceparent"].split("-")[1] == trace_id
    assert sdk.call_tool.call_args.kwargs["meta"]["traceparent"].split("-")[1] == trace_id
    assert get_context() == {}
    provider.shutdown()

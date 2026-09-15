import asyncio
import json

import pytest
from pydantic import ValidationError

from agent_adapter_service.core.types import RiskLevel
from agent_adapter_service.frontend.state.manager import FrontendStateManager
from agent_adapter_service.frontend.tools.dispatcher import FrontendToolDispatcher
from agent_adapter_service.frontend.tools.models import (
    FrontendToolCall,
    FrontendToolError,
    FrontendToolResult,
)
from agent_adapter_service.frontend.tools.policy import FrontendToolPolicy, ToolDecision
from agent_adapter_service.persistence.contracts import StoreScope


class Sink:
    def __init__(self):
        self.events = []
        self.sent = asyncio.Event()

    async def send(self, thread_id, event):
        self.events.append((thread_id, event))
        if event["type"] == "TOOL_CALL_END":
            self.sent.set()


def setup(**kwargs):
    states = FrontendStateManager(StoreScope(tenant_id="tenant", store_id="store"))
    sink = Sink()
    return states, sink, FrontendToolDispatcher(states, sink, **kwargs)


def call(**kwargs):
    return FrontendToolCall(thread_id="t", name="open_cart", **kwargs)


async def start(dispatcher, sink, tool_call):
    task = asyncio.create_task(dispatcher.dispatch(tool_call))
    await asyncio.wait_for(sink.sent.wait(), 1)
    return task


async def test_event_sequence_correlates_result_and_updates_state_once():
    states, sink, dispatcher = setup()
    await states.update("t", {"version": 1})
    tool_call = FrontendToolCall(
        thread_id="t", name="select_variant", arguments={"product_id": "p", "variant_id": "v"}
    )
    task = await start(dispatcher, sink, tool_call)
    assert [e[1]["type"] for e in sink.events] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert json.loads(sink.events[1][1]["delta"]) == tool_call.arguments
    assert all(e[0] == "t" and e[1]["toolCallId"] == tool_call.call_id for e in sink.events)
    result = FrontendToolResult(
        call_id=tool_call.call_id, status="success", resulting_state_version=2
    )
    delta = {
        "base_version": 1,
        "version": 2,
        "changes": {"product": {"id": "p", "variant_id": "v"}},
    }
    assert not await dispatcher.resolve_result("foreign", result, state_or_delta=delta)
    accepted = await asyncio.gather(
        dispatcher.resolve_result("t", result, state_or_delta=delta),
        dispatcher.resolve_result("t", result, state_or_delta=delta),
    )
    assert accepted.count(True) == 1
    assert (await task).resulting_state_version == 2
    assert (await states.get("t")).product.variant_id == "v"
    assert not await dispatcher.resolve_result("t", result, state_or_delta=delta)
    with pytest.raises(FrontendToolError):
        await dispatcher.dispatch(tool_call)


@pytest.mark.parametrize(
    "name,args,status",
    [
        ("submit_payment", {}, "denied"),
        ("place_order", {}, "denied"),
        ("delete_account", {}, "denied"),
        ("unknown", {}, "denied"),
        ("remove_from_cart", {"line_id": "line"}, "confirmation_required"),
        ("change_quantity", {"line_id": "line", "quantity": 2}, "confirmation_required"),
        ("add_to_cart", {"product_id": "p", "variant_id": "v"}, "confirmation_required"),
        ("navigate", {"pathname": "https://evil.example"}, "error"),
        ("navigate", {"pathname": "/?token=secret"}, "error"),
        ("open_cart", {"token": "secret"}, "error"),
        ("change_quantity", {"line_id": "l", "quantity": True}, "error"),
    ],
)
async def test_policy_and_validation_prevent_sending(name, args, status):
    _, sink, dispatcher = setup()
    result = await dispatcher.dispatch(FrontendToolCall(thread_id="t", name=name, arguments=args))
    assert result.status == status
    assert sink.events == []
    assert "secret" not in result.model_dump_json()


async def test_confirmation_and_risk_cannot_be_downgraded():
    policy = FrontendToolPolicy()
    assert policy.evaluate("change_quantity", RiskLevel.LOW) == ToolDecision.CONFIRM
    assert policy.evaluate("navigate", RiskLevel.HIGH) == ToolDecision.CONFIRM
    assert policy.evaluate("place_order", RiskLevel.LOW) == ToolDecision.DENY
    _, sink, dispatcher = setup()
    tool_call = FrontendToolCall(
        thread_id="t", name="change_quantity", arguments={"line_id": "l", "quantity": 2}
    )
    task = asyncio.create_task(dispatcher.dispatch(tool_call, confirmed=True))
    await asyncio.wait_for(sink.sent.wait(), 1)
    assert await dispatcher.resolve_result(
        "t", FrontendToolResult(call_id=tool_call.call_id, status="success")
    )
    assert (await task).status == "success"
    assert (
        await dispatcher.dispatch(
            FrontendToolCall(thread_id="t", name="place_order"), confirmed=True
        )
    ).status == "denied"


async def test_timeout_late_results_and_cancellation_cleanup():
    states, sink, dispatcher = setup(timeout=0.02)
    tool_call = call()
    result = await dispatcher.dispatch(tool_call)
    assert result.status == "timeout"
    assert not await dispatcher.resolve_result(
        "t", FrontendToolResult(call_id=tool_call.call_id, status="success")
    )
    assert await states.get("t") is None
    sink.sent.clear()
    dispatcher.timeout = 5
    task = await start(dispatcher, sink, call())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not dispatcher._pending


async def test_disconnect_unblocks_stuck_sender_and_close_rejects_new_calls():
    _, _, dispatcher = setup(timeout=5)
    entered = asyncio.Event()

    class StuckSink:
        async def send(self, thread_id, event):
            entered.set()
            await asyncio.Event().wait()

    dispatcher.sink = StuckSink()
    task = asyncio.create_task(dispatcher.dispatch(call()))
    await asyncio.wait_for(entered.wait(), 1)
    await dispatcher.disconnect("t")
    assert (await asyncio.wait_for(task, 1)).status == "disconnected"
    await dispatcher.close()
    assert (await dispatcher.dispatch(call())).status == "disconnected"


async def test_sender_error_is_sanitized():
    _, _, dispatcher = setup()

    class BrokenSink:
        async def send(self, thread_id, event):
            raise RuntimeError("private-token")

    dispatcher.sink = BrokenSink()
    result = await dispatcher.dispatch(call())
    assert result.error == "send_failed"
    assert "private-token" not in result.model_dump_json()
    assert not dispatcher._pending


@pytest.mark.parametrize(
    "delta,error",
    [
        ({"base_version": 0, "version": 2, "changes": {}}, "state_conflict"),
        ({"base_version": 1, "version": 2, "changes": {"token": {}}}, "invalid_state"),
    ],
)
async def test_bad_state_completes_with_failure_without_mutation(delta, error):
    states, sink, dispatcher = setup()
    await states.update("t", {"version": 1})
    tool_call = call()
    task = await start(dispatcher, sink, tool_call)
    result = FrontendToolResult(
        call_id=tool_call.call_id, status="success", resulting_state_version=2
    )
    assert await dispatcher.resolve_result("t", result, state_or_delta=delta)
    assert (await task).error == error
    assert (await states.get("t")).version == 1


async def test_result_validation_allows_retry_after_rejection():
    _, sink, dispatcher = setup()
    tool_call = call()
    task = await start(dispatcher, sink, tool_call)
    result = FrontendToolResult(
        call_id=tool_call.call_id, status="success", resulting_state_version=2
    )
    with pytest.raises(FrontendToolError):
        await dispatcher.resolve_result("t", result, state_or_delta={"version": 3})
    with pytest.raises(FrontendToolError):
        await dispatcher.resolve_result("t", result)
    assert await dispatcher.resolve_result(
        "t", FrontendToolResult(call_id=tool_call.call_id, status="error")
    )
    assert (await task).error == "execution_failed"
    with pytest.raises(ValidationError):
        FrontendToolResult(call_id="id", status="success", data={"token": "secret"})


async def test_close_cancels_blocked_state_update():
    states, sink, dispatcher = setup()
    entered = asyncio.Event()

    class SlowStore:
        async def update(self, thread_id, value):
            entered.set()
            await asyncio.Event().wait()
            return await states.update(thread_id, value)

    dispatcher.states = SlowStore()
    tool_call = call()
    task = await start(dispatcher, sink, tool_call)
    resolve = asyncio.create_task(
        dispatcher.resolve_result(
            "t",
            FrontendToolResult(
                call_id=tool_call.call_id,
                status="success",
                resulting_state_version=1,
            ),
            state_or_delta={"version": 1},
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    await dispatcher.close()
    assert (await task).status == "disconnected"
    assert not await resolve
    assert await states.get("t") is None


async def test_capacity_and_payload_bounds():
    _, _, dispatcher = setup(max_calls=1, max_payload_bytes=100)
    result = await dispatcher.dispatch(
        FrontendToolCall(thread_id="t", name="navigate", arguments={"pathname": "/" + "x" * 200})
    )
    assert result.error == "invalid_arguments"
    with pytest.raises(FrontendToolError):
        await dispatcher.dispatch(call())

from unittest.mock import AsyncMock

import pytest

from agent_adapter_service.conversations.models import ConversationBinding
from agent_adapter_service.frontend.events.handler import FrontendEventHandler
from agent_adapter_service.frontend.events.models import FrontendEventType
from agent_adapter_service.frontend.state.models import StateVersionConflict
from agent_adapter_service.frontend.state.reducer import FrontendStateReducer


def binding():
    return ConversationBinding(
        thread_id="t",
        parlant_session_id="session",
        parlant_customer_id="customer",
        agent_id="agent",
    )


async def test_semantic_events_use_bound_session_without_processing():
    gateway = AsyncMock()
    handler = FrontendEventHandler(gateway)
    reducer = FrontendStateReducer()
    state = reducer.apply_snapshot(
        None,
        {
            "version": 1,
            "route": {"pathname": "/products/jacket", "page_type": "product"},
            "product": {"id": "p", "variant_id": "v"},
            "cart": {"item_count": 2},
            "checkout": {"step": "shipping"},
            "store": {"channel": "us"},
        },
    )
    events = await handler.handle_state_change(binding(), None, state)
    assert {e.event_type for e in events} == {
        FrontendEventType.PAGE_CHANGED,
        FrontendEventType.PRODUCT_OPENED,
        FrontendEventType.CART_CHANGED,
        FrontendEventType.CHECKOUT_STEP_CHANGED,
        FrontendEventType.STORE_CHANGED,
    }
    for call in gateway.create.call_args_list:
        assert call.args[0] == "session"
        assert call.kwargs["trigger_processing"] is False
        assert call.kwargs["metadata"]["source_revision"] == 1
    next_state = reducer.apply_delta(
        state,
        {
            "base_version": 1,
            "version": 2,
            "changes": {"product": {"variant_id": "v2"}},
        },
    )
    events = await handler.handle_state_change(
        binding(), state, next_state, trigger_processing=True
    )
    assert [e.event_type for e in events] == [FrontendEventType.VARIANT_CHANGED]
    assert gateway.create.call_args.kwargs["trigger_processing"] is True


async def test_noise_and_stale_revision():
    gateway = AsyncMock()
    handler = FrontendEventHandler(gateway)
    reducer = FrontendStateReducer()
    first = reducer.apply_snapshot(None, {"version": 1})
    second = reducer.apply_delta(
        first,
        {
            "base_version": 1,
            "version": 2,
            "changes": {
                "ui": {"chat_open": True},
                "cart": {"is_open": True},
                "user": {"is_logged_in": True},
            },
        },
    )
    assert await handler.handle_state_change(binding(), first, second) == []
    gateway.create.assert_not_called()
    with pytest.raises(StateVersionConflict):
        handler.diff(second, first)


async def test_checkout_clear_and_gateway_failure():
    gateway = AsyncMock()
    handler = FrontendEventHandler(gateway)
    reducer = FrontendStateReducer()
    first = reducer.apply_snapshot(None, {"version": 1, "checkout": {"step": "review"}})
    second = reducer.apply_snapshot(first, {"version": 2})
    assert handler.diff(first, second)[0].data == {"step": None}
    gateway.create.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        await handler.handle_state_change(binding(), first, second)

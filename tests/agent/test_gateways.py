import asyncio
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_adapter_service.agent.parlant.common import GatewayContext
from agent_adapter_service.agent.parlant.customers import ParlantCustomerGateway
from agent_adapter_service.agent.parlant.events import ParlantEventGateway
from agent_adapter_service.agent.parlant.frontend_events import ParlantFrontendEventGateway
from agent_adapter_service.agent.parlant.messages import ParlantMessageGateway
from agent_adapter_service.agent.parlant.sessions import ParlantSessionGateway
from agent_adapter_service.core.exceptions import IntegrationError


class Source(Enum):
    CUSTOMER = "customer"
    CUSTOMER_UI = "customer_ui"
    AI_AGENT = "ai_agent"


class Kind(Enum):
    MESSAGE = "message"
    CUSTOM = "custom"


@pytest.fixture
def context():
    customer = SimpleNamespace(id="visitor-a", name="Visitor", extra={"visitor_id": "a"})
    session = SimpleNamespace(
        id="session-1", customer_id=customer.id, agent_id="agent-1", title=None
    )
    event = SimpleNamespace(
        id="e1",
        offset=4,
        trace_id="trace-1",
        source=Source.CUSTOMER,
        kind=Kind.MESSAGE,
        data={"message": "hello"},
        metadata={"run_id": "run-1"},
        deleted=False,
    )
    customers = SimpleNamespace(
        read=AsyncMock(return_value=customer),
        create=AsyncMock(return_value=customer),
        update=AsyncMock(return_value=customer),
    )
    sessions = SimpleNamespace(
        read=AsyncMock(return_value=session),
        create=AsyncMock(return_value=session),
        update=AsyncMock(return_value=session),
        create_event=AsyncMock(return_value=event),
        create_customer_message=AsyncMock(return_value=event),
        find_events=AsyncMock(return_value=[event]),
        wait_for_more_events=AsyncMock(return_value=True),
    )
    return GatewayContext(
        SimpleNamespace(customers=customers, sessions=sessions),
        Source,
        Kind,
        SimpleNamespace(AUTO="auto"),
        lambda seconds: seconds,
    )


async def test_customer_ids_extra_and_updates(context):
    gateway = ParlantCustomerGateway(context, SimpleNamespace)
    customer = await gateway.create("Visitor", customer_id="visitor-a", extra={"visitor_id": "a"})
    assert customer.extra == {"visitor_id": "a"}
    context.application.customers.create.assert_awaited_once_with(
        name="Visitor", extra={"visitor_id": "a"}, tags=None, id="visitor-a"
    )
    await gateway.update(customer.id, name="Alice", extra={"saleor_user_id": "u1"})
    params = context.application.customers.update.call_args.kwargs
    assert params["metadata"].set == {"saleor_user_id": "u1"}
    assert (await gateway.get(customer.id)).id == customer.id
    with pytest.raises(ValueError):
        await gateway.create("Shared", customer_id="guest")


async def test_customer_find_only_treats_not_found_as_missing(context):
    common = pytest.importorskip("parlant.core.common")
    gateway = ParlantCustomerGateway(context, SimpleNamespace)
    assert (await gateway.find("visitor-a")).id == "visitor-a"
    context.application.customers.read.side_effect = common.ItemNotFoundError(
        item_id=common.UniqueId("missing")
    )
    assert await gateway.find("missing") is None
    context.application.customers.read.side_effect = RuntimeError("private-token")
    with pytest.raises(IntegrationError, match="Parlant operation failed"):
        await gateway.find("visitor-a")


async def test_session_rebinding_preserves_id(context):
    gateway = ParlantSessionGateway(context)
    session = await gateway.create("agent-1", "visitor-a")
    assert (await gateway.get(session.id)).id == session.id
    rebound = await gateway.update_customer(session.id, "registered-b")
    assert rebound.id == session.id
    context.application.customers.read.assert_awaited_with("registered-b")
    context.application.sessions.update.assert_awaited_once_with(
        session_id=session.id, params={"customer_id": "registered-b"}
    )
    await gateway.update(session.id, title="Shopping", mode="manual")
    assert context.application.sessions.update.call_args.kwargs["params"] == {
        "title": "Shopping",
        "mode": "manual",
    }
    with pytest.raises(ValueError):
        await gateway.create("agent-1", "guest")


async def test_message_metadata_and_ui_default(context):
    message = await ParlantMessageGateway(context).create_customer_message(
        "s1", "hello", trigger_processing=False, metadata={"run_id": "r1"}
    )
    assert message.trace_id == "trace-1"
    params = context.application.sessions.create_customer_message.call_args.kwargs
    assert params["metadata"] == {"run_id": "r1"}
    assert params["source"] is Source.CUSTOMER
    assert params["trigger_processing"] is False
    await ParlantFrontendEventGateway(context).create("s1", "PAGE_CHANGED", {"path": "/products"})
    params = context.application.sessions.create_event.call_args.kwargs
    assert params["source"] is Source.CUSTOMER_UI and params["kind"] is Kind.CUSTOM
    assert params["trigger_processing"] is False
    assert params["data"] == {"type": "PAGE_CHANGED", "data": {"path": "/products"}}


async def test_wait_filters_timeout_and_deleted_events(context):
    gateway = ParlantEventGateway(context)
    assert (
        len(
            await gateway.wait(
                "s1",
                min_offset=4,
                trace_id="trace-1",
                source="ai_agent",
                kinds=("message",),
                timeout_seconds=1,
            )
        )
        == 1
    )
    context.application.sessions.wait_for_more_events.assert_awaited_once_with(
        session_id="s1",
        min_offset=4,
        trace_id="trace-1",
        source=Source.AI_AGENT,
        kinds=[Kind.MESSAGE],
        timeout=1,
    )
    context.application.sessions.wait_for_more_events.return_value = False
    assert await gateway.wait("s1") == []
    assert context.application.sessions.find_events.await_count == 1
    context.application.sessions.find_events.return_value[0].deleted = True
    assert await gateway.list("s1") == []
    with pytest.raises(ValueError):
        await gateway.wait("s1", timeout_seconds=float("nan"))
    with pytest.raises(ValueError):
        await gateway.list("s1", min_offset=-1)


async def test_sdk_error_redaction_and_cancellation(context):
    gateway = ParlantSessionGateway(context)
    context.application.sessions.read.side_effect = RuntimeError("private-key")
    with pytest.raises(IntegrationError) as error:
        await gateway.get("s1")
    assert "private-key" not in str(error.value)
    assert error.value.details["operation"] == "get_session"
    context.application.sessions.read.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await gateway.get("s1")

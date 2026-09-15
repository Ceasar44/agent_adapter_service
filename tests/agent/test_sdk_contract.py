"""Optional signature checks against the pinned SDK, without serving or calling a model."""

# ruff: noqa: E402 - the optional SDK import gate must precede SDK imports

from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest

sdk = pytest.importorskip("parlant.sdk")

from parlant.core.agents import AgentStore
from parlant.core.app_modules.customers import CustomerMetadataUpdateParams, CustomerModule
from parlant.core.app_modules.sessions import Moderation, SessionModule
from parlant.core.async_utils import Timeout
from parlant.core.sessions import EventKind, EventSource

from agent_adapter_service.agent.bootstrap import AgentBootstrapper
from agent_adapter_service.agent.contracts import RegisteredTool, StaticToolRegistry
from agent_adapter_service.agent.parlant.agent import ParlantAgentGateway
from agent_adapter_service.agent.parlant.common import GatewayContext
from agent_adapter_service.agent.parlant.customers import ParlantCustomerGateway
from agent_adapter_service.agent.parlant.events import ParlantEventGateway
from agent_adapter_service.agent.parlant.frontend_events import ParlantFrontendEventGateway
from agent_adapter_service.agent.parlant.messages import ParlantMessageGateway
from agent_adapter_service.agent.parlant.sessions import ParlantSessionGateway


async def test_sdk_application_signatures():
    customers = create_autospec(CustomerModule, instance=True)
    sessions = create_autospec(SessionModule, instance=True)
    customer = SimpleNamespace(id="c1", name="Visitor", extra={})
    session = SimpleNamespace(id="s1", customer_id="c1", agent_id="a1", title=None)
    event = SimpleNamespace(
        id="e1",
        offset=0,
        trace_id="t1",
        source=EventSource.CUSTOMER,
        kind=EventKind.MESSAGE,
        data={},
        metadata={},
        deleted=False,
    )
    for method in (customers.create, customers.read, customers.update):
        method.return_value = customer
    for method in (sessions.create, sessions.read, sessions.update):
        method.return_value = session
    sessions.create_customer_message.return_value = event
    sessions.create_event.return_value = event
    sessions.find_events.return_value = [event]
    sessions.wait_for_more_events.return_value = True
    context = GatewayContext(
        SimpleNamespace(customers=customers, sessions=sessions),
        EventSource,
        EventKind,
        Moderation,
        Timeout,
    )
    cg = ParlantCustomerGateway(context, CustomerMetadataUpdateParams)
    assert (await cg.create("Visitor", customer_id="c1")).id == "c1"
    await cg.get("c1")
    assert (await cg.find("c1")).id == "c1"
    await cg.update("c1", name="Alice", extra={"user_id": "u1"})
    sg = ParlantSessionGateway(context)
    await sg.create("a1", "c1")
    await sg.get("s1")
    await sg.update("s1", title="Shopping")
    await sg.update_customer("s1", "c2")
    await ParlantMessageGateway(context).create_customer_message("s1", "Hello")
    await ParlantFrontendEventGateway(context).create("s1", "PAGE_CHANGED", {})
    await ParlantEventGateway(context).wait("s1", min_offset=0, kinds=("message",))


async def test_owned_processing_gateway_sdk_signatures():
    from parlant.core.emissions import EventEmitterFactory
    from parlant.core.engines.types import Context, Engine
    from parlant.core.tracer import LocalTracer
    from agent_adapter_service.agent.parlant.processing import ParlantProcessingGateway

    engine = create_autospec(Engine, instance=True)
    emitters = create_autospec(EventEmitterFactory, instance=True)
    gateway = ParlantProcessingGateway(engine, emitters, LocalTracer(), Context)
    await gateway.process("session", "agent", run_id="run")
    engine.process.assert_awaited_once_with(
        Context(session_id="session", agent_id="agent"),
        event_emitter=emitters.create_event_emitter.return_value,
    )


async def test_sdk_bootstrap_signatures(loader, sdk_server):
    # Autospec evaluates class descriptors. This context-only property requires a
    # running host; mask just the property while retaining all real method signatures.
    with patch.object(sdk.Server, "current", None):
        server = create_autospec(sdk.Server, instance=True)
    with patch.object(sdk.Agent, "current", None):
        agent = create_autospec(sdk.Agent, instance=True)
    agent.id = "customer_service"
    agent.name = "Assistant"
    agent.description = "Shopping"
    server.find_agent.return_value = None
    server.create_agent.return_value = agent
    server.get_agent.return_value = agent
    store = create_autospec(AgentStore, instance=True)
    # Real signatures validate keyword arguments; deterministic fake entities avoid model calls.
    agent.create_term.side_effect = sdk_server.agent.create_term
    agent.create_guideline.side_effect = sdk_server.agent.create_guideline
    agent.create_journey.side_effect = sdk_server.agent.create_journey
    gateway = ParlantAgentGateway(server, sdk, store)
    registry = StaticToolRegistry({"search_products": RegisteredTool(object())})
    result = await AgentBootstrapper(loader, gateway, registry).bootstrap()
    assert result.id == "customer_service"

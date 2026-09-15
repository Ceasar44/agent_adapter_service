from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_adapter_service.agent.contracts import AgentRecord
from agent_adapter_service.agent.parlant.processing import ParlantProcessingGateway
from agent_adapter_service.agent.runtime import AgentRuntime
from agent_adapter_service.agent.tools.common import ToolDependencies
from agent_adapter_service.agent.tools.registry import AgentToolRegistry
from agent_adapter_service.app.agui import prepare_agui_tools, assemble_agui
from agent_adapter_service.app.lifespan import AppResources
from agent_adapter_service.core.exceptions import IntegrationError
from agent_adapter_service.core.settings import Settings
from agent_adapter_service.security.customer_policy import CustomerToolPolicy
from agent_adapter_service.security.frontend_tool_policy import FrontendToolAuthorizationPolicy
from tests.agui.conftest import collect, run_input


async def test_configured_default_factory_assembles_auth_and_runner(agui):
    from contextlib import asynccontextmanager
    from agent_adapter_service.app.factory import create_app
    from tests.security.test_storefront_bff import VECTOR

    settings = Settings(
        _env_file=None, agui_enabled=True, database_enabled=True, parlant_enabled=True,
        database_url="sqlite+aiosqlite:///:memory:", openai_api_key="test",
        agui_tenant_id="tenant", agui_store_id="store", bff_issuer="storefront",
        bff_audience="adapter", bff_keys={"active": VECTOR["key"]},
    )

    @asynccontextmanager
    async def database(config, resources):
        yield agui.harness.database

    @asynccontextmanager
    async def parlant(config, resources):
        assert resources.agent_tool_registry is not None
        runtime = AgentRuntime(config)
        runtime.host = SimpleNamespace(
            customers=agui.harness.customers, sessions=agui.harness.sessions,
            messages=agui.events, events=agui.events, frontend_events=agui.events,
            processing=agui.processing,
        )
        runtime.get_agent = lambda: AgentRecord(id="agent", name="Agent", description="Test")
        yield runtime

    app = create_app(settings, resource_factories={"database": database, "parlant_runtime": parlant})
    async with app.router.lifespan_context(app):
        assert app.state.storefront_bff_verifier is not None
        assert app.state.resources.services["agui_runner"] is not None
        import httpx
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/health/ready")).status_code == 200
    assert app.state.storefront_bff_verifier is None


async def test_assembly_reuses_registry_states_and_contexts(agui):
    settings = Settings(_env_file=None)
    resources = AppResources(settings, database=agui.harness.database)
    dependencies = ToolDependencies(
        scope=agui.trusted.scope,
        contexts=agui.runner.contexts,
        states=agui.runner.states,
        customer_policy=CustomerToolPolicy(agui.trusted.scope),
        frontend_policy=FrontendToolAuthorizationPolicy(agui.trusted.scope),
    )
    resources.agent_tool_registry = AgentToolRegistry(dependencies)
    prepare_agui_tools(resources, agui.trusted.scope)
    runtime = AgentRuntime(settings)
    runtime.host = SimpleNamespace(
        customers=agui.harness.customers,
        sessions=agui.harness.sessions,
        messages=agui.events,
        events=agui.events,
        frontend_events=agui.events,
        processing=agui.processing,
    )
    runtime.get_agent = lambda: AgentRecord(id="agent", name="Agent", description="Test")
    resources.parlant_runtime = runtime
    runner = assemble_agui(resources, agui.trusted.scope)
    assert runner.contexts is dependencies.contexts and runner.states is dependencies.states
    result = await collect(runner, run_input(), agui.trusted)
    assert result[-1]["type"] == "RUN_FINISHED"
    assert resources.services["agui_runner"] is runner
    await runner.close()


async def test_processing_gateway_uses_owned_engine_task():
    spans = []

    @contextmanager
    def span(name, attributes):
        spans.append((name, attributes))
        yield

    engine = SimpleNamespace(process=AsyncMock(return_value=True))
    emitter = object()
    emitters = SimpleNamespace(create_event_emitter=AsyncMock(return_value=emitter))
    gateway = ParlantProcessingGateway(
        engine, emitters, SimpleNamespace(span=span), SimpleNamespace
    )
    await gateway.process("s1", "a1", run_id="r1")
    emitters.create_event_emitter.assert_awaited_once_with(emitting_agent_id="a1", session_id="s1")
    context = engine.process.call_args.args[0]
    assert context.session_id == "s1" and context.agent_id == "a1"
    assert spans[0][1]["agui.run_id"] == "r1"
    engine.process.return_value = False
    with pytest.raises(IntegrationError):
        await gateway.process("s1", "a1", run_id="r1")

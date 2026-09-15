import asyncio

import pytest

from agent_adapter_service.agent.bootstrap import AgentBootstrapper, validate_registry
from agent_adapter_service.agent.config.loader import ConfigError
from agent_adapter_service.agent.contracts import RegisteredTool, StaticToolRegistry
from agent_adapter_service.agent.parlant.agent import ParlantAgentGateway
from agent_adapter_service.core.exceptions import IntegrationError


async def test_bootstrap_idempotency_order_and_retry(loader, config, sdk_server):
    gateway = ParlantAgentGateway(sdk_server, sdk_server.sdk, sdk_server.store)
    reference = object()

    class Registry(StaticToolRegistry):
        async def register(self, tools):
            sdk_server.calls.append(("tools",))
            return await super().register(tools)

    bootstrap = AgentBootstrapper(
        loader, gateway, Registry({"search_products": RegisteredTool(reference)})
    )
    sdk_server.agent.create_journey.side_effect, original = (
        RuntimeError("private-key"),
        sdk_server.agent.create_journey.side_effect,
    )
    with pytest.raises(IntegrationError):
        await bootstrap.bootstrap()
    sdk_server.agent.create_journey.side_effect = original
    first, second = await asyncio.gather(bootstrap.bootstrap(), bootstrap.bootstrap())
    assert first.id == second.id == "customer_service"
    assert sdk_server.create_agent.await_count == 1
    assert len(sdk_server.entities) == 4  # term, guideline, trigger guideline, journey
    kinds = [call[0] for call in sdk_server.calls]
    assert (
        kinds.index("term")
        < kinds.index("tools")
        < kinds.index("guideline")
        < kinds.index("journey")
    )
    assert kinds.count("edge") == 4
    assert sdk_server.agent.create_guideline.call_args_list[0].kwargs["tools"] == [reference]
    changed = config.model_copy(
        update={"agent": config.agent.model_copy(update={"name": "Changed"})}
    )
    with pytest.raises(ConfigError, match="restart"):
        await bootstrap.bootstrap(changed)


@pytest.mark.parametrize(
    "entry", [RegisteredTool(object(), customer_allowed=False), RegisteredTool(object())]
)
def test_runtime_tool_policy_required(config, entry):
    config = config.model_copy(
        update={"tools": (config.tools[0].model_copy(update={"confirmation": "confirm"}),)}
    )
    with pytest.raises(ConfigError):
        validate_registry(config, StaticToolRegistry({"search_products": entry}))


async def test_validation_precedes_sdk_writes(loader, sdk_server):
    gateway = ParlantAgentGateway(sdk_server, sdk_server.sdk, sdk_server.store)
    with pytest.raises(ConfigError):
        await AgentBootstrapper(loader, gateway, StaticToolRegistry()).bootstrap()
    assert sdk_server.calls == []

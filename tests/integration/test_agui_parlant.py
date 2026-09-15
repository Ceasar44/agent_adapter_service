from agent_adapter_service.agent.bootstrap import AgentBootstrapper
from agent_adapter_service.agent.config.loader import AgentConfigLoader
from agent_adapter_service.agent.parlant.agent import ParlantAgentGateway
from scripts._common import catalog_registry
from tests.agui.conftest import run_input
from tests.support.flows import storefront_run


async def test_reviewed_configuration_bootstraps_all_entities(sdk_server):
    registry = catalog_registry()
    gateway = ParlantAgentGateway(sdk_server, sdk_server.sdk, sdk_server.store)
    loader = AgentConfigLoader("configs/agents/customer_service")
    bootstrapper = AgentBootstrapper(loader, gateway, registry)
    await bootstrapper.bootstrap()
    await bootstrapper.bootstrap()
    config = loader.load()
    assert set(registry.dependencies.enabled) == {tool.name for tool in config.tools}
    assert len([key for key in sdk_server.entities if key[0] == "guideline"]) == (
        len(config.guidelines) + len(config.journeys)
    )
    assert len([key for key in sdk_server.entities if key[0] == "journey"]) == 3


async def test_http_history_replay_does_not_repeat_processing(agui):
    first = await storefront_run(agui, run_input())
    second = await storefront_run(agui, run_input(run_id="retry"))
    assert first[-1]["type"] == second[-1]["type"] == "RUN_FINISHED"
    assert len(agui.events.writes) == 1
    assert agui.processing.process.await_count == 1

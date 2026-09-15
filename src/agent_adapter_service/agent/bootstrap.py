import asyncio

from agent_adapter_service.agent.config.loader import AgentConfigLoader, ConfigError
from agent_adapter_service.agent.config.models import CustomerServiceConfig
from agent_adapter_service.agent.config.validator import AgentConfigValidator
from agent_adapter_service.agent.contracts import AgentRecord, ConfigurationGateway, ToolRegistry
from agent_adapter_service.agent.factory import CustomerServiceAgentFactory


def validate_registry(config: CustomerServiceConfig, registry: ToolRegistry) -> None:
    available = registry.available_tools
    AgentConfigValidator().validate(config, available)
    for tool in config.tools:
        if not tool.enabled:
            continue
        entry = available[tool.name]
        if not entry.customer_allowed:
            raise ConfigError(f"tools.yaml [{tool.name}]: tool is not customer-facing")
        if tool.confirmation == "confirm" and not entry.confirmation_enforced:
            raise ConfigError(
                f"tools.yaml [{tool.name}]: confirmation must be enforced by the tool"
            )


class AgentBootstrapper:
    def __init__(
        self, loader: AgentConfigLoader, gateway: ConfigurationGateway, registry: ToolRegistry
    ) -> None:
        self.loader = loader
        self.gateway = gateway
        self.registry = registry
        self._lock = asyncio.Lock()

    async def bootstrap(self, config: CustomerServiceConfig | None = None) -> AgentRecord:
        async with self._lock:
            config = config if config is not None else await self.loader.aload()
            validate_registry(config, self.registry)
            agent = await CustomerServiceAgentFactory(self.gateway).create(config)
            await self.gateway.sync_glossary(config)
            tools = await self.registry.register(config.tools)
            await self.gateway.sync_guidelines(config, tools)
            await self.gateway.sync_journeys(config, tools)
            return agent

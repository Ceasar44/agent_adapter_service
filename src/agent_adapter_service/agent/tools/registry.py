"""M03 ToolRegistry implementation. Only the explicit configuration whitelist is active."""

from collections.abc import Mapping
from types import MappingProxyType

from agent_adapter_service.agent.config.loader import ConfigError
from agent_adapter_service.agent.config.models import ToolConfig
from agent_adapter_service.agent.contracts import RegisteredTool
from agent_adapter_service.agent.tools.common import ToolDependencies
from agent_adapter_service.security.customer_policy import CustomerToolPolicy

FRONTEND_TOOLS = frozenset(
    {
        "navigate",
        "open_product",
        "select_variant",
        "open_cart",
        "open_checkout",
        "add_to_cart",
    }
)


class AgentToolRegistry:
    def __init__(self, dependencies: ToolDependencies) -> None:
        self.dependencies = dependencies
        self._tools: dict[str, RegisteredTool] = {}

    def add(self, reference) -> None:
        name = reference.tool.name
        if name in self._tools or name not in CustomerToolPolicy.allowed_tools:
            raise ConfigError(f"Duplicate or forbidden customer tool: {name}")
        self._tools[name] = RegisteredTool(
            reference=reference, confirmation_enforced=name in FRONTEND_TOOLS
        )

    @property
    def available_tools(self) -> Mapping[str, RegisteredTool]:
        return MappingProxyType(self._tools)

    async def register(self, tools: tuple[ToolConfig, ...]) -> Mapping[str, object]:
        seen = set()
        enabled = {}
        for config in tools:
            if config.name in seen:
                raise ConfigError(f"Duplicate tool configuration: {config.name}")
            seen.add(config.name)
            if not config.enabled:
                continue
            if config.name not in self._tools or config.confirmation == "deny":
                raise ConfigError(f"Unknown or forbidden customer tool: {config.name}")
            if config.risk != "low" and config.confirmation != "confirm":
                raise ConfigError(f"Confirmation required for tool: {config.name}")
            if config.confirmation == "confirm" and config.name not in FRONTEND_TOOLS:
                raise ConfigError(f"Tool has no confirmation flow: {config.name}")
            enabled[config.name] = config
        self.dependencies.enabled = enabled
        return {name: self._tools[name].reference for name in enabled}


def register_all(dependencies: ToolDependencies) -> AgentToolRegistry:
    # Optional SDK dependency is imported only when real tools are requested.
    from agent_adapter_service.agent.tools import (
        cart,
        checkout,
        frontend,
        knowledge,
        orders,
        products,
    )

    registry = AgentToolRegistry(dependencies)
    for module in (products, orders, cart, checkout, knowledge, frontend):
        for reference in module.create_tools(dependencies):
            registry.add(reference)
    return registry

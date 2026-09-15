"""Models and small ports consumed by subsequent identity/conversation modules."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from agent_adapter_service.agent.config.models import CustomerServiceConfig, ToolConfig


class Record(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)
    id: str


class AgentRecord(Record):
    name: str
    description: str


class CustomerRecord(Record):
    name: str
    extra: dict[str, str]


class SessionRecord(Record):
    customer_id: str
    agent_id: str
    title: str | None = None


class EventRecord(Record):
    offset: int
    trace_id: str
    source: str
    kind: str
    data: JsonValue
    metadata: dict[str, JsonValue]


@dataclass(frozen=True)
class RegisteredTool:
    """M10 supplies an SDK ToolRef already wrapped in its runtime authorization policy."""

    reference: object
    customer_allowed: bool = True
    confirmation_enforced: bool = False


class ToolRegistry(Protocol):
    @property
    def available_tools(self) -> Mapping[str, RegisteredTool]: ...

    async def register(self, tools: tuple[ToolConfig, ...]) -> Mapping[str, object]: ...


class StaticToolRegistry:
    def __init__(self, tools: Mapping[str, RegisteredTool] | None = None) -> None:
        self._tools = dict(tools or {})

    @property
    def available_tools(self) -> Mapping[str, RegisteredTool]:
        return dict(self._tools)

    async def register(self, tools: tuple[ToolConfig, ...]) -> Mapping[str, object]:
        return {tool.name: self._tools[tool.name].reference for tool in tools if tool.enabled}


class ConfigurationGateway(Protocol):
    async def find_or_create_agent(self, config: CustomerServiceConfig) -> AgentRecord: ...
    async def sync_glossary(self, config: CustomerServiceConfig) -> None: ...
    async def sync_guidelines(
        self, config: CustomerServiceConfig, tools: Mapping[str, object]
    ) -> None: ...
    async def sync_journeys(
        self, config: CustomerServiceConfig, tools: Mapping[str, object]
    ) -> None: ...

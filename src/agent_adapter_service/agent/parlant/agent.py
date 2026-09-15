"""SDK configuration writes, with retry-safe progress for a single runtime configuration."""

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from agent_adapter_service.agent.config.loader import ConfigError
from agent_adapter_service.agent.config.models import CustomerServiceConfig
from agent_adapter_service.agent.contracts import AgentRecord
from agent_adapter_service.agent.parlant.common import sdk_errors


class ParlantAgentGateway:
    def __init__(self, server: Any, sdk: Any, agent_store: Any) -> None:
        self.server = server
        self.sdk = sdk
        self.agent_store = agent_store
        self._lock = asyncio.Lock()
        self._agent: Any = None
        self._config: CustomerServiceConfig | None = None
        self._done: set[str] = set()
        self._journeys: dict[str, Any] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._triggers: dict[str, Any] = {}

    def _id(self, category: str, identifier: str) -> str:
        return uuid5(NAMESPACE_URL, f"{self._agent.id}/{category}/{identifier}").hex

    async def find_or_create_agent(self, config: CustomerServiceConfig) -> AgentRecord:
        async with self._lock:
            if self._config is not None and self._config != config:
                raise ConfigError("Agent configuration changed; restart the runtime to apply it")
            with sdk_errors("find_or_create_agent"):
                agent = config.agent
                description = "\n".join(
                    (agent.description, f"Response language: {agent.language}", *agent.behavior)
                )
                existing = await self.server.find_agent(id=agent.id)
                if existing is None:
                    existing = await self.server.create_agent(
                        id=agent.id,
                        name=agent.name,
                        description=description,
                        max_engine_iterations=agent.max_engine_iterations,
                    )
                else:
                    await self.agent_store.update_agent(
                        agent_id=agent.id,
                        params={
                            "name": agent.name,
                            "description": description,
                            "max_engine_iterations": agent.max_engine_iterations,
                        },
                    )
                    existing = await self.server.get_agent(id=agent.id)
                self._agent = existing
                self._config = config
                return AgentRecord(id=agent.id, name=agent.name, description=description)

    async def sync_glossary(self, config: CustomerServiceConfig) -> None:
        with sdk_errors("sync_glossary"):
            for term in config.glossary:
                key = self._id("glossary", term.id)
                if key not in self._done:
                    await self._agent.create_term(
                        id=key, name=term.name, description=term.description, synonyms=term.synonyms
                    )
                    self._done.add(key)

    async def sync_guidelines(
        self, config: CustomerServiceConfig, tools: Mapping[str, object]
    ) -> None:
        with sdk_errors("sync_guidelines"):
            for guideline in config.guidelines:
                key = self._id("guideline", guideline.id)
                if key not in self._done:
                    await self._agent.create_guideline(
                        id=key,
                        condition=guideline.condition,
                        action=guideline.action,
                        priority=guideline.priority,
                        tools=[tools[name] for name in guideline.tools],
                    )
                    self._done.add(key)

    async def sync_journeys(
        self, config: CustomerServiceConfig, tools: Mapping[str, object]
    ) -> None:
        with sdk_errors("sync_journeys"):
            for spec in config.journeys:
                if spec.id not in self._journeys:
                    # A stable condition ID also avoids duplicate trigger guidelines on retries.
                    trigger_key = self._id("trigger", spec.id)
                    if spec.id not in self._triggers:
                        self._triggers[spec.id] = await self._agent.create_guideline(
                            id=trigger_key, condition=spec.trigger
                        )
                    trigger = self._triggers[spec.id]
                    journey = await self._agent.create_journey(
                        id=self._id("journey", spec.id),
                        title=spec.title,
                        description=spec.description,
                        conditions=[trigger],
                    )
                    self._journeys[spec.id] = journey
                    self._states[spec.id] = {
                        "START": journey.initial_state,
                        "END": self.sdk.END_JOURNEY,
                    }
                journey = self._journeys[spec.id]
                states = self._states[spec.id]
                specs = {state.id: state for state in spec.states}
                pending = list(enumerate(spec.transitions))
                while pending:
                    remaining = []
                    for index, edge in pending:
                        key = self._id("edge", f"{spec.id}/{index}")
                        if key in self._done:
                            continue
                        if edge.source not in states:
                            remaining.append((index, edge))
                            continue
                        if edge.target in states:
                            await states[edge.source].transition_to(
                                state=states[edge.target],
                                condition=edge.condition,
                            )
                        else:
                            state = specs[edge.target]
                            kwargs = (
                                {
                                    "tool_state": [tools[name] for name in state.tools],
                                    "tool_instruction": state.action,
                                }
                                if state.tools
                                else {"chat_state": state.action}
                            )
                            transition = await states[edge.source].transition_to(
                                id=self._id("state", f"{spec.id}/{state.id}"),
                                condition=edge.condition,
                                **kwargs,
                            )
                            states[edge.target] = transition.target
                        self._done.add(key)
                    if len(remaining) == len(pending):
                        raise ConfigError(
                            f"journeys.yaml [{spec.id}]: unreachable transition source"
                        )
                    pending = remaining

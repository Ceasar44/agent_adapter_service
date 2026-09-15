from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from agent_adapter_service.agent.config.loader import AgentConfigLoader
from agent_adapter_service.agent.config.models import CustomerServiceConfig


@pytest.fixture
def config():
    return CustomerServiceConfig.model_validate(
        {
            "agent": {"id": "customer_service", "name": "Assistant", "description": "Shopping"},
            "tools": [{"name": "search_products"}],
            "glossary": [{"id": "variant", "name": "Variant", "description": "A size/color"}],
            "guidelines": [
                {
                    "id": "search",
                    "condition": "Shopping",
                    "action": "Find products",
                    "tools": ["search_products"],
                    "priority": 2,
                }
            ],
            "journeys": [
                {
                    "id": "shopping",
                    "title": "Shopping",
                    "description": "Find products",
                    "trigger": "Customer wants to shop",
                    "states": [
                        {"id": "ask", "action": "Ask preferences"},
                        {"id": "search", "action": "Search", "tools": ["search_products"]},
                    ],
                    "transitions": [
                        {"source": "search", "target": "END"},
                        {"source": "ask", "target": "search", "condition": "Preferences known"},
                        {"source": "START", "target": "ask"},
                        {"source": "search", "target": "ask", "condition": "Try again"},
                    ],
                }
            ],
        }
    )


@pytest.fixture
def loader(tmp_path, config):
    for name, data in config.model_dump(mode="json").items():
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return AgentConfigLoader(tmp_path)


@pytest.fixture
def sdk_server():
    calls = []
    entities = {}

    def state(identifier):
        async def transition_to(*, state=None, condition=None, id=None, **kwargs):
            calls.append(("edge", identifier, getattr(state, "id", id)))
            return SimpleNamespace(target=state or make_state(id))

        return SimpleNamespace(id=identifier, transition_to=AsyncMock(side_effect=transition_to))

    make_state = state

    async def create_entity(kind, **kwargs):
        key = (kind, kwargs["id"])
        if key in entities:
            raise AssertionError(f"Duplicate {key}")
        entity = SimpleNamespace(**kwargs)
        if kind == "journey":
            entity.initial_state = state("START")
        entities[key] = entity
        calls.append((kind, kwargs))
        return entity

    agent = SimpleNamespace(
        id="customer_service",
        name="Assistant",
        description="Shopping",
        create_term=AsyncMock(side_effect=lambda **kw: None),
    )

    async def create_term(**kw):
        return await create_entity("term", **kw)

    async def create_guideline(**kw):
        return await create_entity("guideline", **kw)

    async def create_journey(**kw):
        return await create_entity("journey", **kw)

    agent.create_term = AsyncMock(side_effect=create_term)
    agent.create_guideline = AsyncMock(side_effect=create_guideline)
    agent.create_journey = AsyncMock(side_effect=create_journey)
    created = False

    async def find_agent(*, id):
        return agent if created else None

    async def create_agent(**kwargs):
        nonlocal created
        created = True
        calls.append(("agent", kwargs))
        return agent

    return SimpleNamespace(
        find_agent=AsyncMock(side_effect=find_agent),
        create_agent=AsyncMock(side_effect=create_agent),
        get_agent=AsyncMock(return_value=agent),
        agent=agent,
        calls=calls,
        entities=entities,
        store=SimpleNamespace(update_agent=AsyncMock()),
        sdk=SimpleNamespace(END_JOURNEY=state("END")),
    )

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ag_ui.core import RunAgentInput

from agent_adapter_service.agent.contracts import EventRecord, SessionRecord
from agent_adapter_service.agent.tools.context import ActiveToolContexts
from agent_adapter_service.agui.runner import AguiRunRunner
from agent_adapter_service.agui.schemas import TrustedStorefrontContext
from agent_adapter_service.conversations.bindings import ConversationBindingManager
from agent_adapter_service.conversations.service import ConversationService
from agent_adapter_service.customer_identity.models import StorefrontIdentity
from agent_adapter_service.frontend.events.handler import FrontendEventHandler
from agent_adapter_service.frontend.state.manager import FrontendStateManager
from agent_adapter_service.persistence.repositories.conversations import SqlConversationRepository
from agent_adapter_service.persistence.contracts import StoreScope
from tests.customer_identity.conftest import harness  # noqa: F401


def run_input(message_id="m1", text="Hello", *, run_id="r1", thread_id="t1", **kwargs):
    return RunAgentInput(
        thread_id=thread_id,
        run_id=run_id,
        messages=[{"id": message_id, "role": "user", "content": text}],
        tools=[],
        context=[],
        forwarded_props={},
        **kwargs,
    )


class FakeEvents:
    def __init__(self):
        self.records = {}
        self.writes = []

    def add(self, session_id, *, kind="message", source="ai_agent", data=None, metadata=None):
        records = self.records.setdefault(session_id, [])
        event = EventRecord(
            id=f"e{len(records)}",
            offset=len(records),
            trace_id="sdk-trace",
            kind=kind,
            source=source,
            data=data or {},
            metadata=metadata or {},
        )
        records.append(event)
        return event

    async def list(self, session_id, *, min_offset=0):
        return [
            event.model_copy(deep=True)
            for event in self.records.get(session_id, [])
            if event.offset >= min_offset
        ]

    async def create_customer_message(self, session_id, message, *, trigger_processing, metadata):
        assert trigger_processing is False
        self.writes.append((session_id, message))
        return self.add(session_id, source="customer", data={"message": message}, metadata=metadata)

    async def create(self, session_id, event_type, data, *, trigger_processing, metadata):
        assert trigger_processing is False
        self.add(
            session_id,
            source="customer_ui",
            kind="custom",
            data={"event_type": event_type, "data": data},
            metadata=metadata,
        )


@pytest.fixture
async def agui(harness):  # noqa: F811 - pytest fixture imported from the identity harness
    scope = StoreScope(tenant_id="tenant", store_id="store")
    states = FrontendStateManager(scope)
    contexts = ActiveToolContexts(scope)
    events = FakeEvents()
    repository = SqlConversationRepository(harness.database, scope)

    async def create_session(agent_id, customer_id, *, title=None):
        session = SessionRecord(
            id=f"session-{len(harness.sessions.records)}",
            agent_id=agent_id,
            customer_id=customer_id,
        )
        harness.sessions.records[session.id] = session
        return session

    harness.sessions.create = create_session
    conversations = ConversationService(
        ConversationBindingManager(repository, harness.sessions, agent_id="agent"),
        harness.repository,
    )

    async def process(session_id, agent_id, *, run_id):
        events.add(session_id, kind="status", data={"status": "processing"})
        events.add(session_id, data={"message": "Hello shopper"})
        events.add(session_id, kind="status", data={"status": "ready"})

    processing = SimpleNamespace(process=AsyncMock(side_effect=process))
    runner = AguiRunRunner(
        scope=scope,
        identities=harness.resolver(),
        conversations=conversations,
        states=states,
        frontend_events=FrontendEventHandler(events),
        messages=events,
        events=events,
        processing=processing,
        contexts=contexts,
        run_timeout=2,
    )
    trusted = TrustedStorefrontContext(
        scope, StorefrontIdentity(visitor_id="v1", status="anonymous")
    )
    yield SimpleNamespace(
        runner=runner, events=events, processing=processing, trusted=trusted, harness=harness
    )
    await runner.close()


async def collect(runner, input, trusted):
    return [
        event.model_dump(mode="json", by_alias=True) async for event in runner.run(input, trusted)
    ]


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)

import asyncio

import pytest

from agent_adapter_service.conversations.models import ConversationBinding
from agent_adapter_service.core.exceptions import ConversationError
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.repositories.conversations import SqlConversationRepository


def binding(session="session"):
    return ConversationBinding(
        thread_id="thread",
        parlant_session_id=session,
        parlant_customer_id="customer",
        agent_id="agent",
    )


async def test_independent_connections_return_one_canonical_binding(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/conversations.db"
    databases = [Database(url, create_schema=True), Database(url)]
    scope = StoreScope(tenant_id="tenant", store_id="store")
    try:
        for db in databases:
            await db.startup()
        repositories = [SqlConversationRepository(db, scope) for db in databases]
        winners = await asyncio.gather(
            *(repositories[i % 2].get_or_create(binding(f"session-{i}")) for i in range(12))
        )
        assert all(winner == winners[0] for winner in winners)
        assert await repositories[1].find_by_session(winners[0].parlant_session_id) == winners[0]
        with pytest.raises(ConversationError):
            await repositories[0].create(binding("duplicate"))
    finally:
        for db in reversed(databases):
            await db.shutdown()


async def test_scope_isolation_agent_conflict_and_customer_compare_and_set(harness):
    repo = SqlConversationRepository(
        harness.database,
        StoreScope(tenant_id="tenant", store_id="store"),
    )
    foreign = SqlConversationRepository(
        harness.database,
        StoreScope(tenant_id="other", store_id="store"),
    )
    original = await repo.create(binding())
    assert await foreign.find_by_thread("thread") is None
    assert await foreign.find_by_session("session") is None
    with pytest.raises(ConversationError):
        await foreign.get_or_create(binding("foreign-session"))
    with pytest.raises(ConversationError):
        await repo.get_or_create(binding().model_copy(update={"agent_id": "other"}))
    with pytest.raises(ConversationError):
        await repo.get_or_create(binding().model_copy(update={"parlant_customer_id": "other"}))
    with pytest.raises(ConversationError):
        await repo.update_customer(
            "thread", expected_customer_id="wrong", parlant_customer_id="new"
        )
    assert await repo.find_by_thread("thread") == original
    updated = await repo.update_customer(
        "thread",
        expected_customer_id="customer",
        parlant_customer_id="new",
    )
    assert updated.parlant_session_id == original.parlant_session_id
    assert updated.parlant_customer_id == "new"
    with pytest.raises(ConversationError):
        await repo.update_customer(
            "missing", expected_customer_id="customer", parlant_customer_id="new"
        )

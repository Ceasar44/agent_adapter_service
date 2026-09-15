import asyncio
from unittest.mock import AsyncMock

import pytest

from agent_adapter_service.agent.contracts import SessionRecord
from agent_adapter_service.conversations import ConversationBindingManager, ConversationService
from agent_adapter_service.core.exceptions import ConversationError, IntegrationError
from agent_adapter_service.customer_identity.models import IdentityStatus, StorefrontIdentity
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.persistence.errors import PersistenceError
from agent_adapter_service.persistence.repositories.conversations import SqlConversationRepository


@pytest.fixture
def service(harness):
    async def create(agent_id, customer_id, *, title=None):
        await asyncio.sleep(0)
        record = SessionRecord(
            id=f"session-{len(harness.sessions.records)}",
            agent_id=agent_id,
            customer_id=customer_id,
        )
        harness.sessions.records[record.id] = record
        return record

    harness.sessions.create = AsyncMock(side_effect=create)
    repository = SqlConversationRepository(
        harness.database,
        StoreScope(tenant_id="tenant", store_id="store"),
    )
    return ConversationService(
        ConversationBindingManager(repository, harness.sessions, agent_id="agent"),
        harness.repository,
    )


async def anonymous(harness, visitor="visitor"):
    return await harness.resolver().resolve(
        StorefrontIdentity(
            visitor_id=visitor,
            status=IdentityStatus.ANONYMOUS,
        )
    )


async def login(harness, visitor="visitor", user="user"):
    return await harness.resolver().resolve(
        StorefrontIdentity(
            visitor_id=visitor,
            status=IdentityStatus.AUTHENTICATED,
            saleor_user_id=user,
        )
    )


async def test_concurrent_creation_reuses_session_and_keeps_runs_separate(harness, service):
    identity = await anonymous(harness)
    contexts = await asyncio.gather(
        *(service.resolve("thread", f"run-{i}", identity) for i in range(15))
    )
    assert len({c.binding.parlant_session_id for c in contexts}) == 1
    assert len({c.run_id for c in contexts}) == 15
    assert harness.sessions.create.await_count == 1
    other = await service.create_conversation("other-thread", "run-0", identity)
    assert other.binding.parlant_session_id != contexts[0].binding.parlant_session_id
    assert other.binding.parlant_customer_id == identity.parlant_customer_id


async def test_restart_and_frontend_state_are_request_local(harness, service):
    identity = await anonymous(harness)
    first = await service.resolve("thread", "run", identity, frontend_state={"page": "product"})
    restarted = ConversationService(
        ConversationBindingManager(service.bindings.repository, harness.sessions, agent_id="agent"),
        harness.repository,
    )
    second = await restarted.resolve("thread", "run-2", identity)
    assert second.binding == first.binding
    assert second.frontend_state is None
    assert harness.sessions.create.await_count == 1


@pytest.mark.parametrize("existing_user", [False, True])
async def test_login_preserves_thread_and_session(harness, service, existing_user):
    if existing_user:
        await login(harness, "another-browser")
    identity = await anonymous(harness)
    before = await service.resolve("thread", "before", identity)
    authenticated = await login(harness)
    after = await service.resolve("thread", "after", authenticated)
    assert after.binding.thread_id == before.binding.thread_id
    assert after.binding.parlant_session_id == before.binding.parlant_session_id
    assert after.binding.parlant_customer_id == authenticated.parlant_customer_id
    assert len(harness.sessions.updates) == int(existing_user)
    assert await service.reconcile_customer("thread", authenticated) == after.binding


async def test_foreign_customer_and_agent_cannot_reuse_thread(harness, service):
    first = await anonymous(harness)
    await service.resolve("thread", "run", first)
    stranger = await anonymous(harness, "stranger")
    with pytest.raises(ConversationError) as error:
        await service.resolve("thread", "run", stranger)
    assert error.value.http_status == 403
    other_agent = ConversationService(
        ConversationBindingManager(service.bindings.repository, harness.sessions, agent_id="other"),
        harness.repository,
    )
    with pytest.raises(ConversationError, match="agent"):
        await other_agent.resolve("thread", "run", first)
    assert harness.sessions.create.await_count == 1
    assert harness.sessions.updates == []


async def test_forged_or_stale_resolved_identity_rejected(harness, service):
    identity = await anonymous(harness)
    forged = identity.model_copy(update={"parlant_customer_id": "forged"})
    with pytest.raises(ConversationError):
        await service.resolve("thread", "run", forged)
    await login(harness)
    with pytest.raises(ConversationError):
        await service.resolve("thread", "run", identity)
    assert harness.sessions.create.await_count == 0


async def test_auth_unavailable_only_reuses_existing_owner(harness, service):
    identity = await anonymous(harness)
    first = await service.resolve("thread", "run", identity)
    unavailable = await harness.resolver().resolve(
        StorefrontIdentity(
            visitor_id="visitor",
            status=IdentityStatus.AUTH_UNAVAILABLE,
        )
    )
    assert (await service.resolve("thread", "run-2", unavailable)).binding == first.binding
    with pytest.raises(ConversationError) as error:
        await service.resolve("new-thread", "run", unavailable)
    assert error.value.http_status == 503


async def test_remote_failure_and_local_failure_can_retry_login(harness, service, monkeypatch):
    await login(harness, "another-browser")
    before = await service.resolve("thread", "run", await anonymous(harness))
    authenticated = await login(harness)
    harness.sessions.fail_update = True
    with pytest.raises(IntegrationError):
        await service.resolve("thread", "run", authenticated)
    assert await service.bindings.repository.find_by_thread("thread") == before.binding
    harness.sessions.fail_update = False
    original = service.bindings.repository.update_customer
    with monkeypatch.context() as patch:
        patch.setattr(
            service.bindings.repository,
            "update_customer",
            AsyncMock(
                side_effect=PersistenceError("Unavailable"),
            ),
        )
        with pytest.raises(PersistenceError):
            await service.resolve("thread", "run", authenticated)
    assert service.bindings.repository.update_customer == original
    after = await service.resolve("thread", "retry", authenticated)
    assert after.binding.parlant_session_id == before.binding.parlant_session_id
    assert after.binding.parlant_customer_id == authenticated.parlant_customer_id
    assert len(harness.sessions.updates) == 1


async def test_unavailable_auth_cannot_finish_upgrade(harness, service):
    await login(harness, "another-browser")
    await service.resolve("thread", "run", await anonymous(harness))
    await login(harness)
    unavailable = await harness.resolver().resolve(
        StorefrontIdentity(
            visitor_id="visitor",
            status=IdentityStatus.AUTH_UNAVAILABLE,
        )
    )
    with pytest.raises(ConversationError):
        await service.resolve("thread", "run", unavailable)
    assert harness.sessions.updates == []


@pytest.mark.parametrize("field,value", [("customer_id", "foreign"), ("agent_id", "foreign")])
async def test_remote_owner_mismatch_is_not_overwritten(harness, service, field, value):
    identity = await anonymous(harness)
    context = await service.resolve("thread", "run", identity)
    sid = context.binding.parlant_session_id
    harness.sessions.records[sid] = harness.sessions.records[sid].model_copy(update={field: value})
    with pytest.raises(ConversationError):
        await service.resolve("thread", "run", identity)
    assert harness.sessions.updates == []


async def test_gateway_failure_is_not_treated_as_missing_session(harness, service, monkeypatch):
    identity = await anonymous(harness)
    await service.resolve("thread", "run", identity)
    monkeypatch.setattr(harness.sessions, "get", AsyncMock(side_effect=IntegrationError("Offline")))
    with pytest.raises(IntegrationError):
        await service.resolve("thread", "run", identity)
    assert harness.sessions.create.await_count == 1


async def test_m04_thread_upgrade_remains_compatible(harness, service):
    await login(harness, "another-browser")
    before = await service.resolve("thread", "run", await anonymous(harness))
    identity = await harness.resolver().resolve(
        StorefrontIdentity(
            visitor_id="visitor",
            status=IdentityStatus.AUTHENTICATED,
            saleor_user_id="user",
        ),
        thread_id="thread",
    )
    after = await service.resolve("thread", "next", identity)
    assert after.binding.parlant_session_id == before.binding.parlant_session_id
    assert after.binding.parlant_customer_id == identity.parlant_customer_id

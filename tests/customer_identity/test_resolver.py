import asyncio

import pytest
from pydantic import ValidationError

from agent_adapter_service.agent.contracts import SessionRecord
from agent_adapter_service.core.exceptions import IdentityError, IntegrationError
from agent_adapter_service.core.types import AuthState
from agent_adapter_service.customer_identity.models import IdentityStatus, StorefrontIdentity
from agent_adapter_service.persistence.contracts import ConversationBindingCreate


def identity(visitor="v1", user=None, status=None, **kwargs):
    return StorefrontIdentity(
        visitor_id=visitor,
        saleor_user_id=user,
        status=status or (IdentityStatus.AUTHENTICATED if user else IdentityStatus.ANONYMOUS),
        **kwargs,
    )


async def thread(harness, customer, thread_id="thread", session_id="session"):
    await harness.conversations.create(
        ConversationBindingCreate(
            agui_thread_id=thread_id,
            parlant_session_id=session_id,
            parlant_customer_id=customer,
            parlant_agent_id="agent",
        )
    )
    harness.sessions.records[session_id] = SessionRecord(
        id=session_id,
        customer_id=customer,
        agent_id="agent",
    )


async def test_anonymous_isolated_and_stable(harness):
    resolver = harness.resolver()
    a = await resolver.resolve(identity())
    b = await resolver.resolve(identity("v2"))
    assert a.parlant_customer_id != b.parlant_customer_id
    assert a.parlant_customer_id != "guest"
    assert await resolver.resolve(identity()) == a
    assert harness.customers.created == 2
    assert a.auth_state == AuthState.ANONYMOUS


async def test_login_reuses_identity_and_thread(harness):
    resolver = harness.resolver()
    anonymous = await resolver.resolve(identity())
    await thread(harness, anonymous.parlant_customer_id)
    logged_in = await resolver.resolve(
        identity(user="u1", display_name="Alice"), thread_id="thread"
    )
    assert logged_in.parlant_customer_id == anonymous.parlant_customer_id
    assert logged_in.is_authenticated
    assert harness.customers.created == 1
    assert harness.customers.records[logged_in.parlant_customer_id].extra["saleor_user_id"] == "u1"
    assert harness.customers.records[logged_in.parlant_customer_id].name == "Alice"
    assert (await harness.conversations.find_by_thread("thread")).parlant_session_id == "session"
    assert harness.sessions.updates == []


async def test_multiple_visitors_reuse_account_and_keep_unavailable_binding(harness):
    resolver = harness.resolver()
    first = await resolver.resolve(identity(user="u1"))
    second = await resolver.resolve(identity("v2", "u1"))
    assert second.parlant_customer_id == first.parlant_customer_id
    assert harness.customers.created == 1
    for visitor in ("v1", "v2"):
        unavailable = await resolver.resolve(
            identity(visitor, status=IdentityStatus.AUTH_UNAVAILABLE)
        )
        assert unavailable.parlant_customer_id == first.parlant_customer_id
        assert unavailable.saleor_user_id == "u1"
        assert not unavailable.is_authenticated
        assert unavailable.auth_state == AuthState.UNAVAILABLE


async def test_unavailable_does_not_provision_or_touch_parlant(harness):
    resolver = harness.resolver()
    original = await resolver.resolve(identity())
    harness.customers.fail_find = True
    result = await resolver.resolve(identity(status=IdentityStatus.AUTH_UNAVAILABLE))
    assert result.parlant_customer_id == original.parlant_customer_id
    with pytest.raises(IdentityError) as error:
        await resolver.resolve(identity("new", status=IdentityStatus.AUTH_UNAVAILABLE))
    assert error.value.http_status == 503
    assert await harness.repository.find_by_visitor("new") is None
    assert harness.customers.created == 1


async def test_upgrade_to_existing_customer_updates_same_session(harness):
    resolver = harness.resolver()
    account = await resolver.resolve(identity("v2", "u1"))
    anonymous = await resolver.resolve(identity())
    await thread(harness, anonymous.parlant_customer_id)
    result = await resolver.resolve(identity(user="u1"), thread_id="thread")
    assert result.parlant_customer_id == account.parlant_customer_id
    assert harness.sessions.records["session"].customer_id == account.parlant_customer_id
    binding = await harness.conversations.find_by_thread("thread")
    assert binding.parlant_session_id == "session"
    assert binding.parlant_customer_id == account.parlant_customer_id
    await resolver.resolve(identity(user="u1"), thread_id="thread")
    assert len(harness.sessions.updates) == 1


async def test_failed_remote_upgrade_is_retryable(harness):
    resolver = harness.resolver()
    account = await resolver.resolve(identity("v2", "u1"))
    original = await resolver.resolve(identity())
    await thread(harness, original.parlant_customer_id)
    harness.sessions.fail_update = True
    with pytest.raises(IntegrationError):
        await resolver.resolve(identity(user="u1"), thread_id="thread")
    assert (
        await harness.conversations.find_by_thread("thread")
    ).parlant_customer_id == original.parlant_customer_id
    harness.sessions.fail_update = False
    await resolver.resolve(identity(user="u1"), thread_id="thread")
    assert harness.sessions.records["session"].customer_id == account.parlant_customer_id


async def test_remote_success_local_failure_is_retryable(harness, monkeypatch):
    resolver = harness.resolver()
    account = await resolver.resolve(identity("v2", "u1"))
    original = await resolver.resolve(identity())
    await thread(harness, original.parlant_customer_id)
    update = harness.conversations.update_customer

    async def fail(*args, **kwargs):
        raise IntegrationError("Local write failed")

    monkeypatch.setattr(harness.conversations, "update_customer", fail)
    with pytest.raises(IntegrationError):
        await resolver.resolve(identity(user="u1"), thread_id="thread")
    assert harness.sessions.records["session"].customer_id == account.parlant_customer_id
    monkeypatch.setattr(harness.conversations, "update_customer", update)
    await resolver.resolve(identity(user="u1"), thread_id="thread")
    assert (
        await harness.conversations.find_by_thread("thread")
    ).parlant_customer_id == account.parlant_customer_id
    assert len(harness.sessions.updates) == 1


@pytest.mark.parametrize("visitor", ["v1", "new"])
async def test_foreign_thread_cannot_be_claimed(harness, visitor):
    resolver = harness.resolver()
    await resolver.resolve(identity())
    other = await resolver.resolve(identity("v2"))
    await thread(harness, other.parlant_customer_id)
    with pytest.raises(IdentityError, match="conversation|Conversation"):
        await resolver.resolve(identity(visitor, "u1"), thread_id="thread")
    assert await harness.repository.find_by_saleor_user("u1") is None
    assert harness.sessions.updates == []


async def test_mismatched_session_is_rejected(harness):
    resolver = harness.resolver()
    original = await resolver.resolve(identity())
    await thread(harness, original.parlant_customer_id)
    harness.sessions.records["session"] = SessionRecord(
        id="session",
        agent_id="agent",
        customer_id="someone-else",
    )
    with pytest.raises(IdentityError, match="Session"):
        await resolver.resolve(identity(user="u1"), thread_id="thread")
    assert harness.sessions.updates == []


async def test_logout_and_account_switch_require_visitor_rotation(harness):
    resolver = harness.resolver()
    original = await resolver.resolve(identity(user="u1"))
    for incoming in (identity(), identity(user="u2")):
        with pytest.raises(IdentityError):
            await resolver.resolve(incoming)
    assert (
        await harness.repository.find_by_visitor("v1")
    ).parlant_customer_id == original.parlant_customer_id
    assert harness.customers.created == 1


async def test_customer_failure_keeps_stable_id_for_retry(harness):
    resolver = harness.resolver()
    harness.customers.fail_create = True
    with pytest.raises(IntegrationError):
        await resolver.resolve(identity())
    binding = await harness.repository.find_by_visitor("v1")
    harness.customers.fail_create = False
    assert (await resolver.resolve(identity())).parlant_customer_id == binding.parlant_customer_id
    harness.customers.fail_find = True
    with pytest.raises(IntegrationError):
        await resolver.resolve(identity())
    assert harness.customers.created == 1


async def test_concurrent_resolvers_share_one_binding(harness):
    resolver = harness.resolver()
    results = await asyncio.gather(*(resolver.resolve(identity()) for _ in range(6)))
    assert len({result.parlant_customer_id for result in results}) == 1
    assert harness.customers.created == 1
    results = await asyncio.gather(
        *(resolver.resolve(identity(f"device-{i}", "u1")) for i in range(6))
    )
    assert len({result.parlant_customer_id for result in results}) == 1
    assert harness.customers.created == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"visitor_id": ""},
        {"visitor_id": "with spaces"},
        {"status": IdentityStatus.AUTHENTICATED},
        {"status": IdentityStatus.AUTH_UNAVAILABLE, "saleor_user_id": "u1"},
        {"status": IdentityStatus.ANONYMOUS, "saleor_user_id": "u1"},
        {"customer_id": "forged"},
        {"token": "private"},
    ],
)
def test_input_rejects_invalid_or_untrusted_fields(kwargs):
    with pytest.raises(ValidationError):
        StorefrontIdentity(**{"visitor_id": "v1", "status": IdentityStatus.ANONYMOUS, **kwargs})

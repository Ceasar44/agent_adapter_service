import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from agent_adapter_service.persistence.contracts import (
    AuditEvent,
    AuditQuery,
    ConversationBinding,
    ConversationBindingCreate,
    IdentityBinding,
    IdentityBindingCreate,
    StoreScope,
)
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.errors import BindingConflictError, BindingNotFoundError
from agent_adapter_service.persistence.models import AuditLogModel
from agent_adapter_service.persistence.repositories.audit_log import SqlAuditLogRepository
from agent_adapter_service.persistence.repositories.conversation_binding import (
    SqlConversationBindingRepository,
)
from agent_adapter_service.persistence.repositories.identity_binding import (
    SqlIdentityBindingRepository,
)
from agent_adapter_service.persistence.sanitization import REDACTED, sanitize_arguments


def identity(visitor="visitor", customer="customer", user=None):
    return IdentityBindingCreate(
        visitor_id=visitor, saleor_user_id=user, parlant_customer_id=customer
    )


def conversation(thread="thread", session="session", customer="customer", agent="agent"):
    return ConversationBindingCreate(
        agui_thread_id=thread,
        parlant_session_id=session,
        parlant_customer_id=customer,
        parlant_agent_id=agent,
    )


async def test_identity_crud_and_utc(database, scope):
    repo = SqlIdentityBindingRepository(database, scope)
    assert await repo.find_by_visitor_id("absent") is None
    created = await repo.create(identity())
    assert isinstance(created, IdentityBinding)
    assert created.created_at.utcoffset() == timedelta(0)
    assert await repo.find_by_parlant_customer_id("customer") == created
    assert await repo.upsert(identity()) == created
    bound = await repo.bind_saleor_user("visitor", "user", expected_customer_id="customer")
    assert bound.id == created.id
    assert await repo.find_by_saleor_user_id("user") == bound
    changed = await repo.update(
        created.id, expected_customer_id="customer", parlant_customer_id="new"
    )
    assert changed.created_at == created.created_at
    assert changed.updated_at >= created.updated_at
    assert await repo.find_by_parlant_customer_id("customer") is None
    assert (await repo.find_by_visitor_id("visitor")).parlant_customer_id == "new"


@pytest.mark.parametrize(
    "other_scope",
    [
        StoreScope(tenant_id="tenant-b", store_id="store-a"),
        StoreScope(tenant_id="tenant-a", store_id="store-b"),
    ],
)
async def test_identity_scope_isolation(database, scope, other_scope):
    repo = SqlIdentityBindingRepository(database, scope)
    other = SqlIdentityBindingRepository(database, other_scope)
    first = await repo.create(identity(user="user"))
    assert await other.find_by_visitor_id("visitor") is None
    assert await other.find_by_saleor_user_id("user") is None
    assert await other.find_by_parlant_customer_id("customer") is None
    with pytest.raises(BindingNotFoundError):
        await other.update(first.id, expected_customer_id="customer", parlant_customer_id="new")
    assert (await other.create(identity(user="user"))).id != first.id


async def test_identity_conflicts_do_not_overwrite(database, scope):
    repo = SqlIdentityBindingRepository(database, scope)
    first = await repo.create(identity())
    for data in (identity(customer="other"), identity(visitor="other")):
        with pytest.raises(BindingConflictError):
            await repo.create(data)
    with pytest.raises(BindingConflictError):
        await repo.upsert(identity(customer="other"))
    await repo.bind_saleor_user("visitor", "user", expected_customer_id="customer")
    with pytest.raises(BindingConflictError):
        await repo.bind_saleor_user("visitor", "other-user", expected_customer_id="customer")
    with pytest.raises(BindingConflictError):
        await repo.update(first.id, expected_customer_id="stale", parlant_customer_id="new")
    await repo.create(identity("visitor-2", "customer-2"))
    with pytest.raises(BindingConflictError):
        await repo.bind_saleor_user("visitor-2", "user", expected_customer_id="customer-2")
    assert (await repo.find_by_visitor_id("visitor-2")).saleor_user_id is None
    with pytest.raises(BindingNotFoundError):
        await repo.bind_saleor_user("missing", "user", expected_customer_id="customer")


async def test_account_without_visitor(database, scope):
    repo = SqlIdentityBindingRepository(database, scope)
    first = await repo.upsert(identity(None, "customer", "user"))
    assert await repo.upsert(identity(None, "customer", "user")) == first
    assert await repo.find_by_saleor_user_id("user") == first


async def test_upsert_updates_with_compare_and_set(database, scope):
    repo = SqlIdentityBindingRepository(database, scope)
    first = await repo.upsert(identity())
    changed = await repo.upsert(identity(customer="new"), expected_customer_id="customer")
    assert changed.id == first.id
    assert changed.parlant_customer_id == "new"
    with pytest.raises(BindingConflictError):
        await repo.upsert(identity(customer="stale-change"), expected_customer_id="customer")
    assert (await repo.find_by_visitor_id("visitor")).parlant_customer_id == "new"


async def test_concurrent_identity_insert_and_login(database, database_url, scope):
    second_db = Database(database_url)
    await second_db.startup()
    try:
        repos = [SqlIdentityBindingRepository(db, scope) for db in (database, second_db)]
        rows = await asyncio.gather(*(repos[i % 2].upsert(identity()) for i in range(8)))
        assert len({row.id for row in rows}) == 1
        outcomes = await asyncio.gather(
            *(
                repos[i].bind_saleor_user("visitor", f"user-{i}", expected_customer_id="customer")
                for i in range(2)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, IdentityBinding) for item in outcomes) == 1
        assert sum(isinstance(item, BindingConflictError) for item in outcomes) == 1
    finally:
        await second_db.shutdown()


async def test_conversation_crud_and_compare_and_set(database, scope):
    repo = SqlConversationBindingRepository(database, scope)
    assert await repo.find_by_thread("missing") is None
    created = await repo.create(conversation())
    assert isinstance(created, ConversationBinding)
    assert await repo.find_by_session("session") == created
    assert await repo.get_or_create(conversation(session="candidate")) == created
    updated = await repo.update_customer(
        "thread", expected_customer_id="customer", parlant_customer_id="new"
    )
    assert updated.parlant_session_id == created.parlant_session_id
    assert updated.created_at == created.created_at
    assert updated.parlant_customer_id == "new"
    with pytest.raises(BindingConflictError):
        await repo.update_customer(
            "thread", expected_customer_id="customer", parlant_customer_id="stale"
        )
    with pytest.raises(BindingNotFoundError):
        await repo.update_customer(
            "missing", expected_customer_id="customer", parlant_customer_id="new"
        )


async def test_conversation_uniqueness_and_scope(database, scope):
    repo = SqlConversationBindingRepository(database, scope)
    await repo.create(conversation())
    for candidate in (conversation(session="another"), conversation(thread="another")):
        with pytest.raises(BindingConflictError):
            await repo.create(candidate)
    for candidate in (conversation(customer="other"), conversation(agent="other")):
        with pytest.raises(BindingConflictError):
            await repo.get_or_create(candidate)
    other = SqlConversationBindingRepository(
        database, StoreScope(tenant_id="other", store_id="store-a")
    )
    assert await other.find_by_thread("thread") is None
    assert await other.find_by_session("session") is None
    with pytest.raises(BindingConflictError):
        await other.get_or_create(conversation())
    with pytest.raises(BindingNotFoundError):
        await other.update_customer(
            "thread", expected_customer_id="customer", parlant_customer_id="new"
        )


async def test_concurrent_thread_binding_across_connections(database, database_url, scope):
    second_db = Database(database_url)
    await second_db.startup()
    try:
        repos = [SqlConversationBindingRepository(db, scope) for db in (database, second_db)]
        bindings = await asyncio.gather(
            *(repos[i % 2].get_or_create(conversation(session=f"candidate-{i}")) for i in range(12))
        )
        assert len({binding.parlant_session_id for binding in bindings}) == 1
        winner = bindings[0]
        assert await repos[0].find_by_session(winner.parlant_session_id) == winner
    finally:
        await second_db.shutdown()


async def test_audit_redacts_before_storage(database, scope):
    arguments = {
        "password": {"quantity": 1234},
        "headers": {"Authorization": "Bearer private-token"},
        "items": [{"quantity": 2, "card_number": "4111111111111111"}],
        "free_text": "contact person@example.com",
        "currency": "USD",
        "unexpected_credential": "hidden",
        "email": "person@example.com",
    }
    repo = SqlAuditLogRepository(database, scope)
    event = AuditEvent(
        actor="user-1",
        surface="admin_mcp",
        tool_name="update_stock",
        arguments_summary=arguments,
        result_status="success",
        trace_id="trace-1",
    )
    saved = await repo.append(event)
    assert saved.arguments_summary["password"] == REDACTED
    assert saved.arguments_summary["currency"] == "USD"
    assert saved.arguments_summary["items"][0]["quantity"] == 2
    assert arguments["email"] == "person@example.com"
    async with database.session() as session:
        raw = await session.scalar(select(AuditLogModel.arguments_summary))
    serialized = json.dumps(raw)
    for secret in ("private-token", "4111111111111111", "person@example.com", "hidden", "1234"):
        assert secret not in serialized
    assert sanitize_arguments(saved.arguments_summary) == saved.arguments_summary
    assert (await repo.search())[0] == saved


async def test_audit_search_filters_pagination_scope(database, scope):
    repo = SqlAuditLogRepository(database, scope)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(3):
        await repo.append(
            AuditEvent(
                actor="actor",
                surface="frontend_tool",
                tool_name="open_product",
                result_status="success",
                trace_id=f"trace-{i}",
                timestamp=start + timedelta(seconds=i),
            )
        )
    query = AuditQuery(
        actor="actor",
        surface="frontend_tool",
        tool_name="open_product",
        result_status="success",
        since=start,
        until=start + timedelta(seconds=2),
        limit=1,
        offset=1,
    )
    assert (await repo.search(query))[0].trace_id == "trace-1"
    assert len(await repo.search(AuditQuery(trace_id="trace-0"))) == 1
    assert await repo.search(AuditQuery(result_status="denied")) == []
    other = SqlAuditLogRepository(database, StoreScope(tenant_id="other", store_id="store-a"))
    assert await other.search() == []


def test_contracts_and_summary_bounds():
    with pytest.raises(ValidationError):
        IdentityBindingCreate(parlant_customer_id="customer")
    with pytest.raises(ValidationError):
        AuditQuery(limit=201)
    with pytest.raises(ValidationError):
        AuditEvent(
            actor="person@example.com",
            surface="admin_mcp",
            tool_name="tool",
            result_status="success",
        )
    with pytest.raises(ValidationError):
        AuditQuery(since=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None))
    result = sanitize_arguments({"items": [{"quantity": i} for i in range(100)]})
    assert len(result["items"]) == 20
    assert sanitize_arguments({"quantity": float("inf")}) == {"quantity": REDACTED}
    summary = sanitize_arguments({"credentialAsFieldName": "hidden"})
    assert "credentialAsFieldName" not in json.dumps(summary)

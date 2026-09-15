from dataclasses import dataclass, field

import pytest

from agent_adapter_service.agent.contracts import CustomerRecord, SessionRecord
from agent_adapter_service.core.exceptions import IntegrationError
from agent_adapter_service.customer_identity.models import CustomerIdentityBinding
from agent_adapter_service.customer_identity.repository import IdentityBindingConflict
from agent_adapter_service.customer_identity.resolver import CustomerIdentityResolver
from agent_adapter_service.customer_identity.service import CustomerIdentityService
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.repositories.conversation_binding import (
    SqlConversationBindingRepository,
)
from agent_adapter_service.persistence.repositories.customer_identity import (
    SqlCustomerIdentityRepository,
)


class MemoryRepository:
    """An ORM-free test implementation of the domain port."""

    def __init__(self):
        self.visitors = {}
        self.users = {}

    async def find_by_visitor(self, visitor_id):
        return self.visitors.get(visitor_id)

    async def find_by_saleor_user(self, saleor_user_id):
        return self.users.get(saleor_user_id)

    async def create(self, binding):
        return self.visitors.setdefault(
            binding.visitor_id,
            binding.model_copy(
                update={
                    "anonymous_customer_id": binding.parlant_customer_id,
                }
            ),
        )

    async def bind_saleor_user(self, visitor_id, saleor_user_id, *, expected_customer_id):
        old = self.visitors[visitor_id]
        if old.saleor_user_id == saleor_user_id:
            return old
        if old.saleor_user_id is not None or old.parlant_customer_id != expected_customer_id:
            raise IdentityBindingConflict("Identity conflict")
        target = self.users.get(saleor_user_id, old)
        result = CustomerIdentityBinding(
            visitor_id=visitor_id,
            saleor_user_id=saleor_user_id,
            parlant_customer_id=target.parlant_customer_id,
            anonymous_customer_id=old.anonymous_customer_id,
        )
        self.visitors[visitor_id] = result
        self.users.setdefault(saleor_user_id, result)
        return result


class Customers:
    def __init__(self):
        self.records = {}
        self.created = 0
        self.updated = 0
        self.fail_find = False
        self.fail_create = False

    async def find(self, customer_id):
        if self.fail_find:
            raise IntegrationError("Unavailable")
        return self.records.get(customer_id)

    async def create(self, name, *, customer_id=None, extra=None):
        if self.fail_create:
            raise IntegrationError("Unavailable")
        self.created += 1
        record = CustomerRecord(id=customer_id, name=name, extra=dict(extra or {}))
        self.records[customer_id] = record
        return record

    async def update(self, customer_id, *, name=None, extra=None):
        self.updated += 1
        old = self.records[customer_id]
        record = CustomerRecord(
            id=customer_id,
            name=name or old.name,
            extra={**old.extra, **(extra or {})},
        )
        self.records[customer_id] = record
        return record


class Sessions:
    def __init__(self):
        self.records = {}
        self.updates = []
        self.fail_update = False

    async def get(self, session_id):
        return self.records[session_id]

    async def update_customer(self, session_id, customer_id):
        if self.fail_update:
            raise IntegrationError("Unavailable")
        old = self.records[session_id]
        record = SessionRecord(id=old.id, agent_id=old.agent_id, customer_id=customer_id)
        self.records[session_id] = record
        self.updates.append((session_id, customer_id))
        return record


@dataclass
class Harness:
    repository: object
    database: Database
    conversations: SqlConversationBindingRepository
    customers: Customers = field(default_factory=Customers)
    sessions: Sessions = field(default_factory=Sessions)

    def resolver(self):
        return CustomerIdentityResolver(
            CustomerIdentityService(
                self.repository,
                self.customers,
                self.sessions,
                conversations=self.conversations,
            )
        )


@pytest.fixture(params=["memory", "sqlite"])
async def harness(request, tmp_path):
    database = Database(
        f"sqlite+aiosqlite:///{tmp_path.as_posix()}/identity.db", create_schema=True
    )
    await database.startup()
    scope = StoreScope(tenant_id="tenant", store_id="store")
    repository = (
        MemoryRepository()
        if request.param == "memory"
        else SqlCustomerIdentityRepository(database, scope)
    )
    try:
        yield Harness(repository, database, SqlConversationBindingRepository(database, scope))
    finally:
        await database.shutdown()

import asyncio

import pytest

from agent_adapter_service.customer_identity.models import CustomerIdentityBinding
from agent_adapter_service.customer_identity.repository import IdentityBindingConflict
from agent_adapter_service.persistence.contracts import IdentityBindingCreate, StoreScope
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.repositories.customer_identity import (
    SqlCustomerIdentityRepository,
)
from agent_adapter_service.persistence.repositories.identity_binding import (
    SqlIdentityBindingRepository,
)


@pytest.fixture
async def repos(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/identities.db"
    first = Database(url, create_schema=True)
    second = Database(url)
    await first.startup()
    await second.startup()
    scope = StoreScope(tenant_id="tenant", store_id="store")
    try:
        yield [SqlCustomerIdentityRepository(db, scope) for db in (first, second)]
    finally:
        await second.shutdown()
        await first.shutdown()


def binding(visitor, customer):
    return CustomerIdentityBinding(visitor_id=visitor, parlant_customer_id=customer)


async def test_independent_connections_choose_one_visitor_and_account(repos):
    rows = await asyncio.gather(
        *(repos[i % 2].create(binding("visitor", f"candidate-{i}")) for i in range(8))
    )
    assert len({row.parlant_customer_id for row in rows}) == 1
    await repos[0].create(binding("other", "other-customer"))
    rows = await asyncio.gather(
        repos[0].bind_saleor_user(
            "visitor", "user", expected_customer_id=rows[0].parlant_customer_id
        ),
        repos[1].bind_saleor_user("other", "user", expected_customer_id="other-customer"),
    )
    assert rows[0].parlant_customer_id == rows[1].parlant_customer_id


async def test_competing_accounts_cannot_take_over_visitor(repos):
    await repos[0].create(binding("visitor", "customer"))
    results = await asyncio.gather(
        *(
            repos[i].bind_saleor_user("visitor", f"user-{i}", expected_customer_id="customer")
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, CustomerIdentityBinding) for result in results) == 1
    assert sum(isinstance(result, IdentityBindingConflict) for result in results) == 1


async def test_legacy_bindings_adopted_and_aliases_survive_restart(repos):
    repo = repos[0]
    legacy = SqlIdentityBindingRepository(repo.database, repo.scope)
    await legacy.create(IdentityBindingCreate(visitor_id="visitor", parlant_customer_id="customer"))
    assert (await repo.find_by_visitor("visitor")).parlant_customer_id == "customer"
    await repo.bind_saleor_user("visitor", "user", expected_customer_id="customer")
    await repo.create(binding("second", "second-customer"))
    await repo.bind_saleor_user("second", "user", expected_customer_id="second-customer")
    result = await repos[1].find_by_visitor("second")
    assert result.parlant_customer_id == "customer"
    assert result.anonymous_customer_id == "second-customer"
    assert (await legacy.find_by_saleor_user_id("user")).parlant_customer_id == "customer"


@pytest.mark.parametrize(
    "scope",
    [
        StoreScope(tenant_id="other", store_id="store"),
        StoreScope(tenant_id="tenant", store_id="other"),
    ],
)
async def test_store_and_tenant_isolation(repos, scope):
    repo = repos[0]
    await repo.create(binding("visitor", "customer"))
    await repo.bind_saleor_user("visitor", "user", expected_customer_id="customer")
    other = SqlCustomerIdentityRepository(repo.database, scope)
    assert await other.find_by_visitor("visitor") is None
    assert await other.find_by_saleor_user("user") is None
    with pytest.raises(IdentityBindingConflict):
        await other.bind_saleor_user("visitor", "user", expected_customer_id="customer")
    await other.create(binding("visitor", "different-customer"))
    result = await other.bind_saleor_user(
        "visitor", "user", expected_customer_id="different-customer"
    )
    assert result.parlant_customer_id == "different-customer"

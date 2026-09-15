from agent_adapter_service.customer_identity.models import StorefrontIdentity
from agent_adapter_service.customer_identity.resolver import CustomerIdentityResolver
from agent_adapter_service.customer_identity.service import CustomerIdentityService
from tests.customer_identity.conftest import MemoryRepository, Customers, Sessions


async def test_identity_lifecycle_without_database_or_sdk():
    repository = MemoryRepository()
    resolver = CustomerIdentityResolver(
        CustomerIdentityService(repository, Customers(), Sessions())
    )

    async def resolve(visitor, status="anonymous", user=None):
        return await resolver.resolve(
            StorefrontIdentity(
                visitor_id=visitor,
                status=status,
                saleor_user_id=user,
            )
        )

    a, b = await resolve("a"), await resolve("b")
    assert a.parlant_customer_id != b.parlant_customer_id
    logged_in = await resolve("a", "authenticated", "user")
    reused = await resolve("c", "authenticated", "user")
    assert logged_in.parlant_customer_id == reused.parlant_customer_id
    unavailable = await resolve("a", "unavailable")
    assert unavailable.parlant_customer_id == logged_in.parlant_customer_id
    assert unavailable.saleor_user_id == "user"

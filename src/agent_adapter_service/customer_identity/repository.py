from typing import Protocol

from agent_adapter_service.core.exceptions import IdentityError
from agent_adapter_service.customer_identity.models import CustomerIdentityBinding


class IdentityBindingConflict(IdentityError):
    code = "identity_binding_conflict"
    http_status = 409


class IdentityBindingRepository(Protocol):
    """Scope is fixed by composition. Writes are atomic and compare expected ownership.

    create returns the existing winner for the same visitor/user. bind_saleor_user
    reuses an existing canonical user, retains anonymous origin, and rejects account
    switches. Implementations must support concurrent callers without process locks.
    """

    async def find_by_visitor(self, visitor_id: str) -> CustomerIdentityBinding | None: ...
    async def find_by_saleor_user(self, saleor_user_id: str) -> CustomerIdentityBinding | None: ...
    async def create(self, binding: CustomerIdentityBinding) -> CustomerIdentityBinding: ...
    async def bind_saleor_user(
        self, visitor_id: str, saleor_user_id: str, *, expected_customer_id: str
    ) -> CustomerIdentityBinding: ...

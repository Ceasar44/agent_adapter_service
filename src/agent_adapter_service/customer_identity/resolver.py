from agent_adapter_service.core.exceptions import IdentityError
from agent_adapter_service.customer_identity.models import (
    CustomerIdentityBinding,
    IdentityStatus,
    ResolvedCustomerIdentity,
    StorefrontIdentity,
)
from agent_adapter_service.customer_identity.service import CustomerIdentityService


class CustomerIdentityResolver:
    def __init__(self, service: CustomerIdentityService) -> None:
        self.service = service

    async def resolve(
        self, identity: StorefrontIdentity, *, thread_id: str | None = None
    ) -> ResolvedCustomerIdentity:
        if identity.status == IdentityStatus.ANONYMOUS:
            binding = await self._resolve_anonymous(identity)
        elif identity.status == IdentityStatus.AUTHENTICATED:
            binding = await self._resolve_authenticated(identity, thread_id=thread_id)
        else:
            binding = await self._resolve_unavailable(identity)
        return ResolvedCustomerIdentity(
            visitor_id=identity.visitor_id,
            saleor_user_id=binding.saleor_user_id,
            parlant_customer_id=binding.parlant_customer_id,
            status=identity.status,
        )

    async def _resolve_anonymous(self, identity: StorefrontIdentity) -> CustomerIdentityBinding:
        binding = await self.service.visitor_binding(identity.visitor_id)
        if binding.saleor_user_id is not None:
            raise IdentityError(
                "Rotate visitor identity after logout",
                code="visitor_rotation_required",
                http_status=409,
            )
        await self.service.ensure_customer(binding, identity)
        return binding

    async def _resolve_authenticated(
        self, identity: StorefrontIdentity, *, thread_id: str | None = None
    ) -> CustomerIdentityBinding:
        return await self.service.upgrade_anonymous_identity(identity, thread_id=thread_id)

    async def _resolve_unavailable(self, identity: StorefrontIdentity) -> CustomerIdentityBinding:
        binding = await self.service.repository.find_by_visitor(identity.visitor_id)
        if binding is None:
            raise IdentityError(
                "Authentication is unavailable and no prior identity exists",
                code="auth_unavailable",
                http_status=503,
            )
        return binding

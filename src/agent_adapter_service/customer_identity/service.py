import asyncio
from collections.abc import Mapping
from typing import Protocol
from uuid import uuid4

from agent_adapter_service.agent.contracts import CustomerRecord, SessionRecord
from agent_adapter_service.core.exceptions import IdentityError, IntegrationError
from agent_adapter_service.customer_identity.models import (
    CustomerIdentityBinding,
    IdentityStatus,
    StorefrontIdentity,
)
from agent_adapter_service.customer_identity.repository import (
    IdentityBindingConflict,
    IdentityBindingRepository,
)
from agent_adapter_service.persistence.contracts import ConversationBindingRepository


class CustomerGateway(Protocol):
    async def find(self, customer_id: str) -> CustomerRecord | None: ...
    async def create(
        self, name: str, *, customer_id: str | None = None, extra: Mapping[str, str] | None = None
    ) -> CustomerRecord: ...
    async def update(
        self, customer_id: str, *, name: str | None = None, extra: Mapping[str, str] | None = None
    ) -> CustomerRecord: ...


class SessionGateway(Protocol):
    async def get(self, session_id: str) -> SessionRecord: ...
    async def update_customer(self, session_id: str, customer_id: str) -> SessionRecord: ...


class CustomerIdentityService:
    def __init__(
        self,
        repository: IdentityBindingRepository,
        customers: CustomerGateway,
        sessions: SessionGateway,
        *,
        conversations: ConversationBindingRepository | None = None,
    ) -> None:
        self.repository = repository
        self.customers = customers
        self.sessions = sessions
        self.conversations = conversations
        # The embedded Parlant store has a single host; serialize provisioning in that host.
        # Database ownership/uniqueness remains enforced independently by the repository.
        self._customer_lock = asyncio.Lock()

    async def visitor_binding(self, visitor_id: str) -> CustomerIdentityBinding:
        binding = await self.repository.find_by_visitor(visitor_id)
        if binding is None:
            binding = await self.repository.create(
                CustomerIdentityBinding(
                    visitor_id=visitor_id, parlant_customer_id=f"customer-{uuid4()}"
                )
            )
        return binding

    async def ensure_customer(
        self, binding: CustomerIdentityBinding, identity: StorefrontIdentity
    ) -> CustomerRecord:
        """Provision the persisted ID, so retries cannot allocate a different customer.

        Unavailable authentication is handled by the resolver without provisioning or writes.
        """
        if identity.status == IdentityStatus.AUTH_UNAVAILABLE:
            raise IdentityError("Authentication is unavailable", http_status=503)
        if binding.saleor_user_id != identity.saleor_user_id:
            raise IdentityBindingConflict("Identity does not match customer ownership")
        extra = (
            {"saleor_user_id": binding.saleor_user_id}
            if binding.saleor_user_id
            else {"visitor_id": identity.visitor_id}
        )
        name = identity.display_name or ("Customer" if binding.saleor_user_id else "Visitor")
        async with self._customer_lock:
            customer = await self.customers.find(binding.parlant_customer_id)
            if customer is None:
                try:
                    customer = await self.customers.create(
                        name, customer_id=binding.parlant_customer_id, extra=extra
                    )
                except IntegrationError:
                    # Creation may have succeeded before the transport failed.
                    customer = await self.customers.find(binding.parlant_customer_id)
                    if customer is None:
                        raise
            if customer.id != binding.parlant_customer_id:
                raise IntegrationError("Parlant returned a different customer")
            # Do not replace an existing display name with a fallback on every request.
            updated_name = identity.display_name
            if any(customer.extra.get(key) != value for key, value in extra.items()) or (
                updated_name is not None and customer.name != updated_name
            ):
                customer = await self.customers.update(customer.id, name=updated_name, extra=extra)
            return customer

    async def _check_thread(self, thread_id: str, binding: CustomerIdentityBinding) -> None:
        if self.conversations is None:
            raise IdentityError("Conversation repository is required for identity upgrade")
        conversation = await self.conversations.find_by_thread(thread_id)
        allowed = {binding.parlant_customer_id, binding.anonymous_customer_id} - {None}
        if conversation is None or conversation.parlant_customer_id not in allowed:
            raise IdentityError("Conversation does not belong to this identity", http_status=403)

    async def upgrade_anonymous_identity(
        self, identity: StorefrontIdentity, *, thread_id: str | None = None
    ) -> CustomerIdentityBinding:
        if identity.status != IdentityStatus.AUTHENTICATED or identity.saleor_user_id is None:
            raise IdentityError("Authenticated BFF identity is required for upgrade")
        previous = await self.repository.find_by_visitor(identity.visitor_id)
        if previous and previous.saleor_user_id not in (None, identity.saleor_user_id):
            raise IdentityBindingConflict("Rotate visitor identity when switching accounts")
        if thread_id is not None:
            if previous is None:
                raise IdentityError("Unknown visitor cannot claim a conversation", http_status=403)
            await self._check_thread(thread_id, previous)
        previous = previous or await self.visitor_binding(identity.visitor_id)
        binding = await self.repository.bind_saleor_user(
            identity.visitor_id,
            identity.saleor_user_id,
            expected_customer_id=previous.parlant_customer_id,
        )
        await self.ensure_customer(binding, identity)
        if thread_id is not None:
            await self._upgrade_session(thread_id, binding)
        return binding

    async def _upgrade_session(self, thread_id: str, binding: CustomerIdentityBinding) -> None:
        await self._check_thread(thread_id, binding)
        assert self.conversations is not None
        conversation = await self.conversations.find_by_thread(thread_id)
        assert conversation is not None
        session = await self.sessions.get(conversation.parlant_session_id)
        allowed = {conversation.parlant_customer_id, binding.parlant_customer_id}
        if session.customer_id not in allowed or session.agent_id != conversation.parlant_agent_id:
            raise IdentityError("Session does not match conversation ownership", http_status=403)
        if session.customer_id != binding.parlant_customer_id:
            await self.sessions.update_customer(session.id, binding.parlant_customer_id)
        if conversation.parlant_customer_id != binding.parlant_customer_id:
            # Remote first, then local CAS: if the local write fails, the next call repairs it.
            await self.conversations.update_customer(
                thread_id,
                expected_customer_id=conversation.parlant_customer_id,
                parlant_customer_id=binding.parlant_customer_id,
            )

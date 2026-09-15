import asyncio

from pydantic import JsonValue

from agent_adapter_service.conversations.bindings import ConversationBindingManager, identifier
from agent_adapter_service.conversations.models import ConversationBinding, ConversationContext
from agent_adapter_service.core.exceptions import ConversationError, IntegrationError
from agent_adapter_service.customer_identity.models import (
    CustomerIdentityBinding,
    IdentityStatus,
    ResolvedCustomerIdentity,
)
from agent_adapter_service.customer_identity.repository import IdentityBindingRepository


class ConversationService:
    """Consume server-resolved identities and verify ownership against persisted provenance.

    Register one service per trusted store scope. Never construct identity from browser JSON.
    """

    def __init__(
        self, bindings: ConversationBindingManager, identities: IdentityBindingRepository
    ) -> None:
        self.bindings = bindings
        self.identities = identities
        self._lock = asyncio.Lock()

    async def _identity(self, identity: ResolvedCustomerIdentity) -> CustomerIdentityBinding:
        stored = await self.identities.find_by_visitor(identity.visitor_id)
        if (
            stored is None
            or stored.parlant_customer_id != identity.parlant_customer_id
            or stored.saleor_user_id != identity.saleor_user_id
            or (identity.is_authenticated and identity.saleor_user_id is None)
            or (identity.status == IdentityStatus.ANONYMOUS and stored.saleor_user_id is not None)
        ):
            raise ConversationError("Identity does not match stored ownership", http_status=403)
        return stored

    async def _reconcile(
        self,
        binding: ConversationBinding,
        identity: ResolvedCustomerIdentity,
        stored: CustomerIdentityBinding,
    ) -> ConversationBinding:
        self.bindings.check_agent(binding)
        target = identity.parlant_customer_id
        upgrading = binding.parlant_customer_id != target
        if upgrading and not (
            identity.is_authenticated
            and stored.saleor_user_id is not None
            and stored.anonymous_customer_id == binding.parlant_customer_id
        ):
            raise ConversationError("Thread belongs to a different customer", http_status=403)
        session = await self.bindings.sessions.get(binding.parlant_session_id)
        if (
            session.id != binding.parlant_session_id
            or session.agent_id != binding.agent_id
            or session.customer_id not in {binding.parlant_customer_id, target}
        ):
            raise ConversationError(
                "Session does not match conversation ownership", http_status=403
            )
        if session.customer_id != target:
            session = await self.bindings.sessions.update_customer(session.id, target)
            if (
                session.id != binding.parlant_session_id
                or session.agent_id != binding.agent_id
                or session.customer_id != target
            ):
                raise IntegrationError("Parlant returned an unexpected session owner")
        if upgrading:
            # Remote first; persisted anonymous provenance permits repair after local failure.
            return await self.bindings.repository.update_customer(
                binding.thread_id,
                expected_customer_id=binding.parlant_customer_id,
                parlant_customer_id=target,
            )
        return binding

    async def resolve(
        self,
        thread_id: str,
        run_id: str,
        identity: ResolvedCustomerIdentity,
        *,
        frontend_state: dict[str, JsonValue] | None = None,
    ) -> ConversationContext:
        identifier.validate_python(thread_id)
        identifier.validate_python(run_id)
        async with self._lock:
            stored = await self._identity(identity)
            binding = await self.bindings.repository.find_by_thread(thread_id)
            if binding is None:
                if identity.status == IdentityStatus.AUTH_UNAVAILABLE:
                    raise ConversationError("Authentication is unavailable", http_status=503)
                binding = await self.bindings.get_or_create_thread_binding(
                    thread_id,
                    identity.parlant_customer_id,
                )
            binding = await self._reconcile(binding, identity, stored)
            return ConversationContext(
                binding=binding,
                identity=identity,
                run_id=run_id,
                frontend_state=frontend_state,
            )

    async def create_conversation(
        self,
        thread_id: str,
        run_id: str,
        identity: ResolvedCustomerIdentity,
        *,
        frontend_state: dict[str, JsonValue] | None = None,
    ) -> ConversationContext:
        """Idempotent creation; a retry always returns the persisted thread/session mapping."""
        return await self.resolve(thread_id, run_id, identity, frontend_state=frontend_state)

    async def reconcile_customer(
        self, thread_id: str, identity: ResolvedCustomerIdentity
    ) -> ConversationBinding:
        identifier.validate_python(thread_id)
        async with self._lock:
            stored = await self._identity(identity)
            binding = await self.bindings.repository.find_by_thread(thread_id)
            if binding is None:
                raise ConversationError("Conversation was not found", http_status=404)
            return await self._reconcile(binding, identity, stored)

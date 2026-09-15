import asyncio
from typing import Protocol

from pydantic import TypeAdapter

from agent_adapter_service.agent.contracts import SessionRecord
from agent_adapter_service.conversations.models import ConversationBinding
from agent_adapter_service.conversations.repository import ConversationBindingRepository
from agent_adapter_service.core.exceptions import ConversationError, IntegrationError
from agent_adapter_service.customer_identity.models import Identifier

identifier = TypeAdapter(Identifier)


class SessionGateway(Protocol):
    async def create(
        self, agent_id: str, customer_id: str, *, title: str | None = None
    ) -> SessionRecord: ...
    async def get(self, session_id: str) -> SessionRecord: ...
    async def update_customer(self, session_id: str, customer_id: str) -> SessionRecord: ...


class ConversationBindingManager:
    def __init__(
        self, repository: ConversationBindingRepository, sessions: SessionGateway, *, agent_id: str
    ) -> None:
        self.repository = repository
        self.sessions = sessions
        self.agent_id = identifier.validate_python(agent_id)
        # One long-lived manager per scope/embedded Host. SQL remains the uniqueness authority.
        self._creation_lock = asyncio.Lock()

    def check_agent(self, binding: ConversationBinding) -> None:
        if binding.agent_id != self.agent_id:
            raise ConversationError("Thread belongs to a different agent")

    async def get_or_create_thread_binding(
        self, thread_id: str, customer_id: str
    ) -> ConversationBinding:
        identifier.validate_python(thread_id)
        identifier.validate_python(customer_id)
        if customer_id == "guest":
            raise ConversationError("An isolated customer is required")
        async with self._creation_lock:
            binding = await self.repository.find_by_thread(thread_id)
            if binding is None:
                session = await self.sessions.create(self.agent_id, customer_id)
                if session.agent_id != self.agent_id or session.customer_id != customer_id:
                    raise IntegrationError("Parlant returned an unexpected session owner")
                binding = await self.repository.get_or_create(
                    ConversationBinding(
                        thread_id=thread_id,
                        parlant_session_id=session.id,
                        parlant_customer_id=customer_id,
                        agent_id=self.agent_id,
                    )
                )
            self.check_agent(binding)
            if binding.parlant_customer_id != customer_id:
                raise ConversationError("Thread belongs to a different customer", http_status=403)
            return binding

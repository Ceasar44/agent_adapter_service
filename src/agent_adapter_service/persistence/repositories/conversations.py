"""Translate M02 storage records into the M05 domain port."""

from agent_adapter_service.conversations.models import ConversationBinding
from agent_adapter_service.core.exceptions import ConversationError
from agent_adapter_service.persistence.contracts import (
    ConversationBinding as StoredBinding,
)
from agent_adapter_service.persistence.contracts import (
    ConversationBindingCreate,
    StoreScope,
)
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.errors import BindingConflictError, BindingNotFoundError
from agent_adapter_service.persistence.repositories.conversation_binding import (
    SqlConversationBindingRepository,
)


def to_domain(record: StoredBinding) -> ConversationBinding:
    return ConversationBinding(
        thread_id=record.agui_thread_id,
        parlant_session_id=record.parlant_session_id,
        parlant_customer_id=record.parlant_customer_id,
        agent_id=record.parlant_agent_id,
    )


class SqlConversationRepository:
    """Composition wrapper; M04 keeps using its existing storage interface."""

    def __init__(self, database: Database, scope: StoreScope) -> None:
        self.storage = SqlConversationBindingRepository(database, scope)

    async def find_by_thread(self, thread_id: str) -> ConversationBinding | None:
        record = await self.storage.find_by_thread(thread_id)
        return to_domain(record) if record else None

    async def find_by_session(self, session_id: str) -> ConversationBinding | None:
        record = await self.storage.find_by_session(session_id)
        return to_domain(record) if record else None

    async def _write(self, binding: ConversationBinding, *, reuse: bool) -> ConversationBinding:
        data = ConversationBindingCreate(
            agui_thread_id=binding.thread_id,
            parlant_session_id=binding.parlant_session_id,
            parlant_customer_id=binding.parlant_customer_id,
            parlant_agent_id=binding.agent_id,
        )
        try:
            method = self.storage.get_or_create if reuse else self.storage.create
            return to_domain(await method(data))
        except BindingConflictError:
            raise ConversationError(
                "Conversation binding conflicts with an existing thread"
            ) from None

    async def create(self, binding: ConversationBinding) -> ConversationBinding:
        return await self._write(binding, reuse=False)

    async def get_or_create(self, binding: ConversationBinding) -> ConversationBinding:
        return await self._write(binding, reuse=True)

    async def update_customer(
        self, thread_id: str, *, expected_customer_id: str, parlant_customer_id: str
    ) -> ConversationBinding:
        try:
            return to_domain(
                await self.storage.update_customer(
                    thread_id,
                    expected_customer_id=expected_customer_id,
                    parlant_customer_id=parlant_customer_id,
                )
            )
        except (BindingConflictError, BindingNotFoundError):
            raise ConversationError("Conversation binding changed or is unavailable") from None

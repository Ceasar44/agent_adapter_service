from typing import Protocol

from agent_adapter_service.conversations.models import ConversationBinding


class ConversationBindingRepository(Protocol):
    """Fixed trusted store scope; get_or_create returns the database winner.

    Customer changes use compare-and-set, never overwrite a different owner.
    """

    async def find_by_thread(self, thread_id: str) -> ConversationBinding | None: ...
    async def find_by_session(self, session_id: str) -> ConversationBinding | None: ...
    async def create(self, binding: ConversationBinding) -> ConversationBinding: ...
    async def get_or_create(self, binding: ConversationBinding) -> ConversationBinding: ...
    async def update_customer(
        self, thread_id: str, *, expected_customer_id: str, parlant_customer_id: str
    ) -> ConversationBinding: ...

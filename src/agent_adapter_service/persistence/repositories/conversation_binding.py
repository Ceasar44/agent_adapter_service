from sqlalchemy import select, update

from agent_adapter_service.persistence.contracts import (
    ConversationBinding,
    ConversationBindingCreate,
    identifier_adapter,
)
from agent_adapter_service.persistence.errors import BindingConflictError, BindingNotFoundError
from agent_adapter_service.persistence.models.conversation_binding import (
    ConversationBindingModel as Model,
)
from agent_adapter_service.persistence.repositories.base import ScopedRepository


class SqlConversationBindingRepository(ScopedRepository):
    async def _find(self, field: str, value: str) -> ConversationBinding | None:
        async with self.database.session() as session:
            row = await session.scalar(
                select(Model).where(
                    *self.scope_conditions(Model),
                    getattr(Model, field) == value,
                )
            )
            return ConversationBinding.model_validate(row) if row is not None else None

    async def find_by_thread(self, thread_id: str) -> ConversationBinding | None:
        return await self._find("agui_thread_id", thread_id)

    async def find_by_session(self, session_id: str) -> ConversationBinding | None:
        return await self._find("parlant_session_id", session_id)

    async def create(self, data: ConversationBindingCreate) -> ConversationBinding:
        async with self.database.session() as session:
            row = Model(**self.scope.model_dump(), **data.model_dump())
            session.add(row)
            await session.flush()
            result = ConversationBinding.model_validate(row)
        return result

    async def get_or_create(self, data: ConversationBindingCreate) -> ConversationBinding:
        """Persist one binding; callers must use the returned canonical session ID.

        This does not create remote Parlant sessions or authorize thread ownership.
        """
        async with self.database.session() as session:
            await self.insert_if_absent(
                session, Model, {**self.scope.model_dump(), **data.model_dump()}, ["agui_thread_id"]
            )
            row = await session.scalar(
                select(Model).where(
                    *self.scope_conditions(Model),
                    Model.agui_thread_id == data.agui_thread_id,
                )
            )
            if (
                row is None
                or row.parlant_customer_id != data.parlant_customer_id
                or row.parlant_agent_id != data.parlant_agent_id
            ):
                raise BindingConflictError("Thread is already bound to a different scope or owner")
            result = ConversationBinding.model_validate(row)
        return result

    async def update_customer(
        self, thread_id: str, *, expected_customer_id: str, parlant_customer_id: str
    ) -> ConversationBinding:
        identifier_adapter.validate_python(parlant_customer_id)
        async with self.database.session() as session:
            row = (
                await session.scalars(
                    update(Model)
                    .where(
                        *self.scope_conditions(Model),
                        Model.agui_thread_id == thread_id,
                        Model.parlant_customer_id == expected_customer_id,
                    )
                    .values(parlant_customer_id=parlant_customer_id)
                    .returning(Model)
                )
            ).one_or_none()
            if row is None:
                exists = await session.scalar(
                    select(Model.agui_thread_id).where(
                        *self.scope_conditions(Model),
                        Model.agui_thread_id == thread_id,
                    )
                )
                if exists is None:
                    raise BindingNotFoundError("Conversation binding was not found")
                raise BindingConflictError("Conversation binding changed concurrently")
            result = ConversationBinding.model_validate(row)
        return result

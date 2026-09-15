from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.models.base import Base


class ScopedRepository:
    def __init__(self, database: Database, scope: StoreScope) -> None:
        self.database = database
        self.scope = scope

    def scope_conditions(self, model: Any) -> tuple:
        return (model.tenant_id == self.scope.tenant_id, model.store_id == self.scope.store_id)

    async def insert_if_absent(
        self,
        session: AsyncSession,
        model: type[Base],
        values: dict[str, Any],
        conflict_columns: list[str],
    ) -> None:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(model)
        elif dialect == "postgresql":
            statement = pg_insert(model)
        else:
            raise ValueError("Only SQLite and PostgreSQL are supported")
        await session.execute(
            statement.values(**values).on_conflict_do_nothing(
                index_elements=conflict_columns,
            )
        )

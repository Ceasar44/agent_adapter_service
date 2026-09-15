"""Atomic nonce insertion across all workers sharing the database."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agent_adapter_service.core.exceptions import AppError
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.models.bff_nonce import BffNonceRow


class SqlBffNonceRepository:
    def __init__(self, database: Database):
        self.database = database

    async def consume_once(self, **values) -> None:
        insert = sqlite_insert if self.database.engine.dialect.name == "sqlite" else pg_insert
        statement = insert(BffNonceRow).values(**values).on_conflict_do_nothing(
            index_elements=["issuer", "audience", "tenant_id", "store_id", "jti"]
        ).returning(BffNonceRow.jti)
        async with self.database.session() as session:
            if (await session.execute(statement)).scalar_one_or_none() is None:
                raise AppError("Request was already used", code="bff_replay", http_status=409)

    async def purge_expired(self, now: int) -> None:
        async with self.database.session() as session:
            await session.execute(delete(BffNonceRow).where(BffNonceRow.expires_at <= now))

    async def healthcheck(self) -> None:
        async with self.database.session() as session:
            await session.execute(select(BffNonceRow).limit(0))

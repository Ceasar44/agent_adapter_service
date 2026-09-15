"""Async transaction ownership and connection lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_adapter_service.persistence.errors import BindingConflictError, PersistenceError
from agent_adapter_service.persistence.models import Base


class Database:
    def __init__(self, url: str, *, create_schema: bool = False) -> None:
        self._url = url
        self._create_schema = create_schema
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise PersistenceError("Database has not been started")
        return self._engine

    async def startup(self) -> None:
        if self._engine is not None:
            return
        try:
            self._engine = create_async_engine(self._url, pool_pre_ping=True, hide_parameters=True)
            if self._engine.dialect.name == "sqlite":
                # Explicit BEGIN avoids legacy sqlite savepoint/transaction behavior.
                @event.listens_for(self._engine.sync_engine, "connect")
                def configure_sqlite(connection, record):
                    connection.isolation_level = None
                    cursor = connection.cursor()
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.execute("PRAGMA busy_timeout=30000")
                    cursor.close()

                @event.listens_for(self._engine.sync_engine, "begin")
                def begin_sqlite(connection):
                    connection.exec_driver_sql("BEGIN")

            self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
            await self.healthcheck()
            if self._create_schema:
                await self.create_schema()
        except BaseException as exc:
            await self.shutdown()
            if isinstance(exc, (SQLAlchemyError, OSError)):
                raise PersistenceError("Database startup failed") from None
            raise

    async def shutdown(self) -> None:
        engine, self._engine = self._engine, None
        self._sessions = None
        if engine is not None:
            await engine.dispose()

    async def create_schema(self) -> None:
        """Create missing tables for local/bootstrap use; this is not a schema migration."""
        try:
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        except SQLAlchemyError:
            raise PersistenceError("Database schema creation failed") from None

    async def healthcheck(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            raise PersistenceError("Database healthcheck failed") from None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """One session per task: commit on success, rollback on error/cancellation."""
        if self._sessions is None:
            raise PersistenceError("Database has not been started")
        try:
            async with self._sessions.begin() as session:
                yield session
        except IntegrityError:
            raise BindingConflictError("Binding violates a storage constraint") from None
        except SQLAlchemyError:
            raise PersistenceError("Database operation failed") from None

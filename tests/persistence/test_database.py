import asyncio
from typing import Annotated

import httpx
import pytest
from fastapi import Depends
from pydantic import ValidationError
from sqlalchemy import func, inspect, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from agent_adapter_service.app.dependencies import get_database
from agent_adapter_service.app.factory import create_app
from agent_adapter_service.core.settings import Settings
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.errors import BindingConflictError, PersistenceError
from agent_adapter_service.persistence.models import (
    Base,
    ConversationBindingModel,
    IdentityBindingModel,
)


async def test_metadata_health_and_shutdown(database):
    assert await database.healthcheck()
    await database.startup()
    await database.create_schema()
    async with database.engine.connect() as connection:
        names = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
    assert set(names) == {
        "identity_binding", "identity_visitor", "conversation_binding", "audit_log", "bff_nonces"
    }
    await database.shutdown()
    await database.shutdown()
    with pytest.raises(PersistenceError, match="not been started"):
        await database.healthcheck()


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_transaction_rollback_on_error_or_cancellation(database, error):
    with pytest.raises(error):
        async with database.session() as session:
            session.add(
                IdentityBindingModel(
                    tenant_id="t", store_id="s", visitor_id="v", parlant_customer_id="c"
                )
            )
            await session.flush()
            raise error()
    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(IdentityBindingModel)) == 0


async def test_commit_persists_after_reopen(database, database_url):
    async with database.session() as session:
        session.add(
            IdentityBindingModel(
                tenant_id="t", store_id="s", visitor_id="v", parlant_customer_id="c"
            )
        )
    await database.shutdown()
    reopened = Database(database_url)
    await reopened.startup()
    try:
        async with reopened.session() as session:
            assert await session.scalar(select(func.count()).select_from(IdentityBindingModel)) == 1
    finally:
        await reopened.shutdown()


async def test_raw_thread_uniqueness(database):
    values = {
        "agui_thread_id": "thread",
        "tenant_id": "t",
        "store_id": "s",
        "parlant_session_id": "session",
        "parlant_customer_id": "customer",
        "parlant_agent_id": "agent",
    }
    async with database.session() as session:
        session.add(ConversationBindingModel(**values))
    with pytest.raises(BindingConflictError):
        async with database.session() as session:
            session.add(ConversationBindingModel(**{**values, "parlant_session_id": "another"}))
    assert await database.healthcheck()


async def test_storage_errors_are_sanitized(database, tmp_path):
    with pytest.raises(PersistenceError) as caught:
        async with database.session() as session:
            await session.execute(text("SELECT private_token FROM missing_table"))
    assert "private_token" not in str(caught.value)
    assert caught.value.__suppress_context__
    invalid = Database(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/missing/private.db")
    with pytest.raises(PersistenceError):
        await invalid.startup()
    with pytest.raises(PersistenceError, match="not been started"):
        _ = invalid.engine


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_metadata_compiles_for_supported_databases(dialect):
    sql = "\n".join(
        str(CreateTable(table).compile(dialect=dialect)) for table in Base.metadata.sorted_tables
    )
    assert "uq_identity_visitor" in sql
    assert "uq_identity_saleor" in sql
    assert "UNIQUE (parlant_session_id)" in sql


async def test_application_database_lifecycle(monkeypatch, tmp_path, database_url):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            database_enabled=True,
            database_url=database_url,
            database_create_schema=True,
        )
    )

    @app.get("/database-probe")
    async def probe(db: Annotated[Database, Depends(get_database)]):
        return {"healthy": await db.healthcheck()}

    async with app.router.lifespan_context(app):
        db = app.state.resources.database
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/health/ready")).status_code == 200
            assert (await client.get("/database-probe")).json() == {"healthy": True}
            await db.shutdown()
            assert (await client.get("/health/ready")).status_code == 503
    assert app.state.resources is None


def test_no_automatic_schema_creation_in_production(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError, match="DATABASE_CREATE_SCHEMA"):
        Settings(_env_file=None, app_env="production", database_create_schema=True)

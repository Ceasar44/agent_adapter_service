"""Run with python -m scripts.migrate_bff_nonce before enabling AG-UI."""

import asyncio

from agent_adapter_service.core.settings import Settings
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.models.bff_nonce import BffNonceRow


async def apply_migration(database: Database) -> None:
    async with database.engine.begin() as connection:
        await connection.run_sync(lambda conn: BffNonceRow.__table__.create(conn, checkfirst=True))


async def main() -> None:
    settings = Settings()
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required")
    database = Database(settings.database_url.get_secret_value())
    try:
        await database.startup()
        await apply_migration(database)
    finally:
        await database.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

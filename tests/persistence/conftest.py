import pytest

from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.persistence.database import Database


@pytest.fixture
def database_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path.as_posix()}/adapter.db"


@pytest.fixture
async def database(database_url):
    db = Database(database_url, create_schema=True)
    await db.startup()
    try:
        yield db
    finally:
        await db.shutdown()


@pytest.fixture
def scope():
    return StoreScope(tenant_id="tenant-a", store_id="store-a")

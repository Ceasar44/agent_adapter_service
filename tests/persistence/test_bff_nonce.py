import asyncio

import pytest

from agent_adapter_service.core.exceptions import AppError
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.repositories.bff_nonce import SqlBffNonceRepository
from scripts.migrate_bff_nonce import apply_migration


async def test_shared_atomic_nonce_expiry_and_failure(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'nonces.db').as_posix()}"
    first, second = Database(url), Database(url)
    await first.startup()
    await second.startup()
    try:
        await apply_migration(first)
        await apply_migration(first)
        repos = [SqlBffNonceRepository(first), SqlBffNonceRepository(second)]
        values = dict(issuer="bff", audience="adapter", tenant_id="t", store_id="s",
                      jti="same", expires_at=65)
        results = await asyncio.gather(
            *(repos[i % 2].consume_once(**values) for i in range(8)), return_exceptions=True
        )
        assert sum(result is None for result in results) == 1
        assert all(result is None or isinstance(result, AppError) and result.http_status == 409
                   for result in results)
        await repos[0].purge_expired(64)
        with pytest.raises(AppError, match="already used"):
            await repos[1].consume_once(**values)
        await repos[0].purge_expired(65)
        await repos[1].consume_once(**values)
    finally:
        await first.shutdown()
        await second.shutdown()
    with pytest.raises(AppError) as error:
        await repos[0].consume_once(**values)
    assert error.value.http_status == 503

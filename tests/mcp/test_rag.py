import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, ANY

import pytest

from agent_adapter_service.core.exceptions import AuthorizationError, IntegrationError
from agent_adapter_service.mcp.clients.rag import RagMcpClient, RagMcpConfig, RetryPolicy


def fake_client():
    return SimpleNamespace(
        __aenter__=AsyncMock(),
        __aexit__=AsyncMock(),
        call_tool=AsyncMock(return_value=SimpleNamespace(is_error=False)),
        list_tools=AsyncMock(return_value=[SimpleNamespace(name="list_collections")]),
    )


async def test_lifecycle_allowlist_and_health():
    sdk = fake_client()
    client = RagMcpClient(RagMcpConfig(), client=sdk)
    with pytest.raises(IntegrationError, match="not connected"):
        await client.healthcheck()
    async with client:
        await client.connect()
        assert await client.healthcheck()
        await client.call_tool("list_collections", {})
        with pytest.raises(AuthorizationError):
            await client.call_tool("delete_document", {})
    await client.close()
    sdk.__aenter__.assert_awaited_once()
    sdk.__aexit__.assert_awaited_once()
    sdk.call_tool.assert_awaited_once_with("list_collections", {}, meta=ANY)


async def test_errors_timeouts_retry_and_cancellation(caplog):
    sdk = fake_client()
    config = RagMcpConfig(timeout_seconds=0.02, retry=RetryPolicy(max_attempts=2, delay_seconds=0))
    async with RagMcpClient(config, client=sdk) as client:
        sdk.list_tools.side_effect = [ConnectionError("secret"), []]
        assert await client.healthcheck()
        assert sdk.list_tools.await_count == 2
        sdk.call_tool.side_effect = RuntimeError("secret")
        with pytest.raises(IntegrationError) as exc:
            await client.call_tool("list_collections", {})
        assert "secret" not in str(exc.value)
        assert sdk.call_tool.await_count == 1

        async def slow(*args, **kwargs):
            await asyncio.sleep(10)

        sdk.call_tool.side_effect = slow
        with pytest.raises(IntegrationError) as exc:
            await client.call_tool("list_collections", {})
        assert exc.value.code == "rag_timeout"
        task = asyncio.create_task(client.call_tool("list_collections", {}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert "secret" not in caplog.text


async def test_real_fastmcp_server():
    fastmcp = pytest.importorskip("fastmcp")
    server = fastmcp.FastMCP("rag-test")

    @server.tool
    def query_knowledge_hub(query: str) -> dict:
        return {"answer": query, "sources": [{"document_id": "doc-1"}]}

    async with RagMcpClient(RagMcpConfig(), client=fastmcp.Client(server)) as client:
        assert await client.healthcheck()
        result = await client.call_tool("query_knowledge_hub", {"query": "hello"})
        assert result.structured_content["answer"] == "hello"


def test_config_rejects_secrets_in_url_and_literal_headers():
    from pydantic import ValidationError

    for data in ({"url": "https://user:secret@example.com/mcp"}, {"headers": {"key": "secret"}}):
        with pytest.raises(ValidationError):
            RagMcpConfig.model_validate(data)


async def test_streamable_http_and_business_api():
    import socket

    import uvicorn

    from agent_adapter_service.rag.client import RagClient

    fastmcp = pytest.importorskip("fastmcp")
    server = fastmcp.FastMCP("rag-http-test")

    @server.tool
    def query_knowledge_hub(query: str) -> dict:
        return {"answer": query, "sources": [{"document_id": "http-doc"}]}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    http_server = uvicorn.Server(
        uvicorn.Config(
            server.http_app(),
            host="127.0.0.1",
            port=port,
            log_level="critical",
            lifespan="on",
            timeout_graceful_shutdown=2,
        )
    )
    task = asyncio.create_task(http_server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not http_server.started:
                if task.done():
                    await task
                    pytest.fail("Test server stopped before startup")
                await asyncio.sleep(0.01)
        config = RagMcpConfig(url=f"http://127.0.0.1:{port}/mcp", timeout_seconds=5)
        async with RagMcpClient(config) as mcp:
            assert await mcp.healthcheck()
            answer = await RagClient(mcp).search_knowledge("hello HTTP")
            assert answer.answer == "hello HTTP"
            assert answer.sources[0].document_id == "http-doc"
    finally:
        http_server.should_exit = True
        try:
            await asyncio.wait_for(task, 10)
        finally:
            sock.close()


def test_settings_config_override_and_safe_errors(tmp_path):
    from agent_adapter_service.core.settings import Settings

    path = tmp_path / "rag.yaml"
    path.write_text("url: http://localhost:1111/mcp\ntimeout_seconds: 2", encoding="utf-8")
    settings = Settings(
        _env_file=None, rag_config_file=path, rag_mcp_url="http://localhost:2222/mcp"
    )
    config = RagMcpConfig.from_settings(settings)
    assert str(config.url) == "http://localhost:2222/mcp"
    assert config.timeout_seconds == 2
    path.write_text("secret: do-not-leak", encoding="utf-8")
    with pytest.raises(IntegrationError) as exc:
        RagMcpConfig.from_settings(settings)
    assert "do-not-leak" not in str(exc.value)


async def test_rag_resource_closes_on_failed_healthcheck(monkeypatch, tmp_path):
    from agent_adapter_service.app.lifespan import AppResources, rag_resource
    from agent_adapter_service.core.settings import Settings

    path = tmp_path / "rag.yaml"
    path.write_text("url: http://localhost:8002/mcp", encoding="utf-8")
    settings = Settings(_env_file=None, rag_config_file=path)
    sdk = fake_client()
    sdk.list_tools.side_effect = RuntimeError("secret")
    monkeypatch.setattr(RagMcpClient, "_build_client", lambda self: sdk)
    with pytest.raises(IntegrationError):
        async with rag_resource(settings, AppResources(settings)):
            pytest.fail("Unhealthy resource must not be exposed")
    sdk.__aexit__.assert_awaited_once()

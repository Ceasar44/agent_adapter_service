from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import SecretStr, ValidationError

from agent_adapter_service.app.factory import create_app
from agent_adapter_service.core.settings import Settings
from agent_adapter_service.mcp.server.app import (
    TOOL_SERVICES,
    build_mcp_asgi_app,
    register_admin_tools,
)
from agent_adapter_service.mcp.server.auth import AdminMcpAuth
from agent_adapter_service.mcp.server.config import AdminServerConfig
from agent_adapter_service.mcp.server.tools.common import AdminToolRuntime
from agent_adapter_service.persistence.contracts import AuditQuery
from agent_adapter_service.persistence.repositories.audit_log import SqlAuditLogRepository
from agent_adapter_service.saleor.models import ProductReference
from agent_adapter_service.security.admin_mcp_policy import AdminMcpPolicy

TOKEN = "test-admin-token-" + "x" * 32

TOOL_ARGUMENTS = {
    "create_product": {"input": {"name": "Jacket", "productType": "type1"}},
    "update_product": {"id": "p1", "input": {"name": "Jacket"}},
    "publish_product": {"id": "p1", "channel_id": "us", "published": True},
    "update_variant": {"id": "v1", "input": {"sku": "SKU"}},
    "update_variant_price": {"id": "v1", "channel_id": "us", "price": "19.99", "currency": "USD"},
    "update_stock": {"variant_id": "v1", "stocks": [{"warehouse": "w1", "quantity": 3}]},
    "get_stock": {},
    "get_order": {"id": "o1"},
    "cancel_order": {"id": "o1"},
    "fulfill_order": {
        "id": "o1",
        "input": {
            "lines": [{"orderLineId": "line1", "stocks": [{"warehouse": "w1", "quantity": 1}]}]
        },
    },
    "get_customer": {"id": "c1"},
    "update_customer": {"id": "c1", "input": {"firstName": "Name"}},
    "create_collection": {"input": {"name": "Collection"}},
    "update_collection": {"id": "col1", "input": {"name": "New"}},
    "add_products_to_collection": {"id": "col1", "products": ["p1"]},
    "create_promotion": {"input": {"name": "Sale", "type": "CATALOGUE"}},
    "update_promotion": {"id": "promo1", "input": {"name": "New sale"}},
    "disable_promotion": {"id": "promo1"},
}


@pytest.fixture
def config():
    return AdminServerConfig.load(Path("configs/mcp/admin_server.yaml"))


@pytest.fixture
def runtime(config):
    auth = AdminMcpAuth({"operator": (SecretStr(TOKEN), config.clients[0].principal)})
    service = SimpleNamespace(
        update_product=AsyncMock(
            return_value=ProductReference(id="p1", name="Jacket", slug="jacket")
        )
    )
    audit = SimpleNamespace(append=AsyncMock())
    return AdminToolRuntime({"product": service}, AdminMcpPolicy(config.store_scope), auth, audit)


async def test_auth_rejects_invalid_and_missing_context(runtime):
    assert await runtime.auth.verify_token("bad") is None
    assert (await runtime.auth.verify_token(TOKEN)).client_id == "operator"
    result = await runtime.invoke("update_product", "product", "update_product", id="p1")
    assert not result["ok"]
    runtime.services["product"].update_product.assert_not_called()


@pytest.mark.parametrize("tool", TOOL_ARGUMENTS)
async def test_each_tool_dispatches_authorized_service_and_audits(
    tool, config, runtime, monkeypatch
):
    method = {"get_order": "get_admin_order", "get_customer": "get_admin_customer"}.get(tool, tool)
    call = AsyncMock(return_value=ProductReference(id="r1", name="result", slug="result"))
    runtime.services = {TOOL_SERVICES[tool]: SimpleNamespace(**{method: call})}
    monkeypatch.setattr(
        runtime.auth, "get_principal", AsyncMock(return_value=config.clients[0].principal)
    )
    server = FastMCP("test")
    register_admin_tools(server, lambda: runtime, {tool})
    async with Client(server) as client:
        result = await client.call_tool(tool, TOOL_ARGUMENTS[tool])
    assert result.data["ok"], result.data
    call.assert_awaited_once()
    assert call.call_args.args[0].principal_id == "operator"
    assert [c.args[0].result_status for c in runtime.audit.append.call_args_list] == [
        "pending",
        "success",
    ]


async def test_official_schema_and_unauthenticated_inprocess(config, runtime):
    server = FastMCP("test", auth=runtime.auth, mask_error_details=True)
    register_admin_tools(server, lambda: runtime, set(config.enabled_tools))
    async with Client(server) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == set(TOOL_SERVICES)
        schema = next(t.input_schema for t in tools if t.name == "update_variant_price")
        assert {"id", "channel_id", "currency", "price"} <= set(schema["required"])
        assert "context" not in schema["properties"]
        result = await client.call_tool("update_product", {"id": "p1", "input": {"name": "new"}})
        assert result.data["ok"] is False
    runtime.services["product"].update_product.assert_not_called()


def http_factory(app):
    def factory(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

    return factory


async def test_http_authenticated_write_and_redacted_failure(config, runtime, caplog):
    app = build_mcp_asgi_app(config, lambda: runtime, runtime.auth)
    async with app.lifespan(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            assert (await http.post("/", json={})).status_code == 401
            assert (
                await http.post("/", headers={"Authorization": "Bearer bad"}, json={})
            ).status_code == 401
        transport = StreamableHttpTransport(
            "http://test/", auth=TOKEN, httpx_client_factory=http_factory(app)
        )
        async with Client(transport) as client:
            result = await client.call_tool(
                "update_product", {"id": "p1", "input": {"name": "private text"}}
            )
            assert result.data["ok"] is True
            events = [call.args[0] for call in runtime.audit.append.call_args_list]
            assert [e.result_status for e in events] == ["pending", "success"]
            assert "private text" not in str(events)
            assert events[0].trace_id == result.data["trace_id"]
            runtime.services["product"].update_product.side_effect = RuntimeError(TOKEN)
            result = await client.call_tool(
                "update_product", {"id": "p1", "input": {"name": "new"}}
            )
            assert not result.data["ok"] and TOKEN not in str(result)
            assert runtime.audit.append.call_args.args[0].result_status == "failure"
            invalid = await client.call_tool(
                "update_product",
                {"id": {"token": TOKEN}, "input": {}},
                raise_on_error=False,
            )
            assert invalid.is_error
            assert TOKEN not in str(invalid)
            assert TOKEN not in caplog.text


async def test_scope_denial_and_audit_fail_closed(config, runtime, monkeypatch):
    principal = config.clients[0].principal
    monkeypatch.setattr(
        runtime.auth,
        "get_principal",
        AsyncMock(return_value=principal.model_copy(update={"scopes": frozenset()})),
    )
    assert not (await runtime.invoke("update_product", "product", "update_product", id="p1"))["ok"]
    assert runtime.audit.append.call_args.args[0].result_status == "denied"
    monkeypatch.setattr(runtime.auth, "get_principal", AsyncMock(return_value=principal))
    runtime.audit.append.side_effect = RuntimeError("database unavailable")
    assert not (await runtime.invoke("update_product", "product", "update_product", id="p1"))["ok"]
    runtime.services["product"].update_product.assert_not_called()


async def test_completed_write_with_failed_final_audit_is_not_reported_as_failure(
    config, runtime, monkeypatch
):
    monkeypatch.setattr(
        runtime.auth, "get_principal", AsyncMock(return_value=config.clients[0].principal)
    )
    runtime.audit.append.side_effect = [None, RuntimeError("audit unavailable")]
    result = await runtime.invoke("update_product", "product", "update_product", id="p1")
    assert result["error"]["code"] == "outcome_unknown"
    runtime.services["product"].update_product.assert_awaited_once()
    assert runtime.audit.append.await_count == 2


async def test_cross_store_principal_cannot_call_or_pollute_audit(config, runtime, monkeypatch):
    principal = config.clients[0].principal.model_copy(update={"store_id": "another-store"})
    monkeypatch.setattr(runtime.auth, "get_principal", AsyncMock(return_value=principal))
    result = await runtime.invoke("update_product", "product", "update_product", id="p1")
    assert result["error"]["code"] == "authorization_error"
    runtime.services["product"].update_product.assert_not_called()
    runtime.audit.append.assert_not_called()


def test_config_rejects_unknown_tool_wrong_scope_and_customer_fields(config):
    from agent_adapter_service.mcp.server.tools.customers import CustomerUpdate

    for field, value in [
        ("enabled_tools", ["unknown"]),
        ("required_scopes", {"update_product": "orders:read"}),
        ("auth_mode", "none"),
    ]:
        with pytest.raises(ValidationError):
            AdminServerConfig.model_validate({**config.model_dump(), field: value})
    with pytest.raises(ValidationError):
        CustomerUpdate(isActive=False)


async def test_application_mount_lifespan_and_persistent_audit(config, tmp_path, monkeypatch):
    import yaml

    data = config.model_dump(mode="json")
    data["enabled_tools"] = ["update_product"]
    data["required_scopes"] = {"update_product": "products:write"}
    path = tmp_path / "admin.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("ADMIN_MCP_OPERATOR_TOKEN", TOKEN)
    settings = Settings(
        _env_file=None,
        admin_mcp_enabled=True,
        admin_mcp_config_file=path,
        database_enabled=True,
        database_create_schema=True,
        database_url="sqlite+aiosqlite:///:memory:",
    )

    @asynccontextmanager
    async def saleor(settings, resources):
        resources.services["product"] = SimpleNamespace(
            update_product=AsyncMock(return_value=ProductReference(id="p1", name="new", slug="new"))
        )
        yield object()

    app = create_app(settings, resource_factories={"saleor_client": saleor})
    async with app.router.lifespan_context(app):
        transport = StreamableHttpTransport(
            "http://test/mcp/", auth=TOKEN, httpx_client_factory=http_factory(app)
        )
        async with Client(transport) as client:
            assert [t.name for t in await client.list_tools()] == ["update_product"]
            result = await client.call_tool(
                "update_product", {"id": "p1", "input": {"name": "new"}}
            )
            assert result.data["ok"]
        audit = SqlAuditLogRepository(app.state.resources.database, config.store_scope)
        events = await audit.search(AuditQuery(tool_name="update_product"))
        assert {e.result_status for e in events} == {"pending", "success"}
    assert app.state.admin_mcp_runtime is None
    assert app.state.resources is None

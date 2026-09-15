from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import SecretStr

from agent_adapter_service.mcp.server.app import build_mcp_asgi_app, create_runtime
from agent_adapter_service.mcp.server.auth import AdminMcpAuth
from agent_adapter_service.mcp.server.config import AdminServerConfig
from agent_adapter_service.persistence.contracts import AuditQuery
from agent_adapter_service.persistence.repositories.audit_log import SqlAuditLogRepository
from tests.mcp.test_admin import TOKEN, http_factory


async def test_authenticated_http_mcp_to_saleor_and_sql_audit(flow):
    config = AdminServerConfig.load(Path("configs/mcp/admin_server.yaml"))
    auth = AdminMcpAuth({"operator": (SecretStr(TOKEN), config.clients[0].principal)})
    audit = SqlAuditLogRepository(flow.agui.harness.database, config.store_scope)
    runtime = create_runtime(config, flow.services, auth, audit)
    app = build_mcp_asgi_app(config, lambda: runtime, auth)
    flow.responses["ProductUpdate"] = {
        "productUpdate": {
            "product": {"id": "p1", "name": "Updated", "slug": "updated"},
            "errors": [],
        },
    }
    async with (
        app.lifespan(app),
        Client(
            StreamableHttpTransport(
                "http://test/",
                auth=TOKEN,
                httpx_client_factory=http_factory(app),
            )
        ) as client,
    ):
        result = await client.call_tool(
            "update_product", {"id": "p1", "input": {"name": "Updated"}}
        )
        assert result.data["ok"] is True
        flow.responses["ProductUpdate"]["productUpdate"]["errors"] = [
            {"field": "name", "message": "secret upstream text", "code": "INVALID"},
        ]
        failed = await client.call_tool(
            "update_product", {"id": "p1", "input": {"name": "Updated"}}
        )
        assert failed.data["ok"] is False
        assert "secret upstream text" not in str(failed)
    assert [body["operationName"] for body, _ in flow.calls] == ["ProductUpdate", "ProductUpdate"]
    rows = await audit.search(AuditQuery())
    assert sorted(row.result_status for row in rows) == ["failure", "pending", "pending", "success"]
    assert "Updated" not in str(rows) and TOKEN not in str(rows)

"""Inspect real tool catalogs and optionally check deployment dependencies."""

import argparse
import os
from collections import Counter

from agent_adapter_service.agent.bootstrap import validate_registry
from agent_adapter_service.agent.config.loader import AgentConfigLoader
from agent_adapter_service.core.settings import Settings
from agent_adapter_service.mcp.clients.rag import RagMcpClient, RagMcpConfig
from agent_adapter_service.mcp.server.config import AdminServerConfig

try:
    from ._common import catalog_registry, run
except ImportError:
    from _common import catalog_registry, run


def check_names(catalogs):
    counts = Counter(name for names in catalogs.values() for name in names)
    conflicts = sorted(name for name, count in counts.items() if count > 1)
    if conflicts:
        raise ValueError("Tool name conflicts: " + ", ".join(conflicts))


async def verify(settings, *, online=False, admin_url=None, admin_token=None):
    from fastmcp import Client, FastMCP
    from agent_adapter_service.mcp.server.app import register_admin_tools

    registry = catalog_registry()
    config = await AgentConfigLoader(settings.parlant_config_dir).aload()
    validate_registry(config, registry)
    await registry.register(config.tools)
    rag_config = RagMcpConfig.from_settings(settings)
    admin_config = AdminServerConfig.load(settings.admin_mcp_config_file)
    server = FastMCP("catalog-only")

    def unavailable():
        raise RuntimeError("Catalog tools cannot be executed")

    register_admin_tools(server, unavailable, set(admin_config.enabled_tools))
    async with Client(server) as client:
        admin_names = [tool.name for tool in await client.list_tools()]
    catalogs = {
        "parlant": sorted(t.name for t in config.tools if t.enabled),
        "rag": sorted(rag_config.allowed_tools),
        "admin": sorted(admin_names),
    }
    check_names(catalogs)
    checks = {"local_configuration": "passed", "remote_dependencies": "not_checked"}
    if online:
        enabled = set(catalogs["parlant"])
        if (
            enabled
            & {
                "search_products",
                "get_product",
                "get_product_variants",
                "get_my_orders",
                "get_my_order",
            }
            or settings.admin_mcp_enabled
        ):
            if not settings.saleor_enabled:
                raise ValueError("Enabled commerce tools require Saleor")
            from agent_adapter_service.app.lifespan import AppResources, saleor_resource
            from agent_adapter_service.saleor.auth import AdminContext

            resources = AppResources(settings)
            async with saleor_resource(settings, resources):
                await resources.services["channel"].list_channels(AdminContext("tool-verifier"))
            checks["saleor"] = "reachable"
        if "search_knowledge" in enabled or settings.rag_enabled:
            if not settings.rag_enabled:
                raise ValueError("Knowledge tool requires RAG")
            token = settings.rag_mcp_token.get_secret_value() if settings.rag_mcp_token else None
            async with RagMcpClient(rag_config, token=token) as client:
                found = [tool.name for tool in await client.list_tools()]
            if set(catalogs["rag"]) - set(found):
                raise ValueError("Configured RAG tools are missing remotely")
            catalogs["rag"] = sorted(found)
            checks["rag"] = "reachable"
        if settings.admin_mcp_enabled or admin_url:
            if not admin_url or not admin_token:
                raise ValueError("Online admin verification requires URL and token")
            from fastmcp.client.transports import StreamableHttpTransport

            async with Client(
                StreamableHttpTransport(admin_url, auth=admin_token),
                timeout=settings.request_timeout_seconds,
            ) as client:
                found = [tool.name for tool in await client.list_tools()]
            if set(found) != set(catalogs["admin"]):
                raise ValueError("Remote Admin catalog differs from local configuration")
            catalogs["admin"] = sorted(found)
            checks["admin"] = "reachable"
        check_names(catalogs)
        checks["remote_dependencies"] = "passed"
    return {"catalogs": catalogs, "checks": checks}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true", help="Also connect to enabled backends")
    parser.add_argument("--admin-url")
    parser.add_argument("--admin-token-env", default="ADMIN_MCP_OPERATOR_TOKEN")
    args = parser.parse_args()
    return await verify(
        Settings(),
        online=args.online,
        admin_url=args.admin_url,
        admin_token=os.environ.get(args.admin_token_env),
    )


if __name__ == "__main__":
    run(main)

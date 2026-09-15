"""Official FastMCP registration and Streamable HTTP integration."""

import os
from collections.abc import Mapping

from fastmcp import FastMCP
from pydantic import SecretStr

from agent_adapter_service.mcp.server.auth import AdminMcpAuth
from agent_adapter_service.mcp.server.config import AdminServerConfig
from agent_adapter_service.mcp.server.privacy import (
    AdminPrivacyMiddleware,
    install_diagnostic_filter,
)
from agent_adapter_service.mcp.server.tools import (
    collections,
    customers,
    inventory,
    orders,
    products,
    promotions,
    variants,
)
from agent_adapter_service.mcp.server.tools.common import AdminToolRuntime, RuntimeProvider
from agent_adapter_service.persistence.contracts import AuditLogRepository
from agent_adapter_service.security.admin_mcp_policy import AdminMcpPolicy

MODULES = (products, variants, inventory, orders, customers, collections, promotions)
TOOL_SERVICES = {
    "create_product": "product",
    "update_product": "product",
    "publish_product": "product",
    "update_variant": "product",
    "update_variant_price": "product",
    "update_stock": "inventory",
    "get_stock": "inventory",
    "get_order": "order",
    "cancel_order": "order",
    "fulfill_order": "order",
    "get_customer": "customer",
    "update_customer": "customer",
    "create_collection": "collection",
    "update_collection": "collection",
    "add_products_to_collection": "collection",
    "create_promotion": "promotion",
    "update_promotion": "promotion",
    "disable_promotion": "promotion",
}


def register_admin_tools(mcp: FastMCP, runtime: RuntimeProvider, enabled: set[str]) -> None:
    if enabled - TOOL_SERVICES.keys():
        raise ValueError("Unsupported admin tool")
    for module in MODULES:
        module.register(mcp, runtime, enabled)


def build_mcp_asgi_app(config: AdminServerConfig, runtime: RuntimeProvider, auth: AdminMcpAuth):
    mcp = FastMCP("Saleor Admin", auth=auth, mask_error_details=True)
    install_diagnostic_filter()
    mcp.add_middleware(AdminPrivacyMiddleware())
    register_admin_tools(mcp, runtime, set(config.enabled_tools))
    # Stateless requests avoid sharing admin state across clients and worker processes.
    return mcp.http_app(path="/", stateless_http=True)


def create_admin_auth(config: AdminServerConfig) -> AdminMcpAuth:
    credentials = {}
    for client in config.clients:
        value = os.environ.get(client.token_env)
        if not value:
            raise ValueError(f"Missing admin credential environment variable: {client.token_env}")
        credentials[client.principal.client_id] = (SecretStr(value), client.principal)
    return AdminMcpAuth(credentials)


def create_runtime(
    config: AdminServerConfig,
    services: Mapping[str, object],
    auth: AdminMcpAuth,
    audit: AuditLogRepository,
) -> AdminToolRuntime:
    required = {TOOL_SERVICES[tool] for tool in config.enabled_tools}
    if required - services.keys():
        raise ValueError("Admin MCP requires configured Saleor services")
    return AdminToolRuntime(services, AdminMcpPolicy(config.store_scope), auth, audit)

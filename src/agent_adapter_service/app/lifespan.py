"""Lifecycle hooks; concrete integrations are supplied by later modules."""

from collections.abc import AsyncIterator, Callable, Mapping
import base64
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from fastapi import FastAPI

from agent_adapter_service.agent.runtime import AgentRuntime
from agent_adapter_service.agent.contracts import ToolRegistry
from agent_adapter_service.core.settings import Settings
from agent_adapter_service.mcp.clients.rag import RagMcpClient, RagMcpConfig
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.rag.client import RagClient
from agent_adapter_service.saleor.client import SaleorGraphQLClient
from agent_adapter_service.saleor.services.registry import create_services

ResourceName = Literal["database", "saleor_client", "rag_client", "parlant_runtime"]
ResourceFactory: TypeAlias = Callable[
    [Settings, "AppResources"], AbstractAsyncContextManager[object]
]
RESOURCE_ORDER: tuple[tuple[ResourceName, str], ...] = (
    ("database", "database_enabled"),
    ("saleor_client", "saleor_enabled"),
    ("rag_client", "rag_enabled"),
    ("parlant_runtime", "parlant_enabled"),
)


@dataclass
class AppResources:
    settings: Settings
    database: object | None = None
    saleor_client: object | None = None
    rag_client: object | None = None
    parlant_runtime: object | None = None
    # A trusted store-specific factory can compose M10 before Parlant starts.
    agent_tool_registry: ToolRegistry | None = None
    # Until domain interfaces exist, use a typed service registry instead of fake implementations.
    services: dict[str, object] = field(default_factory=dict)
    ready: bool = False


@asynccontextmanager
async def database_resource(settings: Settings, resources: AppResources) -> AsyncIterator[Database]:
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required")
    database = Database(
        settings.database_url.get_secret_value(), create_schema=settings.database_create_schema
    )
    try:
        await database.startup()
        yield database
    finally:
        await database.shutdown()


@asynccontextmanager
async def saleor_resource(
    settings: Settings,
    resources: AppResources,
) -> AsyncIterator[SaleorGraphQLClient]:
    if settings.saleor_api_url is None or settings.saleor_service_token is None:
        raise ValueError("Saleor URL and service token are required")
    client = SaleorGraphQLClient(
        str(settings.saleor_api_url),
        settings.saleor_service_token,
        timeout=settings.request_timeout_seconds,
    )
    try:
        resources.services.update(create_services(client))
        yield client
    finally:
        await client.close()


@asynccontextmanager
async def rag_resource(settings: Settings, resources: AppResources) -> AsyncIterator[RagClient]:
    config = RagMcpConfig.from_settings(settings)
    token = settings.rag_mcp_token.get_secret_value() if settings.rag_mcp_token else None
    async with RagMcpClient(config, token=token) as mcp:
        await mcp.healthcheck()
        yield RagClient(mcp)


@asynccontextmanager
async def parlant_resource(
    settings: Settings, resources: AppResources
) -> AsyncIterator[AgentRuntime]:
    runtime = AgentRuntime(settings, tool_registry=resources.agent_tool_registry)
    try:
        await runtime.start()
        yield runtime
    finally:
        await runtime.stop()


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    resources = AppResources(settings=app.state.settings)
    factories: Mapping[ResourceName, ResourceFactory] = app.state.resource_factories
    app.state.resources = resources
    try:
        async with AsyncExitStack() as stack:
            for name, enabled_flag in RESOURCE_ORDER:
                if name == "parlant_runtime" and app.state.agui_scope is not None:
                    from agent_adapter_service.app.agui import prepare_agui_tools

                    prepare_agui_tools(
                        resources, app.state.agui_scope, app.state.agui_confirmations
                    )
                factory = factories.get(name)
                if factory is None and name == "database" and resources.settings.database_enabled:
                    factory = database_resource
                if (
                    factory is None
                    and name == "saleor_client"
                    and resources.settings.saleor_enabled
                ):
                    factory = saleor_resource
                if factory is None and name == "rag_client" and resources.settings.rag_enabled:
                    factory = rag_resource
                if (
                    factory is None
                    and name == "parlant_runtime"
                    and resources.settings.parlant_enabled
                ):
                    factory = parlant_resource
                if factory is None:
                    if getattr(resources.settings, enabled_flag):
                        raise RuntimeError(f"Enabled resource '{name}' has no registered factory")
                    continue
                # Explicit factories also support fakes without enabling real integrations.
                resource = await stack.enter_async_context(factory(resources.settings, resources))
                setattr(resources, name, resource)
                if name == "database" and resources.settings.agui_enabled:
                    from agent_adapter_service.persistence.repositories.bff_nonce import (
                        SqlBffNonceRepository,
                    )
                    from agent_adapter_service.security.storefront_bff import StorefrontBffVerifier

                    if not isinstance(resource, Database):
                        raise RuntimeError("BFF authentication requires a persistent database")
                    nonces = SqlBffNonceRepository(resource)
                    await nonces.healthcheck()
                    config = resources.settings
                    app.state.storefront_bff_verifier = StorefrontBffVerifier(
                        issuer=config.bff_issuer, audience=config.bff_audience,
                        scope=app.state.agui_scope, nonces=nonces,
                        keys={kid: base64.b64decode(key.get_secret_value(), validate=True)
                              for kid, key in config.bff_keys.items()},
                        max_age=config.bff_max_age_seconds,
                        clock_skew=config.bff_clock_skew_seconds,
                    )
            if app.state.agui_scope is not None:
                from agent_adapter_service.app.agui import assemble_agui

                runner = assemble_agui(resources, app.state.agui_scope)
                stack.push_async_callback(runner.close)
            if app.state.admin_mcp_app is not None:
                from agent_adapter_service.mcp.server.app import create_runtime
                from agent_adapter_service.persistence.repositories.audit_log import (
                    SqlAuditLogRepository,
                )

                if not isinstance(resources.database, Database):
                    raise RuntimeError("Admin MCP requires persistent database audit")
                config = app.state.admin_mcp_config
                app.state.admin_mcp_runtime = create_runtime(
                    config, resources.services, app.state.admin_mcp_auth,
                    SqlAuditLogRepository(resources.database, config.store_scope),
                )
                await stack.enter_async_context(app.state.admin_mcp_app.lifespan(app))
            resources.ready = True
            try:
                yield
            finally:
                resources.ready = False
    finally:
        resources.ready = False
        app.state.storefront_bff_verifier = None
        app.state.admin_mcp_runtime = None
        resources.services.clear()
        resources.agent_tool_registry = None
        for name, _ in RESOURCE_ORDER:
            setattr(resources, name, None)
        app.state.resources = None

"""Build the HTTP shell without importing business integrations."""

import logging
from collections.abc import Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent_adapter_service.agent.runtime import AgentRuntime
from agent_adapter_service.app.lifespan import ResourceFactory, ResourceName, app_lifespan
from agent_adapter_service.core.exceptions import AppError
from agent_adapter_service.core.settings import Settings, get_settings
from agent_adapter_service.persistence.database import Database
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.security.frontend_tool_policy import FrontendConfirmationVerifier

from agent_adapter_service.observability.logging import bind_context, configure_logging
from agent_adapter_service.observability.tracing import span, provider as trace_provider
from agent_adapter_service.observability.metrics import (
    provider as meter_provider, reader as metrics_reader,
)

logger = logging.getLogger(__name__)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                if not any(key.lower() == b"x-request-id" for key, _ in headers):
                    headers.append((b"x-request-id", request_id.encode()))
            await send(message)

        with bind_context(request_id=request_id), span("http.request"):
            await self.app(scope, receive, send_with_id)


def register_exception_handlers(app: FastAPI) -> None:
    def response(request: Request, error: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", uuid4().hex)
        return JSONResponse(
            status_code=error.http_status,
            content={
                "error": {"code": error.code, "message": error.message, "details": error.details},
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(AppError)
    async def expected_error(request: Request, exc: AppError) -> JSONResponse:
        return response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic error input/context can contain credentials; do not echo them.
        return response(
            request, AppError("Invalid request", code="validation_error", http_status=422)
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        result = response(
            request, AppError("HTTP request failed", code="http_error", http_status=exc.status_code)
        )
        if exc.headers:
            result.headers.update(exc.headers)
        return result

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # External exception messages may contain secrets. Log correlation and class only.
        logger.error(
            "Unhandled %s request_id=%s",
            type(exc).__name__,
            getattr(request.state, "request_id", "unknown"),
        )
        return response(
            request, AppError("Internal server error", code="internal_error", http_status=500)
        )


def create_app(
    settings: Settings | None = None,
    *,
    resource_factories: Mapping[ResourceName, ResourceFactory] | None = None,
    agui_scope: StoreScope | None = None,
    agui_confirmations: FrontendConfirmationVerifier | None = None,
) -> FastAPI:
    config = settings if settings is not None else get_settings()
    configure_logging(json_output=config.app_env != "development")
    factories = dict(resource_factories or {})
    if set(factories) - {"database", "saleor_client", "rag_client", "parlant_runtime"}:
        raise ValueError("Unknown resource factory name")
    app = FastAPI(title=config.app_name, lifespan=app_lifespan)
    app.state.trace_provider = trace_provider
    app.state.meter_provider = meter_provider
    app.state.metrics_reader = metrics_reader
    app.state.settings = config
    app.state.resource_factories = factories
    app.state.resources = None
    app.state.storefront_bff_verifier = None
    if config.agui_enabled:
        configured_scope = StoreScope(tenant_id=config.agui_tenant_id, store_id=config.agui_store_id)
        if agui_scope is not None and agui_scope != configured_scope:
            raise ValueError("Injected AG-UI scope must match configured BFF scope")
        agui_scope = configured_scope
    app.state.agui_scope = agui_scope
    app.state.agui_confirmations = agui_confirmations
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def readiness() -> dict[str, str]:
        resources = app.state.resources
        if resources is None or not resources.ready:
            raise AppError("Application resources are not ready", code="not_ready", http_status=503)
        if isinstance(resources.database, Database):
            await resources.database.healthcheck()
        if config.agui_enabled:
            verifier = app.state.storefront_bff_verifier
            if verifier is None or resources.services.get("agui_runner") is None:
                raise AppError("AG-UI is not ready", code="not_ready", http_status=503)
            await verifier.nonces.healthcheck()
        if isinstance(resources.parlant_runtime, AgentRuntime):
            resources.parlant_runtime.get_agent()
        return {"status": "ready"}

    from agent_adapter_service.agui.router import router as agui_router

    app.include_router(agui_router)

    app.state.admin_mcp_app = None
    app.state.admin_mcp_runtime = None
    if config.admin_mcp_enabled:
        from agent_adapter_service.mcp.server.app import build_mcp_asgi_app, create_admin_auth
        from agent_adapter_service.mcp.server.config import AdminServerConfig

        admin_config = AdminServerConfig.load(config.admin_mcp_config_file)
        auth = create_admin_auth(admin_config)
        app.state.admin_mcp_config = admin_config
        app.state.admin_mcp_auth = auth
        mcp_app = build_mcp_asgi_app(admin_config, lambda: app.state.admin_mcp_runtime, auth)
        app.state.admin_mcp_app = mcp_app
        app.mount(admin_config.path, mcp_app)
    else:
        @app.api_route("/mcp", methods=["GET", "POST", "DELETE"], tags=["mcp"])
        async def mcp_disabled() -> None:
            raise AppError("Admin MCP is disabled", code="not_enabled", http_status=503)

    return app

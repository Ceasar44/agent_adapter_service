"""RAG connection policy; all MCP protocol handling belongs to FastMCP."""
from agent_adapter_service.observability.tracing import observed, traces
from agent_adapter_service.observability.logging import get_context

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from time import monotonic
from typing import Any, Protocol, Self, TypeVar
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, model_validator

from agent_adapter_service.core.exceptions import AuthorizationError, IntegrationError
from agent_adapter_service.core.settings import Settings

logger = logging.getLogger(__name__)
T = TypeVar("T")


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = Field(default=1, ge=1, le=5)
    delay_seconds: float = Field(default=0.2, ge=0, le=10, allow_inf_nan=False)


class RagMcpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    url: HttpUrl | None = None
    # Header values are environment variable names, never literal credentials.
    headers_env: dict[str, str] = Field(default_factory=dict)
    auth_token_env: str = "RAG_MCP_TOKEN"
    timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    allowed_tools: frozenset[str] = frozenset(
        {
            "query_knowledge_hub",
            "list_collections",
            "get_document_summary",
        }
    )
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.url and (
            self.url.username or self.url.password or self.url.query or self.url.fragment
        ):
            raise ValueError("RAG URL must not contain credentials, query or fragment")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", name) for name in self.allowed_tools):
            raise ValueError("Invalid RAG tool name")
        for header, env in self.headers_env.items():
            if not re.fullmatch(r"[A-Za-z0-9-]+", header) or not env.strip():
                raise ValueError("Invalid header environment mapping")
            if header.lower() in {"authorization", "host", "content-length", "content-type"}:
                raise ValueError("Reserved RAG header")
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        try:
            data = yaml.safe_load(settings.rag_config_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise TypeError("Expected a mapping")
            if settings.rag_mcp_url is not None:
                data["url"] = str(settings.rag_mcp_url)
            return cls.model_validate(data)
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            raise IntegrationError(
                "Invalid RAG configuration", code="rag_configuration_error"
            ) from None


class McpClientProtocol(Protocol):
    async def __aenter__(self) -> Any: ...
    async def __aexit__(self, *args: object) -> Any: ...
    async def call_tool(self, name: str, arguments: dict[str, JsonValue], **kwargs: Any) -> Any: ...
    async def list_tools(self) -> list[Any]: ...


class RagMcpClient:
    def __init__(
        self,
        config: RagMcpConfig,
        *,
        client: McpClientProtocol | None = None,
        token: str | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._token = token
        self._connected = False
        # Serialize close with active calls and connection transitions.
        self._lock = asyncio.Lock()

    def _build_client(self) -> McpClientProtocol:
        try:
            from fastmcp import Client
            from fastmcp.client.transports import StreamableHttpTransport

            if self.config.url is None:
                raise ValueError("Missing URL")
            headers = {key: os.environ[env] for key, env in self.config.headers_env.items()}
            token = self._token or os.environ.get(self.config.auth_token_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
            return Client(
                StreamableHttpTransport(str(self.config.url), headers=headers),
                timeout=self.config.timeout_seconds,
                init_timeout=self.config.timeout_seconds,
            )
        except (ImportError, KeyError, ValueError):
            raise IntegrationError(
                "Cannot configure RAG client", code="rag_configuration_error"
            ) from None

    async def connect(self) -> None:
        async with self._lock:
            if self._connected:
                return
            if self._client is None:
                self._client = self._build_client()
            await self._execute("connect", self._client.__aenter__, retry=False)
            self._connected = True

    async def close(self) -> None:
        async with self._lock:
            if not self._connected:
                return
            try:
                await self._execute(
                    "close", lambda: self._client.__aexit__(None, None, None), retry=False
                )
            finally:
                self._connected = False

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def _execute(
        self,
        operation: str,
        action: Callable[[], Awaitable[T]],
        *,
        retry: bool,
        trace_id: str | None = None,
    ) -> T:
        correlation = get_context().get("trace_id") or trace_id or uuid4().hex
        started = monotonic()
        attempts = self.config.retry.max_attempts if retry else 1
        try:
            for attempt in range(attempts):
                try:
                    async with asyncio.timeout(self.config.timeout_seconds):
                        return await action()
                except (TimeoutError, ConnectionError, OSError):
                    if attempt + 1 == attempts:
                        raise
                    await asyncio.sleep(self.config.retry.delay_seconds)
            raise AssertionError("Unreachable")
        except TimeoutError:
            raise IntegrationError(
                "RAG request timed out", code="rag_timeout", http_status=504
            ) from None
        except Exception:  # noqa: BLE001 -- sanitize all third-party failures at the boundary
            raise IntegrationError("RAG request failed", code="rag_unavailable") from None
        finally:
            logger.info(
                "RAG operation=%s trace_id=%s duration_ms=%.1f",
                operation,
                correlation,
                (monotonic() - started) * 1000,
            )

    @observed("mcp", "rag_tool")
    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        trace_id: str | None = None,
    ) -> Any:
        if name not in self.config.allowed_tools:
            raise AuthorizationError("RAG tool is not allowed", code="rag_tool_denied")
        async with self._lock:
            self._require_connected()
            # Tool calls are never replayed: a configured tool might have side effects.
            result = await self._execute(
                name,
                lambda: self._client.call_tool(name, dict(arguments), meta=traces.inject()),
                retry=False,
                trace_id=trace_id,
            )
            if result.is_error:
                raise IntegrationError("RAG tool failed", code="rag_tool_error")
            return result

    async def list_tools(self) -> list[Any]:
        async with self._lock:
            self._require_connected()
            tools = await self._execute("list_tools", self._client.list_tools, retry=True)
            return [tool for tool in tools if tool.name in self.config.allowed_tools]

    async def healthcheck(self) -> bool:
        await self.list_tools()
        return True

    def _require_connected(self) -> None:
        if not self._connected:
            raise IntegrationError(
                "RAG client is not connected", code="rag_not_connected", http_status=503
            )

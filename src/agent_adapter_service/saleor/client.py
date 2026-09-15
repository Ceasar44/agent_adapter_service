"""Application wrapper over the existing generated client. No retries on writes."""

from agent_adapter_service.observability.tracing import observed, traces
from agent_adapter_service.observability.logging import get_context

import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from .auth import AuthContext, PublicContext, SaleorAuthProvider, ServiceContext
from .errors import SaleorError, SaleorPermissionError, SaleorValidationError, parse_saleor_errors
from .saleor_client.client import Client
from .saleor_client.exceptions import (
    GraphQLClientError,
    GraphQLClientGraphQLMultiError,
    GraphQLClientHttpError,
)

ResultT = TypeVar("ResultT", bound=BaseModel)
_trace_id: ContextVar[str | None] = ContextVar("saleor_trace_id", default=None)


@contextmanager
def saleor_trace(trace_id: str) -> Iterator[None]:
    """Adapters can bind a run/request ID across all service calls in the current task."""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", trace_id):
        raise SaleorValidationError("Invalid trace ID")
    token = _trace_id.set(trace_id)
    try:
        yield
    finally:
        _trace_id.reset(token)


class SaleorGateway(Protocol):
    api: Client

    async def call(
        self,
        operation: Callable[..., Awaitable[ResultT]],
        *,
        context: AuthContext = PublicContext(),
        trace_id: str | None = None,
        **variables: Any,
    ) -> ResultT: ...


class SaleorGraphQLClient:
    """Owns only HTTP clients it creates. Injected clients remain caller-owned.

    Inject a dedicated, cookie-free HTTP client, not a browser session.
    """

    def __init__(
        self,
        url: str,
        service_token: SecretStr,
        *,
        timeout: float = 30,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not 0 < timeout < float("inf"):
            raise ValueError("timeout must be positive and finite")
        self._owned = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient()
        self._timeout = timeout
        self._closed = False
        self._auth = SaleorAuthProvider(service_token)
        self.api = Client(url=url, http_client=self._http)

    @observed("saleor", "graphql")
    async def call(
        self,
        operation: Callable[..., Awaitable[ResultT]],
        *,
        context: AuthContext = PublicContext(),
        trace_id: str | None = None,
        **variables: Any,
    ) -> ResultT:
        if self._closed:
            raise SaleorError("Saleor client is closed")
        headers = self._auth.headers(context)
        trace_id = trace_id if trace_id is not None else (_trace_id.get() or get_context().get("trace_id"))
        if trace_id is not None:
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", trace_id):
                raise SaleorValidationError("Invalid trace ID")
            headers["X-Request-ID"] = trace_id
        headers.update(traces.inject())
        try:
            result = await operation(
                **variables,
                headers=headers,
                timeout=self._timeout,
                auth=None,
                follow_redirects=False,
            )
        except GraphQLClientGraphQLMultiError as exc:
            raise parse_saleor_errors(
                [e.original or {} for e in exc.errors], mutation=False
            ) from None
        except GraphQLClientHttpError as exc:
            cls = SaleorPermissionError if exc.status_code in (401, 403) else SaleorError
            raise cls("Saleor HTTP request failed", details={"status": exc.status_code}) from None
        except httpx.TimeoutException:
            raise SaleorError("Saleor request timed out", code="saleor_timeout") from None
        except (
            httpx.HTTPError,
            GraphQLClientError,
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
        ):
            raise SaleorError("Invalid Saleor response or transport failure") from None
        # Mutation errors are nested below the operation payload, unlike top-level errors.
        for payload in result.model_dump(by_alias=True).values():
            if isinstance(payload, dict) and payload.get("errors"):
                raise parse_saleor_errors(payload["errors"], mutation=True)
        return result

    async def healthcheck(self) -> None:
        """Explicit remote check; requires channel-list permission. Not run at startup."""
        await self.call(self.api.channel_list, context=ServiceContext())

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._owned:
                await self._http.aclose()

"""Shared tool boundary: authorization, bounded JSON results, safe error messages."""

import json
import inspect
from dataclasses import dataclass, field
from contextlib import AsyncExitStack
from opentelemetry import context as otel_context
from agent_adapter_service.observability.audit import AuditService
from agent_adapter_service.observability.logging import bind_context
from agent_adapter_service.observability.tracing import span
from agent_adapter_service.observability.metrics import metrics
from functools import wraps

from pydantic import BaseModel, ValidationError

from agent_adapter_service.agent.config.models import ToolConfig
from agent_adapter_service.agent.tools.context import (
    AgentToolContext,
    AgentToolContextProvider,
    ToolContext,
)
from agent_adapter_service.core.exceptions import AppError, AuthorizationError, IntegrationError
from agent_adapter_service.core.types import RiskLevel
from agent_adapter_service.frontend.state.manager import FrontendStateManager
from agent_adapter_service.frontend.state.models import FrontendState
from agent_adapter_service.frontend.tools.models import FrontendToolCall, FrontendToolError
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.rag.client import RagClient
from agent_adapter_service.saleor.services.order_service import OrderService
from agent_adapter_service.saleor.services.product_service import ProductService
from agent_adapter_service.security.customer_policy import CustomerToolPolicy
from agent_adapter_service.security.frontend_tool_policy import FrontendToolAuthorizationPolicy


@dataclass
class ToolDependencies:
    scope: StoreScope
    contexts: AgentToolContextProvider
    states: FrontendStateManager
    customer_policy: CustomerToolPolicy
    frontend_policy: FrontendToolAuthorizationPolicy
    audit: AuditService | None = field(default=None, kw_only=True)
    products: ProductService | None = None
    orders: OrderService | None = None
    rag: RagClient | None = None
    # Whitelist is activated atomically by AgentToolRegistry.register.
    enabled: dict[str, ToolConfig] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if any(
            scope != self.scope
            for scope in (
                self.states.scope,
                self.customer_policy.store_scope,
                self.frontend_policy.store_scope,
            )
        ):
            raise ValueError("Tool dependencies must belong to the same store")

    async def authorize(
        self,
        context: ToolContext,
        name: str,
        order_id: str | None = None,
    ) -> AgentToolContext:
        if name not in self.enabled:
            raise AuthorizationError("Tool is disabled")
        active = await self.contexts.resolve(context)
        binding = active.conversation.binding
        if (
            active.scope != self.scope
            or context.session_id != binding.parlant_session_id
            or context.customer_id != binding.parlant_customer_id
            or context.agent_id != binding.agent_id
        ):
            raise AuthorizationError("Tool context does not match the authorized run")
        await self.customer_policy.require_tool_access(
            active.authorization, name, order_id=order_id
        )
        return active

    async def state(self, active: AgentToolContext) -> FrontendState | None:
        return await self.states.get(active.conversation.binding.thread_id)

    async def channel(self, active: AgentToolContext) -> str:
        state = await self.state(active)
        if state is None or not state.store.channel:
            raise AppError("Storefront channel is required", code="channel_required")
        # ChannelService in ProductService verifies the channel against Saleor.
        return state.store.channel

    async def frontend(self, active: AgentToolContext, name: str, arguments: dict):
        if active.dispatcher is None:
            raise IntegrationError("Browser connection unavailable")
        config = self.enabled[name]
        risk = RiskLevel(config.risk)
        if config.confirmation == "confirm" and risk == RiskLevel.LOW:
            risk = RiskLevel.MEDIUM
        call = FrontendToolCall(
            name=name,
            arguments=arguments,
            thread_id=active.conversation.binding.thread_id,
            risk=risk,
        )
        normalized = self.frontend_policy.tools.validate_arguments(name, arguments)
        confirmation_id = None
        for approval in active.approvals:
            if (
                approval.call.name == name
                and approval.call.thread_id == call.thread_id
                and approval.call.arguments == normalized
                and approval.call.risk == self.frontend_policy.tools.effective_risk(name, risk)
            ):
                call = approval.call.model_copy(deep=True)
                confirmation_id = approval.confirmation_id
                break
        result = await self.frontend_policy.dispatch(
            active.authorization, call, active.dispatcher, confirmation_id=confirmation_id
        )
        state = await self.state(active)
        return {
            "result": result.model_dump(mode="json", exclude_none=True),
            "frontend_state": None
            if state is None
            else {
                "version": state.version,
                "cart": state.cart.model_dump(mode="json"),
                "checkout": state.checkout.model_dump(mode="json"),
            },
            "authoritative": False,
        }


def required(service):
    if service is None:
        raise IntegrationError("Tool dependency unavailable")
    return service


def page_size(first: int) -> int:
    if type(first) is not int or not 1 <= first <= 20:
        raise AppError("Page size must be between 1 and 20", code="invalid_arguments")
    return first


def compact(value):
    """Bound collections/text while explicitly marking every truncated object."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    truncated = False

    def visit(item, depth=0):
        nonlocal truncated
        if depth > 12:
            truncated = True
            return None
        if isinstance(item, str) and len(item) > 2000:
            truncated = True
            return item[:2000]
        if isinstance(item, list):
            truncated |= len(item) > 20
            return [visit(v, depth + 1) for v in item[:20]]
        if isinstance(item, dict):
            return {k: visit(v, depth + 1) for k, v in item.items()}
        return item

    result = visit(value)
    if truncated:
        return {"result": result, "truncated": True}
    return result


def _tool_boundary(function):
    """Applied before p.tool; preserve SDK signature and cancellation propagation."""

    @wraps(function)
    async def wrapped(*args, **kwargs):
        from parlant.sdk import ToolResult

        try:
            result = await function(*args, **kwargs)
            encoded = json.dumps(
                {"data": result.data, "metadata": result.metadata},
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            if len(encoded) > 65536:
                return ToolResult({"error": "result_too_large", "retry_with_narrower_query": True})
            return result
        except AuthorizationError as exc:
            code = (
                "confirmation_required" if exc.code == "confirmation_required" else "not_authorized"
            )
            return ToolResult({"error": code})
        except (ValidationError, FrontendToolError):
            return ToolResult({"error": "invalid_arguments"})
        except AppError as exc:
            print(
                f"[DIAG] TOOL_ERROR "
                f"tool={function.__name__} "
                f"type={type(exc).__name__} "
                f"code={getattr(exc, 'code', None)}",
                flush=True,
            )
            code = (
                exc.code
                if exc.code in {"invalid_arguments", "channel_required"}
                else "tool_unavailable"
            )
            return ToolResult({"error": code})
        except Exception:
            print(
                f"[DIAG] TOOL_EXCEPTION "
                f"tool={function.__name__} "
                f"type={type(exc).__name__}",
                flush=True,
            )
            # SDK must not serialize arbitrary upstream exceptions/tokens to the model.
            return ToolResult({"error": "tool_failed"})

    return wrapped


def tool_boundary(deps: ToolDependencies):
    def decorate(function):
        @wraps(function)
        async def instrumented(*args, **kwargs):
            context = kwargs.get("context") or args[0]
            active = await deps.contexts.resolve(context)
            token = otel_context.attach(active.telemetry_context)
            try:
                with bind_context(**active.log_context), metrics.measure("tool", function.__name__), span(
                    "customer_tool", {"operation": function.__name__, "surface": "customer_agent"}
                ):
                    async with AsyncExitStack() as stack:
                        config = deps.enabled.get(function.__name__)
                        if config and config.risk == "high":
                            if deps.audit is None:
                                raise IntegrationError("Persistent audit is required")
                            await stack.enter_async_context(deps.audit.tool_call(
                                actor=active.authorization.actor, surface="customer_agent",
                                tool_name=function.__name__, arguments={
                                    k: v for k, v in inspect.signature(function).bind(
                                        *args, **kwargs
                                    ).arguments.items() if k != "context"
                                },
                            ))
                        return await function(*args, **kwargs)
            finally:
                otel_context.detach(token)
        return _tool_boundary(instrumented)
    return decorate

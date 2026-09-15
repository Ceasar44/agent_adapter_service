"""Trusted, run-scoped capabilities. Never populate these objects from tool arguments."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from opentelemetry.context import Context
from opentelemetry import context as otel_context
from agent_adapter_service.observability.logging import get_context
from typing import Protocol

from agent_adapter_service.conversations.models import ConversationContext
from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.frontend.tools.dispatcher import FrontendToolDispatcher
from agent_adapter_service.frontend.tools.models import FrontendToolCall
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.security.authorization import AuthorizationContext


class ToolContext(Protocol):
    agent_id: str
    session_id: str
    customer_id: str


@dataclass(frozen=True)
class FrontendApproval:
    call: FrontendToolCall
    confirmation_id: str


@dataclass(frozen=True)
class AgentToolContext:
    conversation: ConversationContext
    scope: StoreScope
    trace_id: str | None = None
    dispatcher: FrontendToolDispatcher | None = None
    approvals: tuple[FrontendApproval, ...] = ()

    telemetry_context: Context = field(default_factory=otel_context.get_current, repr=False)
    log_context: dict[str, str] = field(default_factory=get_context, repr=False)

    @property
    def authorization(self) -> AuthorizationContext:
        return AuthorizationContext.from_conversation(
            self.conversation, self.scope, trace_id=self.trace_id
        )


class AgentToolContextProvider(Protocol):
    async def resolve(self, context: ToolContext) -> AgentToolContext: ...


class ActiveToolContexts:
    """Bridge to the embedded SDK's separate HTTP tool tasks, without ContextVars.

    M11 binds only after BFF identity and ConversationService checks, keeps the
    binding through processing, and exits on completion/disconnect. Serial runs
    per session prevent ambiguous auth status. Historical identity bindings alone
    never establish current authentication. One instance per trusted store.
    """

    def __init__(self, scope: StoreScope) -> None:
        self.scope = scope
        self._active: dict[str, AgentToolContext] = {}

    @asynccontextmanager
    async def bind(self, context: AgentToolContext) -> AsyncIterator[None]:
        session = context.conversation.binding.parlant_session_id
        if context.scope != self.scope or session in self._active:
            raise AuthorizationError("Invalid or already active tool session")
        # Validate identity/binding agreement before making it available to tools.
        context.authorization
        self._active[session] = context
        try:
            yield
        finally:
            self._active.pop(session, None)

    async def resolve(self, context: ToolContext) -> AgentToolContext:
        active = self._active.get(context.session_id)
        if active is None:
            raise AuthorizationError("No active authorized tool run")
        binding = active.conversation.binding
        if (
            context.customer_id != binding.parlant_customer_id
            or context.agent_id != binding.agent_id
        ):
            raise AuthorizationError("Tool context does not match the authorized run")
        return active

"""Authorization is not authentication: construct contexts only in trusted adapters."""

from collections.abc import Callable
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from agent_adapter_service.conversations.models import ConversationContext
from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.customer_identity.models import ResolvedCustomerIdentity
from agent_adapter_service.persistence.contracts import Identifier, StoreScope


class AuthorizationSurface(StrEnum):
    CUSTOMER_AGENT = "customer_agent"
    FRONTEND_TOOL = "frontend_tool"
    ADMIN_MCP = "admin_mcp"


class AuthorizationContext(BaseModel):
    """Immutable request context; scopes never come from model/browser arguments.

    For customer surfaces actor is the resolved Parlant customer ID. Store scope
    comes from server routing, thread_id from an authorized conversation binding.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Identifier
    surface: AuthorizationSurface
    store_scope: StoreScope
    customer_identity: ResolvedCustomerIdentity | None = None
    scopes: frozenset[Identifier] = frozenset()
    trace_id: Identifier | None = None
    thread_id: Identifier | None = None

    @model_validator(mode="after")
    def separate_surfaces(self) -> Self:
        if self.surface == AuthorizationSurface.ADMIN_MCP:
            if self.customer_identity is not None or self.thread_id is not None:
                raise ValueError("Admin context cannot contain customer identity or thread")
        elif (
            self.customer_identity is None
            or self.actor != self.customer_identity.parlant_customer_id
        ):
            raise ValueError("Customer actor must match the resolved identity")
        return self

    @classmethod
    def from_conversation(
        cls,
        conversation: ConversationContext,
        store_scope: StoreScope,
        *,
        surface: AuthorizationSurface = AuthorizationSurface.CUSTOMER_AGENT,
        trace_id: str | None = None,
    ) -> Self:
        """Call only after ConversationService.resolve has verified ownership."""
        if (
            surface == AuthorizationSurface.ADMIN_MCP
            or conversation.binding.parlant_customer_id != conversation.identity.parlant_customer_id
        ):
            raise AuthorizationError("Conversation identity does not match")
        return cls(
            actor=conversation.identity.parlant_customer_id,
            surface=surface,
            store_scope=store_scope,
            customer_identity=conversation.identity,
            thread_id=conversation.binding.thread_id,
            trace_id=trace_id,
        )


class AuthorizationService:
    """Exact scope checks and pure policy predicates with one public denial type."""

    def check(
        self, context: AuthorizationContext, policy: Callable[[AuthorizationContext], bool]
    ) -> None:
        if not isinstance(context, AuthorizationContext) or policy(context) is not True:
            raise AuthorizationError("Operation is not authorized")

    def require(self, context: AuthorizationContext, scope: str) -> None:
        # No implicit role expansion or wildcard privileges.
        self.check(context, lambda ctx: bool(scope) and scope in ctx.scopes)

"""Authorize browser actions using M08 schemas and risk policy."""

from typing import Protocol
from agent_adapter_service.observability.audit import AuditService
from agent_adapter_service.observability.tracing import observed

from agent_adapter_service.core.exceptions import AuthorizationError
from agent_adapter_service.frontend.tools.dispatcher import FrontendToolDispatcher
from agent_adapter_service.frontend.tools.models import (
    FrontendToolCall,
    FrontendToolError,
    FrontendToolResult,
)
from agent_adapter_service.frontend.tools.policy import FrontendToolPolicy, ToolDecision
from agent_adapter_service.persistence.contracts import StoreScope
from agent_adapter_service.security.authorization import (
    AuthorizationContext,
    AuthorizationService,
    AuthorizationSurface,
)


class FrontendConfirmationVerifier(Protocol):
    """Trusted approval store, supplied by the user-confirmation integration.

    Atomically consume an unexpired, single-use approval bound to store, actor,
    identity, thread, call_id, tool name and normalized arguments/risk. Approval
    issuance requires explicit user interaction; an LLM/browser boolean is not
    evidence. Return False on mismatch/replay. Propagate outages, never allow.
    """

    async def consume(
        self, confirmation_id: str, context: AuthorizationContext, call: FrontendToolCall
    ) -> bool: ...


class FrontendToolAuthorizationPolicy:
    def __init__(
        self,
        store_scope: StoreScope,
        *,
        confirmations: FrontendConfirmationVerifier | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self.audit = audit
        self.store_scope = store_scope
        self.confirmations = confirmations
        self.tools = FrontendToolPolicy()
        self.authorization = AuthorizationService()

    async def require_tool_access(
        self,
        context: AuthorizationContext,
        call: FrontendToolCall,
        *,
        confirmation_id: str | None = None,
    ) -> FrontendToolCall:
        self.authorization.check(
            context,
            lambda ctx: (
                ctx.surface
                in {AuthorizationSurface.CUSTOMER_AGENT, AuthorizationSurface.FRONTEND_TOOL}
                and ctx.store_scope == self.store_scope
                and ctx.customer_identity is not None
                and ctx.actor == ctx.customer_identity.parlant_customer_id
                and ctx.thread_id is not None
                and ctx.thread_id == call.thread_id
            ),
        )
        decision = self.tools.evaluate(call.name, call.risk)
        if decision == ToolDecision.DENY:
            raise AuthorizationError("Frontend tool is not allowed")
        try:
            arguments = self.tools.validate_arguments(call.name, call.arguments)
        except FrontendToolError:
            raise AuthorizationError("Frontend tool arguments are not allowed") from None
        # Copy before awaiting approval; caller mutations cannot change the approved action.
        normalized = call.model_copy(
            update={
                "arguments": arguments,
                "risk": self.tools.effective_risk(call.name, call.risk),
            },
            deep=True,
        )
        if decision == ToolDecision.CONFIRM:
            if (
                not confirmation_id
                or self.confirmations is None
                or await self.confirmations.consume(confirmation_id, context, normalized)
                is not True
            ):
                raise AuthorizationError(
                    "Explicit confirmation of this action is required",
                    code="confirmation_required",
                )
        return normalized

    @observed("tool", "frontend")
    async def dispatch(
        self,
        context: AuthorizationContext,
        call: FrontendToolCall,
        dispatcher: FrontendToolDispatcher,
        *,
        confirmation_id: str | None = None,
    ) -> FrontendToolResult:
        """Use a dispatcher composed for the same trusted store and connection.

        This is the M10/M11 entry point; confirmed is never a tool argument.
        """
        fields = dict(actor=context.actor, surface="frontend_tool", tool_name=call.name,
                      arguments=call.arguments)
        try:
            authorized = await self.require_tool_access(context, call, confirmation_id=confirmation_id)
        except AuthorizationError:
            if self.audit is not None and context.store_scope == self.store_scope:
                await self.audit.record_tool_call(**fields, result_status="denied")
            raise
        if self.audit is None:
            return await dispatcher.dispatch(authorized, confirmed=True)
        await self.audit.record_tool_call(**fields, result_status="pending")
        try:
            result = await dispatcher.dispatch(authorized, confirmed=True)
        except BaseException:
            from anyio import CancelScope
            with CancelScope(shield=True):
                try:
                    await self.audit.record_tool_call(**fields, result_status="failure")
                except Exception:
                    pass  # Preserve cancellation; durable pending records allow reconciliation.
            raise
        await self.audit.record_tool_call(
            **fields, result_status="success" if result.status == "success" else "failure"
        )
        return result

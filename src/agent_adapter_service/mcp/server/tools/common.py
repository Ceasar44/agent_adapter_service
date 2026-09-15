from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import uuid4
from agent_adapter_service.observability.tracing import observed
from agent_adapter_service.observability.logging import get_context
from agent_adapter_service.observability.audit import AuditService

from pydantic import BaseModel, JsonValue, TypeAdapter

from agent_adapter_service.core.exceptions import AppError, AuthorizationError
from agent_adapter_service.mcp.server.auth import AdminMcpAuth
from agent_adapter_service.persistence.contracts import AuditEvent, AuditLogRepository
from agent_adapter_service.saleor.client import saleor_trace
from agent_adapter_service.security.admin_mcp_policy import (
    AdminMcpPolicy,
    sanitize_tool_arguments_for_audit,
)


@dataclass
class AdminToolRuntime:
    services: Mapping[str, object]
    policy: AdminMcpPolicy
    auth: AdminMcpAuth
    audit: AuditLogRepository

    @observed("tool", "admin")
    async def invoke(self, tool: str, service: str, method: str, **arguments) -> dict:
        trace_id = get_context().get("trace_id") or uuid4().hex
        event = None
        completed = False
        try:
            principal = await self.auth.get_principal()
            try:
                context = self.policy.require_tool_access(principal, tool, trace_id=trace_id)
            except AuthorizationError:
                # Never write another tenant's identity into this store's audit log.
                if principal.store_scope == self.policy.store_scope:
                    await AuditService(self.audit).append(
                        AuditEvent(
                            actor=principal.client_id,
                            surface="admin_mcp",
                            tool_name=tool,
                            result_status="denied",
                            trace_id=trace_id,
                        )
                    )
                raise
            summary = {
                k: v.model_dump(mode="json", exclude_unset=True) if isinstance(v, BaseModel) else v
                for k, v in arguments.items()
            }
            summary = TypeAdapter(dict[str, JsonValue]).validate_python(
                TypeAdapter(dict).dump_python(summary, mode="json")
            )
            event = AuditEvent(
                actor=principal.client_id,
                surface="admin_mcp",
                tool_name=tool,
                arguments_summary=sanitize_tool_arguments_for_audit(summary),
                result_status="pending",
                trace_id=trace_id,
            )
            # Fail closed before a side effect if persistent audit is unavailable.
            await AuditService(self.audit).append(event)
            with saleor_trace(trace_id):
                result = await getattr(self.services[service], method)(context, **arguments)
            completed = True
            await AuditService(self.audit).append(event.model_copy(update={"result_status": "success"}))
            return {"ok": True, "data": result.model_dump(mode="json"), "trace_id": trace_id}
        except Exception as exc:
            if event is not None and not completed:
                try:
                    await AuditService(self.audit).append(event.model_copy(update={"result_status": "failure"}))
                except Exception:
                    pass  # The durable pending event remains for reconciliation.
            code = (
                "outcome_unknown"
                if completed
                else "authorization_error"
                if isinstance(exc, AuthorizationError)
                else ("operation_failed" if isinstance(exc, AppError) else "internal_error")
            )
            return {
                "ok": False,
                "error": {"code": code, "message": "Admin operation failed"},
                "trace_id": trace_id,
            }


RuntimeProvider = Callable[[], AdminToolRuntime]

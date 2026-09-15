"""Durable, sanitized audit for all three authorization surfaces."""

from contextlib import asynccontextmanager
from typing import Literal
from pydantic import JsonValue

from agent_adapter_service.persistence.contracts import AuditEvent, AuditLog, AuditLogRepository
from agent_adapter_service.persistence.sanitization import sanitize_arguments
from .logging import get_context


class AuditService:
    def __init__(self, repository: AuditLogRepository):
        self.repository = repository

    async def append(self, event: AuditEvent) -> AuditLog:
        return await self.repository.append(
            event.model_copy(
                update={
                    "arguments_summary": sanitize_arguments(event.arguments_summary),
                    "trace_id": event.trace_id or get_context().get("trace_id"),
                }
            )
        )

    async def record_tool_call(
        self,
        *,
        actor: str,
        surface: Literal["customer_agent", "frontend_tool", "admin_mcp"],
        tool_name: str,
        arguments: dict[str, JsonValue] | None = None,
        result_status: Literal["success", "failure", "denied", "pending"],
        trace_id: str | None = None,
    ) -> AuditLog:
        return await self.append(
            AuditEvent(
                actor=actor,
                surface=surface,
                tool_name=tool_name,
                arguments_summary=sanitize_arguments(arguments or {}),
                result_status=result_status,
                trace_id=trace_id or get_context().get("trace_id"),
            )
        )

    @asynccontextmanager
    async def tool_call(self, **fields):
        # Persist intent before execution; unavailable audit prevents a side effect.
        await self.record_tool_call(**fields, result_status="pending")
        try:
            yield
        except BaseException:
            from anyio import CancelScope

            with CancelScope(shield=True):
                try:
                    await self.record_tool_call(**fields, result_status="failure")
                except Exception:
                    pass  # Pending remains durable for reconciliation.
            raise
        else:
            await self.record_tool_call(**fields, result_status="success")

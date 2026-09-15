from __future__ import annotations

import math
from typing import Literal

from agent_adapter_service.agent.contracts import EventRecord
from agent_adapter_service.agent.parlant.common import GatewayContext, event_record, sdk_errors

EventSource = Literal[
    "customer",
    "customer_ui",
    "ai_agent",
    "human_agent",
    "human_agent_on_behalf_of_ai_agent",
    "system",
]
EventKind = Literal["message", "tool", "status", "custom"]


class ParlantEventGateway:
    def __init__(self, context: GatewayContext) -> None:
        self.context = context

    def _filters(
        self,
        min_offset: int,
        trace_id: str | None,
        source: EventSource | None,
        kinds: tuple[EventKind, ...],
    ) -> dict:
        if min_offset < 0:
            raise ValueError("min_offset must be nonnegative")
        return {
            "min_offset": min_offset,
            "trace_id": trace_id,
            "source": self.context.event_source(source) if source else None,
            "kinds": [self.context.event_kind(kind) for kind in kinds],
        }

    async def list(
        self,
        session_id: str,
        *,
        min_offset: int = 0,
        trace_id: str | None = None,
        source: EventSource | None = None,
        kinds: tuple[EventKind, ...] = (),
    ) -> list[EventRecord]:
        filters = self._filters(min_offset, trace_id, source, kinds)
        with sdk_errors("list_events"):
            events = await self.context.application.sessions.find_events(
                session_id=session_id, **filters
            )
            return [event_record(event) for event in events if not event.deleted]

    async def wait(
        self,
        session_id: str,
        *,
        min_offset: int = 0,
        trace_id: str | None = None,
        source: EventSource | None = None,
        kinds: tuple[EventKind, ...] = (),
        timeout_seconds: float = 30,
    ) -> list[EventRecord]:
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and nonnegative")
        filters = self._filters(min_offset, trace_id, source, kinds)
        with sdk_errors("wait_events"):
            arrived = await self.context.application.sessions.wait_for_more_events(
                session_id=session_id,
                timeout=self.context.timeout(timeout_seconds),
                **filters,
            )
            if not arrived:
                return []
            return await self.list(
                session_id, min_offset=min_offset, trace_id=trace_id, source=source, kinds=kinds
            )

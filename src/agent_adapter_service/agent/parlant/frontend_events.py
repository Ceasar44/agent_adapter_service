from collections.abc import Mapping

from pydantic import JsonValue

from agent_adapter_service.agent.contracts import EventRecord
from agent_adapter_service.agent.parlant.common import GatewayContext, event_record, sdk_errors


class ParlantFrontendEventGateway:
    def __init__(self, context: GatewayContext) -> None:
        self.context = context

    async def create(
        self,
        session_id: str,
        event_type: str,
        data: Mapping[str, JsonValue],
        *,
        trigger_processing: bool = False,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> EventRecord:
        if not event_type.strip():
            raise ValueError("Frontend event type must not be empty")
        with sdk_errors("create_frontend_event"):
            return event_record(
                await self.context.application.sessions.create_event(
                    session_id=session_id,
                    kind=self.context.event_kind.CUSTOM,
                    source=self.context.event_source.CUSTOMER_UI,
                    data={"type": event_type, "data": dict(data)},
                    metadata=dict(metadata or {}),
                    trigger_processing=trigger_processing,
                )
            )

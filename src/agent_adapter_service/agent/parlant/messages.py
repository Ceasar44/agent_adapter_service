from collections.abc import Mapping

from pydantic import JsonValue

from agent_adapter_service.agent.contracts import EventRecord
from agent_adapter_service.agent.parlant.common import GatewayContext, event_record, sdk_errors


class ParlantMessageGateway:
    def __init__(self, context: GatewayContext) -> None:
        self.context = context

    async def create_customer_message(
        self,
        session_id: str,
        message: str,
        *,
        trigger_processing: bool = True,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> EventRecord:
        if not message.strip():
            raise ValueError("Customer message must not be empty")
        with sdk_errors("create_customer_message"):
            return event_record(
                await self.context.application.sessions.create_customer_message(
                    session_id=session_id,
                    message=message,
                    moderation=self.context.moderation.AUTO,
                    source=self.context.event_source.CUSTOMER,
                    trigger_processing=trigger_processing,
                    metadata=dict(metadata or {}),
                )
            )

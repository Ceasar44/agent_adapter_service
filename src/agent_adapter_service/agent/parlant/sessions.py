from typing import Literal

from agent_adapter_service.agent.contracts import SessionRecord
from agent_adapter_service.agent.parlant.common import GatewayContext, sdk_errors


class ParlantSessionGateway:
    def __init__(self, context: GatewayContext) -> None:
        self.context = context

    async def create(
        self, agent_id: str, customer_id: str, *, title: str | None = None
    ) -> SessionRecord:
        if not customer_id.strip() or customer_id == "guest":
            raise ValueError("An isolated customer_id is required")
        with sdk_errors("create_session"):
            await self.context.application.customers.read(customer_id)
            session = await self.context.application.sessions.create(
                customer_id=customer_id,
                agent_id=agent_id,
                title=title,
                allow_greeting=False,
            )
            return SessionRecord.model_validate(session)

    async def get(self, session_id: str) -> SessionRecord:
        with sdk_errors("get_session"):
            return SessionRecord.model_validate(
                await self.context.application.sessions.read(session_id)
            )

    async def update(
        self,
        session_id: str,
        *,
        title: str | None = None,
        mode: Literal["auto", "manual"] | None = None,
    ) -> SessionRecord:
        params = {}
        if title is not None:
            params["title"] = title
        if mode is not None:
            params["mode"] = mode
        with sdk_errors("update_session"):
            return SessionRecord.model_validate(
                await self.context.application.sessions.update(session_id=session_id, params=params)
            )

    async def update_customer(self, session_id: str, customer_id: str) -> SessionRecord:
        if not customer_id.strip() or customer_id == "guest":
            raise ValueError("An isolated customer_id is required")
        with sdk_errors("update_session_customer"):
            await self.context.application.customers.read(customer_id)
            return SessionRecord.model_validate(
                await self.context.application.sessions.update(
                    session_id=session_id,
                    params={"customer_id": customer_id},
                )
            )

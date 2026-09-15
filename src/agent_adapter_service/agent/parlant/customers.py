from collections.abc import Mapping

from agent_adapter_service.agent.contracts import CustomerRecord
from agent_adapter_service.agent.parlant.common import GatewayContext, sdk_errors


class ParlantCustomerGateway:
    def __init__(self, context: GatewayContext, metadata_update_type: type) -> None:
        self.context = context
        self.metadata_update_type = metadata_update_type

    async def find(self, customer_id: str) -> CustomerRecord | None:
        """Only a confirmed not-found permits identity provisioning."""
        from parlant.core.common import ItemNotFoundError

        with sdk_errors("find_customer"):
            try:
                customer = await self.context.application.customers.read(customer_id)
            except ItemNotFoundError:
                return None
            return CustomerRecord.model_validate(customer)

    async def get(self, customer_id: str) -> CustomerRecord:
        with sdk_errors("get_customer"):
            return CustomerRecord.model_validate(
                await self.context.application.customers.read(customer_id)
            )

    async def create(
        self, name: str, *, customer_id: str | None = None, extra: Mapping[str, str] | None = None
    ) -> CustomerRecord:
        if customer_id == "guest":
            raise ValueError("The shared guest customer cannot be used for visitor identity")
        with sdk_errors("create_customer"):
            customer = await self.context.application.customers.create(
                name=name,
                extra=dict(extra or {}),
                tags=None,
                id=customer_id,
            )
            return CustomerRecord.model_validate(customer)

    async def update(
        self, customer_id: str, *, name: str | None = None, extra: Mapping[str, str] | None = None
    ) -> CustomerRecord:
        if customer_id == "guest":
            raise ValueError("The shared guest customer cannot be updated")
        with sdk_errors("update_customer"):
            customer = await self.context.application.customers.update(
                customer_id=customer_id,
                name=name,
                tags=None,
                metadata=self.metadata_update_type(set=dict(extra)) if extra is not None else None,
            )
            return CustomerRecord.model_validate(customer)

from ..auth import AdminContext, CustomerContext, require_admin
from ..client import SaleorGateway
from ..errors import SaleorPermissionError, SaleorValidationError
from ..models import FulfillResult, OrderDetail, OrderMutationResult, OrderSummary, Page
from ..saleor_client.input_types import CustomerOrderWhereInput, OrderFulfillInput
from .common import dto, identifier, limit, page, payload, unique_ids


def customer_id(context: CustomerContext) -> str:
    if not isinstance(context, CustomerContext):
        raise SaleorPermissionError("Customer context required")
    return context.customer_id()


class OrderService:
    def __init__(self, client: SaleorGateway) -> None:
        self.client = client

    async def list_customer_orders(
        self,
        context: CustomerContext,
        *,
        first: int = 20,
        after: str | None = None,
        where: CustomerOrderWhereInput | None = None,
    ) -> Page[OrderSummary]:
        id = customer_id(context)
        limit(first)
        result = await self.client.call(
            self.client.api.orders_by_customer,
            context=context,
            customerId=id,
            first=first,
            after=after,
            where=where,
        )
        if result.user is None or result.user.id != id:
            raise SaleorPermissionError("Customer orders are unavailable")
        return page(OrderSummary, result.user.orders)

    async def assert_order_ownership(self, context: CustomerContext, id: str) -> None:
        owner = customer_id(context)
        result = await self.client.call(
            self.client.api.order_ownership_by_id, context=context, id=identifier(id)
        )
        order = result.order
        if order is None or order.id != id or order.user is None or order.user.id != owner:
            raise SaleorPermissionError("Order is unavailable to this customer")

    async def get_customer_order(self, context: CustomerContext, id: str) -> OrderDetail:
        await self.assert_order_ownership(context, id)
        result = await self.client.call(self.client.api.order_by_id, context=context, id=id)
        order = result.order
        if (
            order is None
            or order.id != id
            or order.user is None
            or order.user.id != customer_id(context)
        ):
            raise SaleorPermissionError("Order is unavailable to this customer")
        return dto(OrderDetail, order)

    async def get_admin_order(self, context: AdminContext, id: str) -> OrderDetail:
        require_admin(context)
        result = await self.client.call(
            self.client.api.order_by_id, context=context, id=identifier(id)
        )
        return dto(OrderDetail, result.order)

    async def cancel_order(self, context: AdminContext, id: str) -> OrderMutationResult:
        require_admin(context)
        order = await self.get_admin_order(context, id)
        if order.status not in {"UNCONFIRMED", "UNFULFILLED", "PARTIALLY_FULFILLED"}:
            raise SaleorValidationError("Order cannot be cancelled in its current state")
        result = await self.client.call(self.client.api.order_cancel, context=context, id=id)
        return dto(OrderMutationResult, payload(result.orderCancel).order)

    async def fulfill_order(
        self,
        context: AdminContext,
        id: str,
        input: OrderFulfillInput,
    ) -> FulfillResult:
        require_admin(context)
        if not input.lines or input.allowStockToBeExceeded is True:
            raise SaleorValidationError("Fulfillment requires lines and cannot exceed stock")
        unique_ids([line.orderLineId for line in input.lines])
        for line in input.lines:
            if not line.stocks:
                raise SaleorValidationError("Fulfillment requires warehouse allocations")
            unique_ids([stock.warehouse for stock in line.stocks])
            if any(stock.quantity <= 0 for stock in line.stocks):
                raise SaleorValidationError("Fulfillment quantities must be positive")
        order = await self.get_admin_order(context, id)
        if order.status not in {"UNFULFILLED", "PARTIALLY_FULFILLED"}:
            raise SaleorValidationError("Order cannot be fulfilled in its current state")
        remaining = {line.id: line.quantity_to_fulfill for line in order.lines}
        for line in input.lines:
            if (
                line.orderLineId not in remaining
                or sum(s.quantity for s in line.stocks) > remaining[line.orderLineId]
            ):
                raise SaleorValidationError("Fulfillment exceeds remaining order line quantity")
        result = await self.client.call(
            self.client.api.order_fulfill, context=context, orderId=id, input=input
        )
        return dto(FulfillResult, payload(result.orderFulfill))

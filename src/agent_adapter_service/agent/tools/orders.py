import parlant.sdk as p

from agent_adapter_service.agent.tools.common import (
    ToolDependencies,
    compact,
    page_size,
    required,
    tool_boundary,
)
from agent_adapter_service.saleor.auth import CustomerContext


def create_tools(deps: ToolDependencies):
    @p.tool
    @tool_boundary(deps)
    async def get_my_orders(
        context: p.ToolContext,
        first: int = 10,
        after: str | None = None,
    ) -> p.ToolResult:
        """List orders for the currently authenticated customer only."""
        active = await deps.authorize(context, "get_my_orders")
        result = await required(deps.orders).list_customer_orders(
            CustomerContext(active.conversation.identity), first=page_size(first), after=after
        )
        return p.ToolResult(compact(result))

    @p.tool
    @tool_boundary(deps)
    async def get_my_order(context: p.ToolContext, order_id: str) -> p.ToolResult:
        """Read an order only after verifying that it belongs to the authenticated customer."""
        active = await deps.authorize(context, "get_my_order", order_id=order_id)
        result = await required(deps.orders).get_customer_order(
            CustomerContext(active.conversation.identity), order_id
        )
        return p.ToolResult(compact(result.model_dump(mode="json", exclude={"user"})))

    return (get_my_orders, get_my_order)

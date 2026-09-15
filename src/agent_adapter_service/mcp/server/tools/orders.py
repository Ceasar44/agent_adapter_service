from fastmcp import FastMCP
from agent_adapter_service.persistence.contracts import Identifier
from agent_adapter_service.saleor.saleor_client.input_types import OrderFulfillInput
from .common import RuntimeProvider


def register(mcp: FastMCP, runtime: RuntimeProvider, enabled: set[str]) -> None:
    if "get_order" in enabled:

        @mcp.tool()
        async def get_order(id: Identifier) -> dict:
            """Get order; requires an authorized admin client."""
            return await runtime().invoke("get_order", "order", "get_admin_order", id=id)

    if "cancel_order" in enabled:

        @mcp.tool()
        async def cancel_order(id: Identifier) -> dict:
            """Cancel order; requires an authorized admin client."""
            return await runtime().invoke("cancel_order", "order", "cancel_order", id=id)

    if "fulfill_order" in enabled:

        @mcp.tool()
        async def fulfill_order(id: Identifier, input: OrderFulfillInput) -> dict:
            """Fulfill order; requires an authorized admin client."""
            return await runtime().invoke(
                "fulfill_order", "order", "fulfill_order", id=id, input=input
            )

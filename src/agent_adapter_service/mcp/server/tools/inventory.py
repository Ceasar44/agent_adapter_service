from fastmcp import FastMCP
from agent_adapter_service.persistence.contracts import Identifier
from agent_adapter_service.saleor.saleor_client.input_types import StockInput, StockFilterInput
from .common import RuntimeProvider


def register(mcp: FastMCP, runtime: RuntimeProvider, enabled: set[str]) -> None:
    if "update_stock" in enabled:

        @mcp.tool()
        async def update_stock(variant_id: Identifier, stocks: list[StockInput]) -> dict:
            """Update stock; requires an authorized admin client."""
            return await runtime().invoke(
                "update_stock", "inventory", "update_stock", variant_id=variant_id, stocks=stocks
            )

    if "get_stock" in enabled:

        @mcp.tool()
        async def get_stock(
            first: int = 20, after: str | None = None, filter: StockFilterInput | None = None
        ) -> dict:
            """Get stock; requires an authorized admin client."""
            return await runtime().invoke(
                "get_stock", "inventory", "get_stock", first=first, after=after, filter=filter
            )

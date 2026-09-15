from fastmcp import FastMCP
from agent_adapter_service.persistence.contracts import Identifier
from agent_adapter_service.saleor.saleor_client.input_types import ProductVariantInput
from .common import RuntimeProvider
from decimal import Decimal


def register(mcp: FastMCP, runtime: RuntimeProvider, enabled: set[str]) -> None:
    if "update_variant" in enabled:

        @mcp.tool()
        async def update_variant(id: Identifier, input: ProductVariantInput) -> dict:
            """Update variant; requires an authorized admin client."""
            return await runtime().invoke(
                "update_variant", "product", "update_variant", id=id, input=input
            )

    if "update_variant_price" in enabled:

        @mcp.tool()
        async def update_variant_price(
            id: Identifier, channel_id: Identifier, price: Decimal, currency: str
        ) -> dict:
            """Update variant price; requires an authorized admin client."""
            return await runtime().invoke(
                "update_variant_price",
                "product",
                "update_variant_price",
                id=id,
                channel_id=channel_id,
                price=price,
                currency=currency,
            )

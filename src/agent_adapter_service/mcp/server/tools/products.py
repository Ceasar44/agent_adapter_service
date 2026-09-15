from fastmcp import FastMCP
from agent_adapter_service.persistence.contracts import Identifier
from agent_adapter_service.saleor.saleor_client.input_types import ProductCreateInput, ProductInput
from .common import RuntimeProvider


def register(mcp: FastMCP, runtime: RuntimeProvider, enabled: set[str]) -> None:
    if "create_product" in enabled:

        @mcp.tool()
        async def create_product(input: ProductCreateInput) -> dict:
            """Create product; requires an authorized admin client."""
            return await runtime().invoke(
                "create_product", "product", "create_product", input=input
            )

    if "update_product" in enabled:

        @mcp.tool()
        async def update_product(id: Identifier, input: ProductInput) -> dict:
            """Update product; requires an authorized admin client."""
            return await runtime().invoke(
                "update_product", "product", "update_product", id=id, input=input
            )

    if "publish_product" in enabled:

        @mcp.tool()
        async def publish_product(id: Identifier, channel_id: Identifier, published: bool) -> dict:
            """Publish product; requires an authorized admin client."""
            return await runtime().invoke(
                "publish_product",
                "product",
                "publish_product",
                id=id,
                channel_id=channel_id,
                published=published,
            )

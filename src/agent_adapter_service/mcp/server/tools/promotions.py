from fastmcp import FastMCP
from agent_adapter_service.persistence.contracts import Identifier
from agent_adapter_service.saleor.saleor_client.input_types import (
    PromotionCreateInput,
    PromotionUpdateInput,
)
from .common import RuntimeProvider


def register(mcp: FastMCP, runtime: RuntimeProvider, enabled: set[str]) -> None:
    if "create_promotion" in enabled:

        @mcp.tool()
        async def create_promotion(input: PromotionCreateInput) -> dict:
            """Create promotion; requires an authorized admin client."""
            return await runtime().invoke(
                "create_promotion", "promotion", "create_promotion", input=input
            )

    if "update_promotion" in enabled:

        @mcp.tool()
        async def update_promotion(id: Identifier, input: PromotionUpdateInput) -> dict:
            """Update promotion; requires an authorized admin client."""
            return await runtime().invoke(
                "update_promotion", "promotion", "update_promotion", id=id, input=input
            )

    if "disable_promotion" in enabled:

        @mcp.tool()
        async def disable_promotion(id: Identifier) -> dict:
            """Disable promotion; requires an authorized admin client."""
            return await runtime().invoke(
                "disable_promotion", "promotion", "disable_promotion", id=id
            )

from fastmcp import FastMCP
from agent_adapter_service.persistence.contracts import Identifier
from agent_adapter_service.saleor.saleor_client.input_types import (
    CollectionCreateInput,
    CollectionInput,
)
from .common import RuntimeProvider
from pydantic import BaseModel, ConfigDict, JsonValue


class CollectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    slug: str | None = None
    description: JsonValue = None


class CollectionCreate(CollectionUpdate):
    name: str
    products: list[Identifier] | None = None


def register(mcp: FastMCP, runtime: RuntimeProvider, enabled: set[str]) -> None:
    if "create_collection" in enabled:

        @mcp.tool()
        async def create_collection(input: CollectionCreate) -> dict:
            """Create collection; requires an authorized admin client."""
            return await runtime().invoke(
                "create_collection",
                "collection",
                "create_collection",
                input=CollectionCreateInput(**input.model_dump(exclude_unset=True)),
            )

    if "update_collection" in enabled:

        @mcp.tool()
        async def update_collection(id: Identifier, input: CollectionUpdate) -> dict:
            """Update collection; requires an authorized admin client."""
            return await runtime().invoke(
                "update_collection",
                "collection",
                "update_collection",
                id=id,
                input=CollectionInput(**input.model_dump(exclude_unset=True)),
            )

    if "add_products_to_collection" in enabled:

        @mcp.tool()
        async def add_products_to_collection(id: Identifier, products: list[Identifier]) -> dict:
            """Add products to collection; requires an authorized admin client."""
            return await runtime().invoke(
                "add_products_to_collection",
                "collection",
                "add_products_to_collection",
                id=id,
                products=products,
            )

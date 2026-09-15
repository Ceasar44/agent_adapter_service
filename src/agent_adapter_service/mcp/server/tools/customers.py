from fastmcp import FastMCP
from agent_adapter_service.persistence.contracts import Identifier
from agent_adapter_service.saleor.saleor_client.input_types import CustomerInput
from .common import RuntimeProvider
from pydantic import BaseModel, ConfigDict


class CustomerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    firstName: str | None = None
    lastName: str | None = None


def register(mcp: FastMCP, runtime: RuntimeProvider, enabled: set[str]) -> None:
    if "get_customer" in enabled:

        @mcp.tool()
        async def get_customer(id: Identifier) -> dict:
            """Get customer; requires an authorized admin client."""
            return await runtime().invoke("get_customer", "customer", "get_admin_customer", id=id)

    if "update_customer" in enabled:

        @mcp.tool()
        async def update_customer(id: Identifier, input: CustomerUpdate) -> dict:
            """Update customer; requires an authorized admin client."""
            return await runtime().invoke(
                "update_customer",
                "customer",
                "update_customer",
                id=id,
                input=CustomerInput(**input.model_dump(exclude_unset=True)),
            )

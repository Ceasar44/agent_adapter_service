import parlant.sdk as p

from agent_adapter_service.agent.tools.common import ToolDependencies, tool_boundary


def create_tools(deps: ToolDependencies):
    @p.tool
    @tool_boundary(deps)
    async def get_cart(context: p.ToolContext) -> p.ToolResult:
        """Read the last browser cart summary, not authoritative prices or checkout totals."""
        active = await deps.authorize(context, "get_cart")
        state = await deps.state(active)
        return p.ToolResult(
            {
                "source": "frontend" if state else "unavailable",
                "authoritative": False,
                "version": state.version if state else None,
                "cart": state.cart.model_dump(mode="json") if state else None,
            }
        )

    @p.tool(consequential=True)
    @tool_boundary(deps)
    async def add_to_cart(
        context: p.ToolContext,
        product_id: str,
        variant_id: str,
        quantity: int = 1,
    ) -> p.ToolResult:
        """Add a variant through the browser only with a verified approval for this exact action."""
        active = await deps.authorize(context, "add_to_cart")
        return p.ToolResult(
            await deps.frontend(
                active,
                "add_to_cart",
                {
                    "product_id": product_id,
                    "variant_id": variant_id,
                    "quantity": quantity,
                },
            )
        )

    return (get_cart, add_to_cart)

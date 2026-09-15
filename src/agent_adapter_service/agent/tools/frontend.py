import parlant.sdk as p

from agent_adapter_service.agent.tools.common import ToolDependencies, tool_boundary


def create_tools(deps: ToolDependencies):
    @p.tool(consequential=True)
    @tool_boundary(deps)
    async def navigate(context: p.ToolContext, pathname: str) -> p.ToolResult:
        """Navigate the browser to a local storefront pathname without query parameters."""
        active = await deps.authorize(context, "navigate")
        return p.ToolResult(await deps.frontend(active, "navigate", {"pathname": pathname}))

    @p.tool(consequential=True)
    @tool_boundary(deps)
    async def open_product(context: p.ToolContext, product_id: str) -> p.ToolResult:
        """Open a product in the customer's browser."""
        active = await deps.authorize(context, "open_product")
        return p.ToolResult(await deps.frontend(active, "open_product", {"product_id": product_id}))

    @p.tool(consequential=True)
    @tool_boundary(deps)
    async def select_variant(
        context: p.ToolContext,
        product_id: str,
        variant_id: str,
    ) -> p.ToolResult:
        """Select a variant in the browser; this does not prove inventory or reserve stock."""
        active = await deps.authorize(context, "select_variant")
        return p.ToolResult(
            await deps.frontend(
                active, "select_variant", {"product_id": product_id, "variant_id": variant_id}
            )
        )

    @p.tool(consequential=True)
    @tool_boundary(deps)
    async def open_cart(context: p.ToolContext) -> p.ToolResult:
        """Open the customer's cart panel in the browser."""
        active = await deps.authorize(context, "open_cart")
        return p.ToolResult(await deps.frontend(active, "open_cart", {}))

    return (navigate, open_product, select_variant, open_cart)

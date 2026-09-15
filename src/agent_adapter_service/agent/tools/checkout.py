import parlant.sdk as p

from agent_adapter_service.agent.tools.common import ToolDependencies, tool_boundary


def create_tools(deps: ToolDependencies):
    @p.tool(consequential=True)
    @tool_boundary(deps)
    async def open_checkout(context: p.ToolContext) -> p.ToolResult:
        """Navigate to checkout. Does not place an order or submit a payment."""
        active = await deps.authorize(context, "open_checkout")
        return p.ToolResult(await deps.frontend(active, "open_checkout", {}))

    @p.tool
    @tool_boundary(deps)
    async def get_checkout_summary(context: p.ToolContext) -> p.ToolResult:
        """Read the last browser checkout step; totals and payment status are unknown."""
        active = await deps.authorize(context, "get_checkout_summary")
        state = await deps.state(active)
        return p.ToolResult(
            {
                "source": "frontend" if state else "unavailable",
                "authoritative": False,
                "version": state.version if state else None,
                "checkout": state.checkout.model_dump(mode="json") if state else None,
            }
        )

    return (open_checkout, get_checkout_summary)
